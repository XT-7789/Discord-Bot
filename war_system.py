"""Configurable X BOT war units and safe legacy migration."""
import sqlite3


DEFAULT_UNITS = (
    ("infantry", "Infantry", "🪖", "land", 50, 1, "Standard ground troops."),
    ("light_tank", "Light Tank Mk I", "🛡️", "tank", 400, 6, "Fast armoured support for land battles."),
    ("heavy_tank", "Heavy Tank Mk I", "🛡️", "tank", 900, 13, "Expensive heavy armour with high battle power."),
    ("fighter", "Fighter Aircraft", "✈️", "air", 150, 3, "Standard combat aircraft."),
    ("utility_helicopter", "Utility Helicopter", "🚁", "air", 300, 5, "A versatile helicopter for aerial support and rapid response."),
    ("warship", "Warship", "🚢", "navy", 250, 5, "Standard naval combat unit."),
)


def initialise(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS war_unit_categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT NOT NULL UNIQUE COLLATE NOCASE,
        emoji TEXT NOT NULL DEFAULT '⚔️',
        position INTEGER NOT NULL DEFAULT 99,
        enabled INTEGER NOT NULL DEFAULT 1,
        system_category INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS war_unit_types(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        emoji TEXT NOT NULL DEFAULT '🪖',
        branch TEXT NOT NULL DEFAULT 'land',
        cost INTEGER NOT NULL DEFAULT 50,
        power INTEGER NOT NULL DEFAULT 1,
        description TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        position INTEGER NOT NULL DEFAULT 99
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS player_war_units(
        user_id INTEGER NOT NULL,
        unit_type_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id, unit_type_id)
    )""")
    unit_columns = {row["name"] for row in db.execute("PRAGMA table_info(war_unit_types)")}
    if "category_id" not in unit_columns:
        db.execute("ALTER TABLE war_unit_types ADD COLUMN category_id INTEGER")
    category_columns = {row["name"] for row in db.execute("PRAGMA table_info(war_unit_categories)")}
    if "parent_branch" not in category_columns:
        db.execute("ALTER TABLE war_unit_categories ADD COLUMN parent_branch TEXT NOT NULL DEFAULT 'land'")
    # Tank is a Land category, not an independent battle branch.  This keeps
    # combat fair: land competes with land, air with air, and navy with navy.
    db.execute("UPDATE war_unit_types SET branch='land' WHERE branch IN ('tank','special')")
    categories = (
        ("Army", "🪖", 1, 1), ("Air Force", "✈️", 2, 1), ("Navy", "⚓", 3, 1),
        ("Infantry", "🪖", 10, 0), ("Tank", "🛡️", 20, 0),
        ("Light Tank", "🛡️", 20, 0), ("Medium Tank", "🛡️", 30, 0),
        ("Heavy Tank", "🛡️", 40, 0), ("Main Battle Tank (MBT)", "⚔️", 50, 0),
        ("Aircraft", "✈️", 30, 0), ("Helicopter", "🚁", 40, 0), ("Fleet", "⚓", 40, 0),
    )
    db.executemany("INSERT OR IGNORE INTO war_unit_categories(label,emoji,position,system_category) VALUES(?,?,?,?)", categories)
    db.execute("UPDATE war_unit_categories SET parent_branch='land' WHERE label IN ('Army','Infantry','Tank','Light Tank','Medium Tank','Heavy Tank','Main Battle Tank (MBT)')")
    db.execute("UPDATE war_unit_categories SET parent_branch='air' WHERE label IN ('Air Force','Aircraft','Helicopter')")
    db.execute("UPDATE war_unit_categories SET parent_branch='navy' WHERE label IN ('Navy','Fleet')")
    # Existing Dashboard categories may already contain Air or Navy models.
    # Carry their real service over automatically, so the new three-button
    # recruit screen does not hide the server's existing custom models.
    db.execute("""UPDATE war_unit_categories SET parent_branch='air'
        WHERE id IN (SELECT DISTINCT category_id FROM war_unit_types WHERE branch='air' AND category_id IS NOT NULL)""")
    db.execute("""UPDATE war_unit_categories SET parent_branch='navy'
        WHERE id IN (SELECT DISTINCT category_id FROM war_unit_types WHERE branch='navy' AND category_id IS NOT NULL)""")
    for position, row in enumerate(DEFAULT_UNITS, 1):
        db.execute("""INSERT OR IGNORE INTO war_unit_types
            (code,name,emoji,branch,cost,power,description,position)
            VALUES(?,?,?,?,?,?,?,?)""", row + (position,))
    db.execute("""UPDATE war_unit_types SET category_id=(SELECT id FROM war_unit_categories WHERE label='Army')
        WHERE category_id IS NULL AND branch='land'""")
    db.execute("""UPDATE war_unit_types SET category_id=(SELECT id FROM war_unit_categories WHERE label='Air Force')
        WHERE category_id IS NULL AND branch='air'""")
    db.execute("""UPDATE war_unit_types SET category_id=(SELECT id FROM war_unit_categories WHERE label='Navy')
        WHERE category_id IS NULL AND branch='navy'""")
    # Put the starter models into useful small categories without overwriting
    # any Dashboard-created model/category setup.
    db.execute("UPDATE war_unit_types SET category_id=(SELECT id FROM war_unit_categories WHERE label='Infantry') WHERE code='infantry'")
    db.execute("UPDATE war_unit_types SET category_id=(SELECT id FROM war_unit_categories WHERE label='Tank') WHERE code IN ('light_tank','heavy_tank')")
    db.execute("UPDATE war_unit_types SET category_id=(SELECT id FROM war_unit_categories WHERE label='Aircraft') WHERE code='fighter'")
    db.execute("UPDATE war_unit_types SET category_id=(SELECT id FROM war_unit_categories WHERE label='Helicopter') WHERE code='utility_helicopter'")
    db.execute("UPDATE war_unit_types SET category_id=(SELECT id FROM war_unit_categories WHERE label='Fleet') WHERE code='warship'")
    # Land Army has clear tank classes.  These updates preserve all unit
    # quantities/prices, only moving the displayed model into its class.
    tank_categories = {
        "Light Tank": ("light_tank",),
        "Medium Tank": ("medium_tank",),
        "Heavy Tank": ("heavy_tank",),
        "Main Battle Tank (MBT)": (
            "Main Battle Tank (MBT)", "Type 99A (China)", "M1A2 Abrams SEPv3", "Leopard 2A7+ (Germany)",
            "Challenger 3", "K2 Black Panther (South Korea)",
        ),
    }
    for category_label, identifiers in tank_categories.items():
        category = db.execute("SELECT id FROM war_unit_categories WHERE label=?", (category_label,)).fetchone()
        for identifier in identifiers:
            db.execute("""UPDATE war_unit_types SET category_id=?,branch='land'
                WHERE code=? OR name=? COLLATE NOCASE""", (category["id"], identifier, identifier))
    # A dashboard First Page must point at a visible small class, not the old
    # catch-all Army category.
    infantry_category = db.execute("SELECT id FROM war_unit_categories WHERE label='Infantry'").fetchone()
    current_default = db.execute("SELECT value FROM economy_settings WHERE key='army_recruit_default_category_id'").fetchone()
    current_category = None
    if current_default:
        try:
            current_category = db.execute("SELECT label FROM war_unit_categories WHERE id=?", (int(current_default["value"]),)).fetchone()
        except (TypeError, ValueError):
            current_category = None
    if infantry_category and (current_category is None or current_category["label"] == "Army"):
        db.execute("""INSERT INTO economy_settings(key,value) VALUES('army_recruit_default_category_id',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(infantry_category["id"]),))
    legacy = {
        "infantry": "land_army",
        "fighter": "air_army",
        "warship": "navy",
    }
    player_columns = {row[1] for row in db.execute("PRAGMA table_info(players)")}
    if "user_id" in player_columns:
        for code, column in legacy.items():
            if column not in player_columns:
                continue
            unit = db.execute("SELECT id FROM war_unit_types WHERE code=?", (code,)).fetchone()
            db.execute(f"""INSERT OR IGNORE INTO player_war_units(user_id,unit_type_id,quantity)
                SELECT user_id,?,{column} FROM players""", (unit["id"],))
    db.commit()


