"""Beta 0.5 war command centre, defence, repair, history and rankings."""
import json
import os
import time
import random
import sqlite3
import io
import hashlib
import calendar

import discord
from discord import app_commands

import war_system
import xbot_ui


WORLD_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ne_110m_admin_0_countries.geojson")
WORLD_PROVINCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ne_10m_admin_1_states_provinces.geojson")
COUNTRY_CAPITALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "country_capitals.json")
CAPITAL_COORDINATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "world-capital-coordinates.json")


def _world_features():
    with open(WORLD_MAP_PATH, "r", encoding="utf-8") as source:
        return json.load(source)["features"]


WORLD_TILE_CACHE = None
WORLD_PROVINCE_CACHE = None
WORLD_CAPITAL_CACHE = None


def _country_key(value):
    """Normalise country labels from Natural Earth and the capital data source."""
    text = str(value or "").casefold().replace("the ", "").replace("republic of ", "")
    return "".join(char for char in text if char.isalnum())


def world_capital_tiles():
    """Return one separate Capital City Land for every country in the world map.

    Capital lands intentionally have a CAP- code instead of an ADM1/state code.
    This lets Copenhagen and Hovedstaden, for example, be two different Lands.
    The bundled capital lists supply both the official city name and its real
    longitude/latitude.  A country label point is only used as a safe fallback
    for an uncommon territory that has no coordinate record.
    """
    global WORLD_CAPITAL_CACHE
    if WORLD_CAPITAL_CACHE is not None:
        return WORLD_CAPITAL_CACHE
    with open(COUNTRY_CAPITALS_PATH, "r", encoding="utf-8") as source:
        raw_capitals = json.load(source)
    with open(CAPITAL_COORDINATES_PATH, "r", encoding="utf-8") as source:
        raw_coordinates = json.load(source)
    capitals = {}
    for row in raw_capitals:
        country, capital = str(row.get("country", "")).strip(), str(row.get("capital", "")).strip()
        if country and capital and _country_key(country) not in capitals:
            capitals[_country_key(country)] = capital
    coordinates = {}
    for row in raw_coordinates:
        iso = str(row.get("countryCode3", "")).strip()
        try:
            lon, lat = float(row["longitude"]), float(row["latitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if iso and -180 <= lon <= 180 and -90 <= lat <= 90:
            coordinates[iso] = (lon, lat)
    aliases = {
        "unitedstatesofamerica": "unitedstates", "russia": "russianfederation",
        "southkorea": "koreasouth", "northkorea": "koreanorth",
        "czechia": "czechrepublic", "ivorycoast": "cotedivoire",
        "democraticrepublicofthecongo": "democraticrepublicofthecongo",
        "tanzania": "unitedrepublicoftanzania", "moldova": "moldova",
        "laos": "laopeoplesdemocraticrepublic", "syria": "syrianarabrepublic",
        "bolivia": "bolivia", "venezuela": "venezuela",
    }
    tiles = {}
    for feature in _world_features():
        props = feature.get("properties") or {}
        country = str(props.get("ADMIN") or props.get("NAME") or "").strip()
        iso = str(props.get("ADM0_A3") or props.get("ISO_A3") or "").strip()
        if not country or not iso or iso == "-99":
            continue
        key = _country_key(country)
        capital = capitals.get(key) or capitals.get(aliases.get(key, ""))
        if not capital:
            continue
        lon, lat = coordinates.get(
            iso, (float(props.get("LABEL_X", 0)), float(props.get("LABEL_Y", 0)))
        )
        code = f"CAP-{iso}"
        tiles[code] = (code, f"{capital} — Capital City, {country}", (lon, lat),
                       (lon - .4, lat - .4, lon + .4, lat + .4))
    WORLD_CAPITAL_CACHE = tiles
    return tiles


def _province_features():
    global WORLD_PROVINCE_CACHE
    if WORLD_PROVINCE_CACHE is None:
        with open(WORLD_PROVINCE_PATH, "r", encoding="utf-8") as source:
            WORLD_PROVINCE_CACHE = json.load(source)["features"]
    return WORLD_PROVINCE_CACHE


def _geometry_polygons(feature):
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    return [coordinates] if geometry.get("type") == "Polygon" else coordinates


def _ring_area_centroid(ring):
    area_twice = centroid_x = centroid_y = 0.0
    for index in range(len(ring) - 1):
        x1, y1 = ring[index][:2]
        x2, y2 = ring[index + 1][:2]
        cross = x1 * y2 - x2 * y1
        area_twice += cross
        centroid_x += (x1 + x2) * cross
        centroid_y += (y1 + y2) * cross
    if abs(area_twice) < 0.000001:
        points = [point[:2] for point in ring] or [(0, 0)]
        return 0.0, (sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points))
    return abs(area_twice) / 2, (centroid_x / (3 * area_twice), centroid_y / (3 * area_twice))


def _feature_meta(feature):
    """Return a stable code, display name, label point and bounds for one real ADM1 region."""
    properties = feature.get("properties") or {}
    polygons = [polygon for polygon in _geometry_polygons(feature) if polygon and polygon[0]]
    if not polygons:
        return None
    ranked = sorted((_ring_area_centroid(polygon[0]) for polygon in polygons), reverse=True)
    _, centre = ranked[0]
    points = [point for polygon in polygons for ring in polygon for point in ring]
    west = min(point[0] for point in points); east = max(point[0] for point in points)
    south = min(point[1] for point in points); north = max(point[1] for point in points)
    code = str(properties.get("adm1_code") or properties.get("gn_id") or properties.get("name") or "").strip()
    # Prefer the official ADM1 name.  Some Natural Earth English aliases are
    # ambiguous (for example both Washington State and the District of
    # Columbia are labelled "Washington" in name_en).
    region = str(properties.get("name") or properties.get("name_en") or code).strip()
    country = str(properties.get("admin") or "").strip()
    region_type = str(properties.get("type_en") or properties.get("type") or "Region").strip()
    if country and country != region:
        name = f"{region} — {region_type}, {country}"
    else:
        name = f"{region} — {region_type}" if region_type else region
    return code, name, centre, (west, south, east, north)


def _point_in_ring(lon, lat, ring):
    """Small dependency-free point-in-polygon test for the country boundaries."""
    inside = False
    previous = len(ring) - 1
    for current, point in enumerate(ring):
        x, y = point[0], point[1]
        px, py = ring[previous][0], ring[previous][1]
        if (y > lat) != (py > lat) and lon < (px - x) * (lat - y) / ((py - y) or 0.000001) + x:
            inside = not inside
        previous = current
    return inside


def _is_land(lon, lat, features):
    for feature in features:
        geometry = feature.get("geometry") or {}
        polygons = geometry.get("coordinates") or []
        if geometry.get("type") == "Polygon":
            polygons = [polygons]
        for polygon in polygons:
            if polygon and _point_in_ring(lon, lat, polygon[0]):
                # Holes (lakes) should not become city sectors.
                if not any(_point_in_ring(lon, lat, hole) for hole in polygon[1:]):
                    return True
    return False


def world_city_tiles(features):
    """Return real first-level administrative regions. One region is exactly one Land."""
    global WORLD_TILE_CACHE
    if WORLD_TILE_CACHE is not None:
        return WORLD_TILE_CACHE
    entries = []
    used_codes = set()
    for index, feature in enumerate(features):
        meta = _feature_meta(feature)
        if meta is None:
            continue
        code, name, centre, bounds = meta
        code = code or f"ADM1-{index}"
        if code in used_codes:
            code = f"{code}-{index}"
        used_codes.add(code)
        entries.append((code, name, centre, bounds))
    # A few source records legitimately share the same complete administrative
    # name. Add the stable ADM1 code only to those ambiguous labels so every
    # Dashboard choice is unique without shortening normal names.
    name_counts = {}
    for _code, name, _centre, _bounds in entries:
        name_counts[name] = name_counts.get(name, 0) + 1
    tiles = {}
    for code, name, centre, bounds in entries:
        unique_name = f"{name} [{code}]" if name_counts[name] > 1 else name
        tiles[code] = (code, unique_name, centre, bounds)
    WORLD_TILE_CACHE = tiles
    return tiles


def _province_polygon(code, bounds, project):
    """Create a stable, shared-edge irregular province shape for one city sector.

    The game still stores one Land as one city sector, but the rendered map no
    longer looks like a spreadsheet. Adjacent provinces share the same jittered
    corner and edge points, so an expanding Nation remains visually connected.
    """
    west, south, east, north = bounds

    def shared_point(lon, lat, axis):
        digest = hashlib.sha256(f"{lon:.3f}:{lat:.3f}:{axis}".encode("ascii")).digest()
        # Keep distortion small enough that province borders cannot fold over.
        lon_jitter = (digest[0] / 255.0 - 0.5) * 0.85
        lat_jitter = (digest[1] / 255.0 - 0.5) * 0.85
        return project((lon + lon_jitter, lat + lat_jitter))

    middle_lon = (west + east) / 2
    middle_lat = (south + north) / 2
    return [
        shared_point(west, north, "corner"),
        shared_point(middle_lon, north, "horizontal"),
        shared_point(east, north, "corner"),
        shared_point(east, middle_lat, "vertical"),
        shared_point(east, south, "corner"),
        shared_point(middle_lon, south, "horizontal"),
        shared_point(west, south, "corner"),
        shared_point(west, middle_lat, "vertical"),
    ]


def sync_map_ownership(db, tile_info):
    """Add missing map ownership without deleting saved player territory.

    Map rendering and search call this helper often. It must therefore be
    repair-only: removing an unfamiliar or temporarily unavailable code here
    can make a Nation's Discord map disappear while its Cities still reference
    those Lands.
    """
    # Capital City Lands are a separate layer from normal state/province Lands.
    # Keep them while synchronising the normal map tiles.
    capital_tiles = world_capital_tiles()
    players = db.execute("""SELECT user_id,nation_name,MAX(0,land) AS desired
        FROM players WHERE nation_name IS NOT NULL AND TRIM(nation_name)<>'' ORDER BY user_id""").fetchall()

    # Codes are the stable database identity; display names may improve over
    # time. Refresh them without moving land or changing its owner/level.
    for code, (_stable_code, full_name, _centre, _bounds) in tile_info.items():
        db.execute("UPDATE map_territories SET territory_name=? WHERE territory_code=?", (full_name, code))
    for code, (_stable_code, full_name, _centre, _bounds) in capital_tiles.items():
        db.execute("UPDATE map_territories SET territory_name=? WHERE territory_code=?", (full_name, code))

    assigned = {row["territory_code"] for row in db.execute("SELECT territory_code FROM map_territories")}
    available = [code for code in tile_info if code not in assigned]
    for player in players:
        user_id = int(player["user_id"])
        desired = max(0, min(int(player["desired"]), len(tile_info)))
        owned = [row["territory_code"] for row in db.execute(
            "SELECT territory_code FROM map_territories WHERE owner_user_id=? ORDER BY is_capital DESC,acquired_at",
            (user_id,),
        )]
        while len(owned) < desired and available:
            valid_owned = [code for code in owned if code in tile_info]
            if not valid_owned:
                digest = hashlib.sha256(str(user_id).encode("ascii")).digest()
                chosen = available[int.from_bytes(digest[:4], "big") % len(available)]
            else:
                owned_points = [tile_info[code][2] for code in valid_owned]
                def distance(code):
                    lon, lat = tile_info[code][2]
                    return min((min(abs(lon - x), 360 - abs(lon - x)) ** 2 + (lat - y) ** 2) for x, y in owned_points)
                chosen = min(available, key=distance)
            code, name, _, _ = tile_info[chosen]
            db.execute("""INSERT OR REPLACE INTO map_territories
                (territory_code,territory_name,owner_user_id,is_capital,acquired_at)
                VALUES(?,?,?,?,?)""", (code, name, user_id, 1 if not owned else 0, int(time.time())))
            owned.append(chosen)
            available.remove(chosen)
    db.commit()


def transfer_one_land(db, attacker_id: int, defender_id: int):
    """Move one real mapped Land after a successful battle.

    Keeping the map row in sync here means a battle does not only change the
    number shown as Land: it changes the actual province/state displayed on
    /map and /map_detail as well.
    """
    territory = db.execute("""SELECT territory_code,territory_name,is_capital
        FROM map_territories WHERE owner_user_id=?
        ORDER BY is_capital ASC, acquired_at ASC, territory_code ASC LIMIT 1""",
        (defender_id,)).fetchone()
    if not territory:
        return None
    db.execute("""UPDATE map_territories SET owner_user_id=?, is_capital=0,
        acquired_at=? WHERE territory_code=?""",
        (attacker_id, int(time.time()), territory["territory_code"]))
    return territory


def _lands_are_connected(first_bounds, second_bounds):
    """Return True when two real map regions touch or are very close together.

    Natural Earth province borders are not always stored with exactly identical
    floating-point coordinates.  The small tolerance prevents a false gap at a
    shared border, while still preventing a Nation from jumping across a sea or
    to another side of the world.
    """
    first_west, first_south, first_east, first_north = first_bounds
    second_west, second_south, second_east, second_north = second_bounds
    longitude_gap = max(0.0, max(first_west, second_west) - min(first_east, second_east))
    latitude_gap = max(0.0, max(first_south, second_south) - min(first_north, second_north))
    return longitude_gap <= 0.18 and latitude_gap <= 0.18


def connected_free_land_codes(db, user_id: int, tiles):
    """Return only unclaimed regions connected to a Nation's current Land."""
    owned_rows = db.execute(
        "SELECT territory_code FROM map_territories WHERE owner_user_id=?", (user_id,)
    ).fetchall()
    owned_codes = {row["territory_code"] for row in owned_rows}
    claimed_codes = {row["territory_code"] for row in db.execute(
        "SELECT territory_code FROM map_territories"
    ).fetchall()}
    # A new Nation starts on a separate Capital City Land (CAP-XXX).  It is not
    # a normal province tile, but it must count as its first border for free
    # expansion.
    ownership_tiles = dict(tiles)
    ownership_tiles.update(world_capital_tiles())
    owned_bounds = [ownership_tiles[code][3] for code in owned_codes if code in ownership_tiles]
    if not owned_bounds:
        return set()
    return {
        code for code, (_code, _name, _centre, bounds) in tiles.items()
        if code not in claimed_codes and any(_lands_are_connected(bounds, owned) for owned in owned_bounds)
    }


def nations_are_adjacent(db, first_user_id: int, second_user_id: int, tiles=None):
    """Whether two Nations share a real Land border on the strategic map."""
    tiles = tiles or world_city_tiles(_province_features())
    ownership_tiles = dict(tiles)
    ownership_tiles.update(world_capital_tiles())
    first_codes = [row["territory_code"] for row in db.execute(
        "SELECT territory_code FROM map_territories WHERE owner_user_id=?", (first_user_id,)
    ).fetchall()]
    second_codes = [row["territory_code"] for row in db.execute(
        "SELECT territory_code FROM map_territories WHERE owner_user_id=?", (second_user_id,)
    ).fetchall()]
    first_bounds = [ownership_tiles[code][3] for code in first_codes if code in ownership_tiles]
    second_bounds = [ownership_tiles[code][3] for code in second_codes if code in ownership_tiles]
    return any(_lands_are_connected(first, second) for first in first_bounds for second in second_bounds)


def active_nation_war(db, first_user_id: int, second_user_id: int = None):
    """Return a current declaration involving one or two Nations, expiring old ones."""
    now = int(time.time())
    db.execute("UPDATE nation_wars SET active=0,ended_at=? WHERE active=1 AND ends_at<=?", (now, now))
    if second_user_id is None:
        return db.execute("""SELECT * FROM nation_wars WHERE active=1
            AND (attacker_id=? OR defender_id=?) ORDER BY id DESC LIMIT 1""",
            (first_user_id, first_user_id)).fetchone()
    return db.execute("""SELECT * FROM nation_wars WHERE active=1 AND
        ((attacker_id=? AND defender_id=?) OR (attacker_id=? AND defender_id=?))
        ORDER BY id DESC LIMIT 1""", (first_user_id, second_user_id, second_user_id, first_user_id)).fetchone()


def _draw_geo_feature(draw, feature, project, fill, outline=None, width=1, hole_fill="#102c3d"):
    for polygon in _geometry_polygons(feature):
        if not polygon:
            continue
        outer = [project(point) for point in polygon[0]]
        if len(outer) < 3:
            continue
        draw.polygon(outer, fill=fill, outline=outline, width=width)
        for hole in polygon[1:]:
            points = [project(point) for point in hole]
            if len(points) >= 3:
                draw.polygon(points, fill=hole_fill, outline=outline, width=1)


class _SafeMapDraw:
    """ImageDraw proxy that keeps maps working when Termux lacks _imagingft."""
    def __init__(self, raw):
        self.raw = raw

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def text(self, xy, text, *args, **kwargs):
        if kwargs.get("font") is None:
            return None
        try:
            return self.raw.text(xy, text, *args, **kwargs)
        except (ImportError, OSError, UnicodeError):
            return None

    def textbbox(self, xy, text, *args, **kwargs):
        if kwargs.get("font") is None:
            return (xy[0], xy[1], xy[0] + len(str(text)) * 7, xy[1] + 14)
        try:
            return self.raw.textbbox(xy, text, *args, **kwargs)
        except (ImportError, OSError, UnicodeError):
            return (xy[0], xy[1], xy[0] + len(str(text)) * 7, xy[1] + 14)

    def textlength(self, text, *args, **kwargs):
        if kwargs.get("font") is None:
            return len(str(text)) * 7
        try:
            return self.raw.textlength(text, *args, **kwargs)
        except (ImportError, OSError, UnicodeError):
            return len(str(text)) * 7


def _safe_map_draw(ImageDraw, image):
    return _SafeMapDraw(ImageDraw.Draw(image))


def _map_fonts():
    try:
        from PIL import ImageFont
    except (ImportError, OSError):
        return (None, None, None, None, None)
    font_path = next((path for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        "/system/fonts/NotoSansCJK-Regular.ttc",
        "/system/fonts/NotoSans-Regular.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/data/data/com.termux/files/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ) if os.path.exists(path)), None)
    try:
        if font_path:
            return (ImageFont.truetype(font_path, 22), ImageFont.truetype(font_path, 15),
                    ImageFont.truetype(font_path, 27), ImageFont.truetype(font_path, 17),
                    ImageFont.truetype(font_path, 13))
        return tuple(ImageFont.load_default() for _ in range(5))
    except (ImportError, OSError):
        print("Pillow font support is unavailable; rendering map without bitmap labels.")
        return (None, None, None, None, None)


def _player_map_colours(db, players=None):
    colours = ["#ff5e5e", "#4de0c1", "#69a8ff", "#ffcf59", "#db8cff", "#ff9657", "#7ed66f",
               "#fb83a8", "#62d2e8", "#d2a36f", "#b89cff", "#f7de69", "#67bd9e", "#ea7aff"]
    rows = players or db.execute("SELECT user_id FROM players ORDER BY user_id").fetchall()
    return {int(row["user_id"]): colours[index % len(colours)]
            for index, row in enumerate(sorted(rows, key=lambda row: int(row["user_id"])))}


def build_strategic_map(db):
    """Render the full world map using persistent Nation territory ownership."""
    try:
        from PIL import Image, ImageDraw, ImageChops, ImageFilter
    except ImportError:
        return None

    if not os.path.exists(WORLD_MAP_PATH):
        return None
    features = _world_features()
    tiles = world_city_tiles(features)
    sync_map_ownership(db, tiles)
    width, height = 1600, 900
    image = Image.new("RGB", (width, height), "#102c3d")
    draw = _safe_map_draw(ImageDraw, image)
    land_mask = Image.new("L", (width, height), 0)
    land_draw = ImageDraw.Draw(land_mask)
    # Microsoft YaHei supports Chinese Nation names on Windows; fall back safely
    # if the dashboard/bot is later moved to another operating system.
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    title_font, small_font, _, _, _ = _map_fonts()
    map_top, map_bottom, margin = 105, 850, 18
    map_width, map_height = width - margin * 2, map_bottom - map_top
    def project(point):
        lon, lat = point
        return (margin + (lon + 180) / 360 * map_width, map_top + (90 - lat) / 180 * map_height)

    for lon in range(-180, 181, 30):
        x, _ = project((lon, 0)); draw.line((x, map_top, x, map_bottom), fill="#1b4657", width=1)
    for lat in range(-60, 91, 30):
        _, y = project((0, lat)); draw.line((margin, y, width - margin, y), fill="#1b4657", width=1)

    players = db.execute("""SELECT user_id,nation_name,land FROM players
        WHERE nation_name IS NOT NULL AND TRIM(nation_name)<>'' ORDER BY land DESC,nation_name""").fetchall()
    ownership = {row["territory_code"]: int(row["owner_user_id"]) for row in db.execute("SELECT * FROM map_territories")}
    colors = ["#ff5e5e", "#4de0c1", "#69a8ff", "#ffcf59", "#db8cff", "#ff9657", "#7ed66f", "#fb83a8", "#62d2e8", "#d2a36f", "#b89cff", "#f7de69", "#67bd9e", "#ea7aff"]
    # Rankings may change after every battle, but a Nation's map colour should not.
    stable_players = sorted(players, key=lambda row: int(row["user_id"]))
    player_colors = {int(row["user_id"]): colors[index % len(colors)] for index, row in enumerate(stable_players)}
    for feature in features:
        geometry = feature.get("geometry") or {}
        polygons = geometry.get("coordinates") or []
        if geometry.get("type") == "Polygon": polygons = [polygons]
        for polygon in polygons:
            if not polygon: continue
            outer = [project(point) for point in polygon[0]]
            if len(outer) >= 3:
                draw.polygon(outer, fill="#394b3b", outline="#849578", width=1)
                land_draw.polygon(outer, fill=255)
                # Preserve lakes and other holes in the world boundary data.
                for hole in polygon[1:]:
                    hole_points = [project(point) for point in hole]
                    if len(hole_points) >= 3:
                        land_draw.polygon(hole_points, fill=0)

    # Merge adjacent city sectors into soft territory regions. The database
    # still counts each sector separately, while the public map shows natural
    # Nation borders instead of visible 4x4-degree squares.
    owned_by_player = {}
    for code, owner_id in ownership.items():
        if code in tiles:
            owned_by_player.setdefault(owner_id, []).append(tiles[code][2])
    for owner_id, centres in owned_by_player.items():
        territory_mask = Image.new("L", (width, height), 0)
        territory_draw = ImageDraw.Draw(territory_mask)
        projected = [project(point) for point in centres]
        radius_x = map_width * 2.7 / 360
        radius_y = map_height * 2.7 / 180
        # Close neighbours become one continuous national territory.
        for index, (x, y) in enumerate(projected):
            territory_draw.ellipse((x - radius_x, y - radius_y, x + radius_x, y + radius_y), fill=255)
            lon, lat = centres[index]
            for other_index in range(index):
                other_lon, other_lat = centres[other_index]
                if abs(lon - other_lon) <= 5 and abs(lat - other_lat) <= 5:
                    territory_draw.line((x, y, projected[other_index][0], projected[other_index][1]), fill=255,
                                        width=max(5, int(radius_y * 1.45)))
        territory_mask = territory_mask.filter(ImageFilter.GaussianBlur(radius=2.2))
        territory_mask = territory_mask.point(lambda value: 255 if value >= 70 else 0)
        territory_mask = ImageChops.multiply(territory_mask, land_mask)
        border_mask = territory_mask.filter(ImageFilter.MaxFilter(7))
        border_mask = ImageChops.subtract(border_mask, territory_mask)
        image.paste("#eef4ed", (0, 0, width, height), border_mask)
        image.paste(player_colors.get(owner_id, "#83958a"), (0, 0, width, height), territory_mask)
    # Continue drawing labels and UI on the newly composited image.
    draw = _safe_map_draw(ImageDraw, image)

    # Capital flags are attached to the oldest retained territory for every Nation.
    for row in players:
        user_id = int(row["user_id"])
        capital = db.execute("""SELECT territory_code FROM map_territories WHERE owner_user_id=?
            ORDER BY is_capital DESC,acquired_at LIMIT 1""", (user_id,)).fetchone()
        if not capital or capital["territory_code"] not in tiles: continue
        x, y = project(tiles[capital["territory_code"]][2])
        color = player_colors[user_id]
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#ffffff", outline="#111111", width=2)
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color)
        label = str(row["nation_name"])[:18]
        draw.rounded_rectangle((x + 10, y - 12, x + 24 + len(label) * 8, y + 14), radius=5, fill="#111a22", outline=color)
        draw.text((x + 17, y - 7), label, fill="#ffffff", font=small_font)

    draw.rectangle((0, 0, width, 100), fill="#14202d")
    draw.rectangle((0, 96, width, 101), fill="#e7a629")
    draw.text((30, 18), "X BOT - WORLD CONQUEST MAP", fill="#f5f7fb", font=title_font)
    claimed = len(ownership)
    draw.text((30, 58), f"{len(players)} Nations - {claimed}/{len(tiles)} City Sectors claimed - 1 Land = 1 City Sector", fill="#9db7c7", font=small_font)
    legend_x = 850
    for index, row in enumerate(players[:7]):
        x = legend_x + (index % 4) * 180; y = 20 + (index // 4) * 35
        draw.rectangle((x, y, x + 18, y + 18), fill=player_colors[int(row["user_id"])])
        draw.text((x + 25, y + 1), str(row["nation_name"])[:16], fill="#f5f7fb", font=small_font)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def build_tactical_map(db, user_id):
    """Render a zoomed hex-sector map around one Nation's owned Land."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    if not os.path.exists(WORLD_MAP_PATH):
        return None

    features = _world_features()
    tiles = world_city_tiles(features)
    sync_map_ownership(db, tiles)
    nation = db.execute("SELECT user_id,nation_name,land,capital_name FROM players WHERE user_id=?", (user_id,)).fetchone()
    if nation is None:
        return None
    owned_rows = db.execute("""SELECT territory_code,is_capital FROM map_territories
        WHERE owner_user_id=? ORDER BY is_capital DESC,acquired_at""", (user_id,)).fetchall()
    if not owned_rows:
        return None

    def cell_position(code):
        parts = code.split("_")
        return int(parts[1]), int(parts[2])

    owned_codes = {row["territory_code"] for row in owned_rows}
    capital_code = next((row["territory_code"] for row in owned_rows if row["is_capital"]), owned_rows[0]["territory_code"])
    owned_positions = [cell_position(code) for code in owned_codes]
    padding = 3
    min_column = min(value[0] for value in owned_positions) - padding
    max_column = max(value[0] for value in owned_positions) + padding
    min_row = min(value[1] for value in owned_positions) - padding
    max_row = max(value[1] for value in owned_positions) + padding
    visible = {
        code: data for code, data in tiles.items()
        if min_column <= cell_position(code)[0] <= max_column and min_row <= cell_position(code)[1] <= max_row
    }

    width, height = 1200, 820
    image = Image.new("RGB", (width, height), "#0d2635")
    draw = _safe_map_draw(ImageDraw, image)
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    _, _, title_font, normal_font, small_font = _map_fonts()
    draw.rectangle((0, 0, width, 112), fill="#151f2d")
    draw.rectangle((0, 108, width, 113), fill="#e7a629")
    draw.text((30, 20), f"TACTICAL MAP - {nation['nation_name']}", fill="#f5f7fb", font=title_font)
    draw.text((30, 66), f"{len(owned_codes)} Land / City sectors  |  Capital: {nation['capital_name']}", fill="#9db7c7", font=normal_font)

    columns = max(1, max_column - min_column + 1)
    rows = max(1, max_row - min_row + 1)
    radius = min(43, (width - 90) / (1.74 * (columns + 0.5)), (height - 170) / (1.5 * rows + 0.5))
    radius = max(16, radius)
    step_x, step_y = 1.732 * radius, 1.5 * radius
    grid_width = step_x * (columns + 0.5)
    grid_height = step_y * max(0, rows - 1) + radius * 2
    origin_x = (width - grid_width) / 2 + radius
    origin_y = 135 + max(0, (height - 155 - grid_height) / 2) + radius

    all_players = db.execute("SELECT user_id FROM players ORDER BY user_id").fetchall()
    colours = ["#ff5e5e", "#4de0c1", "#69a8ff", "#ffcf59", "#db8cff", "#ff9657", "#7ed66f", "#fb83a8", "#62d2e8", "#d2a36f", "#b89cff", "#f7de69", "#67bd9e", "#ea7aff"]
    player_colours = {int(row["user_id"]): colours[index % len(colours)] for index, row in enumerate(all_players)}
    ownership = {row["territory_code"]: int(row["owner_user_id"]) for row in db.execute("SELECT territory_code,owner_user_id FROM map_territories")}

    def hexagon(center_x, center_y):
        import math
        return [
            (center_x + radius * math.cos(math.radians(60 * index - 30)),
             center_y + radius * math.sin(math.radians(60 * index - 30)))
            for index in range(6)
        ]

    for code in sorted(visible, key=lambda value: (cell_position(value)[1], cell_position(value)[0])):
        column, row = cell_position(code)
        local_column, local_row = column - min_column, row - min_row
        center_x = origin_x + step_x * (local_column + 0.5 * (local_row % 2))
        center_y = origin_y + step_y * local_row
        owner_id = ownership.get(code)
        if owner_id == user_id:
            fill, outline, line_width = player_colours.get(user_id, "#4de0c1"), "#ffffff", 3
        elif owner_id is not None:
            fill, outline, line_width = player_colours.get(owner_id, "#786a77"), "#d6a4b3", 2
        else:
            fill, outline, line_width = "#263f48", "#56717b", 2
        draw.polygon(hexagon(center_x, center_y), fill=fill, outline=outline, width=line_width)
        if code == capital_code:
            marker = "★"
            box = draw.textbbox((0, 0), marker, font=title_font)
            draw.text((center_x - (box[2] - box[0]) / 2, center_y - (box[3] - box[1]) / 2 - 4), marker,
                      fill="#111820", stroke_width=1, stroke_fill="#ffffff", font=title_font)
        elif owner_id == user_id and radius >= 28:
            draw.text((center_x - 8, center_y - 9), "●", fill="#eef8f5", font=small_font)

    draw.rounded_rectangle((24, height - 54, width - 24, height - 16), radius=9, fill="#151f2d", outline="#536b78", width=2)
    draw.text((42, height - 44), "★ Capital   Coloured hex = owned City   Dark hex = available Land   Other colour = rival Nation",
              fill="#dce8ed", font=small_font)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def build_real_strategic_map(db):
    """Render Nation territory directly on real province/state polygons."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    if not os.path.exists(WORLD_MAP_PATH) or not os.path.exists(WORLD_PROVINCE_PATH):
        return None
    countries = _world_features()
    provinces = _province_features()
    tiles = world_city_tiles(provinces)
    capital_tiles = world_capital_tiles()
    sync_map_ownership(db, tiles)
    width, height = 1600, 900
    image = Image.new("RGB", (width, height), "#102c3d")
    draw = _safe_map_draw(ImageDraw, image)
    title_font, small_font, _, _, _ = _map_fonts()
    map_top, map_bottom, margin = 105, 850, 18
    map_width, map_height = width - margin * 2, map_bottom - map_top

    def project(point):
        lon, lat = point[:2]
        return margin + (lon + 180) / 360 * map_width, map_top + (90 - lat) / 180 * map_height

    for lon in range(-180, 181, 30):
        x, _ = project((lon, 0))
        draw.line((x, map_top, x, map_bottom), fill="#1b4657", width=1)
    for lat in range(-60, 91, 30):
        _, y = project((0, lat))
        draw.line((margin, y, width - margin, y), fill="#1b4657", width=1)
    for feature in countries:
        _draw_geo_feature(draw, feature, project, "#394b3b", "#849578", 1)

    players = db.execute("""SELECT user_id,nation_name,land FROM players
        WHERE nation_name IS NOT NULL AND TRIM(nation_name)<>'' ORDER BY land DESC,nation_name""").fetchall()
    ownership = {row["territory_code"]: int(row["owner_user_id"])
                 for row in db.execute("SELECT territory_code,owner_user_id FROM map_territories")}
    player_colours = _player_map_colours(db, players)
    feature_by_code = {}
    for feature in provinces:
        meta = _feature_meta(feature)
        if not meta:
            continue
        feature_by_code[meta[0]] = feature
        owner_id = ownership.get(meta[0])
        fill = player_colours.get(owner_id, "#405345") if owner_id else "#405345"
        outline = "#f3f6f3" if owner_id else "#637463"
        _draw_geo_feature(draw, feature, project, fill, outline, 2 if owner_id else 1)

    for row in players:
        user_id = int(row["user_id"])
        capital = db.execute("""SELECT territory_code FROM map_territories WHERE owner_user_id=?
            ORDER BY is_capital DESC,acquired_at LIMIT 1""", (user_id,)).fetchone()
        if not capital:
            continue
        capital_tile = capital_tiles.get(capital["territory_code"]) or tiles.get(capital["territory_code"])
        if not capital_tile:
            continue
        x, y = project(capital_tile[2])
        colour = player_colours[user_id]
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#ffffff", outline="#111111", width=2)
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=colour)
        label = str(row["nation_name"])[:18]
        draw.rounded_rectangle((x + 10, y - 12, x + 24 + len(label) * 8, y + 14), radius=5,
                               fill="#111a22", outline=colour)
        draw.text((x + 17, y - 7), label, fill="#ffffff", font=small_font)

    draw.rectangle((0, 0, width, 100), fill="#14202d")
    draw.rectangle((0, 96, width, 101), fill="#e7a629")
    draw.text((30, 18), "X BOT - REAL WORLD CONQUEST MAP", fill="#f5f7fb", font=title_font)
    draw.text((30, 58),
              f"{len(players)} Nations - {len(ownership)}/{len(tiles)} State / Province Lands claimed · Capital City Lands are separate",
              fill="#9db7c7", font=small_font)
    legend_x = 840
    for index, row in enumerate(players[:8]):
        x = legend_x + (index % 4) * 185
        y = 18 + (index // 4) * 35
        draw.rectangle((x, y, x + 18, y + 18), fill=player_colours[int(row["user_id"])])
        draw.text((x + 25, y + 1), str(row["nation_name"])[:16], fill="#f5f7fb", font=small_font)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def build_real_tactical_map(db, user_id):
    """Render a zoomed real geographic map around a Nation's actual provinces."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    provinces = _province_features()
    tiles = world_city_tiles(provinces)
    capital_tiles = world_capital_tiles()
    all_tiles = {**tiles, **capital_tiles}
    sync_map_ownership(db, tiles)
    nation = db.execute(
        "SELECT user_id,nation_name,land,capital_name FROM players WHERE user_id=?", (user_id,)
    ).fetchone()
    if nation is None:
        return None
    owned_rows = db.execute("""SELECT territory_code,is_capital FROM map_territories
        WHERE owner_user_id=? ORDER BY is_capital DESC,acquired_at""", (user_id,)).fetchall()
    if not owned_rows:
        return None
    owned_codes = {row["territory_code"] for row in owned_rows}
    capital_code = next((row["territory_code"] for row in owned_rows if row["is_capital"]),
                        owned_rows[0]["territory_code"])
    # A capital is a separate city land (CAP-XXX), so it has a point-sized
    # boundary rather than a province polygon.  Include it here as well: a
    # nation that only owns its capital must still be able to open /map_detail.
    owned_bounds = [all_tiles[code][3] for code in owned_codes if code in all_tiles]
    if not owned_bounds:
        return None
    west = min(bounds[0] for bounds in owned_bounds)
    south = min(bounds[1] for bounds in owned_bounds)
    east = max(bounds[2] for bounds in owned_bounds)
    north = max(bounds[3] for bounds in owned_bounds)
    lon_span = max(5.0, east - west)
    lat_span = max(4.0, north - south)
    west -= lon_span * .35; east += lon_span * .35
    south -= lat_span * .35; north += lat_span * .35
    # Reserve the right side for a clean territory list.
    target_ratio = 815 / 660
    if (east - west) / max(.1, north - south) < target_ratio:
        extra = ((north - south) * target_ratio - (east - west)) / 2
        west -= extra; east += extra
    else:
        extra = ((east - west) / target_ratio - (north - south)) / 2
        south -= extra; north += extra
    west = max(-180, west); east = min(180, east)
    south = max(-89, south); north = min(89, north)

    width, height = 1200, 820
    map_top, map_bottom, margin, panel_x = 112, 770, 28, 870
    image = Image.new("RGB", (width, height), "#0d2635")
    draw = _safe_map_draw(ImageDraw, image)
    _, _, title_font, normal_font, small_font = _map_fonts()

    def project(point):
        lon, lat = point[:2]
        return (margin + (lon - west) / max(.01, east - west) * (panel_x - margin * 2),
                map_top + (north - lat) / max(.01, north - south) * (map_bottom - map_top))

    visible = []
    for feature in provinces:
        meta = _feature_meta(feature)
        if not meta:
            continue
        bounds = meta[3]
        if bounds[2] >= west and bounds[0] <= east and bounds[3] >= south and bounds[1] <= north:
            visible.append((feature, meta))
    ownership = {row["territory_code"]: int(row["owner_user_id"])
                 for row in db.execute("SELECT territory_code,owner_user_id FROM map_territories")}
    player_colours = _player_map_colours(db)
    for feature, meta in visible:
        owner_id = ownership.get(meta[0])
        fill = player_colours.get(owner_id, "#263f48") if owner_id else "#263f48"
        outline = "#ffffff" if meta[0] in owned_codes else ("#d8b1bd" if owner_id else "#5c7580")
        _draw_geo_feature(draw, feature, project, fill, outline, 3 if meta[0] in owned_codes else 1)
    ordered_codes = sorted(
        owned_codes,
        key=lambda value: (value != capital_code, all_tiles.get(value, ("", value))[1]),
    )
    # The geographic map uses compact numbered markers. Full names stay in the
    # side panel, so small European/Asian provinces never create a text pile.
    for number, code in enumerate(ordered_codes, 1):
        if code not in all_tiles:
            continue
        x, y = project(all_tiles[code][2])
        radius = 11 if code == capital_code else 9
        draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                     fill="#111a22", outline="#ffffff", width=2)
        marker = "★" if code == capital_code else str(number)
        marker_box = draw.textbbox((0, 0), marker, font=small_font)
        draw.text((x - (marker_box[2] - marker_box[0]) / 2,
                   y - (marker_box[3] - marker_box[1]) / 2 - 2),
                  marker, fill="#ffffff", font=small_font)

    # Territory intelligence panel.
    draw.rounded_rectangle((panel_x, map_top + 12, width - 22, map_bottom),
                           radius=12, fill="#151f2d", outline="#536b78", width=2)
    draw.text((panel_x + 20, map_top + 28), "TERRITORY CONTROL", fill="#f5f7fb", font=normal_font)
    draw.line((panel_x + 18, map_top + 62, width - 40, map_top + 62), fill="#536b78", width=1)
    visible_limit = 20
    for index, code in enumerate(ordered_codes[:visible_limit]):
        region_name = all_tiles.get(code, ("", code))[1]
        region, _, country = region_name.partition(", ")
        y = map_top + 78 + index * 27
        badge = "★" if code == capital_code else str(index + 1)
        draw.rounded_rectangle((panel_x + 18, y - 3, panel_x + 47, y + 20), radius=5,
                               fill=player_colours.get(user_id, "#4de0c1"))
        draw.text((panel_x + 27 - draw.textlength(badge, font=small_font) / 2, y + 1),
                  badge, fill="#101820", font=small_font)
        draw.text((panel_x + 57, y), region[:25], fill="#f5f7fb", font=small_font)
        if country:
            draw.text((panel_x + 57, y + 14), country[:25], fill="#829bab", font=small_font)
    if len(ordered_codes) > visible_limit:
        draw.text((panel_x + 20, map_top + 78 + visible_limit * 27),
                  f"+ {len(ordered_codes) - visible_limit} more regions", fill="#e7a629", font=small_font)

    draw.rectangle((0, 0, width, 108), fill="#151f2d")
    draw.rectangle((0, 106, width, 112), fill="#e7a629")
    draw.text((30, 19), f"TACTICAL MAP - {nation['nation_name']}", fill="#f5f7fb", font=title_font)
    draw.text((30, 67), f"{len(owned_codes)} real regions  |  Capital: {nation['capital_name']}",
              fill="#9db7c7", font=normal_font)
    draw.rounded_rectangle((24, height - 43, width - 24, height - 10), radius=8,
                           fill="#151f2d", outline="#536b78", width=2)
    draw.text((42, height - 35),
              "★ Capital   Number = Territory List   Bright border = your real province/state",
              fill="#dce8ed", font=small_font)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def build_tactical_map_safe(db, user_id):
    """Render the real map when possible, with a compact phone-safe fallback."""
    try:
        image = build_real_tactical_map(db, user_id)
        if image is not None:
            return image
        print(f"Real tactical map returned no image for {user_id}; using compact fallback.")
    except Exception as error:
        print(f"Real tactical map fallback for {user_id}: {error}")
    try:
        return build_tactical_summary_map(db, user_id)
    except Exception as fallback_error:
        print(f"Tactical map fallback also failed for {user_id}: {fallback_error}")
        return None


def build_nation_map_resilient(db, user_id):
    """Always prefer a Nation map, then fall back to the proven World map."""
    image = build_tactical_map_safe(db, user_id)
    if image is not None:
        return image, True
    try:
        image = build_real_strategic_map(db)
        if image is not None:
            print(f"Using World map fallback for Nation {user_id}.")
            return image, False
    except Exception as error:
        print(f"World map fallback also failed for Nation {user_id}: {error}")
    return None, False


def nation_land_summary(db, user_id, limit=18):
    rows = db.execute("""SELECT territory_name,is_capital,level FROM map_territories
        WHERE owner_user_id=? ORDER BY is_capital DESC,acquired_at,territory_name LIMIT ?""",
        (user_id, limit)).fetchall()
    if not rows:
        return "No claimed Land is recorded for this Nation."
    lines = [f"{'🏛️' if row['is_capital'] else '🗺️'} **{row['territory_name']}** · Land Lv {row['level']}"
             for row in rows]
    total = db.execute("SELECT COUNT(*) total FROM map_territories WHERE owner_user_id=?", (user_id,)).fetchone()["total"]
    if total > len(rows):
        lines.append(f"…and **{total-len(rows)}** more Land regions.")
    return "\n".join(lines)


def build_tactical_summary_map(db, user_id):
    """Phone-safe map for real ADM1/CAP codes; never assumes the legacy grid format."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    tiles = {**world_city_tiles(_province_features()), **world_capital_tiles()}
    nation = db.execute("SELECT nation_name,capital_name FROM players WHERE user_id=?", (user_id,)).fetchone()
    owned_rows = db.execute("""SELECT territory_code,territory_name,is_capital FROM map_territories
        WHERE owner_user_id=? ORDER BY is_capital DESC,acquired_at,territory_name""", (user_id,)).fetchall()
    # Never hide a Nation merely because an old Dashboard saved a territory
    # code that a newer map asset cannot resolve. Known codes use their real
    # coordinates; legacy codes receive stable approximate positions so the
    # owner can still open and inspect their map.
    located = []
    for index, row in enumerate(owned_rows):
        tile = tiles.get(row["territory_code"])
        if tile is None:
            column, grid_row = index % 5, index // 5
            centre = (float(column * 4), float(-grid_row * 4))
            tile = (row["territory_code"], row["territory_name"], centre,
                    (centre[0] - 1, centre[1] - 1, centre[0] + 1, centre[1] + 1))
        located.append((row, tile))
    if nation is None or not owned_rows:
        return None

    width, height = 1200, 820
    image = Image.new("RGB", (width, height), "#0d2635")
    draw = _safe_map_draw(ImageDraw, image)
    _, _, title_font, normal_font, small_font = _map_fonts()
    draw.rectangle((0, 0, width, 108), fill="#151f2d")
    draw.rectangle((0, 106, width, 112), fill="#e7a629")
    # Keep bitmap text ASCII-safe on minimal Android font installations. The
    # Discord embed above the image still shows the player's full Unicode name.
    draw.text((30, 20), "X BOT - YOUR NATION MAP", fill="#f5f7fb", font=title_font)
    draw.text((30, 67), f"{len(located)} mapped Land regions", fill="#9db7c7", font=normal_font)

    map_left, map_right, map_top, map_bottom = 35, 835, 145, 755
    centres = [tile[2] for _row, tile in located]
    west, east = min(point[0] for point in centres), max(point[0] for point in centres)
    south, north = min(point[1] for point in centres), max(point[1] for point in centres)
    lon_span, lat_span = max(4.0, east - west), max(3.0, north - south)
    west -= lon_span * .3; east += lon_span * .3
    south -= lat_span * .3; north += lat_span * .3

    def project(point):
        lon, lat = point
        return (map_left + (lon - west) / max(.01, east - west) * (map_right - map_left),
                map_top + (north - lat) / max(.01, north - south) * (map_bottom - map_top))

    for index, (row, tile) in enumerate(located, 1):
        x, y = project(tile[2])
        radius = 14 if row["is_capital"] else 11
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill="#4de0c1", outline="#ffffff", width=3)
        marker = "C" if row["is_capital"] else str(index)
        box = draw.textbbox((0, 0), marker, font=small_font)
        draw.text((x-(box[2]-box[0])/2, y-(box[3]-box[1])/2-1), marker, fill="#101820", font=small_font)

    panel_x = 870
    draw.rounded_rectangle((panel_x, 135, width-22, 770), radius=12, fill="#151f2d", outline="#536b78", width=2)
    draw.text((panel_x+18, 158), "LAND REGIONS", fill="#f5f7fb", font=normal_font)
    for index, (row, _tile) in enumerate(located[:18], 1):
        label = str(row["territory_name"] or row["territory_code"])
        # Real map labels are mostly Latin; replacement keeps rare unsupported
        # glyphs from breaking Pillow on Termux.
        safe_label = label.encode("ascii", "replace").decode("ascii")
        prefix = "C" if row["is_capital"] else str(index)
        draw.text((panel_x+18, 195+(index-1)*29), f"{prefix}. {safe_label[:31]}", fill="#dce8ed", font=small_font)
    if len(located) > 18:
        draw.text((panel_x+18, 195+18*29), f"+ {len(located)-18} more", fill="#e7a629", font=small_font)
    draw.text((35, 785), "C = Capital   Number = Land region", fill="#dce8ed", font=small_font)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


DEFAULTS = {
    "attack_cooldown": "60",
    "capital_damage": "25",
    "capital_reward_percent": "25",
    "capital_repair_cost_per_hp": "10",
    "fortified_defense_bonus": "20",
    "aggressive_attack_bonus": "10",
    "scout_cost": "25",
    "scout_cooldown": "300",
    "demobilize_refund_percent": "50",
    "fortify_base_cost": "250",
    "fortify_power_percent": "5",
    "fortify_max_level": "10",
    "war_max_supply": "1000", "war_supply_cost": "2", "war_supply_per_attack": "25",
    "war_readiness_per_prepare": "10", "war_prepare_supply_cost": "20",
    "war_battle_variance_percent": "10",
    "war_winner_loss_percent": "10", "war_loser_loss_percent": "30",
    "war_readiness_loss_per_battle": "5",
    "war_winner_morale_gain": "5", "war_loser_morale_loss": "10",
    "war_rally_supply_cost": "25", "war_rally_morale_gain": "10",
    "war_air_superiority_bonus": "15",
    "war_navy_blockade_penalty": "10",
    "war_navy_supply_damage": "25",
    "war_season_win_points": "3", "war_season_loss_points": "1",
    "war_season_land_points": "2", "war_season_capital_damage_per_point": "10",
    "war_season_defense_points": "2", "war_season_capital_capture_points": "10",
    "war_season_land_hold_points": "1", "war_season_participation_min_battles": "3",
    "war_season_participation_xc": "200", "war_new_nation_protection_seconds": "172800",
    "war_pair_attack_cooldown": "43200",
    # A formal nation war keeps ordinary attacks meaningful.  Players declare
    # against a bordering Nation first; the declaration then expires safely.
    "nation_war_duration": "172800",
    # Free expansion is deliberately slow: one real province/state per Nation
    # every 12 hours. It never costs War Credits or Supply.
    "free_land_claim_cooldown": "43200",
    # City System — production is deliberately separate from the old Land collect command.
    "city_collect_cooldown": "43200",
    "city_capital_war_credits": "100", "city_capital_supply": "20",
    "city_civilian_build_cost": "250", "city_industrial_build_cost": "400",
    "city_civilian_war_credits": "150", "city_industrial_war_credits": "50",
    "city_industrial_supply": "100", "city_upgrade_base_cost": "250",
    "city_rename_cost": "100", "land_max_level": "5",
    "land_upgrade_2_credits": "500", "land_upgrade_2_supply": "100",
    "land_upgrade_3_credits": "1200", "land_upgrade_3_supply": "250",
    "land_upgrade_4_credits": "2500", "land_upgrade_4_supply": "500",
    "land_upgrade_5_credits": "5000", "land_upgrade_5_supply": "1000",
}


def initialise(db):
    db.execute("""CREATE TABLE IF NOT EXISTS player_war_settings(
        user_id INTEGER PRIMARY KEY,
        defense_stance TEXT NOT NULL DEFAULT 'balanced'
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS battle_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attacker_id INTEGER NOT NULL,
        defender_id INTEGER NOT NULL,
        attacker_power INTEGER NOT NULL,
        defender_power INTEGER NOT NULL,
        winner_id INTEGER NOT NULL,
        outcome TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS scout_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scout_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL
    )""")
    state_columns = {row["name"] for row in db.execute("PRAGMA table_info(player_war_settings)")}
    for name,definition in {
        "fortification_level":"INTEGER NOT NULL DEFAULT 0",
        "morale":"INTEGER NOT NULL DEFAULT 100",
        "supply":"INTEGER NOT NULL DEFAULT 500",
        "readiness":"INTEGER NOT NULL DEFAULT 100",
        "armed_forces_name":"TEXT NOT NULL DEFAULT ''",
        "army_name":"TEXT NOT NULL DEFAULT ''",
        "airforce_name":"TEXT NOT NULL DEFAULT ''",
        "navy_name":"TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in state_columns: db.execute(f"ALTER TABLE player_war_settings ADD COLUMN {name} {definition}")
    history_columns = {row["name"] for row in db.execute("PRAGMA table_info(battle_history)")}
    for name, definition in {
        "war_id":"INTEGER",
        "attacker_losses":"TEXT NOT NULL DEFAULT '{}'",
        "defender_losses":"TEXT NOT NULL DEFAULT '{}'",
        "land_captured":"INTEGER NOT NULL DEFAULT 0",
        "capital_damage":"INTEGER NOT NULL DEFAULT 0",
        "credits_captured":"INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in history_columns: db.execute(f"ALTER TABLE battle_history ADD COLUMN {name} {definition}")
    db.execute("""CREATE TABLE IF NOT EXISTS division_templates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',service TEXT NOT NULL DEFAULT 'land',created_at INTEGER NOT NULL,UNIQUE(user_id,name)
    )""")
    template_columns = {row["name"] for row in db.execute("PRAGMA table_info(division_templates)")}
    if "service" not in template_columns:
        db.execute("ALTER TABLE division_templates ADD COLUMN service TEXT NOT NULL DEFAULT 'land'")
    db.execute("""CREATE TABLE IF NOT EXISTS division_template_units(
        template_id INTEGER NOT NULL,unit_type_id INTEGER NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(template_id,unit_type_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS player_cities(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        city_type TEXT NOT NULL CHECK(city_type IN ('civilian','industrial')),
        name TEXT NOT NULL,
        level INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        UNIQUE(user_id, name)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS player_city_state(
        user_id INTEGER PRIMARY KEY,
        last_collect INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS player_free_land_claims(
        user_id INTEGER PRIMARY KEY,
        last_claim_at INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS map_territories(
        territory_code TEXT PRIMARY KEY,
        territory_name TEXT NOT NULL,
        owner_user_id INTEGER NOT NULL,
        is_capital INTEGER NOT NULL DEFAULT 0,
        acquired_at INTEGER NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_map_territories_owner ON map_territories(owner_user_id)")
    db.execute("""CREATE TABLE IF NOT EXISTS nation_wars(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attacker_id INTEGER NOT NULL,
        defender_id INTEGER NOT NULL,
        started_at INTEGER NOT NULL,
        ends_at INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        ended_at INTEGER,
        ended_by INTEGER
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_nation_wars_active ON nation_wars(active,attacker_id,defender_id)")
    territory_columns = {row["name"] for row in db.execute("PRAGMA table_info(map_territories)")}
    if "level" not in territory_columns:
        db.execute("ALTER TABLE map_territories ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
    city_columns = {row["name"] for row in db.execute("PRAGMA table_info(player_cities)")}
    if "territory_code" not in city_columns:
        db.execute("ALTER TABLE player_cities ADD COLUMN territory_code TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS war_seasons(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        starts_at INTEGER NOT NULL,
        ends_at INTEGER,
        reward_xc INTEGER NOT NULL DEFAULT 0,
        reward_war_credits INTEGER NOT NULL DEFAULT 0,
        champion_user_id INTEGER,
        created_at INTEGER NOT NULL
    )""")
    season_columns = {row["name"] for row in db.execute("PRAGMA table_info(war_seasons)")}
    for name, definition in {
        "reward_xc_2": "INTEGER NOT NULL DEFAULT 0",
        "reward_war_credits_2": "INTEGER NOT NULL DEFAULT 0",
        "reward_xc_3": "INTEGER NOT NULL DEFAULT 0",
        "reward_war_credits_3": "INTEGER NOT NULL DEFAULT 0",
        "scheduled_ends_at": "INTEGER",
        "ended_at": "INTEGER",
        "participation_xc": "INTEGER NOT NULL DEFAULT 0",
        "land_hold_awarded": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in season_columns:
            db.execute(f"ALTER TABLE war_seasons ADD COLUMN {name} {definition}")
    db.execute("""CREATE TABLE IF NOT EXISTS war_season_scores(
        season_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        battles INTEGER NOT NULL DEFAULT 0,
        wins INTEGER NOT NULL DEFAULT 0,
        losses INTEGER NOT NULL DEFAULT 0,
        points INTEGER NOT NULL DEFAULT 0,
        land_captured INTEGER NOT NULL DEFAULT 0,
        capital_damage INTEGER NOT NULL DEFAULT 0,
        credits_captured INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(season_id,user_id)
    )""")
    score_columns = {row["name"] for row in db.execute("PRAGMA table_info(war_season_scores)")}
    for name in ("defences", "capitals_captured", "land_hold_points"):
        if name not in score_columns:
            db.execute(f"ALTER TABLE war_season_scores ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
    db.execute("""CREATE TABLE IF NOT EXISTS war_season_rewards(
        season_id INTEGER NOT NULL,
        rank_from INTEGER NOT NULL,
        rank_to INTEGER NOT NULL,
        reward_xc INTEGER NOT NULL DEFAULT 0,
        reward_war_credits INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(season_id,rank_from,rank_to)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS war_season_payouts(
        season_id INTEGER NOT NULL,user_id INTEGER NOT NULL,rank INTEGER NOT NULL,
        reward_xc INTEGER NOT NULL DEFAULT 0,reward_war_credits INTEGER NOT NULL DEFAULT 0,
        paid_at INTEGER NOT NULL,PRIMARY KEY(season_id,user_id)
    )""")
    # Weekly missions are deliberately stored separately from the score board.
    # A player can claim each mission once per UTC week without changing the
    # competitive Season ranking.
    db.execute("""CREATE TABLE IF NOT EXISTS war_season_weekly_claims(
        season_id INTEGER NOT NULL,user_id INTEGER NOT NULL,week_key TEXT NOT NULL,
        mission_key TEXT NOT NULL,claimed_at INTEGER NOT NULL,
        PRIMARY KEY(season_id,user_id,week_key,mission_key)
    )""")
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))
    db.commit()


def setting(db, key):
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return int(row["value"] if row else DEFAULTS[key])


def ensure_city_locations(db, user_id):
    """Attach legacy cities to owned real Land without deleting any city."""
    lands = db.execute("""SELECT territory_code,MAX(1,level) level FROM map_territories
        WHERE owner_user_id=? ORDER BY is_capital DESC,acquired_at""", (user_id,)).fetchall()
    if not lands:
        return
    counts = {row["territory_code"]: db.execute(
        "SELECT COUNT(*) count FROM player_cities WHERE user_id=? AND territory_code=?",
        (user_id, row["territory_code"])).fetchone()["count"] for row in lands}
    legacy = db.execute("""SELECT id FROM player_cities WHERE user_id=?
        AND (territory_code IS NULL OR territory_code='') ORDER BY created_at,id""", (user_id,)).fetchall()
    for city in legacy:
        candidates = [row for row in lands if counts[row["territory_code"]] < int(row["level"])]
        chosen = min(candidates or lands, key=lambda row: counts[row["territory_code"]])
        db.execute("UPDATE player_cities SET territory_code=? WHERE id=?", (chosen["territory_code"], city["id"]))
        counts[chosen["territory_code"]] += 1


def city_slots(db, user_id):
    """Every real Land starts with one city slot; upgrades add more slots."""
    row = db.execute("SELECT COALESCE(SUM(MAX(1,level)),0) slots FROM map_territories WHERE owner_user_id=?",
                     (user_id,)).fetchone()
    return max(0, int(row["slots"] or 0))


def city_income(db, user_id):
    """Return War Credit and Supply production for one 12-hour city cycle."""
    credits = setting(db, "city_capital_war_credits")
    supply = setting(db, "city_capital_supply")
    # Keep the City id and Land reference for all player-facing City panels.
    # Existing income calculations still use the same name/type/level fields.
    rows = db.execute("SELECT id,name,city_type,level,territory_code FROM player_cities WHERE user_id=? ORDER BY created_at,id", (user_id,)).fetchall()
    for row in rows:
        level = max(1, int(row["level"]))
        if row["city_type"] == "civilian":
            credits += setting(db, "city_civilian_war_credits") * level
        else:
            credits += setting(db, "city_industrial_war_credits") * level
            supply += setting(db, "city_industrial_supply") * level
    return credits, supply, rows


def city_log(db, user_id, action, detail):
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
               (user_id, action, detail[:1000], int(time.time())))


def stance(db, user_id):
    row = db.execute("SELECT defense_stance FROM player_war_settings WHERE user_id=?", (user_id,)).fetchone()
    return row["defense_stance"] if row else "balanced"


def service_names(db, user_id):
    """Player-facing names with clean defaults for the three armed services."""
    row = db.execute("SELECT armed_forces_name,army_name,airforce_name,navy_name FROM player_war_settings WHERE user_id=?", (user_id,)).fetchone()
    defaults = {"forces": "Armed Forces", "land": "Land Army", "air": "Air Force", "navy": "Navy"}
    if row is None:
        return defaults
    return {
        "forces": row["armed_forces_name"].strip() or defaults["forces"],
        "land": row["army_name"].strip() or defaults["land"],
        "air": row["airforce_name"].strip() or defaults["air"],
        "navy": row["navy_name"].strip() or defaults["navy"],
    }


def effective_power(db, user_id, attacking=False):
    base = war_system.total_power(db, user_id); current = stance(db, user_id)
    state = db.execute("SELECT fortification_level,morale,readiness,supply FROM player_war_settings WHERE user_id=?", (user_id,)).fetchone()
    morale = state["morale"] if state else 100; readiness = state["readiness"] if state else 100; supply = state["supply"] if state else 500
    base = base * morale // 100
    base = base * max(25,readiness) // 100
    if supply <= 0: base = base // 2
    if attacking and current == "aggressive":
        return base * (100 + setting(db, "aggressive_attack_bonus")) // 100
    if not attacking:
        fortification = (state["fortification_level"] if state else 0) * setting(db, "fortify_power_percent")
        stance_bonus = setting(db, "fortified_defense_bonus") if current == "fortified" else 0
        return base * (100 + fortification + stance_bonus) // 100
    return base


def effective_service_power(db, user_id, service, attacking=False):
    """Calculate combat power for one front: land, air, or navy.

    Morale, readiness and supply affect every service. Stance and
    fortification only change the Land front, so air and navy remain useful
    instead of being hidden inside one total-power number.
    """
    if service not in {"land", "air", "navy"}:
        return 0
    # Tanks are deliberately stronger than raw infantry in actual Land battle.
    # Air and Navy retain their visible recruitment Power unchanged.
    base = war_system.land_combat_power(db, user_id) if service == "land" else war_system.branch_power(db, user_id, service)
    current = stance(db, user_id)
    state = db.execute("SELECT fortification_level,morale,readiness,supply FROM player_war_settings WHERE user_id=?", (user_id,)).fetchone()
    morale = state["morale"] if state else 100
    readiness = state["readiness"] if state else 100
    supply = state["supply"] if state else 500
    base = base * morale // 100
    base = base * max(25, readiness) // 100
    if supply <= 0:
        base //= 2
    if service == "land" and attacking and current == "aggressive":
        base = base * (100 + setting(db, "aggressive_attack_bonus")) // 100
    if service == "land" and not attacking:
        fortification = (state["fortification_level"] if state else 0) * setting(db, "fortify_power_percent")
        stance_bonus = setting(db, "fortified_defense_bonus") if current == "fortified" else 0
        base = base * (100 + fortification + stance_bonus) // 100
    return base


def log_battle(db, attacker_id, defender_id, attacker_power, defender_power, winner_id, outcome,
               *, war_id=None, attacker_losses=None, defender_losses=None, land_captured=0,
               capital_damage=0, credits_captured=0):
    db.execute("""INSERT INTO battle_history(
        attacker_id,defender_id,attacker_power,defender_power,winner_id,outcome,created_at,
        war_id,attacker_losses,defender_losses,land_captured,capital_damage,credits_captured)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        attacker_id, defender_id, attacker_power, defender_power, winner_id, outcome[:500], int(time.time()),
        war_id, json.dumps(attacker_losses or {}), json.dumps(defender_losses or {}),
        int(land_captured), int(capital_damage), int(credits_captured),
    ))


def consume_battle_resources(db, user_id):
    db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)",(user_id,))
    db.execute("UPDATE player_war_settings SET supply=MAX(0,supply-?),readiness=MAX(25,readiness-?) WHERE user_id=?",(
        setting(db,"war_supply_per_attack"), setting(db,"war_readiness_loss_per_battle"), user_id))


def active_season(db):
    return db.execute(
        "SELECT * FROM war_seasons WHERE status='active' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _season_week_key(now=None):
    """Stable UTC week key, so weekly missions reset at Monday 00:00 UTC."""
    stamp = time.gmtime(now or time.time())
    return time.strftime("%G-W%V", stamp)


def _season_week_start(now=None):
    stamp = time.gmtime(now or time.time())
    return int(calendar.timegm((stamp.tm_year, stamp.tm_mon, stamp.tm_mday - stamp.tm_wday,
                                0, 0, 0, 0, 0, 0)))


def weekly_season_missions(db, user_id, season=None, now=None):
    """Return simple, visible missions with reliable data from existing systems."""
    season = season or active_season(db)
    if not season:
        return []
    current = int(now or time.time())
    week_key = _season_week_key(current)
    week_start = _season_week_start(current)
    battles = db.execute("""SELECT COUNT(*) total FROM battle_history
        WHERE created_at>=? AND (attacker_id=? OR defender_id=?)""",
        (week_start, user_id, user_id)).fetchone()["total"]
    land = db.execute("SELECT COUNT(*) total FROM map_territories WHERE owner_user_id=? AND acquired_at>=?",
                      (user_id, week_start)).fetchone()["total"]
    cities = db.execute("SELECT COUNT(*) total FROM player_cities WHERE user_id=? AND created_at>=?",
                        (user_id, week_start)).fetchone()["total"]
    raw = (
        ("battle", "⚔️ First Clash", "Complete 1 battle this week.", int(battles), 1, 75, 40),
        ("land", "🗺️ Expand the Nation", "Claim or capture 1 Land this week.", int(land), 1, 100, 60),
        ("city", "🏙️ Develop Home", "Build 1 City this week.", int(cities), 1, 100, 60),
    )
    missions = []
    for key, title, description, progress, target, xc, credits in raw:
        claimed = db.execute("""SELECT 1 FROM war_season_weekly_claims
            WHERE season_id=? AND user_id=? AND week_key=? AND mission_key=?""",
            (season["id"], user_id, week_key, key)).fetchone() is not None
        missions.append({"key": key, "title": title, "description": description,
                         "progress": progress, "target": target, "xc": xc,
                         "credits": credits, "claimed": claimed})
    return missions


def claim_weekly_season_mission(db, user_id, mission_key):
    season = active_season(db)
    if not season:
        return False, "There is no active War Season."
    mission = next((row for row in weekly_season_missions(db, user_id, season) if row["key"] == mission_key), None)
    if not mission:
        return False, "That weekly mission is not available."
    if mission["claimed"]:
        return False, "You already claimed that weekly mission."
    if mission["progress"] < mission["target"]:
        return False, f"Mission progress is **{mission['progress']}/{mission['target']}**."
    try:
        db.execute("""INSERT INTO war_season_weekly_claims(season_id,user_id,week_key,mission_key,claimed_at)
            VALUES(?,?,?,?,?)""", (season["id"], user_id, _season_week_key(), mission_key, int(time.time())))
    except sqlite3.IntegrityError:
        return False, "You already claimed that weekly mission."
    db.execute("UPDATE players SET xc=xc+?,money=money+? WHERE user_id=?",
               (mission["xc"], mission["credits"], user_id))
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
               (user_id, "season_weekly_mission", f"{mission['title']}: +{mission['xc']} XC, +{mission['credits']} War Credits", int(time.time())))
    db.commit()
    return True, f"✅ Claimed **{mission['title']}**: **+{mission['xc']} XC** and **+{mission['credits']} War Credits**."


def settle_expired_seasons(db, now=None):
    """Safely end due Seasons and pay ranked rewards once. Returns settlement summaries."""
    current = int(now or time.time())
    expired = db.execute("""SELECT * FROM war_seasons WHERE status='active'
        AND scheduled_ends_at IS NOT NULL AND scheduled_ends_at>0 AND scheduled_ends_at<=?""", (current,)).fetchall()
    summaries = []
    for season in expired:
        ranks = db.execute("""SELECT s.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(s.user_id AS TEXT)) player_name
            FROM war_season_scores s LEFT JOIN players p ON p.user_id=s.user_id
            WHERE s.season_id=? ORDER BY s.points DESC,s.wins DESC,s.land_captured DESC,s.user_id ASC""", (season["id"],)).fetchall()
        rewards = ((1, season["reward_xc"], season["reward_war_credits"]),
                   (2, season["reward_xc_2"], season["reward_war_credits_2"]),
                   (3, season["reward_xc_3"], season["reward_war_credits_3"]))
        paid = 0
        for rank, xc, credits in rewards:
            if len(ranks) < rank:
                continue
            user_id = ranks[rank - 1]["user_id"]
            existing = db.execute("SELECT 1 FROM war_season_payouts WHERE season_id=? AND user_id=?", (season["id"], user_id)).fetchone()
            if existing:
                continue
            db.execute("UPDATE players SET xc=xc+?,money=money+? WHERE user_id=?", (int(xc), int(credits), user_id))
            db.execute("INSERT INTO war_season_payouts(season_id,user_id,rank,reward_xc,reward_war_credits,paid_at) VALUES(?,?,?,?,?,?)",
                       (season["id"], user_id, rank, int(xc), int(credits), current))
            paid += 1
        minimum = setting(db, "war_season_participation_min_battles")
        participation = int(season["participation_xc"] or setting(db, "war_season_participation_xc"))
        for row in ranks:
            if int(row["battles"]) < minimum:
                continue
            already = db.execute("SELECT 1 FROM economy_logs WHERE user_id=? AND action='season_participation' AND detail=?",
                                 (row["user_id"], f"Season {season['id']} participation")).fetchone()
            if not already:
                db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (participation, row["user_id"]))
                db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
                           (row["user_id"], "season_participation", f"Season {season['id']} participation", current))
        champion = ranks[0] if ranks else None
        if champion:
            trophy = db.execute("SELECT id FROM items WHERE name='Season 1 Champion Trophy' COLLATE NOCASE").fetchone()
            if trophy:
                db.execute("INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,1) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+1",
                           (champion["user_id"], trophy["id"]))
        db.execute("UPDATE war_seasons SET status='ended',ended_at=?,champion_user_id=? WHERE id=?",
                   (current, champion["user_id"] if champion else None, season["id"]))
        summaries.append(f"{season['name']} ended · {paid} ranked rewards paid")
    if expired:
        db.commit()
    return summaries


def record_season_battle(db, attacker_id, defender_id, winner_id, *, land_captured=0,
                         capital_damage=0, credits_captured=0, capital_captured=False):
    """Add battle score to the active campaign without using War Items."""
    season = active_season(db)
    if not season:
        return
    loser_id = defender_id if winner_id == attacker_id else attacker_id
    for user_id in (attacker_id, defender_id):
        db.execute(
            "INSERT OR IGNORE INTO war_season_scores(season_id,user_id) VALUES(?,?)",
            (season["id"], user_id),
        )
    win_points = setting(db, "war_season_win_points")
    loss_points = setting(db, "war_season_loss_points")
    land_points = setting(db, "war_season_land_points") * max(0, int(land_captured))
    damage_step = max(1, setting(db, "war_season_capital_damage_per_point"))
    defence_points = setting(db, "war_season_defense_points") if winner_id == defender_id else 0
    capital_points = setting(db, "war_season_capital_capture_points") if capital_captured else 0
    objective_points = land_points + max(0, int(capital_damage)) // damage_step + defence_points + capital_points
    db.execute("""UPDATE war_season_scores SET battles=battles+1,wins=wins+1,
        points=points+?,land_captured=land_captured+?,capital_damage=capital_damage+?,
        credits_captured=credits_captured+?,defences=defences+?,capitals_captured=capitals_captured+?
        WHERE season_id=? AND user_id=?""",
        (win_points + objective_points, max(0, int(land_captured)), max(0, int(capital_damage)),
         max(0, int(credits_captured)), int(winner_id == defender_id), int(bool(capital_captured)),
         season["id"], winner_id))
    db.execute("""UPDATE war_season_scores SET battles=battles+1,losses=losses+1,
        points=points+? WHERE season_id=? AND user_id=?""",
        (loss_points, season["id"], loser_id))


def register_commands(bot, db, create_player, get_active_war, get_alliance_for_user):
    guild_id = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
    player_command_kwargs = {"guild": discord.Object(id=guild_id)} if guild_id else {}
    async def division_template_autocomplete(interaction: discord.Interaction, current: str):
        """Show this player's saved templates as a Discord picker."""
        rows = db.execute("""SELECT name FROM division_templates
            WHERE user_id=? AND name LIKE ? COLLATE NOCASE
            ORDER BY name LIMIT 25""", (interaction.user.id, f"%{current}%")).fetchall()
        return [app_commands.Choice(name=row["name"][:100], value=row["name"]) for row in rows]

    async def division_model_autocomplete(interaction: discord.Interaction, current: str):
        """Show enabled unit models, so players never need to remember codes."""
        template_name = str(getattr(interaction.namespace, "template", "") or "")
        selected = db.execute("SELECT service FROM division_templates WHERE user_id=? AND name=? COLLATE NOCASE",
            (interaction.user.id, template_name)).fetchone()
        service = selected["service"] if selected else None
        service_where = " AND branch=?" if service in {"land", "air", "navy"} else ""
        parameters = [f"%{current}%", f"%{current}%"] + ([service] if service_where else [])
        rows = db.execute("""SELECT code,name,emoji FROM war_unit_types
            WHERE enabled=1 AND (name LIKE ? COLLATE NOCASE OR code LIKE ? COLLATE NOCASE)""" + service_where +
            " ORDER BY branch,position,name LIMIT 25", parameters).fetchall()
        return [app_commands.Choice(name=f"{row['emoji']} {row['name']}"[:100], value=row["code"]) for row in rows]

    async def city_autocomplete(interaction: discord.Interaction, current: str):
        rows = db.execute("""SELECT name,city_type,level FROM player_cities
            WHERE user_id=? AND name LIKE ? COLLATE NOCASE ORDER BY name LIMIT 25""",
            (interaction.user.id, f"%{current}%")).fetchall()
        return [app_commands.Choice(
            name=f"{'🏙️' if r['city_type']=='civilian' else '🏭'} {r['name']} · Lv {r['level']}"[:100],
            value=r["name"],
        ) for r in rows]

    async def territory_autocomplete(interaction: discord.Interaction, current: str):
        rows = db.execute("""SELECT territory_code,territory_name,level FROM map_territories
            WHERE owner_user_id=? AND territory_name LIKE ? COLLATE NOCASE
            ORDER BY is_capital DESC,territory_name LIMIT 25""",
            (interaction.user.id, f"%{current}%")).fetchall()
        return [app_commands.Choice(
            name=f"🗺️ {row['territory_name']} · Land Lv {row['level']}"[:100],
            value=row["territory_code"],
        ) for row in rows]

    async def free_land_autocomplete(interaction: discord.Interaction, current: str):
        """Let a Nation choose a connected, unclaimed real province/state."""
        tiles = world_city_tiles(_province_features())
        # Ensure older Nations that existed before the real-map system have
        # their current Land represented before we calculate their border.
        sync_map_ownership(db, tiles)
        connected = connected_free_land_codes(db, interaction.user.id, tiles)
        phrase = (current or "").strip().lower()
        choices = []
        for code, (_code, name, _centre, _bounds) in tiles.items():
            if code not in connected or (phrase and phrase not in name.lower()):
                continue
            choices.append(app_commands.Choice(name=f"🗺️ {name}"[:100], value=code))
            if len(choices) == 25:
                break
        return choices

    @bot.tree.command(name="claim_land", description="Claim one connected free Land every 12 hours", **player_command_kwargs)
    @app_commands.describe(land="Choose a free Land connected to your Nation")
    @app_commands.autocomplete(land=free_land_autocomplete)
    async def claim_land(interaction: discord.Interaction, land: str):
        """Season 1 peaceful expansion: one free real Land each 12 hours."""
        player = create_player(interaction.user)
        tiles = world_city_tiles(_province_features())
        sync_map_ownership(db, tiles)
        selected = tiles.get(land)
        if not selected:
            await interaction.response.send_message(
                "❌ Choose an available Land from the command list. It may have been claimed already.",
                ephemeral=True,
            )
            return
        existing = db.execute("SELECT owner_user_id FROM map_territories WHERE territory_code=?", (land,)).fetchone()
        if existing:
            await interaction.response.send_message("❌ That Land has already been claimed by another Nation.", ephemeral=True)
            return
        if land not in connected_free_land_codes(db, interaction.user.id, tiles):
            await interaction.response.send_message(view=xbot_ui.warning(
                "🗺️ Land Must Connect to Your Nation",
                "Choose a free province/state that touches one of your current Lands. "
                "You cannot claim a disconnected region across the map."), ephemeral=True)
            return
        db.execute("INSERT OR IGNORE INTO player_free_land_claims(user_id) VALUES(?)", (interaction.user.id,))
        claim_state = db.execute("SELECT last_claim_at FROM player_free_land_claims WHERE user_id=?", (interaction.user.id,)).fetchone()
        now = int(time.time())
        cooldown = setting(db, "free_land_claim_cooldown")
        remaining = cooldown - (now - int(claim_state["last_claim_at"]))
        if remaining > 0:
            await interaction.response.send_message(view=xbot_ui.warning(
                "⏳ Free Land Claim Not Ready",
                f"Your Nation may claim another free Land <t:{now + remaining}:R>.\n"
                "Free Land does not cost War Credits or Supply."), ephemeral=True)
            return
        _code, land_name, _centre, _bounds = selected
        db.execute("""INSERT INTO map_territories
            (territory_code,territory_name,owner_user_id,is_capital,acquired_at,level)
            VALUES(?,?,?,?,?,1)""", (land, land_name, interaction.user.id, 0, now))
        db.execute("UPDATE players SET land=land+1 WHERE user_id=?", (interaction.user.id,))
        db.execute("UPDATE player_free_land_claims SET last_claim_at=? WHERE user_id=?", (now, interaction.user.id))
        city_log(db, interaction.user.id, "free_land_claim", f"Claimed free Land: {land_name}")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success(
            "🗺️ Free Land Claimed",
            f"**{land_name}** is now part of **{player['nation_name']}**.\n"
            f"🌍 Nation Land: **{int(player['land']) + 1}** · Next free claim: <t:{now + cooldown}:R>\n\n"
            "This Land is connected to your Nation. Open `/war` → **City Centre** to upgrade or develop it."
        ))

    @bot.tree.command(name="map", description="View the X BOT strategic world map", **player_command_kwargs)
    async def strategic_map(interaction: discord.Interaction):
        """Public first version of the visual War Map."""
        await interaction.response.defer()
        image = build_real_strategic_map(db)
        if image is None:
            await interaction.followup.send(
                "❌ The map image engine is not installed yet. Run `pip install -r requirements.txt` once.",
                ephemeral=True,
            )
            return
        file = discord.File(image, filename="xbot-strategic-map.png")
        embed = discord.Embed(
            title="🗺️ X BOT World Conquest Map",
            description=(
                "Every colour represents a Nation. Each Nation begins with a Capital territory, "
                "and its territory expands when it captures Land in battle."
            ),
            colour=discord.Colour.teal(),
        )
        embed.set_image(url="attachment://xbot-strategic-map.png")
        embed.set_footer(text="Use /war to inspect your Land, armed forces, Cities and Season progress.")
        await interaction.followup.send(embed=embed, file=file)

    @bot.tree.command(name="map_detail", description="Open a zoomed real-region tactical map for a Nation", **player_command_kwargs)
    @app_commands.describe(nation="Choose a Nation owner, or leave empty to view your own Nation")
    async def map_detail(interaction: discord.Interaction, nation: discord.Member = None):
        target = nation or interaction.user
        create_player(target)
        await interaction.response.defer()
        image, detailed = build_nation_map_resilient(db, target.id)
        if image is None:
            embed = discord.Embed(title=f"🧭 {target.display_name}'s Territory Overview",
                                  description=nation_land_summary(db, target.id), colour=discord.Colour.gold())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        filename = "xbot-tactical-map.png" if detailed else "xbot-world-map.png"
        file = discord.File(image, filename=filename)
        embed = discord.Embed(
            title=f"🧭 {target.display_name}'s Tactical Map",
            description=("Each coloured real province/state is **1 Land**. The star marks the Nation's Capital."
                         if detailed else "The detailed phone map was unavailable, so X BOT opened the World map.\n\n" + nation_land_summary(db, target.id, 8)),
            colour=discord.Colour.gold(),
        )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text="Use /map for the clean World overview.")
        await interaction.followup.send(embed=embed, file=file)

    class CityMenuButton(discord.ui.Button):
        def __init__(self, owner_id: int, action: str, label: str, emoji: str, style: discord.ButtonStyle):
            super().__init__(label=label, emoji=emoji, style=style)
            self.owner_id, self.action = owner_id, action

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("This City menu belongs to another player. Use `/city` for your own Nation.", ephemeral=True)
                return
            if self.action == "collect":
                await city_collect.callback(interaction)
                return
            if self.action == "build":
                await interaction.response.edit_message(view=CityBuildView(self.owner_id))
                return
            if self.action == "upgrade":
                await interaction.response.edit_message(view=CityUpgradeView(self.owner_id))
                return
            if self.action == "land":
                await interaction.response.edit_message(view=LandUpgradeView(self.owner_id))
                return
            if self.action == "rename":
                await interaction.response.edit_message(view=CityRenameView(self.owner_id))
                return
            if self.action == "overview":
                # Building a tactical image can take longer than Discord's
                # component-response window.  Keep the City Centre panel in
                # place and post the map directly below it.  Mixing a normal
                # View with the City Centre LayoutView in one edit can make
                # Discord ignore the component interaction on mobile.
                await interaction.response.defer()
                image, detailed = build_nation_map_resilient(db, self.owner_id)
                player = db.execute("SELECT nation_name,capital_name FROM players WHERE user_id=?", (self.owner_id,)).fetchone()
                embed = discord.Embed(
                    title=f"🗺️ {player['nation_name']} — Territory Overview",
                    description=(
                        f"🏛️ Capital: **{player['capital_name']}**\n"
                        + ("Every coloured province/state is one of your Land regions. Tap the image to zoom in."
                           if detailed else "The detailed phone map was unavailable. Your saved Land is listed below.\n\n" + nation_land_summary(db, self.owner_id, 8))
                    ),
                    colour=discord.Colour.teal(),
                )
                embed.set_footer(text="Use City Centre to collect income, build, upgrade, or rename Cities.")
                if image is None:
                    await interaction.followup.send(embed=embed, view=CityMapView(self.owner_id), ephemeral=True)
                else:
                    filename = "xbot-my-nation-map.png" if detailed else "xbot-world-map.png"
                    file = discord.File(image, filename=filename)
                    embed.set_image(url=f"attachment://{filename}")
                    await interaction.followup.send(embed=embed, file=file, view=CityMapView(self.owner_id))
                return
            await interaction.response.edit_message(view=CitySystemView(self.owner_id, show_costs=self.action == "costs"))

    class CityBackButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Back to City Centre", emoji="⬅️", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=CitySystemView(self.owner_id))

    class CityWarBackButton(discord.ui.Button):
        """Return from City Centre to the same player's main War panel."""
        def __init__(self, owner_id: int):
            super().__init__(label="Back to War Centre", emoji="⚔️", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            # WarCommandView is defined later in this command-registration
            # scope and is available by the time a user clicks this button.
            await interaction.response.edit_message(view=WarCommandView(self.owner_id))

    class CityMapView(discord.ui.View):
        """Small, native component view used on the image attachment map."""
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            self.add_item(CityMapBackButton(owner_id))

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This map belongs to another player. Open `/city` for your own Nation.", ephemeral=True)
            return False

    class CityMapBackButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Back to City Centre", emoji="🏙️", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            # The City Centre panel remains above the map.  Close only this
            # map message instead of trying to replace a LayoutView message.
            await interaction.response.defer()
            await interaction.message.delete()

    class CityBuildNameModal(discord.ui.Modal):
        def __init__(self, city_type: str, territory_code: str):
            super().__init__(title=f"Build {city_type.title()} City")
            self.city_type, self.territory_code = city_type, territory_code
            self.name_input = discord.ui.TextInput(label="New City name", placeholder="Example: New Avalon", min_length=3, max_length=40)
            self.add_item(self.name_input)

        async def on_submit(self, interaction: discord.Interaction):
            choice = app_commands.Choice(name=f"{self.city_type.title()} City", value=self.city_type)
            await city_build.callback(interaction, choice, str(self.name_input.value), self.territory_code)

    class CityBuildLandSelect(discord.ui.Select):
        def __init__(self, owner_id: int, city_type: str):
            lands = db.execute("""SELECT t.territory_code,t.territory_name,t.level,COUNT(c.id) city_count
                FROM map_territories t LEFT JOIN player_cities c ON c.territory_code=t.territory_code AND c.user_id=t.owner_user_id
                WHERE t.owner_user_id=? GROUP BY t.territory_code ORDER BY t.is_capital DESC,t.territory_name LIMIT 25""", (owner_id,)).fetchall()
            options = [discord.SelectOption(
                label=(row['territory_name'][:72]), value=row['territory_code'],
                description=f"Land Lv {row['level']} · City slots {row['city_count']}/{row['level']}"
            ) for row in lands]
            super().__init__(placeholder="Choose the Land for this City", options=options, disabled=not options)
            self.owner_id, self.city_type = owner_id, city_type

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.send_modal(CityBuildNameModal(self.city_type, self.values[0]))

    class CityBuildTypeButton(discord.ui.Button):
        def __init__(self, owner_id: int, city_type: str):
            label, emoji = ("Civilian City", "🏙️") if city_type == "civilian" else ("Industrial City", "🏭")
            super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.primary)
            self.owner_id, self.city_type = owner_id, city_type

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=CityBuildLandView(self.owner_id, self.city_type))

    class CityBuildView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300); self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay(
                "## 🏗️ Build a City\nChoose the city type first. The next screen asks for your Land and city name.\n\n"
                f"🏙️ Civilian: **{setting(db, 'city_civilian_build_cost'):,} War Credits** · more credits\n"
                f"🏭 Industrial: **{setting(db, 'city_industrial_build_cost'):,} War Credits** · more supply"
            ))
            container.add_item(discord.ui.ActionRow(CityBuildTypeButton(owner_id, "civilian"), CityBuildTypeButton(owner_id, "industrial"), CityBackButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction):
            return interaction.user.id == self.owner_id

    class CityBuildLandView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, city_type: str):
            super().__init__(timeout=300); self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay(f"## {('🏙️' if city_type == 'civilian' else '🏭')} Build {city_type.title()} City\nChoose a Land with an available City slot."))
            container.add_item(discord.ui.ActionRow(CityBuildLandSelect(owner_id, city_type)))
            container.add_item(discord.ui.ActionRow(CityBackButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction):
            return interaction.user.id == self.owner_id

    class CityUpgradeSelect(discord.ui.Select):
        def __init__(self, owner_id: int):
            rows = db.execute("SELECT id,name,level,city_type FROM player_cities WHERE user_id=? ORDER BY level,name LIMIT 25", (owner_id,)).fetchall()
            options = [discord.SelectOption(label=row['name'][:100], value=str(row['id']), description=f"{row['city_type'].title()} · Level {row['level']} · Upgrade {setting(db, 'city_upgrade_base_cost') * int(row['level']):,} WC") for row in rows]
            super().__init__(placeholder="Choose a City to upgrade", options=options, disabled=not options)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            row = db.execute("SELECT id,name,level,city_type FROM player_cities WHERE id=? AND user_id=?", (int(self.values[0]), self.owner_id)).fetchone()
            await interaction.response.edit_message(view=CityUpgradeConfirmView(self.owner_id, row))

    class CityUpgradeConfirmButton(discord.ui.Button):
        """Complete an upgrade from the panel without creating a second message.

        A component can be tapped twice on slower mobile connections.  Keeping
        the complete transaction here lets us lock the button immediately and
        turn the confirmation card into the result card in-place.
        """
        def __init__(self, owner_id: int, city_id: int):
            super().__init__(label="Confirm Upgrade", emoji="✅", style=discord.ButtonStyle.success)
            self.owner_id, self.city_id = owner_id, city_id
            self.completed = False

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("This upgrade panel belongs to another player. Use `/city` for your own Nation.", ephemeral=True)
                return
            if self.completed:
                await interaction.response.send_message("This upgrade was already completed.", ephemeral=True)
                return
            city = db.execute("SELECT * FROM player_cities WHERE id=? AND user_id=?", (self.city_id, self.owner_id)).fetchone()
            player = db.execute("SELECT money FROM players WHERE user_id=?", (self.owner_id,)).fetchone()
            if not city or not player:
                await interaction.response.send_message("❌ This City is no longer available.", ephemeral=True)
                return
            if int(city["level"]) >= 10:
                await interaction.response.send_message("✅ This City is already the maximum Level 10.", ephemeral=True)
                return
            cost = setting(db, "city_upgrade_base_cost") * int(city["level"])
            if int(player["money"]) < cost:
                await interaction.response.send_message(view=xbot_ui.danger("💰 Not Enough War Credits", f"You need **{cost:,} War Credits** for this upgrade."), ephemeral=True)
                return
            self.completed = True
            self.disabled = True
            next_level = int(city["level"]) + 1
            db.execute("UPDATE player_cities SET level=? WHERE id=?", (next_level, city["id"]))
            db.execute("UPDATE players SET money=money-? WHERE user_id=?", (cost, self.owner_id))
            city_log(db, self.owner_id, "city_upgrade", f"Upgraded {city['name']} to level {next_level} for {cost} War Credits")
            db.commit()
            await interaction.response.edit_message(view=CityUpgradeResultView(self.owner_id, city, next_level, cost))

    class CityUpgradeConfirmView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, row):
            super().__init__(timeout=180); self.owner_id = owner_id
            next_level = int(row['level']) + 1
            cost = setting(db, 'city_upgrade_base_cost') * int(row['level'])
            if row['city_type'] == 'industrial':
                production = f"📦 Next 12-hour gain: **+{setting(db, 'city_industrial_supply'):,} Supply** + **+{setting(db, 'city_industrial_war_credits'):,} War Credits**"
            else:
                production = f"💰 Next 12-hour gain: **+{setting(db, 'city_civilian_war_credits'):,} War Credits**"
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay(
                f"## ⬆️ Upgrade Ready\n"
                f"{('🏙️' if row['city_type'] == 'civilian' else '🏭')} **{row['name']}**\n"
                f"Level **{row['level']}** → **{next_level}**\n"
                f"{production}\n\n"
                f"💰 Cost: **{cost:,} War Credits**\n"
                "-# Your War Credits are charged only after you confirm."
            ))
            container.add_item(discord.ui.ActionRow(CityUpgradeConfirmButton(owner_id, row['id']), CityBackButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction): return interaction.user.id == self.owner_id

    class CityUpgradeResultView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, city, level: int, cost: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            if city['city_type'] == 'industrial':
                production = f"📦 Production is now **+{setting(db, 'city_industrial_supply') * level:,} Supply** and **+{setting(db, 'city_industrial_war_credits') * level:,} War Credits** every 12 hours."
            else:
                production = f"💰 Production is now **+{setting(db, 'city_civilian_war_credits') * level:,} War Credits** every 12 hours."
            container = discord.ui.Container(accent_color=discord.Color.green())
            container.add_item(discord.ui.TextDisplay(
                f"## ✅ City Upgraded\n"
                f"{('🏙️' if city['city_type'] == 'civilian' else '🏭')} **{city['name']}** is now **Level {level}**.\n"
                f"💸 Paid: **{cost:,} War Credits**\n\n"
                f"{production}\n"
                "-# The upgrade is complete. No second confirmation or duplicate message is sent."
            ))
            container.add_item(discord.ui.ActionRow(CityBackButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction):
            return interaction.user.id == self.owner_id

    class CityUpgradeView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300); self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay("## ⬆️ Upgrade a City\nThe exact next upgrade cost is shown in every choice."))
            container.add_item(discord.ui.ActionRow(CityUpgradeSelect(owner_id)))
            container.add_item(discord.ui.ActionRow(CityBackButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction): return interaction.user.id == self.owner_id

    class LandUpgradeSelect(discord.ui.Select):
        def __init__(self, owner_id: int):
            rows = db.execute("SELECT territory_code,territory_name,level FROM map_territories WHERE owner_user_id=? ORDER BY is_capital DESC,territory_name LIMIT 25", (owner_id,)).fetchall()
            maximum = setting(db, "land_max_level")
            options = [discord.SelectOption(label=row['territory_name'][:100], value=row['territory_code'], description=("Maximum level" if int(row['level']) >= maximum else f"Land Lv {row['level']} → Lv {int(row['level']) + 1}")) for row in rows]
            super().__init__(placeholder="Choose Land to upgrade", options=options, disabled=not options)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            row = db.execute("SELECT territory_code,territory_name,level FROM map_territories WHERE territory_code=? AND owner_user_id=?", (self.values[0], self.owner_id)).fetchone()
            await interaction.response.edit_message(view=LandUpgradeConfirmView(self.owner_id, row))

    class LandUpgradeConfirmButton(discord.ui.Button):
        def __init__(self, owner_id: int, territory_code: str):
            super().__init__(label="Confirm Upgrade", emoji="✅", style=discord.ButtonStyle.success)
            self.owner_id, self.territory_code = owner_id, territory_code

        async def callback(self, interaction: discord.Interaction):
            await land_upgrade.callback(interaction, self.territory_code)

    class LandUpgradeConfirmView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, row):
            super().__init__(timeout=180); self.owner_id = owner_id
            target = int(row['level']) + 1
            credit_cost = setting(db, f"land_upgrade_{target}_credits")
            supply_cost = setting(db, f"land_upgrade_{target}_supply")
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay(
                f"## 🗺️ Confirm Land Upgrade\n"
                f"**{row['territory_name']}** · Land Level **{row['level']} → {target}**\n\n"
                f"💰 Cost: **{credit_cost:,} War Credits**\n"
                f"📦 Cost: **{supply_cost:,} Supply**\n"
                "The new Land Level unlocks one additional City slot."
            ))
            container.add_item(discord.ui.ActionRow(LandUpgradeConfirmButton(owner_id, row['territory_code']), CityBackButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction): return interaction.user.id == self.owner_id

    class LandUpgradeView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300); self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay("## 🗺️ Upgrade Land\nA Land level gives that real region one extra City slot. The command shows the exact resource cost before spending."))
            container.add_item(discord.ui.ActionRow(LandUpgradeSelect(owner_id)))
            container.add_item(discord.ui.ActionRow(CityBackButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction): return interaction.user.id == self.owner_id

    class CityRenameModal(discord.ui.Modal, title="Rename City"):
        def __init__(self, city_name: str):
            super().__init__(title="Rename City")
            self.city_name = city_name
            self.name_input = discord.ui.TextInput(label="New City name", default=city_name, min_length=2, max_length=32)
            self.add_item(self.name_input)

        async def on_submit(self, interaction: discord.Interaction):
            await city_rename.callback(interaction, self.city_name, str(self.name_input.value))

    class CityRenameSelect(discord.ui.Select):
        def __init__(self, owner_id: int):
            rows = db.execute("SELECT id,name,city_type,level FROM player_cities WHERE user_id=? ORDER BY name LIMIT 25", (owner_id,)).fetchall()
            options = [discord.SelectOption(label=row['name'][:100], value=str(row['id']), description=f"{row['city_type'].title()} · Level {row['level']}") for row in rows]
            super().__init__(placeholder="Choose a City to rename", options=options, disabled=not options)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            row = db.execute("SELECT name FROM player_cities WHERE id=? AND user_id=?", (int(self.values[0]), self.owner_id)).fetchone()
            await interaction.response.send_modal(CityRenameModal(row['name']))

    class CityRenameView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300); self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay(f"## 🏷️ Rename City\nCost: **{setting(db, 'city_rename_cost'):,} War Credits**"))
            container.add_item(discord.ui.ActionRow(CityRenameSelect(owner_id)))
            container.add_item(discord.ui.ActionRow(CityBackButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction): return interaction.user.id == self.owner_id

    class CitySystemView(discord.ui.LayoutView):
        """A player-friendly City dashboard; prices are visible before a command is used."""
        def __init__(self, user_id: int, show_costs: bool = False):
            super().__init__(timeout=300)
            self.owner_id = user_id
            # /city always creates the profile before opening this panel.  Querying
            # the stored player avoids requiring a Discord User object in buttons.
            player = db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
            if not player:
                container = discord.ui.Container(accent_color=discord.Color.red())
                container.add_item(discord.ui.TextDisplay("## ⚠️ City Centre\nYour player profile could not be found. Run `/city` again."))
                self.add_item(container)
                return
            sync_map_ownership(db, world_city_tiles(_province_features()))
            ensure_city_locations(db, user_id)
            db.execute("INSERT OR IGNORE INTO player_city_state(user_id) VALUES(?)", (user_id,))
            state = db.execute("SELECT last_collect FROM player_city_state WHERE user_id=?", (user_id,)).fetchone()
            credits, supply, cities = city_income(db, user_id)
            capital_land = db.execute("SELECT territory_name FROM map_territories WHERE owner_user_id=? AND is_capital=1 LIMIT 1", (user_id,)).fetchone()
            capital_location = capital_land["territory_name"] if capital_land else "Unmapped location"
            city_locations = {row["id"]: row["territory_name"] or "Unmapped location" for row in db.execute(
                "SELECT c.id,t.territory_name FROM player_cities c LEFT JOIN map_territories t ON t.territory_code=c.territory_code WHERE c.user_id=?", (user_id,)
            ).fetchall()}
            cooldown = setting(db, "city_collect_cooldown")
            remaining = max(0, cooldown - (int(time.time()) - int(state["last_collect"])))
            max_land = setting(db, "land_max_level")
            base_upgrade = setting(db, "city_upgrade_base_cost")
            container = discord.ui.Container(accent_color=discord.Color.teal())
            if show_costs:
                body = (
                    f"## 🧾 City & Land Cost Guide\n"
                    f"💰 Your balance: **{player['money']:,} War Credits**\n\n"
                    f"🏙️ Civilian City: **{setting(db, 'city_civilian_build_cost'):,} War Credits**\n"
                    f"🏭 Industrial City: **{setting(db, 'city_industrial_build_cost'):,} War Credits**\n"
                    f"🏷️ Rename a City: **{setting(db, 'city_rename_cost'):,} War Credits**\n"
                    f"⬆️ City upgrade: **{base_upgrade:,} × current City Level**\n"
                    f"🗺️ Land levels: **1–{max_land}**; every new Land level unlocks one more City slot.\n\n"
                    f"Use **Build**, **Upgrade**, **Land**, and **Rename City** below."
                )
            else:
                city_lines = [f"🏛️ **{player['capital_name']}** · Capital · 📍 {capital_location} · Base production"]
                city_lines += [
                    f"{'🏙️' if row['city_type']=='civilian' else '🏭'} **{row['name']}** · Lv {row['level']}"
                    f" · 📍 {city_locations.get(row['id'], 'Unmapped location')}"
                    f" · Next upgrade **{base_upgrade * int(row['level']):,} WC**"
                    for row in cities
                ]
                collect_text = "✅ **Ready to collect now**" if remaining == 0 else f"⏳ Ready <t:{int(time.time()) + remaining}:R>"
                body = (
                    f"## 🏙️ {player['nation_name']} — City Centre\n"
                    f"💰 **{player['money']:,} War Credits** · 🗺️ Land **{player['land']}** · "
                    f"City slots **{len(cities)}/{city_slots(db, user_id)}**\n"
                    f"{collect_text}\n\n"
                    f"### Every 12 hours\n💰 **+{credits:,} War Credits** · 📦 **+{supply:,} Supply**\n\n"
                    f"### Cities\n" + "\n".join(city_lines)
                )
            container.add_item(discord.ui.TextDisplay(body))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(
                CityMenuButton(user_id, "collect", "Collect", "💰", discord.ButtonStyle.success),
                CityMenuButton(user_id, "build", "Build", "🏗️", discord.ButtonStyle.primary),
                CityMenuButton(user_id, "upgrade", "Upgrade", "⬆️", discord.ButtonStyle.primary),
                CityMenuButton(user_id, "land", "Land", "🗺️", discord.ButtonStyle.secondary),
                CityMenuButton(user_id, "costs", "Costs", "🧾", discord.ButtonStyle.primary),
            ))
            container.add_item(discord.ui.ActionRow(
                CityMenuButton(user_id, "rename", "Rename City", "🏷️", discord.ButtonStyle.secondary),
                CityMenuButton(user_id, "overview", "Overview", "🏙️", discord.ButtonStyle.secondary),
                CityWarBackButton(user_id),
            ))
            container.add_item(discord.ui.TextDisplay("-# Use the buttons above — you do not need to type City commands."))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This City menu belongs to another player. Use `/city` for your own Nation.", ephemeral=True)
            return False

    @bot.tree.command(name="city", description="Open your Nation City Centre and 12-hour income", **player_command_kwargs)
    async def city(interaction: discord.Interaction):
        create_player(interaction.user)
        await interaction.response.send_message(view=CitySystemView(interaction.user.id))

    @bot.tree.command(name="city_build", description="Build a Civilian or Industrial City using War Credits", **player_command_kwargs)
    @app_commands.choices(city_type=[
        app_commands.Choice(name="🏙️ Civilian City — more War Credits", value="civilian"),
        app_commands.Choice(name="🏭 Industrial City — more Supply", value="industrial"),
    ])
    @app_commands.autocomplete(land=territory_autocomplete)
    async def city_build(interaction: discord.Interaction, city_type: app_commands.Choice[str], name: str, land: str):
        player = create_player(interaction.user)
        sync_map_ownership(db, world_city_tiles(_province_features()))
        ensure_city_locations(db, interaction.user.id)
        clean_name = name.strip()
        if not 3 <= len(clean_name) <= 40:
            await interaction.response.send_message("❌ City names must be 3–40 characters.", ephemeral=True); return
        territory = db.execute("""SELECT territory_code,territory_name,level FROM map_territories
            WHERE owner_user_id=? AND territory_code=?""", (interaction.user.id, land)).fetchone()
        if not territory:
            await interaction.response.send_message("❌ Select one of your real Land regions.", ephemeral=True); return
        built = db.execute("SELECT COUNT(*) count FROM player_cities WHERE user_id=? AND territory_code=?",
                           (interaction.user.id, land)).fetchone()["count"]
        if built >= int(territory["level"]):
            await interaction.response.send_message(view=xbot_ui.warning(
                "🏙️ This Land Is Full",
                f"**{territory['territory_name']}** is Land Level **{territory['level']}** and has "
                f"**{built}/{territory['level']}** City slots. Use /land_upgrade first."), ephemeral=True); return
        cost = setting(db, f"city_{city_type.value}_build_cost")
        if player["money"] < cost:
            await interaction.response.send_message(view=xbot_ui.danger("💰 Not Enough War Credits", f"Building a {city_type.name} costs **{cost:,} War Credits**. You have **{player['money']:,}**."), ephemeral=True); return
        try:
            db.execute("""INSERT INTO player_cities(user_id,city_type,name,level,created_at,territory_code)
                VALUES(?,?,?,?,?,?)""", (interaction.user.id, city_type.value, clean_name, 1, int(time.time()), land))
        except sqlite3.IntegrityError:
            await interaction.response.send_message("❌ You already have a city with that name.", ephemeral=True); return
        db.execute("UPDATE players SET money=money-? WHERE user_id=?", (cost, interaction.user.id))
        city_log(db, interaction.user.id, "city_build",
                 f"Built {city_type.value} city {clean_name} in {territory['territory_name']} for {cost} War Credits")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🏗️ City Built", f"**{clean_name}** is now a Level 1 {city_type.name}.\nCost: **{cost:,} War Credits** · Use `/city` to view its production."))

    @bot.tree.command(name="city_rename", description="Rename one of your Cities", **player_command_kwargs)
    @app_commands.autocomplete(city=city_autocomplete)
    async def city_rename(interaction: discord.Interaction, city: str, new_name: str):
        player = create_player(interaction.user)
        row = db.execute("SELECT * FROM player_cities WHERE user_id=? AND name=? COLLATE NOCASE",
                         (interaction.user.id, city.strip())).fetchone()
        clean_name = new_name.strip()
        if not row:
            await interaction.response.send_message("❌ Select one of your Cities.", ephemeral=True); return
        if not 2 <= len(clean_name) <= 32 or "discord.gg/" in clean_name.lower() or "@everyone" in clean_name.lower():
            await interaction.response.send_message("❌ City names must be 2–32 safe characters.", ephemeral=True); return
        cost = setting(db, "city_rename_cost")
        if int(player["money"]) < cost:
            await interaction.response.send_message(f"❌ Renaming costs **{cost:,} War Credits**.", ephemeral=True); return
        try:
            db.execute("UPDATE player_cities SET name=? WHERE id=?", (clean_name, row["id"]))
        except sqlite3.IntegrityError:
            await interaction.response.send_message("❌ You already have a City with that name.", ephemeral=True); return
        db.execute("UPDATE players SET money=money-? WHERE user_id=?", (cost, interaction.user.id))
        city_log(db, interaction.user.id, "city_rename", f"Renamed {row['name']} to {clean_name} for {cost} War Credits")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success(
            "🏷️ City Renamed", f"**{row['name']}** is now **{clean_name}**.\nCost: **{cost:,} War Credits**."))

    @bot.tree.command(name="land_info", description="View your real Land levels and City capacity", **player_command_kwargs)
    async def land_info(interaction: discord.Interaction):
        create_player(interaction.user)
        sync_map_ownership(db, world_city_tiles(_province_features()))
        ensure_city_locations(db, interaction.user.id)
        db.commit()
        lands = db.execute("""SELECT t.territory_code,t.territory_name,t.level,t.is_capital,
            COUNT(c.id) city_count FROM map_territories t LEFT JOIN player_cities c
            ON c.territory_code=t.territory_code AND c.user_id=t.owner_user_id
            WHERE t.owner_user_id=? GROUP BY t.territory_code ORDER BY t.is_capital DESC,t.territory_name""",
            (interaction.user.id,)).fetchall()
        lines = [f"{'★' if row['is_capital'] else '🗺️'} **{row['territory_name']}** · "
                 f"Land Lv **{row['level']}** · Cities **{row['city_count']}/{row['level']}**" for row in lands]
        await interaction.response.send_message(view=xbot_ui.panel(
            "🗺️ Nation Land Development", "\n".join(lines) or "Your Nation has no mapped Land.",
            colour=discord.Color.teal(), footer="Use /land_upgrade to unlock more City slots."))

    @bot.tree.command(name="land_upgrade", description="Upgrade real Land to unlock another City slot", **player_command_kwargs)
    @app_commands.autocomplete(land=territory_autocomplete)
    async def land_upgrade(interaction: discord.Interaction, land: str):
        player = create_player(interaction.user)
        db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (interaction.user.id,))
        territory = db.execute("""SELECT territory_code,territory_name,level FROM map_territories
            WHERE owner_user_id=? AND territory_code=?""", (interaction.user.id, land)).fetchone()
        if not territory:
            await interaction.response.send_message("❌ Select one of your Land regions.", ephemeral=True); return
        current = max(1, int(territory["level"])); maximum = setting(db, "land_max_level")
        if current >= maximum:
            await interaction.response.send_message(f"✅ This Land is already maximum Level **{maximum}**.", ephemeral=True); return
        target = current + 1
        credit_cost = setting(db, f"land_upgrade_{target}_credits")
        supply_cost = setting(db, f"land_upgrade_{target}_supply")
        state = db.execute("SELECT supply FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
        if int(player["money"]) < credit_cost or int(state["supply"]) < supply_cost:
            await interaction.response.send_message(view=xbot_ui.warning(
                "📦 Upgrade Resources Required",
                f"Land Lv {target} costs **{credit_cost:,} War Credits** + **{supply_cost:,} Supply**.\n"
                f"You have **{player['money']:,} War Credits** + **{state['supply']:,} Supply**."), ephemeral=True); return
        db.execute("UPDATE map_territories SET level=? WHERE territory_code=?", (target, land))
        db.execute("UPDATE players SET money=money-? WHERE user_id=?", (credit_cost, interaction.user.id))
        db.execute("UPDATE player_war_settings SET supply=supply-? WHERE user_id=?", (supply_cost, interaction.user.id))
        city_log(db, interaction.user.id, "land_upgrade",
                 f"Upgraded {territory['territory_name']} to Land Level {target}")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success(
            "⬆️ Land Upgraded", f"**{territory['territory_name']}** is now Land Level **{target}**.\n"
            f"City capacity: **{target}**."))

    @bot.tree.command(name="city_upgrade", description="Upgrade one of your cities to increase its production", **player_command_kwargs)
    @app_commands.autocomplete(city=city_autocomplete)
    async def city_upgrade(interaction: discord.Interaction, city: str):
        player = create_player(interaction.user)
        row = db.execute("SELECT * FROM player_cities WHERE user_id=? AND name=? COLLATE NOCASE", (interaction.user.id, city.strip())).fetchone()
        if not row:
            await interaction.response.send_message("❌ Select one of your cities from the command list.", ephemeral=True); return
        if row["level"] >= 10:
            await interaction.response.send_message("✅ This city is already the maximum Level 10.", ephemeral=True); return
        cost = setting(db, "city_upgrade_base_cost") * int(row["level"])
        if player["money"] < cost:
            await interaction.response.send_message(view=xbot_ui.danger("💰 Not Enough War Credits", f"Upgrading **{row['name']}** to Level {row['level'] + 1} costs **{cost:,} War Credits**."), ephemeral=True); return
        db.execute("UPDATE player_cities SET level=level+1 WHERE id=?", (row["id"],))
        db.execute("UPDATE players SET money=money-? WHERE user_id=?", (cost, interaction.user.id))
        city_log(db, interaction.user.id, "city_upgrade", f"Upgraded {row['name']} to level {row['level'] + 1} for {cost} War Credits")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("⬆️ City Upgraded", f"**{row['name']}** is now **Level {row['level'] + 1}**.\nCost: **{cost:,} War Credits**."))

    @bot.tree.command(name="city_collect", description="Collect War Credits and Supply from your Cities every 12 hours", **player_command_kwargs)
    async def city_collect(interaction: discord.Interaction):
        player = create_player(interaction.user)
        db.execute("INSERT OR IGNORE INTO player_city_state(user_id) VALUES(?)", (interaction.user.id,))
        state = db.execute("SELECT last_collect FROM player_city_state WHERE user_id=?", (interaction.user.id,)).fetchone()
        cooldown = setting(db, "city_collect_cooldown")
        remaining = cooldown - (int(time.time()) - int(state["last_collect"]))
        if remaining > 0:
            await interaction.response.send_message(view=xbot_ui.warning("⏳ City Production Not Ready", f"Your Cities will be ready <t:{int(time.time()) + remaining}:R>.\nCollection happens every **12 hours**."), ephemeral=True); return
        credits, supply_gain, cities = city_income(db, interaction.user.id)
        db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (interaction.user.id,))
        war_state = db.execute("SELECT supply FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
        maximum = setting(db, "war_max_supply")
        actual_supply = min(supply_gain, max(0, maximum - int(war_state["supply"])))
        now = int(time.time())
        db.execute("UPDATE players SET money=money+? WHERE user_id=?", (credits, interaction.user.id))
        db.execute("UPDATE player_war_settings SET supply=supply+? WHERE user_id=?", (actual_supply, interaction.user.id))
        db.execute("UPDATE player_city_state SET last_collect=? WHERE user_id=?", (now, interaction.user.id))
        city_log(db, interaction.user.id, "city_collect", f"Collected {credits} War Credits and {actual_supply}/{supply_gain} Supply from {len(cities) + 1} cities")
        db.commit()
        supply_note = f"📦 **+{actual_supply:,} Supply**" + (f" (storage cap: {maximum:,})" if actual_supply < supply_gain else "")
        await interaction.response.send_message(view=xbot_ui.success("🏙️ City Production Collected", f"💰 **+{credits:,} War Credits**\n{supply_note}\n\nNext collection: <t:{now + cooldown}:R>"))

    # City actions are now reached from /city buttons. Keep their callback
    # functions for the interface, but remove every old separate slash entry
    # from the *same guild scope* where they were registered.  Calling
    # remove_command without this guild left the old guild commands visible.
    for _legacy_city_command in (
        "city_build", "city_upgrade", "city_collect", "city_rename",
        "land_info", "land_upgrade",
    ):
        bot.tree.remove_command(
            _legacy_city_command,
            guild=discord.Object(id=guild_id) if guild_id else None,
        )

    @bot.tree.command(name="collect", description="Collect your 12-hour City production", **player_command_kwargs)
    async def collect(interaction: discord.Interaction):
        """Small public shortcut; the full City system remains button-driven."""
        await city_collect.callback(interaction)

    @bot.tree.command(name="military_name", description="Rename your Armed Forces overview or one military service", **player_command_kwargs)
    @app_commands.choices(service=[
        app_commands.Choice(name="⚔️ Armed Forces Overview / Overall Name", value="forces"),
        app_commands.Choice(name="🪖 Land Army", value="land"),
        app_commands.Choice(name="✈️ Air Force", value="air"),
        app_commands.Choice(name="⚓ Navy", value="navy"),
    ])
    async def military_name(interaction: discord.Interaction, service: app_commands.Choice[str], name: str):
        clean_name = name.strip()
        if not 3 <= len(clean_name) <= 40:
            await interaction.response.send_message("❌ Military names must be 3–40 characters.", ephemeral=True)
            return
        create_player(interaction.user)
        column = {"forces": "armed_forces_name", "land": "army_name", "air": "airforce_name", "navy": "navy_name"}[service.value]
        db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (interaction.user.id,))
        db.execute(f"UPDATE player_war_settings SET {column}=? WHERE user_id=?", (clean_name, interaction.user.id))
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success(
            "🏷️ Armed Service Renamed",
            f"Your **{service.name}** is now called **{clean_name}**."
        ))

    def season_view(user_id):
        season = active_season(db)
        if not season:
            return WarDetailView(
                user_id,
                "🏁 No Active War Season",
                "There is no War Campaign running right now. Check back when Season 1 has been opened by the staff.",
                discord.Color.gold(),
            )
        score = db.execute("SELECT * FROM war_season_scores WHERE season_id=? AND user_id=?", (season["id"], user_id)).fetchone()
        leaders = db.execute("""SELECT s.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(s.user_id AS TEXT)) player_name
            FROM war_season_scores s LEFT JOIN players p ON p.user_id=s.user_id
            WHERE s.season_id=? ORDER BY s.points DESC,s.wins DESC,s.land_captured DESC LIMIT 5""", (season["id"],)).fetchall()
        medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
        leaderboard = "\n".join(f"{medals[index - 1]} **{row['player_name']}** — **{row['points']} pts**"
            for index, row in enumerate(leaders, 1)) or "No scored battles yet — win a battle to enter the ranking."
        end_line = f"⏳ Ends <t:{season['scheduled_ends_at']}:R>" if season["scheduled_ends_at"] else "⏳ End date will be announced by staff"
        body = (
            f"### 📅 {season['name']}\n{end_line}\n\n"
            "### 🏆 Season rewards\n"
            f"🥇 **{season['reward_xc']:,} XC** + **{season['reward_war_credits']:,} War Credits**\n"
            f"🥈 **{season['reward_xc_2']:,} XC** + **{season['reward_war_credits_2']:,} War Credits**\n"
            f"🥉 **{season['reward_xc_3']:,} XC** + **{season['reward_war_credits_3']:,} War Credits**\n\n"
            "### 🎯 Your campaign\n"
            f"**{score['points'] if score else 0} points** · {score['battles'] if score else 0} battles\n"
            f"✅ Wins **{score['wins'] if score else 0}** · ❌ Losses **{score['losses'] if score else 0}** · 🛡️ Defences **{score['defences'] if score else 0}**\n"
            f"🗺️ Land captured **{score['land_captured'] if score else 0}** · 🏛️ Capitals **{score['capitals_captured'] if score else 0}**\n"
            f"🎁 Participation: **{season['participation_xc']:,} XC** after **{setting(db, 'war_season_participation_min_battles')} battles**\n\n"
            f"### 🏅 Top Nations\n{leaderboard}\n\n"
            "-# Points come from battles, defence, land capture and capital objectives."
        )
        # This must be an interactive page, rather than a one-way result panel.
        # WarDetailView keeps the player inside their original message and supplies
        # both Back to War Centre and Lobby navigation buttons.
        return WarDetailView(user_id, "🏁 X BOT War Season", body, discord.Color.gold(), season_page=True)

    def season_missions_view(user_id, notice=""):
        season = active_season(db)
        if not season:
            return WarDetailView(user_id, "📜 Weekly Season Missions", "There is no active War Season right now.", discord.Color.gold())
        missions = weekly_season_missions(db, user_id, season)
        lines = []
        for mission in missions:
            state = "🎁 Claimed" if mission["claimed"] else "✅ Ready to claim" if mission["progress"] >= mission["target"] else "⬜ In progress"
            lines.append(f"**{mission['title']}** — {mission['progress']}/{mission['target']}\n{mission['description']}\n{state} · Reward: **{mission['xc']} XC + {mission['credits']} War Credits**")
        return SeasonMissionsView(user_id, "\n\n".join(lines), notice)

    @bot.tree.command(name="season", description="View the active War Season, rewards and rankings", **player_command_kwargs)
    async def season_command(interaction: discord.Interaction):
        create_player(interaction.user)
        await interaction.response.send_message(view=season_view(interaction.user.id))

    @bot.tree.command(name="war_objectives", description="See Season 1 objectives and how a Nation wins", **player_command_kwargs)
    async def war_objectives(interaction: discord.Interaction):
        create_player(interaction.user)
        season = active_season(db)
        season_name = season["name"] if season else "No active season"
        body = (
            f"🏁 **Campaign:** {season_name}\n\n"
            "🗺️ **Conquer Land** — Win the Land front in `/attack` to capture one real connected enemy Land.\n"
            "✈️ **Air Superiority** — Win the air front to support your Land attack and stop enemy air pressure.\n"
            "⚓ **Naval Blockade** — Win the navy front to reduce enemy Supply before the Land battle.\n"
            "🏛️ **Capital Siege** — When a defender has no ordinary Land left, a successful attack can damage their Capital.\n"
            "🛡️ **Defend Your Nation** — Defending wins, strong Supply, Readiness and Morale protect your territory.\n\n"
            "**Season points** are earned from wins, defence, Land captured and Capital damage/capture. "
            "Use `/season` for live rewards and the leaderboard.\n\n"
            "**Peaceful growth:** `/claim_land` gives one free Land every 12 hours, but it must touch your Nation."
        )
        await interaction.response.send_message(view=xbot_ui.panel(
            "🎯 X BOT Season 1 — War Objectives", body, colour=discord.Color.gold(),
            footer="Build your Nation before you fight: /supply_buy · /prepare · /armed_forces"
        ))

    @bot.tree.command(name="season_missions", description="View your Season 1 war and development missions", **player_command_kwargs)
    async def season_missions(interaction: discord.Interaction):
        create_player(interaction.user)
        await interaction.response.send_message(view=season_missions_view(interaction.user.id))

    @bot.tree.command(name="declare_war", description="Declare war on a bordering Nation", **player_command_kwargs)
    @app_commands.describe(target="Choose a neighbouring Nation to declare war on")
    async def declare_war(interaction: discord.Interaction, target: discord.Member):
        if target.bot or target.id == interaction.user.id:
            await interaction.response.send_message("❌ Choose another player Nation.", ephemeral=True)
            return
        attacker = create_player(interaction.user)
        defender = db.execute("SELECT * FROM players WHERE user_id=?", (target.id,)).fetchone()
        if not defender:
            await interaction.response.send_message("❌ That player has not created a Nation yet.", ephemeral=True)
            return
        if int(attacker["capital_health"]) <= 0 or int(defender["capital_health"]) <= 0:
            await interaction.response.send_message("❌ A conquered Nation cannot start or receive a new declaration.", ephemeral=True)
            return
        own_alliance = get_alliance_for_user(interaction.user.id)
        target_alliance = get_alliance_for_user(target.id)
        if own_alliance and target_alliance and own_alliance["id"] == target_alliance["id"]:
            await interaction.response.send_message("❌ You cannot declare war on a member of your own Alliance.", ephemeral=True)
            return
        tiles = world_city_tiles(_province_features())
        sync_map_ownership(db, tiles)
        existing = active_nation_war(db, interaction.user.id)
        if existing:
            await interaction.response.send_message("❌ Your Nation already has an active declaration. Use `/nation_war_status` or `/offer_peace`.", ephemeral=True)
            return
        if not nations_are_adjacent(db, interaction.user.id, target.id, tiles):
            await interaction.response.send_message(view=xbot_ui.warning(
                "🗺️ Nations Are Not Neighbours",
                "You can only declare war on a Nation sharing a real Land border with yours. Expand with `/claim_land` or capture connecting Land first."), ephemeral=True)
            return
        now = int(time.time())
        duration = max(3600, setting(db, "nation_war_duration"))
        db.execute("""INSERT INTO nation_wars(attacker_id,defender_id,started_at,ends_at)
            VALUES(?,?,?,?)""", (interaction.user.id, target.id, now, now + duration))
        db.commit()
        await interaction.response.send_message(view=xbot_ui.danger(
            "⚔️ War Declared",
            f"**{attacker['nation_name']}** has declared war on **{defender['nation_name']}**.\n"
            f"🗺️ The Nations share a Land border.\n⏳ War ends <t:{now + duration}:R>.\n\n"
            "Both sides may now use `/attack` against each other. Win the Land front to capture a connected enemy Land."
        ))

    @bot.tree.command(name="nation_war_status", description="View your current Nation war", **player_command_kwargs)
    async def nation_war_status(interaction: discord.Interaction):
        create_player(interaction.user)
        war = active_nation_war(db, interaction.user.id)
        db.commit()
        if not war:
            await interaction.response.send_message(view=xbot_ui.success(
                "🕊️ No Active Nation War", "Your Nation is currently at peace. Use `/declare_war` against a bordering Nation to begin a formal war."
            ))
            return
        attacker = db.execute("SELECT nation_name FROM players WHERE user_id=?", (war["attacker_id"],)).fetchone()
        defender = db.execute("SELECT nation_name FROM players WHERE user_id=?", (war["defender_id"],)).fetchone()
        await interaction.response.send_message(view=xbot_ui.panel(
            "⚔️ Active Nation War",
            f"**{attacker['nation_name']}** vs **{defender['nation_name']}**\n"
            f"Started <t:{war['started_at']}:R> · Ends <t:{war['ends_at']}:R>\n\n"
            "Use `/attack` only against this enemy Nation. Either side may use `/offer_peace` to end the current war.",
            colour=discord.Color.dark_red()
        ))

    @bot.tree.command(name="offer_peace", description="End your current Nation war", **player_command_kwargs)
    async def offer_peace(interaction: discord.Interaction):
        create_player(interaction.user)
        war = active_nation_war(db, interaction.user.id)
        if not war:
            await interaction.response.send_message("❌ Your Nation does not have an active war to end.", ephemeral=True)
            return
        db.execute("UPDATE nation_wars SET active=0,ended_at=?,ended_by=? WHERE id=?",
            (int(time.time()), interaction.user.id, war["id"]))
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success(
            "🕊️ Peace Restored", "The Nation war has ended. Both Nations may prepare and expand again."
        ))

    @bot.tree.command(name="division_create",description="Create a Land, Air Force, or Navy Division Template")
    @app_commands.choices(service=[
        app_commands.Choice(name="🪖 Land Division", value="land"),
        app_commands.Choice(name="✈️ Air Force Wing", value="air"),
        app_commands.Choice(name="⚓ Navy Task Force", value="navy"),
    ])
    async def division_create(interaction:discord.Interaction,name:str,service:app_commands.Choice[str],description:str=""):
        create_player(interaction.user)
        try:
            db.execute("INSERT INTO division_templates(user_id,name,description,service,created_at) VALUES(?,?,?,?,?)",(interaction.user.id,name.strip()[:50],description.strip()[:200],service.value,int(time.time())));db.commit()
            label = {"land":"Land Division", "air":"Air Force Wing", "navy":"Navy Task Force"}[service.value]
            await interaction.response.send_message(view=xbot_ui.success("📐 Division Template Created",f"Saved **{name[:50]}** as a **{label}**. Use `/division_add`, then choose this template and its unit model from the lists."))
        except sqlite3.IntegrityError:
            await interaction.response.send_message("❌ You already have a template with that name.",ephemeral=True)

    @bot.tree.command(name="division_add",description="Choose a unit model and add it to a Division Template")
    @app_commands.autocomplete(template=division_template_autocomplete, model=division_model_autocomplete)
    async def division_add(interaction:discord.Interaction,template:str,model:str,quantity:app_commands.Range[int,1,10000]):
        row=db.execute("SELECT id FROM division_templates WHERE user_id=? AND name=? COLLATE NOCASE",(interaction.user.id,template.strip())).fetchone()
        unit=db.execute("SELECT * FROM war_unit_types WHERE (name=? COLLATE NOCASE OR code=? COLLATE NOCASE) AND enabled=1",(model.strip(),model.strip())).fetchone()
        if not row or not unit:
            await interaction.response.send_message("❌ Template or unit model not found.",ephemeral=True);return
        service = db.execute("SELECT service FROM division_templates WHERE id=?", (row["id"],)).fetchone()["service"]
        if unit["branch"] != service:
            await interaction.response.send_message(f"❌ This is a **{service.title()}** template. Choose a {service.title()} unit model.", ephemeral=True); return
        db.execute("""INSERT INTO division_template_units(template_id,unit_type_id,quantity) VALUES(?,?,?) ON CONFLICT(template_id,unit_type_id) DO UPDATE SET quantity=excluded.quantity""",(row['id'],unit['id'],quantity));db.commit()
        await interaction.response.send_message(view=xbot_ui.success("📐 Division Updated",f"**{template}** now requires **{quantity}x {unit['emoji']} {unit['name']}**."))

    def division_detail(template_id: int):
        template = db.execute("SELECT * FROM division_templates WHERE id=?", (template_id,)).fetchone()
        units = db.execute("""SELECT d.quantity,u.name,u.emoji,u.power,u.branch
            FROM division_template_units d JOIN war_unit_types u ON u.id=d.unit_type_id
            WHERE d.template_id=? ORDER BY u.position,u.name""", (template_id,)).fetchall()
        template_power = sum(int(unit["quantity"]) * int(unit["power"]) for unit in units)
        return template, units, template_power

    class DivisionTemplateSelect(discord.ui.Select):
        def __init__(self, owner_id: int, templates, selected_id: int):
            options = []
            icons = {"land": "🪖", "air": "✈️", "navy": "⚓"}
            for template in templates[:25]:
                options.append(discord.SelectOption(
                    label=template["name"][:100], value=str(template["id"]),
                    emoji=icons.get(template["service"], "📐"),
                    description=(template["description"] or f"{template['service'].title()} template")[:100],
                    default=template["id"] == selected_id,
                ))
            super().__init__(placeholder="Choose a Division Template", options=options)
            self.owner_id, self.templates = owner_id, templates

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(
                view=DivisionTemplateView(self.owner_id, self.templates, int(self.values[0]))
            )

    class DivisionTemplateView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, templates, selected_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            template, units, template_power = division_detail(selected_id)
            if template is None:
                return
            icons = {"land": "🪖", "air": "✈️", "navy": "⚓"}
            icon = icons.get(template["service"], "📐")
            container = discord.ui.Container(accent_color=discord.Color.dark_red())
            container.add_item(discord.ui.TextDisplay(
                f"## 📐 {template['name']}\n"
                f"{icon} **{template['service'].title()} Template** · 💥 **{template_power:,} Template Power**"
            ))
            container.add_item(discord.ui.TextDisplay(
                f"> {template['description'] or 'No description has been set for this Division.'}"
            ))
            container.add_item(discord.ui.ActionRow(DivisionTemplateSelect(owner_id, templates, selected_id)))
            container.add_item(discord.ui.Separator())
            if units:
                for unit in units:
                    unit_power = int(unit["quantity"]) * int(unit["power"])
                    container.add_item(discord.ui.TextDisplay(
                        f"### {unit['emoji']} {unit['name']}\n"
                        f"**{int(unit['quantity']):,} units** · 💥 **{unit_power:,} power**"
                    ))
            else:
                container.add_item(discord.ui.TextDisplay(
                    "### No units chosen yet\nUse `/division_add`, select this template, then select a unit model."
                ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(
                "-# A Template is a saved battle plan. It does not create or consume units."
            ))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This Division menu belongs to another player.", ephemeral=True)
            return False

    @bot.tree.command(name="divisions",description="Browse your Division Templates with a visual selector")
    async def divisions(interaction:discord.Interaction):
        rows = db.execute("SELECT * FROM division_templates WHERE user_id=? ORDER BY service,name", (interaction.user.id,)).fetchall()
        if not rows:
            await interaction.response.send_message(view=xbot_ui.warning(
                "📐 No Division Templates",
                "Create one with `/division_create`. Choose Land, Air Force, or Navy first, then add unit models."
            ), ephemeral=True)
            return
        await interaction.response.send_message(view=DivisionTemplateView(interaction.user.id, rows, rows[0]["id"]))

    @bot.tree.command(name="division_remove", description="Choose and remove a unit model from a Division Template")
    @app_commands.autocomplete(template=division_template_autocomplete, model=division_model_autocomplete)
    async def division_remove(interaction: discord.Interaction, template: str, model: str):
        row = db.execute("SELECT id FROM division_templates WHERE user_id=? AND name=? COLLATE NOCASE", (interaction.user.id, template.strip())).fetchone()
        unit = db.execute("SELECT id,name,emoji FROM war_unit_types WHERE name=? COLLATE NOCASE OR code=? COLLATE NOCASE", (model.strip(), model.strip())).fetchone()
        if not row or not unit:
            await interaction.response.send_message("❌ Template or unit model not found.", ephemeral=True); return
        result = db.execute("DELETE FROM division_template_units WHERE template_id=? AND unit_type_id=?", (row["id"], unit["id"]))
        db.commit()
        if not result.rowcount:
            await interaction.response.send_message("❌ That model is not in this template.", ephemeral=True); return
        await interaction.response.send_message(view=xbot_ui.success("📐 Division Updated", f"Removed **{unit['emoji']} {unit['name']}** from **{template}**."))

    @bot.tree.command(name="division_delete", description="Choose and delete one of your Division Templates")
    @app_commands.autocomplete(template=division_template_autocomplete)
    async def division_delete(interaction: discord.Interaction, template: str):
        row = db.execute("SELECT id,name FROM division_templates WHERE user_id=? AND name=? COLLATE NOCASE", (interaction.user.id, template.strip())).fetchone()
        if not row:
            await interaction.response.send_message("❌ Division Template not found.", ephemeral=True); return
        db.execute("DELETE FROM division_template_units WHERE template_id=?", (row["id"],))
        db.execute("DELETE FROM division_templates WHERE id=?", (row["id"],)); db.commit()
        await interaction.response.send_message(view=xbot_ui.warning("📐 Division Deleted", f"Deleted **{row['name']}**."), ephemeral=True)

    @bot.tree.command(name="supply_buy",description="Buy military Supply using War Credits")
    async def supply_buy(interaction:discord.Interaction,amount:app_commands.Range[int,1,10000]):
        player=create_player(interaction.user);cost=amount*setting(db,"war_supply_cost");db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)",(interaction.user.id,));state=db.execute("SELECT supply FROM player_war_settings WHERE user_id=?",(interaction.user.id,)).fetchone()
        actual=min(amount,setting(db,"war_max_supply")-state['supply']);cost=actual*setting(db,"war_supply_cost")
        if actual<=0 or player['money']<cost:
            await interaction.response.send_message("❌ Supply storage is full or you do not have enough War Credits.",ephemeral=True);return
        db.execute("UPDATE players SET money=money-? WHERE user_id=?",(cost,interaction.user.id));db.execute("UPDATE player_war_settings SET supply=supply+? WHERE user_id=?",(actual,interaction.user.id));db.commit()
        await interaction.response.send_message(view=xbot_ui.success("📦 Supply Purchased",f"Purchased **{actual} Supply** for **{cost:,} War Credits**."))

    @bot.tree.command(name="prepare",description="Spend Supply to improve military Readiness")
    async def prepare(interaction:discord.Interaction):
        create_player(interaction.user);db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)",(interaction.user.id,));state=db.execute("SELECT supply,readiness FROM player_war_settings WHERE user_id=?",(interaction.user.id,)).fetchone();cost=setting(db,"war_prepare_supply_cost")
        if state['readiness'] >= 100:
            await interaction.response.send_message("✅ Your Readiness is already at 100%.", ephemeral=True); return
        if state['supply'] < cost:
            await interaction.response.send_message(f"❌ You need **{cost:,} Supply** to prepare, but you have **{state['supply']:,}**.", ephemeral=True); return
        gain=min(setting(db,"war_readiness_per_prepare"),100-state['readiness']);db.execute("UPDATE player_war_settings SET supply=supply-?,readiness=readiness+? WHERE user_id=?",(cost,gain,interaction.user.id));db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🎯 Forces Prepared",f"Readiness **+{gain}%** · Supply **-{cost}**."))

    @bot.tree.command(name="rally", description="Spend Supply to restore military Morale")
    async def rally(interaction: discord.Interaction):
        create_player(interaction.user)
        db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (interaction.user.id,))
        state = db.execute("SELECT supply,morale FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
        cost = setting(db, "war_rally_supply_cost")
        if state["morale"] >= 100:
            await interaction.response.send_message(view=xbot_ui.warning("🔥 Morale Full", "Your military Morale is already 100%."), ephemeral=True); return
        if state["supply"] < cost:
            await interaction.response.send_message(view=xbot_ui.danger("📦 Not Enough Supply", f"A rally requires **{cost} Supply**."), ephemeral=True); return
        gain = min(setting(db, "war_rally_morale_gain"), 100 - state["morale"])
        db.execute("UPDATE player_war_settings SET supply=supply-?,morale=morale+? WHERE user_id=?", (cost, gain, interaction.user.id))
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🔥 Military Rally", f"Morale **+{gain}%** · Supply **-{cost}**."))

    class SupplyPurchaseModal(discord.ui.Modal, title="Buy Military Supply"):
        def __init__(self, owner_id: int, source_message: discord.Message):
            super().__init__()
            self.owner_id = owner_id
            self.source_message = source_message
            self.amount = discord.ui.TextInput(
                label="Supply amount",
                placeholder="Example: 100",
                min_length=1,
                max_length=5,
            )
            self.add_item(self.amount)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                amount = int(str(self.amount.value).strip())
            except ValueError:
                await interaction.response.send_message("❌ Enter a whole number for the Supply amount.", ephemeral=True)
                return
            if not 1 <= amount <= 10_000:
                await interaction.response.send_message("❌ Supply amount must be between 1 and 10,000.", ephemeral=True)
                return
            player = create_player(interaction.user)
            db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (interaction.user.id,))
            state = db.execute("SELECT supply FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
            actual = min(amount, setting(db, "war_max_supply") - int(state["supply"]))
            cost = actual * setting(db, "war_supply_cost")
            if actual <= 0:
                await interaction.response.send_message("❌ Your Supply storage is already full.", ephemeral=True)
                return
            if int(player["money"]) < cost:
                await interaction.response.send_message(f"❌ You need **{cost:,} War Credits** but only have **{player['money']:,}**.", ephemeral=True)
                return
            db.execute("UPDATE players SET money=money-? WHERE user_id=?", (cost, interaction.user.id))
            db.execute("UPDATE player_war_settings SET supply=supply+? WHERE user_id=?", (actual, interaction.user.id))
            db.commit()
            await interaction.response.defer()
            await self.source_message.edit(view=WarCommandView(
                self.owner_id,
                notice=f"✅ Bought **{actual:,} Supply** for **{cost:,} War Credits**.",
            ))

    class WarBackButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Back to War Centre", emoji="⬅️", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=WarCommandView(self.owner_id))

    class WarLobbyButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Lobby", emoji="✨", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open your own X BOT panel with `/lobby`.", ephemeral=True)
                return
            builder = getattr(bot, "xbot_player_lobby_builder", None)
            if builder is None:
                await interaction.response.send_message("The X BOT Lobby is loading. Please try again in a moment.", ephemeral=True)
                return
            await interaction.response.edit_message(view=builder(self.owner_id))

    class WarCustomiseModal(discord.ui.Modal, title="Customise your Nation & Forces"):
        """One form avoids asking players to type five separate slash commands."""
        def __init__(self, owner_id: int, source_message):
            self.owner_id = owner_id
            self.source_message = source_message
            player = db.execute("SELECT nation_name FROM players WHERE user_id=?", (owner_id,)).fetchone()
            names = service_names(db, owner_id)
            super().__init__(timeout=300)
            self.nation = discord.ui.TextInput(label="Nation name", default=str(player["nation_name"])[:50], min_length=3, max_length=50)
            self.forces = discord.ui.TextInput(label="Armed forces overview name", default=str(names["forces"])[:40], min_length=3, max_length=40)
            self.land = discord.ui.TextInput(label="Land Army name", default=str(names["land"])[:40], min_length=3, max_length=40)
            self.air = discord.ui.TextInput(label="Air Force name", default=str(names["air"])[:40], min_length=3, max_length=40)
            self.navy = discord.ui.TextInput(label="Navy name", default=str(names["navy"])[:40], min_length=3, max_length=40)
            for field in (self.nation, self.forces, self.land, self.air, self.navy):
                self.add_item(field)

        async def on_submit(self, interaction: discord.Interaction):
            nation, forces, land, air, navy = (field.value.strip() for field in (self.nation, self.forces, self.land, self.air, self.navy))
            if not all((nation, forces, land, air, navy)):
                await interaction.response.send_message("❌ Names cannot be empty.", ephemeral=True)
                return
            db.execute("UPDATE players SET nation_name=? WHERE user_id=?", (nation, self.owner_id))
            db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (self.owner_id,))
            db.execute("""UPDATE player_war_settings
                SET armed_forces_name=?,army_name=?,airforce_name=?,navy_name=? WHERE user_id=?""",
                (forces, land, air, navy, self.owner_id))
            db.commit()
            await interaction.response.defer()
            if self.source_message is not None:
                await self.source_message.edit(view=WarCommandView(self.owner_id, notice="✅ Nation and service names saved."))

    class WarCustomiseButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Customise", emoji="📝", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.send_modal(WarCustomiseModal(self.owner_id, interaction.message))

    class WarDetailView(discord.ui.LayoutView):
        """A temporary page inside the original War message, never a new output."""
        def __init__(self, owner_id: int, title: str, body: str, colour=discord.Color.dark_red(), season_page=False):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            container = discord.ui.Container(accent_color=colour)
            container.add_item(discord.ui.TextDisplay(f"## {title}\n{body}"))
            buttons = [WarBackButton(owner_id), WarLobbyButton(owner_id)]
            if season_page:
                buttons.insert(0, SeasonMissionsButton(owner_id))
            container.add_item(discord.ui.ActionRow(*buttons))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This War panel belongs to another player. Open `/lobby` for your own panel.", ephemeral=True)
            return False

    class SeasonMissionsButton(discord.ui.Button):
        def __init__(self, owner_id):
            super().__init__(label="Weekly Missions", emoji="📜", style=discord.ButtonStyle.primary)
            self.owner_id = owner_id

        async def callback(self, interaction):
            await interaction.response.edit_message(view=season_missions_view(self.owner_id))

    class SeasonMissionClaimButton(discord.ui.Button):
        def __init__(self, owner_id, mission):
            ready = mission["progress"] >= mission["target"] and not mission["claimed"]
            super().__init__(label=mission["title"][:80], emoji="🎁" if ready else "📜",
                             style=discord.ButtonStyle.success if ready else discord.ButtonStyle.secondary,
                             disabled=not ready)
            self.owner_id = owner_id
            self.mission_key = mission["key"]

        async def callback(self, interaction):
            success, message = claim_weekly_season_mission(db, interaction.user.id, self.mission_key)
            await interaction.response.edit_message(view=season_missions_view(self.owner_id, message))

    class SeasonMissionsView(discord.ui.LayoutView):
        def __init__(self, owner_id, body, notice=""):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.gold())
            suffix = f"\n\n-# {notice}" if notice else ""
            container.add_item(discord.ui.TextDisplay("## 📜 Weekly Season Missions\n" + body + suffix))
            season = active_season(db)
            missions = weekly_season_missions(db, owner_id, season) if season else []
            if missions:
                container.add_item(discord.ui.ActionRow(*(SeasonMissionClaimButton(owner_id, mission) for mission in missions)))
            container.add_item(discord.ui.ActionRow(SeasonMissionsButton(owner_id), WarBackButton(owner_id), WarLobbyButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This Season panel belongs to another player. Open `/war` for your own panel.", ephemeral=True)
            return False

    class WarOperationsButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Operations", emoji="🎯", style=discord.ButtonStyle.primary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=WarOperationsView(self.owner_id))

    class WarOperationsView(discord.ui.LayoutView):
        """Second War page so combat actions are not crowded into the overview."""
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            player = db.execute("SELECT money FROM players WHERE user_id=?", (owner_id,)).fetchone()
            state = db.execute("SELECT supply,readiness,morale FROM player_war_settings WHERE user_id=?", (owner_id,)).fetchone()
            state = state or {"supply": 0, "readiness": 0, "morale": 0}
            container = discord.ui.Container(accent_color=discord.Color.dark_red())
            container.add_item(discord.ui.TextDisplay(
                "## 🎯 Military Operations\n"
                f"⚔️ **{int(player['money']):,} War Credits** · 📦 Supply **{int(state['supply']):,}**\n"
                f"🎯 Readiness **{int(state['readiness'])}%** · 🔥 Morale **{int(state['morale'])}%**\n"
                "Choose an action; costs are shown before anything is charged."
            ))
            container.add_item(discord.ui.ActionRow(
                WarQuickButton("recruit", "Recruit", "🪖", discord.ButtonStyle.primary),
                WarQuickButton("supply", "Buy Supply", "📦", discord.ButtonStyle.success),
                WarQuickButton("prepare", "Prepare", "🎯"),
                WarQuickButton("rally", "Rally", "🔥"),
                WarQuickButton("costs", "Costs", "🧾"),
            ))
            container.add_item(discord.ui.ActionRow(WarBackButton(owner_id), WarLobbyButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This War panel belongs to another player. Open `/lobby` for your own panel.", ephemeral=True)
            return False

    class WarQuickButton(discord.ui.Button):
        def __init__(self, action: str, label: str, emoji: str, style=discord.ButtonStyle.secondary):
            super().__init__(label=label, emoji=emoji, style=style)
            self.action = action

        async def callback(self, interaction: discord.Interaction):
            if self.action == "supply":
                await interaction.response.send_modal(SupplyPurchaseModal(interaction.user.id, interaction.message))
                return
            if self.action == "prepare":
                create_player(interaction.user)
                db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (interaction.user.id,))
                state = db.execute("SELECT supply,readiness FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
                cost = setting(db, "war_prepare_supply_cost")
                if int(state["readiness"]) >= 100:
                    text, colour = "Your Readiness is already **100%**.", discord.Color.gold()
                elif int(state["supply"]) < cost:
                    text, colour = f"You need **{cost:,} Supply** but have **{state['supply']:,}**.", discord.Color.red()
                else:
                    gain = min(setting(db, "war_readiness_per_prepare"), 100 - int(state["readiness"]))
                    db.execute("UPDATE player_war_settings SET supply=supply-?,readiness=readiness+? WHERE user_id=?", (cost, gain, interaction.user.id))
                    db.commit()
                    text, colour = f"Readiness **+{gain}%** · Supply **-{cost:,}**.", discord.Color.green()
                await interaction.response.edit_message(view=WarDetailView(interaction.user.id, "🎯 Forces Prepared", text, colour))
                return
            if self.action == "rally":
                create_player(interaction.user)
                db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (interaction.user.id,))
                state = db.execute("SELECT supply,morale FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
                cost = setting(db, "war_rally_supply_cost")
                if int(state["morale"]) >= 100:
                    text, colour = "Your military Morale is already **100%**.", discord.Color.gold()
                elif int(state["supply"]) < cost:
                    text, colour = f"A rally needs **{cost:,} Supply** but you have **{state['supply']:,}**.", discord.Color.red()
                else:
                    gain = min(setting(db, "war_rally_morale_gain"), 100 - int(state["morale"]))
                    db.execute("UPDATE player_war_settings SET supply=supply-?,morale=morale+? WHERE user_id=?", (cost, gain, interaction.user.id))
                    db.commit()
                    text, colour = f"Morale **+{gain}%** · Supply **-{cost:,}**.", discord.Color.green()
                await interaction.response.edit_message(view=WarDetailView(interaction.user.id, "🔥 Military Rally", text, colour))
                return
            if self.action == "recruit":
                await interaction.response.edit_message(view=ArmyShopView(interaction.user.id))
                return
            if self.action == "city":
                await interaction.response.edit_message(view=CitySystemView(interaction.user.id))
                return

            player = create_player(interaction.user)
            state = db.execute("SELECT supply,readiness,morale FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
            fort_row = db.execute("SELECT fortification_level FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
            fort_level = int(fort_row["fortification_level"]) if fort_row else 0
            fort_cost = setting(db, "fortify_base_cost") * (fort_level + 1)
            body = (
                f"### Your current state\n"
                f"💰 **{player['money']:,} War Credits** · 📦 **{state['supply']:,}/{setting(db, 'war_max_supply'):,} Supply**\n"
                f"🎯 Readiness **{state['readiness']}%** · 🔥 Morale **{state['morale']}%**\n\n"
                f"### Action costs\n"
                f"📦 Supply: **{setting(db, 'war_supply_cost'):,} War Credits per Supply**\n"
                f"🎯 Prepare: **{setting(db, 'war_prepare_supply_cost'):,} Supply** → **+{setting(db, 'war_readiness_per_prepare')}% Readiness**\n"
                f"🔥 Rally: **{setting(db, 'war_rally_supply_cost'):,} Supply** → **+{setting(db, 'war_rally_morale_gain')}% Morale**\n"
                f"🏙️ Civilian City: **{setting(db, 'city_civilian_build_cost'):,} War Credits**\n"
                f"🏭 Industrial City: **{setting(db, 'city_industrial_build_cost'):,} War Credits**\n"
                f"🏰 Fortification Lv {fort_level + 1}: **{fort_cost:,} War Credits**"
            )
            await interaction.response.edit_message(view=WarDetailView(interaction.user.id, "🧾 War Cost Guide", body, discord.Color.gold()))

    class WarInfoButton(discord.ui.Button):
        def __init__(self, action, label, emoji, style=discord.ButtonStyle.secondary):
            super().__init__(label=label, emoji=emoji, style=style); self.action = action
        async def callback(self, interaction):
            player = create_player(interaction.user)
            if self.action == "forces":
                # Do not fill the player panel with every shop model at 0 units.
                # Group real units by service so the force composition is easy
                # to scan on a phone as well as on desktop.
                units = [u for u in war_system.units_for_player(db, interaction.user.id, enabled_only=True) if int(u["quantity"] or 0) > 0]
                service_order = (
                    ("land", "🪖 Land Army"),
                    ("air", "✈️ Air Force"),
                    ("navy", "⚓ Navy"),
                )
                grouped = {key: [] for key, _label in service_order}
                for unit in units:
                    branch = str(unit["branch"] or "land").lower()
                    service = "air" if branch in {"air", "aircraft", "helicopter"} else "navy" if branch in {"navy", "fleet", "sea"} else "land"
                    grouped[service].append(unit)
                parts = []
                for service, label in service_order:
                    rows = grouped[service]
                    if not rows:
                        continue
                    power = sum(int(row["quantity"]) * int(row["power"]) for row in rows)
                    parts.append(f"### {label} · **{power:,} Power**")
                    parts.extend(f"{row['emoji']} **{row['name']}** · {int(row['quantity']):,} units · {int(row['quantity']) * int(row['power']):,} Power" for row in rows)
                total_power = sum(int(row["quantity"]) * int(row["power"]) for row in units)
                body = (f"**Total strength:** {total_power:,} Power\n\n" + "\n".join(parts)) if parts else "No units recruited yet. Open **Recruit** to build your force."
                title, colour = "⚔️ Your Armed Forces", discord.Color.dark_red()
            elif self.action == "capital":
                title, body, colour = "🏛️ Capital Defence", f"**{player['capital_name']}**\n❤️ Health: **{player['capital_health']}/100**\n🛡️ Stance: **{stance(db,interaction.user.id).title()}**", discord.Color.gold()
            elif self.action == "season":
                # Season has its own interactive controls, so it replaces this message as well.
                await interaction.response.edit_message(view=season_view(interaction.user.id))
                return
            else:
                war = get_active_war(); alliance = get_alliance_for_user(interaction.user.id)
                text = "No active Alliance War." if not war else f"Active War **#{war['id']}** · Started <t:{war['started_at']}:R>"
                text += f"\nYour Alliance: **[{alliance['tag']}] {alliance['name']}**" if alliance else "\nYour Alliance: **None**"
                title, body, colour = "🌍 War Status", text, discord.Color.red() if war else discord.Color.green()
            await interaction.response.edit_message(view=WarDetailView(interaction.user.id, title, body, colour))

    class WarCommandView(discord.ui.LayoutView):
        def __init__(self, user_id, notice: str | None = None):
            super().__init__(timeout=300)
            self.owner_id = user_id
            player = db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
            names = service_names(db, user_id)
            powers = war_system.service_powers(db, user_id)
            container = discord.ui.Container(accent_color=discord.Color.dark_red())
            container.add_item(discord.ui.TextDisplay(
                f"## ⚔️ {player['nation_name']} — {names['forces']}\n"
                f"🏛️ Capital **{player['capital_health']}/100 HP** · 🛡️ **{stance(db,user_id).title()}** stance\n\n"
                f"🪖 **{names['land']}** · **{powers['land']:,} Power**\n"
                f"✈️ **{names['air']}** · **{powers['air']:,} Power**\n"
                f"⚓ **{names['navy']}** · **{powers['navy']:,} Power**\n"
                f"-# Total Armed Forces Power: {war_system.total_power(db,user_id):,}"
            ))
            if notice:
                container.add_item(discord.ui.TextDisplay(f"-# {notice}"))
            container.add_item(discord.ui.ActionRow(
                WarInfoButton("forces", "Armed Forces", "🪖", discord.ButtonStyle.primary),
                WarInfoButton("capital", "Capital", "🏛️", discord.ButtonStyle.success),
                WarInfoButton("status", "War Status", "🌍", discord.ButtonStyle.danger),
                WarInfoButton("season", "Season", "🏁", discord.ButtonStyle.secondary),
            ))
            container.add_item(discord.ui.ActionRow(
                WarQuickButton("city", "City Centre", "🏙️", discord.ButtonStyle.secondary),
                WarOperationsButton(user_id),
                WarCustomiseButton(user_id),
                WarLobbyButton(user_id),
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(
                "### Command centre\nOpen **Operations** to recruit, prepare, rally and buy Supply.\n"
                "-# Customise changes your Nation, Armed Forces, Land Army, Air Force and Navy names in one form."
            ))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This War panel belongs to another player. Open `/lobby` for your own panel.", ephemeral=True)
            return False

    bot.xbot_player_panel_builders = getattr(bot, "xbot_player_panel_builders", {})
    bot.xbot_player_panel_builders["war"] = lambda owner_id: WarCommandView(owner_id)

    @bot.tree.command(name="war", description="Open your X BOT War Centre")
    async def war(interaction: discord.Interaction):
        create_player(interaction.user)
        await interaction.response.send_message(view=WarCommandView(interaction.user.id))

    def is_war_staff(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        if interaction.guild.owner_id == interaction.user.id:
            return True
        permissions = getattr(interaction.user, "guild_permissions", None)
        if permissions and (permissions.administrator or permissions.moderate_members):
            return True
        role_names = {str(role.name).casefold() for role in getattr(interaction.user, "roles", [])}
        return any(name in role_names for name in {"administrator", "admin", "moderator", "moterator"})

    @bot.tree.command(name="forces_check", description="Staff: inspect a member's armed forces", **player_command_kwargs)
    @app_commands.describe(user="Member whose Land Army, Air Force and Navy you want to inspect")
    async def forces_check(interaction: discord.Interaction, user: discord.Member):
        if not is_war_staff(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators and Moderators can inspect another member's armed forces."), ephemeral=True)
            return
        target = create_player(user)
        units = [u for u in war_system.units_for_player(db, user.id, enabled_only=True) if int(u["quantity"] or 0) > 0]
        groups = {"land": [], "air": [], "navy": []}
        for unit in units:
            branch = str(unit["branch"] or "land").lower()
            service = "air" if branch in {"air", "aircraft", "helicopter"} else "navy" if branch in {"navy", "fleet", "sea"} else "land"
            groups[service].append(unit)
        labels = {"land": "🪖 Land Army", "air": "✈️ Air Force", "navy": "⚓ Navy"}
        lines = [f"## ⚔️ Staff Forces Check\n🏳️ **{target['nation_name']}** · Owner: {user.mention}"]
        total = 0
        for service in ("land", "air", "navy"):
            power = sum(int(row["quantity"]) * int(row["power"]) for row in groups[service])
            total += power
            lines.append(f"\n### {labels[service]} · **{power:,} Power**")
            lines.extend(f"{row['emoji']} **{row['name']}** ×{int(row['quantity']):,}" for row in groups[service]) or lines.append("*No units*")
        lines.append(f"\n**Total Armed Forces Power: {total:,}**\n⚔️ War Credits: **{target['money']:,}**")
        await interaction.response.send_message(view=xbot_ui.panel("🛰️ Staff War Inspection", "\n".join(lines), colour=discord.Color.dark_red()), ephemeral=True)

    @bot.tree.command(name="defense", description="Set your Nation's military stance")
    @app_commands.choices(stance_choice=[
        app_commands.Choice(name="Balanced — no bonus or penalty", value="balanced"),
        app_commands.Choice(name="Fortified — stronger defence", value="fortified"),
        app_commands.Choice(name="Aggressive — stronger attacks", value="aggressive"),
    ])
    async def defense(interaction: discord.Interaction, stance_choice: app_commands.Choice[str]):
        create_player(interaction.user)
        db.execute("""INSERT INTO player_war_settings(user_id,defense_stance) VALUES(?,?)
            ON CONFLICT(user_id) DO UPDATE SET defense_stance=excluded.defense_stance""", (interaction.user.id, stance_choice.value))
        db.commit()
        bonus = f"+{setting(db,'fortified_defense_bonus')}% defence" if stance_choice.value == "fortified" else f"+{setting(db,'aggressive_attack_bonus')}% attack" if stance_choice.value == "aggressive" else "No modifier"
        await interaction.response.send_message(view=xbot_ui.success("🛡️ Defence Stance Updated", f"Stance: **{stance_choice.name}**\nEffect: **{bonus}**"))

    @bot.tree.command(name="repair", description="Repair your Capital using War Credits")
    async def repair(interaction: discord.Interaction, health: app_commands.Range[int, 1, 100]):
        player = create_player(interaction.user); needed = 100 - player["capital_health"]; repaired = min(health, needed)
        if repaired <= 0:
            await interaction.response.send_message(view=xbot_ui.warning("🏛️ Capital Repair", "Your Capital already has full health."), ephemeral=True); return
        cost = repaired * setting(db, "capital_repair_cost_per_hp")
        if player["money"] < cost:
            await interaction.response.send_message(view=xbot_ui.danger("Not Enough War Credits", f"Repairing **{repaired} HP** costs **{cost:,} War Credits**."), ephemeral=True); return
        db.execute("UPDATE players SET capital_health=capital_health+?,money=money-? WHERE user_id=?", (repaired, cost, interaction.user.id))
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🏛️ Capital Repaired", f"Restored **{repaired} HP** for **{cost:,} War Credits**.\nNew health: **{player['capital_health']+repaired}/100**"))

    @bot.tree.command(name="war_history", description="View recent X BOT battles")
    @app_commands.describe(player="Optional: show only battles involving this player")
    async def war_history(interaction: discord.Interaction, player: discord.Member | None = None):
        if player:
            rows = db.execute("SELECT * FROM battle_history WHERE attacker_id=? OR defender_id=? ORDER BY id DESC LIMIT 10", (player.id, player.id)).fetchall()
        else:
            rows = db.execute("SELECT * FROM battle_history ORDER BY id DESC LIMIT 10").fetchall()
        if not rows:
            await interaction.response.send_message(view=xbot_ui.warning("📜 Battle History", "No battles have been recorded yet.")); return
        lines = [f"**#{r['id']}** <@{r['attacker_id']}> ⚔️ <@{r['defender_id']}> · Winner <@{r['winner_id']}> · <t:{r['created_at']}:R>" for r in rows]
        await interaction.response.send_message(view=xbot_ui.panel("📜 Recent Battle History", "\n".join(lines), colour=discord.Color.dark_red()))

    @bot.tree.command(name="war_leaderboard", description="View the strongest Nations")
    async def war_leaderboard(interaction: discord.Interaction):
        rows = db.execute("""SELECT p.user_id,p.nation_name,COALESCE(SUM(w.quantity*u.power),0) power
            FROM players p LEFT JOIN player_war_units w ON w.user_id=p.user_id
            LEFT JOIN war_unit_types u ON u.id=w.unit_type_id AND u.enabled=1
            GROUP BY p.user_id ORDER BY power DESC LIMIT 10""").fetchall()
        body = "\n".join(f"**{index}.** 🏳️ {r['nation_name']} · **{r['power']:,} Power**" for index, r in enumerate(rows, 1)) or "No Nations yet."
        await interaction.response.send_message(view=xbot_ui.panel("🏆 War Power Leaderboard", body, colour=discord.Color.gold()))

    @bot.tree.command(name="war_stats", description="View a Nation's battle record")
    async def war_stats(interaction: discord.Interaction, player: discord.Member | None = None):
        selected = player or interaction.user; data = create_player(selected)
        stats = db.execute("""SELECT COUNT(*) battles,
            COALESCE(SUM(CASE WHEN winner_id=? THEN 1 ELSE 0 END),0) wins,
            COALESCE(SUM(CASE WHEN winner_id<>? THEN 1 ELSE 0 END),0) losses
            FROM battle_history WHERE attacker_id=? OR defender_id=?""", (selected.id, selected.id, selected.id, selected.id)).fetchone()
        units = db.execute("SELECT COALESCE(SUM(quantity),0) total FROM player_war_units WHERE user_id=?", (selected.id,)).fetchone()["total"]
        body = (f"🏳️ **Nation:** {data['nation_name']}\n⚔️ **Battles:** {stats['battles']}\n"
                f"🏆 **Wins:** {stats['wins']} · 💀 **Losses:** {stats['losses']}\n"
                f"💥 **Power:** {war_system.total_power(db,selected.id):,}\n🪖 **Total Units:** {units:,}\n"
                f"🛡️ **Stance:** {stance(db,selected.id).title()}")
        await interaction.response.send_message(view=xbot_ui.panel("📊 War Statistics", body, colour=discord.Color.dark_red()))

    @bot.tree.command(name="scout", description="Scout another Nation before attacking")
    async def scout(interaction: discord.Interaction, target: discord.Member):
        if target.bot or target.id == interaction.user.id:
            await interaction.response.send_message(view=xbot_ui.danger("🔭 Scout Rejected", "Choose another human player."), ephemeral=True); return
        player = create_player(interaction.user); target_data = create_player(target)
        last = db.execute("SELECT created_at FROM scout_history WHERE scout_id=? ORDER BY id DESC LIMIT 1", (interaction.user.id,)).fetchone()
        remaining = setting(db, "scout_cooldown") - (int(time.time()) - last["created_at"]) if last else 0
        if remaining > 0:
            await interaction.response.send_message(view=xbot_ui.warning("🔭 Scouts Recovering", f"Try again in **{remaining} seconds**."), ephemeral=True); return
        cost = setting(db, "scout_cost")
        if player["money"] < cost:
            await interaction.response.send_message(view=xbot_ui.danger("Not Enough War Credits", f"Scouting costs **{cost:,} War Credits**."), ephemeral=True); return
        db.execute("UPDATE players SET money=money-? WHERE user_id=?", (cost, interaction.user.id))
        db.execute("INSERT INTO scout_history(scout_id,target_id,created_at) VALUES(?,?,?)", (interaction.user.id, target.id, int(time.time())))
        db.commit()
        actual = war_system.total_power(db, target.id); estimate = max(0, round(actual * random.uniform(.9, 1.1)))
        alliance = get_alliance_for_user(target.id); alliance_text = f"[{alliance['tag']}] {alliance['name']}" if alliance else "None"
        body = (f"🏳️ **Target:** {target_data['nation_name']}\n💥 **Estimated Power:** {estimate:,}\n"
                f"🏛️ **Capital HP:** {target_data['capital_health']}/100\n🗺️ **Land:** {target_data['land']}\n"
                f"🤝 **Alliance:** {alliance_text}\n🛡️ **Observed Stance:** {stance(db,target.id).title()}")
        await interaction.response.send_message(view=xbot_ui.panel("🔭 Intelligence Report", body, colour=discord.Color.teal(), footer=f"Cost: {cost} War Credits · Power estimate has ±10% uncertainty."), ephemeral=True)

    @bot.tree.command(name="demobilize", description="Retire military units for a partial War Credit refund")
    async def demobilize(interaction: discord.Interaction, model: str, amount: int):
        create_player(interaction.user)
        unit = db.execute("SELECT * FROM war_unit_types WHERE name=? COLLATE NOCASE OR code=? COLLATE NOCASE", (model.strip(), model.strip())).fetchone()
        owned = None if unit is None else db.execute("SELECT quantity FROM player_war_units WHERE user_id=? AND unit_type_id=?", (interaction.user.id, unit["id"])).fetchone()
        if unit is None or amount <= 0 or owned is None or owned["quantity"] < amount:
            await interaction.response.send_message(view=xbot_ui.danger("Demobilization Rejected", "Check the model name and quantity in `/army`."), ephemeral=True); return
        refund = unit["cost"] * amount * setting(db, "demobilize_refund_percent") // 100
        db.execute("UPDATE player_war_units SET quantity=quantity-? WHERE user_id=? AND unit_type_id=?", (amount, interaction.user.id, unit["id"]))
        db.execute("UPDATE players SET money=money+? WHERE user_id=?", (refund, interaction.user.id)); db.commit()
        await interaction.response.send_message(view=xbot_ui.warning("🪖 Units Demobilized", f"Retired **{amount}x {unit['emoji']} {unit['name']}**.\nRefund: **{refund:,} War Credits**"))

    @bot.tree.command(name="war_readiness", description="View your Nation's complete military readiness")
    async def war_readiness(interaction: discord.Interaction):
        player = create_player(interaction.user)
        state = db.execute("SELECT * FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
        level = state["fortification_level"] if state else 0
        morale = state["morale"] if state else 100
        supply = state["supply"] if state else 500
        readiness = state["readiness"] if state else 100
        base = war_system.total_power(db, interaction.user.id)
        defended = effective_power(db, interaction.user.id, attacking=False)
        body = (f"🏳️ **{player['nation_name']}**\n💥 Base military power: **{base:,}**\n"
                f"🛡️ Defensive power: **{defended:,}**\n🏰 Fortification: **Level {level}/{setting(db,'fortify_max_level')}**\n"
                f"🔥 Morale: **{morale}%**\n🎯 Readiness: **{readiness}%**\n📦 Supply: **{supply}/{setting(db,'war_max_supply')}**\n⚔️ Stance: **{stance(db,interaction.user.id).title()}**\n"
                f"🏛️ Capital: **{player['capital_health']}/100 HP**")
        await interaction.response.send_message(view=xbot_ui.panel("🛰️ War Readiness", body, colour=discord.Color.dark_red()))

    @bot.tree.command(name="fortify", description="Upgrade your Nation's defensive fortifications")
    async def fortify(interaction: discord.Interaction):
        player = create_player(interaction.user)
        state = db.execute("SELECT fortification_level FROM player_war_settings WHERE user_id=?", (interaction.user.id,)).fetchone()
        level = state["fortification_level"] if state else 0
        maximum = setting(db, "fortify_max_level")
        if level >= maximum:
            await interaction.response.send_message(view=xbot_ui.warning("🏰 Maximum Fortification", f"Your fortification is already Level **{maximum}**."), ephemeral=True); return
        cost = setting(db, "fortify_base_cost") * (level + 1)
        if player["money"] < cost:
            await interaction.response.send_message(view=xbot_ui.danger("Not Enough War Credits", f"Level {level+1} costs **{cost:,} War Credits**."), ephemeral=True); return
        db.execute("UPDATE players SET money=money-? WHERE user_id=?", (cost, interaction.user.id))
        db.execute("""INSERT INTO player_war_settings(user_id,fortification_level) VALUES(?,1)
            ON CONFLICT(user_id) DO UPDATE SET fortification_level=fortification_level+1""", (interaction.user.id,))
        db.commit()
        bonus = (level + 1) * setting(db, "fortify_power_percent")
        await interaction.response.send_message(view=xbot_ui.success("🏰 Fortification Upgraded", f"Fortification is now **Level {level+1}**.\nDefensive power bonus: **+{bonus}%**."))