def units_for_player(db: sqlite3.Connection, user_id: int, enabled_only: bool = False):
    where = "WHERE u.enabled=1" if enabled_only else ""
    return db.execute(f"""SELECT u.*,COALESCE(p.quantity,0) quantity
        FROM war_unit_types u LEFT JOIN player_war_units p
        ON p.unit_type_id=u.id AND p.user_id=? {where}
        ORDER BY u.position,u.branch,u.name""", (user_id,)).fetchall()


def total_power(db: sqlite3.Connection, user_id: int) -> int:
    row = db.execute("""SELECT COALESCE(SUM(p.quantity*u.power),0) total
        FROM player_war_units p JOIN war_unit_types u ON u.id=p.unit_type_id
        WHERE p.user_id=? AND u.enabled=1""", (user_id,)).fetchone()
    return int(row["total"])


def branch_power(db: sqlite3.Connection, user_id: int, branch: str) -> int:
    """Return raw power for one real service branch: land, air or navy."""
    if branch not in {"land", "air", "navy"}:
        return 0
    row = db.execute("""SELECT COALESCE(SUM(p.quantity*u.power),0) total
        FROM player_war_units p JOIN war_unit_types u ON u.id=p.unit_type_id
        WHERE p.user_id=? AND u.enabled=1 AND u.branch=?""", (user_id, branch)).fetchone()
    return int(row["total"])


def land_combat_power(db: sqlite3.Connection, user_id: int) -> int:
    """Land battle value with an armour advantage over unarmoured infantry.

    Recruit-screen Power remains simple and visible.  In real Land combat,
    Tank / MBT classes receive a 60% armour effectiveness bonus.  This keeps
    100 cheap Infantry from automatically outperforming an equally-priced
    modern tank, while still allowing infantry numbers to matter.
    """
    row = db.execute("""SELECT COALESCE(SUM(p.quantity*u.power * CASE
            WHEN LOWER(COALESCE(c.label,'')) LIKE '%tank%'
              OR LOWER(u.name) LIKE '%tank%' THEN 1.60
            ELSE 1.00 END),0) total
        FROM player_war_units p
        JOIN war_unit_types u ON u.id=p.unit_type_id
        LEFT JOIN war_unit_categories c ON c.id=u.category_id
        WHERE p.user_id=? AND u.enabled=1 AND u.branch='land'""", (user_id,)).fetchone()
    return int(row["total"])


def service_powers(db: sqlite3.Connection, user_id: int) -> dict[str, int]:
    return {branch: branch_power(db, user_id, branch) for branch in ("land", "air", "navy")}


def alliance_total_power(db: sqlite3.Connection, alliance_id: int) -> int:
    row = db.execute("""SELECT COALESCE(SUM(p.quantity*u.power),0) total
        FROM alliance_members a JOIN player_war_units p ON p.user_id=a.user_id
        JOIN war_unit_types u ON u.id=p.unit_type_id
        WHERE a.alliance_id=? AND u.enabled=1""", (alliance_id,)).fetchone()
    return int(row["total"])


def calculate_losses(db: sqlite3.Connection, user_id: int, percentage: float):
    losses = {}
    for row in units_for_player(db, user_id, enabled_only=True):
        quantity = row["quantity"]
        losses[row["id"]] = max(1, int(quantity * percentage)) if quantity > 0 and percentage > 0 else 0
    return losses


def save_losses(db: sqlite3.Connection, user_id: int, losses: dict[int, int]) -> None:
    for unit_id, amount in losses.items():
        db.execute("""UPDATE player_war_units SET quantity=MAX(0,quantity-?)
            WHERE user_id=? AND unit_type_id=?""", (amount, user_id, unit_id))


def losses_text(db: sqlite3.Connection, losses: dict[int, int]) -> str:
    if not losses:
        return "No units lost"
    names = {row["id"]: (row["emoji"], row["name"]) for row in db.execute("SELECT id,emoji,name FROM war_unit_types")}
    return " · ".join(f"{names.get(unit_id, ('⚔️','Unit'))[0]} {names.get(unit_id, ('⚔️','Unit'))[1]} -{amount}" for unit_id, amount in losses.items() if amount) or "No units lost"
