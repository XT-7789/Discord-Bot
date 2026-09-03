"""Tier 7 Warfront 2.0: planned battles, territorial defence and reports.

The Discord UI is registered at the bottom of this module.  The battle engine
above it is deliberately synchronous: once a player confirms an attack there
is no ``await`` between validation and commit, so a double click cannot spend
or award anything twice.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import sqlite3
import time
from typing import Optional

import discord
from discord import app_commands

import war_system
import war_tier


DEFAULTS = {
    "tier7_war_enabled": "1",
    "tier7_plan_expiry_seconds": "900",
    "tier7_attack_cooldown_seconds": "30",
    "tier7_pair_cooldown_seconds": "300",
    "tier7_probe_force_percent": "35",
    "tier7_probe_attack_bonus": "-5",
    "tier7_probe_supply_cost": "10",
    "tier7_probe_casualty_percent": "70",
    "tier7_standard_force_percent": "70",
    "tier7_standard_attack_bonus": "0",
    "tier7_standard_supply_cost": "25",
    "tier7_standard_casualty_percent": "100",
    "tier7_breakthrough_force_percent": "100",
    "tier7_breakthrough_attack_bonus": "12",
    "tier7_breakthrough_supply_cost": "50",
    "tier7_breakthrough_casualty_percent": "135",
    "tier7_air_support_percent": "15",
    "tier7_navy_support_percent": "10",
    "tier7_terrain_plains_bonus": "0",
    "tier7_terrain_forest_bonus": "8",
    "tier7_terrain_hills_bonus": "12",
    "tier7_terrain_mountains_bonus": "18",
    "tier7_terrain_urban_bonus": "15",
    "tier7_terrain_coast_bonus": "5",
    "tier7_fortification_bonus_per_level": "6",
    "tier7_fortification_max_level": "5",
    "tier7_fortification_base_cost": "300",
    "tier7_city_defence_bonus": "4",
    "tier7_capital_defence_bonus": "20",
    "tier7_capital_priority_bonus": "10",
    "tier7_default_garrison_percent": "100",
    "tier7_defender_supply_cost": "15",
    "tier7_batch_fortify_limit": "5",
    "tier7_report_page_size": "5",
}


SETTING_LIMITS = {
    "tier7_war_enabled": (0, 1),
    "tier7_plan_expiry_seconds": (60, 86400),
    "tier7_attack_cooldown_seconds": (0, 86400),
    "tier7_pair_cooldown_seconds": (0, 604800),
    "tier7_probe_force_percent": (1, 100),
    "tier7_probe_attack_bonus": (-100, 100),
    "tier7_probe_supply_cost": (0, 100000),
    "tier7_probe_casualty_percent": (0, 300),
    "tier7_standard_force_percent": (1, 100),
    "tier7_standard_attack_bonus": (-100, 100),
    "tier7_standard_supply_cost": (0, 100000),
    "tier7_standard_casualty_percent": (0, 300),
    "tier7_breakthrough_force_percent": (1, 100),
    "tier7_breakthrough_attack_bonus": (-100, 100),
    "tier7_breakthrough_supply_cost": (0, 100000),
    "tier7_breakthrough_casualty_percent": (0, 300),
    "tier7_air_support_percent": (0, 100),
    "tier7_navy_support_percent": (0, 100),
    "tier7_terrain_plains_bonus": (0, 100),
    "tier7_terrain_forest_bonus": (0, 100),
    "tier7_terrain_hills_bonus": (0, 100),
    "tier7_terrain_mountains_bonus": (0, 100),
    "tier7_terrain_urban_bonus": (0, 100),
    "tier7_terrain_coast_bonus": (0, 100),
    "tier7_fortification_bonus_per_level": (0, 100),
    "tier7_fortification_max_level": (0, 25),
    "tier7_fortification_base_cost": (0, 100000000),
    "tier7_city_defence_bonus": (0, 100),
    "tier7_capital_defence_bonus": (0, 100),
    "tier7_capital_priority_bonus": (0, 100),
    "tier7_default_garrison_percent": (10, 100),
    "tier7_defender_supply_cost": (0, 100000),
    "tier7_batch_fortify_limit": (1, 25),
    "tier7_report_page_size": (1, 10),
}


MODE_LABELS = {
    "probe": ("Recon Probe", "🔎"),
    "standard": ("Standard Assault", "⚔️"),
    "breakthrough": ("Breakthrough", "💥"),
}
TERRAINS = ("plains", "forest", "hills", "mountains", "urban", "coast")


def setting(db, key: str) -> int:
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    raw = row["value"] if row else DEFAULTS[key]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(DEFAULTS[key])


def mode_config(db, mode: str) -> dict:
    mode = mode if mode in MODE_LABELS else "standard"
    label, emoji = MODE_LABELS[mode]
    return {
        "key": mode,
        "label": label,
        "emoji": emoji,
        "force_percent": setting(db, f"tier7_{mode}_force_percent"),
        "attack_bonus": setting(db, f"tier7_{mode}_attack_bonus"),
        "supply_cost": setting(db, f"tier7_{mode}_supply_cost"),
        "casualty_percent": setting(db, f"tier7_{mode}_casualty_percent"),
    }


def initialise(db) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS tier7_battle_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL UNIQUE,
            attacker_id INTEGER NOT NULL,
            defender_id INTEGER,
            territory_code TEXT,
            mode TEXT NOT NULL DEFAULT 'standard',
            status TEXT NOT NULL DEFAULT 'draft',
            preview_json TEXT NOT NULL DEFAULT '{}',
            error_text TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            resolved_at INTEGER,
            battle_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_tier7_plans_owner
            ON tier7_battle_plans(attacker_id,status,created_at);

        CREATE TABLE IF NOT EXISTS tier7_plan_units (
            plan_id INTEGER NOT NULL,
            unit_type_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            PRIMARY KEY(plan_id,unit_type_id)
        );

        CREATE TABLE IF NOT EXISTS tier7_territory_defence (
            territory_code TEXT PRIMARY KEY,
            owner_user_id INTEGER NOT NULL,
            terrain TEXT NOT NULL DEFAULT 'plains',
            fortification_level INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tier7_territory_owner
            ON tier7_territory_defence(owner_user_id,fortification_level);

        CREATE TABLE IF NOT EXISTS tier7_defence_profiles (
            user_id INTEGER PRIMARY KEY,
            garrison_percent INTEGER NOT NULL DEFAULT 100,
            capital_priority INTEGER NOT NULL DEFAULT 1,
            auto_reinforce INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tier7_battle_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            battle_id INTEGER NOT NULL UNIQUE,
            plan_id INTEGER NOT NULL UNIQUE,
            attacker_id INTEGER NOT NULL,
            defender_id INTEGER NOT NULL,
            territory_code TEXT NOT NULL,
            territory_name TEXT NOT NULL,
            terrain TEXT NOT NULL,
            mode TEXT NOT NULL,
            attacker_score INTEGER NOT NULL,
            defender_score INTEGER NOT NULL,
            winner_id INTEGER NOT NULL,
            land_captured INTEGER NOT NULL DEFAULT 0,
            capital_damage INTEGER NOT NULL DEFAULT 0,
            credits_captured INTEGER NOT NULL DEFAULT 0,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tier7_reports_players
            ON tier7_battle_reports(attacker_id,defender_id,created_at);

        CREATE TABLE IF NOT EXISTS tier7_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            plan_id INTEGER,
            event_type TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tier7_events_time
            ON tier7_events(created_at,event_type);
        """
    )
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))
    db.commit()
    ensure_defence_rows(db)


def _event(db, event_type: str, *, user_id=None, plan_id=None, detail=None) -> None:
    db.execute(
        "INSERT INTO tier7_events(user_id,plan_id,event_type,detail_json,created_at) VALUES(?,?,?,?,?)",
        (user_id, plan_id, event_type, json.dumps(detail or {}, ensure_ascii=False, default=str), int(time.time())),
    )


def _terrain_for(code: str, name: str, is_capital: bool, city_count: int = 0) -> str:
    if is_capital or city_count:
        return "urban"
    lowered = f"{code} {name}".casefold()
    if any(word in lowered for word in ("island", "coast", "bay", "cape", "beach")):
        return "coast"
    if any(word in lowered for word in ("mount", "alps", "himal", "rocky")):
        return "mountains"
    digest = hashlib.sha256(code.encode("utf-8", "ignore")).digest()[0]
    return ("plains", "plains", "forest", "hills", "coast")[digest % 5]


def ensure_defence_rows(db, user_id: int | None = None) -> None:
    caller_transaction = db.in_transaction
    parameters = () if user_id is None else (user_id,)
    where = "" if user_id is None else " WHERE t.owner_user_id=?"
    rows = db.execute(
        """SELECT t.territory_code,t.territory_name,t.owner_user_id,t.is_capital,
           (SELECT COUNT(*) FROM player_cities c WHERE c.territory_code=t.territory_code) city_count
           FROM map_territories t""" + where,
        parameters,
    ).fetchall()
    now = int(time.time())
    changed = False
    for row in rows:
        existing = db.execute(
            "SELECT owner_user_id FROM tier7_territory_defence WHERE territory_code=?",
            (row["territory_code"],),
        ).fetchone()
        if existing:
            if int(existing["owner_user_id"]) != int(row["owner_user_id"]):
                db.execute(
                    "UPDATE tier7_territory_defence SET owner_user_id=?,updated_at=? WHERE territory_code=?",
                    (row["owner_user_id"], now, row["territory_code"]),
                )
                changed = True
            continue
        terrain = _terrain_for(
            row["territory_code"], row["territory_name"], bool(row["is_capital"]), int(row["city_count"] or 0)
        )
        db.execute(
            """INSERT INTO tier7_territory_defence
               (territory_code,owner_user_id,terrain,fortification_level,updated_at)
               VALUES(?,?,?,?,?)""",
            (row["territory_code"], row["owner_user_id"], terrain, 0, now),
        )
        changed = True
    if changed and not caller_transaction:
        db.commit()


def ensure_player_state(db, user_id: int) -> None:
    caller_transaction = db.in_transaction
    state_inserted = profile_inserted = 0
    if db.execute("SELECT 1 FROM player_war_settings WHERE user_id=?", (user_id,)).fetchone() is None:
        state_inserted = db.execute("INSERT INTO player_war_settings(user_id) VALUES(?)", (user_id,)).rowcount
    if db.execute("SELECT 1 FROM tier7_defence_profiles WHERE user_id=?", (user_id,)).fetchone() is None:
        profile_inserted = db.execute(
            """INSERT INTO tier7_defence_profiles
               (user_id,garrison_percent,capital_priority,auto_reinforce,updated_at)
               VALUES(?,?,?,?,?)""",
            (user_id, setting(db, "tier7_default_garrison_percent"), 1, 0, int(time.time())),
        ).rowcount
    ensure_defence_rows(db, user_id)
    if (state_inserted or profile_inserted) and not caller_transaction:
        db.commit()


def defence_profile(db, user_id: int):
    ensure_player_state(db, user_id)
    return db.execute("SELECT * FROM tier7_defence_profiles WHERE user_id=?", (user_id,)).fetchone()


def _player_label(row) -> str:
    if row is None:
        return "Unknown Nation"
    keys = set(row.keys())
    return str((row["display_name"] if "display_name" in keys else "") or row["nation_name"] or row["user_id"])


def eligible_targets(db, attacker_id: int, get_active_war, get_alliance_for_user) -> list:
    """Return Nations which are in a current formal conflict with the player."""
    target_ids: set[int] = set()
    alliance_war = get_active_war()
    attacker_alliance = get_alliance_for_user(attacker_id)
    if alliance_war and attacker_alliance:
        sides = {int(alliance_war["attacker_alliance_id"]), int(alliance_war["defender_alliance_id"])}
        if len(sides) == 2 and int(attacker_alliance["id"]) in sides:
            enemy_alliance = next(value for value in sides if value != int(attacker_alliance["id"]))
            target_ids.update(
                int(row["user_id"])
                for row in db.execute("SELECT user_id FROM alliance_members WHERE alliance_id=?", (enemy_alliance,)).fetchall()
            )
    # Alliance and Nation wars may exist at the same time. The old ``else``
    # incorrectly hid a player's Nation-war enemies whenever any Alliance war
    # was active on the server, even when that Alliance war was unrelated.
    caller_transaction = db.in_transaction
    war_tier.active_nation_war(db, attacker_id)  # Also expires old declarations.
    target_ids.update(
        int(row["target_id"])
        for row in db.execute(
            """SELECT CASE WHEN attacker_id=? THEN defender_id ELSE attacker_id END target_id
               FROM nation_wars WHERE active=1 AND (attacker_id=? OR defender_id=?)""",
            (attacker_id, attacker_id, attacker_id),
        ).fetchall()
    )
    if not caller_transaction:
        db.commit()
    if not target_ids:
        return []
    placeholders = ",".join("?" for _ in target_ids)
    rows = db.execute(
        f"""SELECT * FROM players WHERE user_id IN ({placeholders})
            AND user_id<>? AND capital_health>0 ORDER BY nation_name,user_id""",
        (*sorted(target_ids), attacker_id),
    ).fetchall()
    own_alliance = get_alliance_for_user(attacker_id)
    return [
        row for row in rows
        if not own_alliance
        or not (get_alliance_for_user(int(row["user_id"])) and
                int(get_alliance_for_user(int(row["user_id"]))["id"]) == int(own_alliance["id"]))
    ]


def _all_map_tiles() -> dict:
    tiles = dict(war_tier.world_city_tiles(war_tier._province_features()))
    tiles.update(war_tier.world_capital_tiles())
    return tiles


def _territory_bonus(db, territory) -> tuple[int, dict]:
    ensure_defence_rows(db, int(territory["owner_user_id"]))
    defence = db.execute(
        "SELECT * FROM tier7_territory_defence WHERE territory_code=?", (territory["territory_code"],)
    ).fetchone()
    terrain = defence["terrain"] if defence and defence["terrain"] in TERRAINS else "plains"
    fortification = int(defence["fortification_level"] if defence else 0)
    cities = int(db.execute(
        "SELECT COUNT(*) amount FROM player_cities WHERE territory_code=?", (territory["territory_code"],)
    ).fetchone()["amount"])
    profile = defence_profile(db, int(territory["owner_user_id"]))
    bonuses = {
        "terrain": setting(db, f"tier7_terrain_{terrain}_bonus"),
        "fortification": fortification * setting(db, "tier7_fortification_bonus_per_level"),
        "cities": cities * setting(db, "tier7_city_defence_bonus"),
        "capital": setting(db, "tier7_capital_defence_bonus") if territory["is_capital"] else 0,
        "capital_priority": (
            setting(db, "tier7_capital_priority_bonus")
            if territory["is_capital"] and int(profile["capital_priority"]) else 0
        ),
    }
    return sum(bonuses.values()), {
        "terrain": terrain,
        "fortification_level": fortification,
        "city_count": cities,
        "bonuses": bonuses,
    }


def attackable_territories(db, attacker_id: int, defender_id: int) -> list:
    ensure_player_state(db, attacker_id)
    ensure_player_state(db, defender_id)
    targets = db.execute(
        """SELECT * FROM map_territories WHERE owner_user_id=?
           ORDER BY is_capital ASC,acquired_at ASC,territory_name""",
        (defender_id,),
    ).fetchall()
    ordinary = [row for row in targets if not int(row["is_capital"])]
    # A Capital only enters the candidate list after every ordinary Land has
    # fallen, but it must still share the active front.  Previously a remote
    # Capital could be attacked from anywhere once it was the final tile.
    candidates = ordinary or [row for row in targets if int(row["is_capital"])]
    if not candidates:
        return []
    tiles = _all_map_tiles()
    attacker_codes = [row["territory_code"] for row in db.execute(
        "SELECT territory_code FROM map_territories WHERE owner_user_id=?", (attacker_id,)
    ).fetchall()]
    attacker_bounds = [tiles[code][3] for code in attacker_codes if code in tiles]
    front = [
        row for row in candidates
        if row["territory_code"] in tiles and any(
            war_tier._lands_are_connected(tiles[row["territory_code"]][3], bounds) for bounds in attacker_bounds
        )
    ]
    # Legacy rows without geometry use a compatibility fallback.  When both
    # sides have real map geometry, an empty front means the Land does not
    # share a border and must not be selectable.
    has_target_geometry = any(row["territory_code"] in tiles for row in candidates)
    choices = front if front else (candidates if not attacker_bounds or not has_target_geometry else [])
    return sorted(choices, key=lambda row: (_territory_bonus(db, row)[0], int(row["level"]), row["territory_name"]))


def _selected_unit_ids(db, plan_id: int) -> list[int]:
    return [int(row["unit_type_id"]) for row in db.execute(
        "SELECT unit_type_id FROM tier7_plan_units WHERE plan_id=? ORDER BY unit_type_id", (plan_id,)
    ).fetchall()]


def set_plan_units(
    db, plan_id: int, unit_ids: list[int] | None = None, *, commit: bool = True,
) -> None:
    plan = db.execute("SELECT * FROM tier7_battle_plans WHERE id=?", (plan_id,)).fetchone()
    if not plan or plan["status"] != "draft":
        return
    mode = mode_config(db, plan["mode"])
    owned = war_system.units_for_player(db, int(plan["attacker_id"]), enabled_only=True)
    allowed = {int(unit_id) for unit_id in unit_ids} if unit_ids is not None else None
    db.execute("DELETE FROM tier7_plan_units WHERE plan_id=?", (plan_id,))
    for unit in owned:
        quantity = int(unit["quantity"] or 0)
        if quantity <= 0 or (allowed is not None and int(unit["id"]) not in allowed):
            continue
        deployed = max(1, quantity * int(mode["force_percent"]) // 100)
        db.execute(
            "INSERT INTO tier7_plan_units(plan_id,unit_type_id,quantity) VALUES(?,?,?)",
            (plan_id, unit["id"], min(quantity, deployed)),
        )
    if commit:
        db.commit()


def create_plan(db, attacker_id: int, defender_id: int | None = None, mode: str = "standard", now=None):
    now = int(now or time.time())
    mode = mode if mode in MODE_LABELS else "standard"
    ensure_player_state(db, attacker_id)
    db.execute(
        "UPDATE tier7_battle_plans SET status='expired' WHERE attacker_id=? AND status='draft' AND expires_at<=?",
        (attacker_id, now),
    )
    cursor = db.execute(
        """INSERT INTO tier7_battle_plans
           (token,attacker_id,defender_id,mode,status,created_at,expires_at)
           VALUES(?,?,?,?,?,?,?)""",
        (secrets.token_urlsafe(12), attacker_id, defender_id, mode, "draft", now,
         now + setting(db, "tier7_plan_expiry_seconds")),
    )
    plan_id = int(cursor.lastrowid)
    if defender_id is not None:
        choices = attackable_territories(db, attacker_id, defender_id)
        if choices:
            db.execute(
                "UPDATE tier7_battle_plans SET territory_code=? WHERE id=?",
                (choices[0]["territory_code"], plan_id),
            )
    set_plan_units(db, plan_id)
    _event(db, "plan_created", user_id=attacker_id, plan_id=plan_id, detail={"defender_id": defender_id, "mode": mode})
    db.commit()
    return db.execute("SELECT * FROM tier7_battle_plans WHERE id=?", (plan_id,)).fetchone()


def update_plan_target(db, plan_id: int, defender_id: int) -> None:
    plan = db.execute("SELECT * FROM tier7_battle_plans WHERE id=?", (plan_id,)).fetchone()
    if not plan or plan["status"] != "draft":
        return
    choices = attackable_territories(db, int(plan["attacker_id"]), defender_id)
    db.execute(
        "UPDATE tier7_battle_plans SET defender_id=?,territory_code=?,preview_json='{}' WHERE id=?",
        (defender_id, choices[0]["territory_code"] if choices else None, plan_id),
    )
    db.commit()


def update_plan_territory(db, plan_id: int, territory_code: str) -> None:
    db.execute(
        "UPDATE tier7_battle_plans SET territory_code=?,preview_json='{}' WHERE id=? AND status='draft'",
        (territory_code, plan_id),
    )
    db.commit()


def update_plan_mode(db, plan_id: int, mode: str) -> None:
    if mode not in MODE_LABELS:
        return
    selected = _selected_unit_ids(db, plan_id)
    db.execute(
        "UPDATE tier7_battle_plans SET mode=?,preview_json='{}' WHERE id=? AND status='draft'",
        (mode, plan_id),
    )
    db.commit()
    # Preserve an intentional empty/multi-page selection.  ``None`` is only
    # used when a brand-new plan should auto-select every owned model.
    set_plan_units(db, plan_id, selected)


def _conflict_for(db, attacker_id: int, defender_id: int, get_active_war, get_alliance_for_user):
    attacker_alliance = get_alliance_for_user(attacker_id)
    defender_alliance = get_alliance_for_user(defender_id)
    if attacker_alliance and defender_alliance and int(attacker_alliance["id"]) == int(defender_alliance["id"]):
        return None, "You cannot attack a member of your own Alliance."
    alliance_war = get_active_war()
    if alliance_war:
        sides = {int(alliance_war["attacker_alliance_id"]), int(alliance_war["defender_alliance_id"])}
        is_alliance_conflict = (
            attacker_alliance and defender_alliance
            and int(attacker_alliance["id"]) in sides
            and int(defender_alliance["id"]) in sides
            and int(attacker_alliance["id"]) != int(defender_alliance["id"])
        )
        if is_alliance_conflict:
            return {"kind": "alliance", "id": int(alliance_war["id"])}, None
        # An unrelated Alliance war must not cancel a valid Nation war.
    nation_war = war_tier.active_nation_war(db, attacker_id, defender_id)
    if nation_war is None:
        return None, "Declare war on a bordering Nation from Diplomacy before attacking."
    return {"kind": "nation", "id": int(nation_war["id"])}, None


def validate_plan(db, plan, get_active_war, get_alliance_for_user, now=None):
    now = int(now or time.time())
    errors: list[str] = []
    metadata = {"conflict": None}
    if plan is None:
        return ["This battle plan no longer exists."], metadata
    if plan["status"] != "draft":
        return [f"This battle plan is already {plan['status']}."], metadata
    if int(plan["expires_at"]) <= now:
        return ["This battle plan expired. Open a new Attack Planner."], metadata
    if not setting(db, "tier7_war_enabled"):
        errors.append("Warfront 2.0 is temporarily closed by an administrator.")
    attacker = db.execute("SELECT * FROM players WHERE user_id=?", (plan["attacker_id"],)).fetchone()
    defender = db.execute("SELECT * FROM players WHERE user_id=?", (plan["defender_id"],)).fetchone() if plan["defender_id"] else None
    if attacker is None:
        errors.append("Your Nation could not be found.")
    elif int(attacker["capital_health"]) <= 0:
        errors.append("Your Capital is conquered and cannot launch an attack.")
    if defender is None:
        errors.append("Choose a target Nation.")
    elif int(defender["capital_health"]) <= 0:
        errors.append("That Nation's Capital is already conquered.")
    if attacker is not None and defender is not None:
        conflict, conflict_error = _conflict_for(
            db, int(attacker["user_id"]), int(defender["user_id"]), get_active_war, get_alliance_for_user
        )
        metadata["conflict"] = conflict
        if conflict_error:
            errors.append(conflict_error)
        protection = war_tier.setting(db, "war_new_nation_protection_seconds")
        created_at = int(defender["nation_created_at"] or 0)
        if created_at and now < created_at + protection:
            errors.append(f"The target has New Nation Protection until <t:{created_at + protection}:R>.")
        pair_last = db.execute(
            "SELECT created_at FROM battle_history WHERE attacker_id=? AND defender_id=? ORDER BY created_at DESC LIMIT 1",
            (attacker["user_id"], defender["user_id"]),
        ).fetchone()
        pair_left = setting(db, "tier7_pair_cooldown_seconds") - (now - int(pair_last["created_at"])) if pair_last else 0
        if pair_left > 0:
            errors.append(f"Target cooldown ends <t:{now + pair_left}:R>.")
        general_left = setting(db, "tier7_attack_cooldown_seconds") - (now - int(attacker["last_attack"] or 0))
        if general_left > 0:
            errors.append(f"Attack cooldown ends <t:{now + general_left}:R>.")
    territory = db.execute(
        "SELECT * FROM map_territories WHERE territory_code=?", (plan["territory_code"],)
    ).fetchone() if plan["territory_code"] else None
    if territory is None or defender is None or int(territory["owner_user_id"]) != int(defender["user_id"]):
        errors.append("Choose an enemy Land that is still owned by the target.")
    elif int(territory["is_capital"]):
        ordinary = db.execute(
            "SELECT 1 FROM map_territories WHERE owner_user_id=? AND is_capital=0 LIMIT 1", (defender["user_id"],)
        ).fetchone()
        if ordinary:
            errors.append("Capture the target's ordinary Land before attacking its Capital.")
        elif plan["territory_code"] not in {
            row["territory_code"]
            for row in attackable_territories(db, int(plan["attacker_id"]), int(plan["defender_id"]))
        }:
            errors.append("That Capital is not connected to your active front line.")
    elif plan["territory_code"] not in {row["territory_code"] for row in attackable_territories(db, int(plan["attacker_id"]), int(plan["defender_id"]))}:
        errors.append("That Land is no longer on your active front line.")
    deployments = db.execute(
        """SELECT d.*,COALESCE(p.quantity,0) owned,u.branch FROM tier7_plan_units d
           JOIN war_unit_types u ON u.id=d.unit_type_id
           LEFT JOIN player_war_units p ON p.user_id=? AND p.unit_type_id=d.unit_type_id
           WHERE d.plan_id=? AND u.enabled=1""",
        (plan["attacker_id"], plan["id"]),
    ).fetchall()
    if not deployments:
        errors.append("Choose at least one military unit model.")
    elif not any(row["branch"] == "land" and min(int(row["quantity"]), int(row["owned"])) > 0 for row in deployments):
        errors.append("Every attack needs at least one Land Army unit.")
    if any(int(row["quantity"]) > int(row["owned"]) for row in deployments):
        errors.append("Your forces changed; reopen or refresh the plan before attacking.")
    state = db.execute("SELECT supply FROM player_war_settings WHERE user_id=?", (plan["attacker_id"],)).fetchone()
    cost = mode_config(db, plan["mode"])["supply_cost"]
    if state is None or int(state["supply"]) < cost:
        errors.append(f"This attack needs {cost:,} Supply.")
    return list(dict.fromkeys(errors)), metadata


def _unit_rows(db, plan_id: int, user_id: int, *, defender=False, garrison_percent=100) -> list[dict]:
    if defender:
        rows = db.execute(
            """SELECT u.id unit_type_id,u.name,u.branch,u.power,COALESCE(c.label,'') category_label,
               COALESCE(p.quantity,0) owned FROM war_unit_types u
               LEFT JOIN war_unit_categories c ON c.id=u.category_id
               JOIN player_war_units p ON p.unit_type_id=u.id AND p.user_id=?
               WHERE u.enabled=1 AND p.quantity>0""",
            (user_id,),
        ).fetchall()
        return [dict(row, quantity=max(1, int(row["owned"]) * garrison_percent // 100)) for row in rows]
    rows = db.execute(
        """SELECT u.id unit_type_id,u.name,u.branch,u.power,COALESCE(c.label,'') category_label,
           d.quantity FROM tier7_plan_units d JOIN war_unit_types u ON u.id=d.unit_type_id
           LEFT JOIN war_unit_categories c ON c.id=u.category_id
           WHERE d.plan_id=? AND d.quantity>0 AND u.enabled=1""",
        (plan_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _raw_branch_powers(rows: list[dict]) -> dict[str, int]:
    powers = {"land": 0, "air": 0, "navy": 0}
    for row in rows:
        branch = str(row["branch"] or "land").lower()
        branch = branch if branch in powers else "land"
        multiplier = 1.6 if branch == "land" and (
            "tank" in str(row.get("category_label", "")).casefold() or "tank" in str(row["name"]).casefold()
        ) else 1.0
        powers[branch] += int(int(row["quantity"]) * int(row["power"]) * multiplier)
    return powers


def _adjust_services(db, user_id: int, raw: dict[str, int], *, attacking: bool, mode=None) -> dict[str, int]:
    state = db.execute(
        "SELECT morale,readiness,supply,defense_stance FROM player_war_settings WHERE user_id=?", (user_id,)
    ).fetchone()
    morale = int(state["morale"] if state else 100)
    readiness = max(25, int(state["readiness"] if state else 100))
    supplied = int(state["supply"] if state else 500) > 0
    factor = morale * readiness / 10000
    if not supplied:
        factor *= 0.5
    output = {key: int(value * factor) for key, value in raw.items()}
    stance = str(state["defense_stance"] if state else "balanced")
    if attacking and stance == "aggressive":
        output["land"] = output["land"] * (100 + war_tier.setting(db, "aggressive_attack_bonus")) // 100
    if attacking and mode:
        output["land"] = output["land"] * (100 + int(mode["attack_bonus"])) // 100
    if not attacking and stance == "fortified":
        output["land"] = output["land"] * (100 + war_tier.setting(db, "fortified_defense_bonus")) // 100
    return output


def battle_snapshot(db, plan, *, generator: random.Random | None = None) -> dict:
    territory = db.execute("SELECT * FROM map_territories WHERE territory_code=?", (plan["territory_code"],)).fetchone()
    profile = defence_profile(db, int(plan["defender_id"]))
    attacker_rows = _unit_rows(db, int(plan["id"]), int(plan["attacker_id"]))
    defender_rows = _unit_rows(
        db, int(plan["id"]), int(plan["defender_id"]), defender=True,
        garrison_percent=max(10, min(100, int(profile["garrison_percent"])))
    )
    mode = mode_config(db, plan["mode"])
    attacker = _adjust_services(db, int(plan["attacker_id"]), _raw_branch_powers(attacker_rows), attacking=True, mode=mode)
    defender = _adjust_services(db, int(plan["defender_id"]), _raw_branch_powers(defender_rows), attacking=False)
    territory_bonus, defence_detail = _territory_bonus(db, territory)
    defender["land"] = defender["land"] * (100 + territory_bonus) // 100
    variance = max(0, min(50, war_tier.setting(db, "war_battle_variance_percent")))
    rolls = {side: {branch: 100 for branch in ("land", "air", "navy")} for side in ("attacker", "defender")}
    if generator is not None:
        for side in rolls:
            for branch in rolls[side]:
                rolls[side][branch] = generator.randint(100 - variance, 100 + variance)
    rolled_attacker = {branch: attacker[branch] * rolls["attacker"][branch] // 100 for branch in attacker}
    rolled_defender = {branch: defender[branch] * rolls["defender"][branch] // 100 for branch in defender}
    fronts = {}
    for branch in ("air", "navy"):
        a, d = rolled_attacker[branch], rolled_defender[branch]
        fronts[branch] = "none" if not (a or d) else ("attacker" if a > d else "defender")
    attacker_modifier = defender_modifier = 0
    if fronts["air"] == "attacker":
        attacker_modifier += setting(db, "tier7_air_support_percent")
    elif fronts["air"] == "defender":
        defender_modifier += setting(db, "tier7_air_support_percent")
    if fronts["navy"] == "attacker":
        defender_modifier -= setting(db, "tier7_navy_support_percent")
    elif fronts["navy"] == "defender":
        attacker_modifier -= setting(db, "tier7_navy_support_percent")
    attacker_score = max(0, rolled_attacker["land"] * max(0, 100 + attacker_modifier) // 100)
    defender_score = max(0, rolled_defender["land"] * max(0, 100 + defender_modifier) // 100)
    total = attacker_score + defender_score
    chance = 50 if total <= 0 else max(5, min(95, round(attacker_score * 100 / total)))
    return {
        "mode": mode,
        "attacker_services": attacker,
        "defender_services": defender,
        "rolled_attacker": rolled_attacker,
        "rolled_defender": rolled_defender,
        "rolls": rolls,
        "fronts": fronts,
        "attacker_modifier": attacker_modifier,
        "defender_modifier": defender_modifier,
        "attacker_score": attacker_score,
        "defender_score": defender_score,
        "attacker_win_chance": chance,
        "power_range": {
            "attacker": [attacker_score * (100 - variance) // 100, attacker_score * (100 + variance) // 100],
            "defender": [defender_score * (100 - variance) // 100, defender_score * (100 + variance) // 100],
        },
        "defence": defence_detail,
        "attacker_rows": attacker_rows,
        "defender_rows": defender_rows,
    }


def preview_plan(db, plan_id: int, get_active_war, get_alliance_for_user) -> tuple[dict | None, list[str]]:
    plan = db.execute("SELECT * FROM tier7_battle_plans WHERE id=?", (plan_id,)).fetchone()
    if plan is not None and plan["status"] == "draft":
        # Dashboard balance changes apply consistently to both the displayed
        # preview and the eventual result; keep the same selected models but
        # recalculate their deployed quantity with the current mode settings.
        set_plan_units(db, plan_id, _selected_unit_ids(db, plan_id), commit=False)
    errors, _metadata = validate_plan(db, plan, get_active_war, get_alliance_for_user)
    if errors:
        if plan is not None and plan["status"] == "draft" and int(plan["expires_at"]) <= int(time.time()):
            db.execute("UPDATE tier7_battle_plans SET status='expired' WHERE id=? AND status='draft'", (plan_id,))
        db.commit()
        return None, errors
    preview = battle_snapshot(db, plan)
    serializable = {key: value for key, value in preview.items() if key not in {"attacker_rows", "defender_rows"}}
    db.execute(
        "UPDATE tier7_battle_plans SET preview_json=? WHERE id=? AND status='draft'",
        (json.dumps(serializable), plan_id),
    )
    _event(db, "plan_previewed", user_id=plan["attacker_id"], plan_id=plan_id, detail={
        "chance": preview["attacker_win_chance"], "territory": plan["territory_code"]
    })
    db.commit()
    return preview, []


def _losses(rows: list[dict], percentage: float) -> dict[int, int]:
    losses: dict[int, int] = {}
    for row in rows:
        quantity = max(0, int(row["quantity"]))
        amount = min(quantity, max(1, int(quantity * percentage))) if quantity and percentage > 0 else 0
        if amount:
            losses[int(row["unit_type_id"])] = amount
    return losses


def _loss_text(db, losses: dict[int, int], *, max_items: int = 8, max_chars: int = 700) -> str:
    """Return a Discord-safe casualty summary for even very large unit libraries."""
    nonzero = [(int(unit_id), int(amount)) for unit_id, amount in losses.items() if int(amount) > 0]
    if not nonzero:
        return "No units lost"
    names = {
        int(row["id"]): (str(row["emoji"] or "⚔️"), str(row["name"] or "Unit"))
        for row in db.execute("SELECT id,emoji,name FROM war_unit_types").fetchall()
    }
    parts = [
        f"{names.get(unit_id, ('⚔️', 'Unit'))[0]} {names.get(unit_id, ('⚔️', 'Unit'))[1]} -{amount:,}"
        for unit_id, amount in nonzero
    ]
    kept = parts[:max_items]
    while kept:
        remaining = len(parts) - len(kept)
        suffix = f" · +{remaining} more model{'s' if remaining != 1 else ''}" if remaining else ""
        text = " · ".join(kept) + suffix
        if len(text) <= max_chars:
            return text
        kept.pop()
    return f"Casualties across {len(parts)} unit model{'s' if len(parts) != 1 else ''}"


def resolve_plan(db, plan_id: int, get_active_war, get_alliance_for_user, *, seed: str | None = None):
    """Resolve one plan exactly once and return ``(success, report, errors)``."""
    plan = db.execute("SELECT * FROM tier7_battle_plans WHERE id=?", (plan_id,)).fetchone()
    if plan and plan["status"] == "resolved" and plan["battle_id"]:
        report = db.execute("SELECT * FROM tier7_battle_reports WHERE plan_id=?", (plan_id,)).fetchone()
        if report is not None:
            return True, report, []
        return False, None, ["This battle is saved in history, but its detailed report is missing. Ask an administrator to run Tier 7 Repair."]
    if plan is None:
        return False, None, ["This battle plan no longer exists."]

    # Finish lazy state migration before opening the battle transaction.  Once
    # BEGIN IMMEDIATE succeeds, another Bot process or Dashboard writer cannot
    # make a second plan pass the same cooldown/resources/territory checks.
    ensure_player_state(db, int(plan["attacker_id"]))
    if plan["defender_id"] is not None:
        ensure_player_state(db, int(plan["defender_id"]))
    if db.in_transaction:
        db.commit()
    try:
        db.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        db.rollback()
        return False, None, ["The battle system is busy saving another action. Please press Launch Attack again."]

    try:
        plan = db.execute("SELECT * FROM tier7_battle_plans WHERE id=?", (plan_id,)).fetchone()
        if plan and plan["status"] == "resolved" and plan["battle_id"]:
            report = db.execute("SELECT * FROM tier7_battle_reports WHERE plan_id=?", (plan_id,)).fetchone()
            db.commit()
            if report is not None:
                return True, report, []
            return False, None, ["This battle is saved in history, but its detailed report is missing. Ask an administrator to run Tier 7 Repair."]
        if plan is not None and plan["status"] == "draft":
            set_plan_units(db, plan_id, _selected_unit_ids(db, plan_id), commit=False)
        errors, metadata = validate_plan(db, plan, get_active_war, get_alliance_for_user)
        if errors:
            if plan is not None and plan["status"] == "draft" and int(plan["expires_at"]) <= int(time.time()):
                db.execute("UPDATE tier7_battle_plans SET status='expired' WHERE id=? AND status='draft'", (plan_id,))
            db.commit()
            return False, None, errors
        claimed = db.execute(
            "UPDATE tier7_battle_plans SET status='resolving' WHERE id=? AND status='draft'", (plan_id,)
        )
        if claimed.rowcount != 1:
            db.rollback()
            return False, None, ["This battle plan is already being resolved."]

        # Auto reinforcement is a defender-owned, pre-battle safety setting.
        profile = defence_profile(db, int(plan["defender_id"]))
        defender_state = db.execute(
            "SELECT supply,readiness FROM player_war_settings WHERE user_id=?", (plan["defender_id"],)
        ).fetchone()
        prepare_cost = war_tier.setting(db, "war_prepare_supply_cost")
        if int(profile["auto_reinforce"]) and int(defender_state["readiness"]) < 100 and int(defender_state["supply"]) >= prepare_cost:
            gain = min(war_tier.setting(db, "war_readiness_per_prepare"), 100 - int(defender_state["readiness"]))
            db.execute(
                "UPDATE player_war_settings SET supply=supply-?,readiness=readiness+? WHERE user_id=?",
                (prepare_cost, gain, plan["defender_id"]),
            )
        generator = random.Random(seed or f"xbot-tier7:{plan['token']}:{int(time.time())}")
        snapshot = battle_snapshot(db, plan, generator=generator)
        attacker_spend = db.execute(
            """UPDATE player_war_settings
               SET supply=supply-?,readiness=MAX(25,readiness-?)
               WHERE user_id=? AND supply>=?""",
            (int(snapshot["mode"]["supply_cost"]), war_tier.setting(db, "war_readiness_loss_per_battle"),
             plan["attacker_id"], int(snapshot["mode"]["supply_cost"])),
        )
        if attacker_spend.rowcount != 1:
            raise RuntimeError("Attacker Supply changed before the battle transaction")
        attacker_won = int(snapshot["attacker_score"]) > int(snapshot["defender_score"])
        winner_id = int(plan["attacker_id"] if attacker_won else plan["defender_id"])
        loser_id = int(plan["defender_id"] if attacker_won else plan["attacker_id"])
        mode = snapshot["mode"]
        winner_rate = war_tier.setting(db, "war_winner_loss_percent") / 100
        loser_rate = war_tier.setting(db, "war_loser_loss_percent") / 100
        casualty_factor = int(mode["casualty_percent"]) / 100
        attacker_losses = _losses(snapshot["attacker_rows"], (winner_rate if attacker_won else loser_rate) * casualty_factor)
        defender_losses = _losses(snapshot["defender_rows"], (loser_rate if attacker_won else winner_rate) * casualty_factor)
        war_system.save_losses(db, int(plan["attacker_id"]), attacker_losses)
        war_system.save_losses(db, int(plan["defender_id"]), defender_losses)

        territory = db.execute("SELECT * FROM map_territories WHERE territory_code=?", (plan["territory_code"],)).fetchone()
        land_captured = capital_damage = credits_captured = 0
        capital_captured = False
        if attacker_won and not int(territory["is_capital"]):
            moved = db.execute(
                """UPDATE map_territories SET owner_user_id=?,is_capital=0,acquired_at=?
                   WHERE territory_code=? AND owner_user_id=? AND is_capital=0""",
                (plan["attacker_id"], int(time.time()), plan["territory_code"], plan["defender_id"]),
            )
            if moved.rowcount == 1:
                land_captured = 1
                db.execute("UPDATE players SET land=land+1 WHERE user_id=?", (plan["attacker_id"],))
                db.execute("UPDATE players SET land=MAX(0,land-1) WHERE user_id=?", (plan["defender_id"],))
                # Preserve every City but detach it from Land its owner lost.
                db.execute(
                    "UPDATE player_cities SET territory_code=NULL WHERE user_id=? AND territory_code=?",
                    (plan["defender_id"], plan["territory_code"]),
                )
                db.execute(
                    "UPDATE tier7_territory_defence SET owner_user_id=?,updated_at=? WHERE territory_code=?",
                    (plan["attacker_id"], int(time.time()), plan["territory_code"]),
                )
        elif attacker_won and int(territory["is_capital"]):
            defender = db.execute("SELECT capital_health,money FROM players WHERE user_id=?", (plan["defender_id"],)).fetchone()
            new_health = max(0, int(defender["capital_health"]) - war_tier.setting(db, "capital_damage"))
            capital_damage = int(defender["capital_health"]) - new_health
            db.execute("UPDATE players SET capital_health=? WHERE user_id=?", (new_health, plan["defender_id"]))
            if new_health == 0:
                capital_captured = True
                credits_captured = max(0, int(defender["money"]) * war_tier.setting(db, "capital_reward_percent") // 100)
                db.execute("UPDATE players SET money=money+? WHERE user_id=?", (credits_captured, plan["attacker_id"]))
                db.execute("UPDATE players SET money=MAX(0,money-?) WHERE user_id=?", (credits_captured, plan["defender_id"]))

        if snapshot["fronts"]["navy"] in {"attacker", "defender"}:
            blocked_id = int(plan["defender_id"] if snapshot["fronts"]["navy"] == "attacker" else plan["attacker_id"])
            db.execute(
                "UPDATE player_war_settings SET supply=MAX(0,supply-?) WHERE user_id=?",
                (war_tier.setting(db, "war_navy_supply_damage"), blocked_id),
            )
        readiness_loss = war_tier.setting(db, "war_readiness_loss_per_battle")
        db.execute(
            "UPDATE player_war_settings SET supply=MAX(0,supply-?),readiness=MAX(25,readiness-?) WHERE user_id=?",
            (setting(db, "tier7_defender_supply_cost"), readiness_loss, plan["defender_id"]),
        )
        db.execute(
            "UPDATE player_war_settings SET morale=MIN(100,morale+?) WHERE user_id=?",
            (war_tier.setting(db, "war_winner_morale_gain"), winner_id),
        )
        db.execute(
            "UPDATE player_war_settings SET morale=MAX(0,morale-?) WHERE user_id=?",
            (war_tier.setting(db, "war_loser_morale_loss"), loser_id),
        )
        db.execute("UPDATE players SET last_attack=? WHERE user_id=?", (int(time.time()), plan["attacker_id"]))
        conflict = metadata["conflict"] or {}
        outcome = (
            f"Tier 7 {mode['label']} · {'Attacker victory' if attacker_won else 'Defender victory'}"
            f" · Air {snapshot['fronts']['air']} · Navy {snapshot['fronts']['navy']}"
        )
        war_tier.log_battle(
            db, int(plan["attacker_id"]), int(plan["defender_id"]),
            int(snapshot["attacker_score"]), int(snapshot["defender_score"]), winner_id, outcome,
            war_id=conflict.get("id") if conflict.get("kind") == "alliance" else None,
            attacker_losses=attacker_losses, defender_losses=defender_losses,
            land_captured=land_captured, capital_damage=capital_damage, credits_captured=credits_captured,
        )
        battle_id = int(db.execute("SELECT last_insert_rowid() value").fetchone()["value"])
        war_tier.record_season_battle(
            db, int(plan["attacker_id"]), int(plan["defender_id"]), winner_id,
            land_captured=land_captured, capital_damage=capital_damage,
            credits_captured=credits_captured, capital_captured=capital_captured,
        )
        detail = {
            "fronts": snapshot["fronts"], "rolls": snapshot["rolls"],
            "attacker_services": snapshot["attacker_services"], "defender_services": snapshot["defender_services"],
            "attacker_losses": attacker_losses, "defender_losses": defender_losses,
            "attacker_loss_text": _loss_text(db, attacker_losses),
            "defender_loss_text": _loss_text(db, defender_losses),
            "defence": snapshot["defence"], "mode": mode,
        }
        report_cursor = db.execute(
            """INSERT INTO tier7_battle_reports
               (battle_id,plan_id,attacker_id,defender_id,territory_code,territory_name,terrain,mode,
                attacker_score,defender_score,winner_id,land_captured,capital_damage,credits_captured,details_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (battle_id, plan_id, plan["attacker_id"], plan["defender_id"], territory["territory_code"],
             territory["territory_name"], snapshot["defence"]["terrain"], mode["key"],
             snapshot["attacker_score"], snapshot["defender_score"], winner_id, land_captured,
             capital_damage, credits_captured, json.dumps(detail), int(time.time())),
        )
        db.execute(
            "UPDATE tier7_battle_plans SET status='resolved',resolved_at=?,battle_id=? WHERE id=?",
            (int(time.time()), battle_id, plan_id),
        )
        _event(db, "battle_resolved", user_id=plan["attacker_id"], plan_id=plan_id, detail={
            "report_id": int(report_cursor.lastrowid), "winner_id": winner_id,
            "territory": territory["territory_code"], "land_captured": land_captured,
        })
        db.commit()
        return True, db.execute("SELECT * FROM tier7_battle_reports WHERE id=?", (report_cursor.lastrowid,)).fetchone(), []
    except Exception as error:
        db.rollback()
        db.execute(
            "UPDATE tier7_battle_plans SET status='failed',error_text=? WHERE id=? AND status IN ('draft','resolving')",
            (str(error)[:500], plan_id),
        )
        _event(db, "battle_failed", user_id=plan["attacker_id"], plan_id=plan_id, detail={"error": str(error)[:500]})
        db.commit()
        return False, None, ["The battle was cancelled safely before a duplicate result could be created."]


def quick_plan(db, attacker_id: int, get_active_war, get_alliance_for_user):
    candidates = eligible_targets(db, attacker_id, get_active_war, get_alliance_for_user)
    ranked = []
    for candidate in candidates:
        territories = attackable_territories(db, attacker_id, int(candidate["user_id"]))
        if territories:
            ranked.append((_territory_bonus(db, territories[0])[0], war_system.total_power(db, int(candidate["user_id"])), candidate))
    if not ranked:
        return None, ["No attackable enemy is available. Open Diplomacy to declare a bordering war."]
    target = min(ranked, key=lambda value: (value[0], value[1], int(value[2]["user_id"])))[2]
    plan = create_plan(db, attacker_id, int(target["user_id"]), "standard")
    return plan, []


def reports_for_player(db, user_id: int, page: int = 0) -> tuple[list, int]:
    page_size = setting(db, "tier7_report_page_size")
    count = int(db.execute(
        "SELECT COUNT(*) amount FROM tier7_battle_reports WHERE attacker_id=? OR defender_id=?", (user_id, user_id)
    ).fetchone()["amount"])
    pages = max(1, (count + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    rows = db.execute(
        """SELECT r.*,COALESCE(NULLIF(a.display_name,''),a.nation_name,CAST(r.attacker_id AS TEXT)) attacker_name,
           COALESCE(NULLIF(d.display_name,''),d.nation_name,CAST(r.defender_id AS TEXT)) defender_name
           FROM tier7_battle_reports r LEFT JOIN players a ON a.user_id=r.attacker_id
           LEFT JOIN players d ON d.user_id=r.defender_id
           WHERE r.attacker_id=? OR r.defender_id=? ORDER BY r.id DESC LIMIT ? OFFSET ?""",
        (user_id, user_id, page_size, page * page_size),
    ).fetchall()
    return rows, pages


def fortify_territory(db, user_id: int, territory_code: str) -> tuple[bool, str]:
    """Buy exactly one fortification level for an owned Land."""
    ensure_player_state(db, user_id)
    territory = db.execute(
        """SELECT t.territory_name,d.fortification_level FROM map_territories t
           JOIN tier7_territory_defence d ON d.territory_code=t.territory_code
           WHERE t.territory_code=? AND t.owner_user_id=?""",
        (territory_code, user_id),
    ).fetchone()
    if territory is None:
        return False, "That Land is no longer owned by your Nation."
    level = int(territory["fortification_level"])
    maximum = setting(db, "tier7_fortification_max_level")
    if level >= maximum:
        return False, f"{territory['territory_name']} is already at maximum Fortification Lv {maximum}."
    cost = setting(db, "tier7_fortification_base_cost") * (level + 1)
    charged = db.execute(
        "UPDATE players SET money=money-? WHERE user_id=? AND money>=?",
        (cost, user_id, cost),
    )
    if charged.rowcount != 1:
        db.rollback()
        return False, f"Fortification Lv {level + 1} costs {cost:,} War Credits."
    db.execute(
        """UPDATE tier7_territory_defence SET fortification_level=fortification_level+1,updated_at=?
           WHERE territory_code=? AND owner_user_id=?""",
        (int(time.time()), territory_code, user_id),
    )
    _event(db, "territory_fortified", user_id=user_id, detail={
        "territory": territory_code, "new_level": level + 1, "cost": cost,
    })
    db.commit()
    return True, f"Fortified {territory['territory_name']} to Lv {level + 1} for {cost:,} War Credits."


def batch_fortify(db, user_id: int) -> tuple[bool, str]:
    """Upgrade several weakest Lands by one level in one confirmed click."""
    ensure_player_state(db, user_id)
    maximum = setting(db, "tier7_fortification_max_level")
    limit = setting(db, "tier7_batch_fortify_limit")
    rows = db.execute(
        """SELECT t.territory_code,t.territory_name,d.fortification_level
           FROM map_territories t JOIN tier7_territory_defence d ON d.territory_code=t.territory_code
           WHERE t.owner_user_id=? AND d.fortification_level<?
           ORDER BY d.fortification_level,t.is_capital DESC,t.territory_name LIMIT ?""",
        (user_id, maximum, limit),
    ).fetchall()
    player = db.execute("SELECT money FROM players WHERE user_id=?", (user_id,)).fetchone()
    budget = int(player["money"] if player else 0)
    chosen = []
    total = 0
    base = setting(db, "tier7_fortification_base_cost")
    for row in rows:
        cost = base * (int(row["fortification_level"]) + 1)
        if total + cost > budget:
            continue
        chosen.append((row, cost))
        total += cost
    if not chosen:
        return False, "No eligible Land can be fortified with your current War Credits."
    charged = db.execute(
        "UPDATE players SET money=money-? WHERE user_id=? AND money>=?", (total, user_id, total)
    )
    if charged.rowcount != 1:
        db.rollback()
        return False, "Your War Credit balance changed. Please try again."
    now = int(time.time())
    for row, _cost in chosen:
        db.execute(
            "UPDATE tier7_territory_defence SET fortification_level=fortification_level+1,updated_at=? WHERE territory_code=?",
            (now, row["territory_code"]),
        )
    _event(db, "batch_fortified", user_id=user_id, detail={
        "territories": [row["territory_code"] for row, _cost in chosen], "cost": total,
    })
    db.commit()
    return True, f"Fortified {len(chosen)} Land(s) by one level for {total:,} War Credits."


def save_defence_profile(
    db, user_id: int, *, garrison_percent: int | None = None,
    capital_priority: int | None = None, auto_reinforce: int | None = None,
) -> None:
    ensure_player_state(db, user_id)
    current = db.execute("SELECT * FROM tier7_defence_profiles WHERE user_id=?", (user_id,)).fetchone()
    values = (
        max(10, min(100, int(garrison_percent if garrison_percent is not None else current["garrison_percent"]))),
        int(bool(capital_priority if capital_priority is not None else current["capital_priority"])),
        int(bool(auto_reinforce if auto_reinforce is not None else current["auto_reinforce"])),
        int(time.time()), user_id,
    )
    db.execute(
        """UPDATE tier7_defence_profiles SET garrison_percent=?,capital_priority=?,auto_reinforce=?,updated_at=?
           WHERE user_id=?""",
        values,
    )
    _event(db, "defence_profile_saved", user_id=user_id, detail={
        "garrison_percent": values[0], "capital_priority": values[1], "auto_reinforce": values[2],
    })
    db.commit()


def health_report(db) -> dict:
    now = int(time.time())
    ensure_defence_rows(db)
    counts = {
        "draft_plans": db.execute("SELECT COUNT(*) amount FROM tier7_battle_plans WHERE status='draft'").fetchone()["amount"],
        "expired_drafts": db.execute(
            "SELECT COUNT(*) amount FROM tier7_battle_plans WHERE status='draft' AND expires_at<=?", (now,)
        ).fetchone()["amount"],
        "stuck_resolving": db.execute(
            "SELECT COUNT(*) amount FROM tier7_battle_plans WHERE status='resolving' AND expires_at<=?", (now,)
        ).fetchone()["amount"],
        "reports": db.execute("SELECT COUNT(*) amount FROM tier7_battle_reports").fetchone()["amount"],
        "missing_reports": db.execute(
            """SELECT COUNT(*) amount FROM tier7_battle_plans p
               LEFT JOIN tier7_battle_reports r ON r.plan_id=p.id
               WHERE p.status='resolved' AND r.id IS NULL"""
        ).fetchone()["amount"],
        "territories": db.execute("SELECT COUNT(*) amount FROM tier7_territory_defence").fetchone()["amount"],
        "profiles": db.execute("SELECT COUNT(*) amount FROM tier7_defence_profiles").fetchone()["amount"],
        "orphan_defence": db.execute(
            """SELECT COUNT(*) amount FROM tier7_territory_defence d
               LEFT JOIN map_territories t ON t.territory_code=d.territory_code
               WHERE t.territory_code IS NULL OR t.owner_user_id<>d.owner_user_id"""
        ).fetchone()["amount"],
        "invalid_profiles": db.execute(
            "SELECT COUNT(*) amount FROM tier7_defence_profiles WHERE garrison_percent<10 OR garrison_percent>100"
        ).fetchone()["amount"],
    }
    counts["healthy"] = not any(counts[key] for key in (
        "expired_drafts", "stuck_resolving", "missing_reports", "orphan_defence", "invalid_profiles"
    ))
    return counts


def repair(db) -> str:
    now = int(time.time())
    expired = db.execute(
        "UPDATE tier7_battle_plans SET status='expired' WHERE status='draft' AND expires_at<=?", (now,)
    ).rowcount
    failed = db.execute(
        """UPDATE tier7_battle_plans SET status='failed',error_text='Recovered an interrupted battle safely'
           WHERE status='resolving' AND expires_at<=?""",
        (now,),
    ).rowcount
    missing = db.execute(
        """UPDATE tier7_battle_plans SET status='failed',error_text='Resolved battle report is missing; battle history was preserved'
           WHERE status='resolved' AND NOT EXISTS(
             SELECT 1 FROM tier7_battle_reports r WHERE r.plan_id=tier7_battle_plans.id
           )"""
    ).rowcount
    db.execute("UPDATE tier7_defence_profiles SET garrison_percent=MIN(100,MAX(10,garrison_percent))")
    db.execute(
        "UPDATE tier7_territory_defence SET fortification_level=MIN(?,MAX(0,fortification_level))",
        (setting(db, "tier7_fortification_max_level"),),
    )
    db.execute(
        "DELETE FROM tier7_territory_defence WHERE territory_code NOT IN (SELECT territory_code FROM map_territories)"
    )
    db.commit()
    ensure_defence_rows(db)
    health = health_report(db)
    db.commit()
    return (
        f"Tier 7 repair complete: {expired} expired plan(s) closed; {failed} interrupted plan(s) recovered; "
        f"{missing} missing-report plan(s) quarantined; {health['territories']} territory defence record(s) verified."
    )


def register_commands(bot, db, create_player, get_active_war, get_alliance_for_user) -> None:
    """Replace the old War and Attack screens without adding slash commands."""
    guild_id = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
    guild = discord.Object(id=guild_id) if guild_id else None
    player_command_kwargs = {"guild": guild} if guild else {}
    builders = getattr(bot, "xbot_player_panel_builders", {})
    legacy_war_builder = builders.get("war")

    def player_row(user_id: int):
        return db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()

    def player_name(user_id: int) -> str:
        return _player_label(player_row(user_id))

    def active_plan(plan_id: int):
        return db.execute("SELECT * FROM tier7_battle_plans WHERE id=?", (plan_id,)).fetchone()

    async def deferred_edit(interaction: discord.Interaction, factory) -> None:
        await interaction.response.defer()
        await interaction.edit_original_response(view=factory())

    class OwnedView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, *, timeout=None):
            # Keep every return/navigation button alive for at least as long as
            # an Attack Plan.  The old five-minute timeout could strand a valid
            # fifteen-minute plan on slower mobile play sessions.
            if timeout is None:
                timeout = max(300, setting(db, "tier7_plan_expiry_seconds") + 60)
            super().__init__(timeout=timeout)
            self.owner_id = owner_id

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message(
                "This panel belongs to another player. Open your own panel with `/war`.", ephemeral=True
            )
            return False

    class LobbyButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Lobby", emoji="✨", style=discord.ButtonStyle.secondary)

        async def callback(self, interaction: discord.Interaction):
            builder = getattr(bot, "xbot_player_lobby_builder", None)
            if builder is None:
                await interaction.response.send_message("The Lobby is still loading. Please try again.", ephemeral=True)
                return
            await deferred_edit(interaction, lambda: builder(interaction.user.id))

    class WarCentreButton(discord.ui.Button):
        def __init__(self, label="War Centre"):
            super().__init__(label=label, emoji="⬅️", style=discord.ButtonStyle.secondary)

        async def callback(self, interaction: discord.Interaction):
            await deferred_edit(interaction, lambda: WarCentreView(interaction.user.id))

    class ExternalPanelButton(discord.ui.Button):
        def __init__(self, key: str, label: str, emoji: str, style=discord.ButtonStyle.secondary):
            super().__init__(label=label, emoji=emoji, style=style)
            self.key = key

        async def callback(self, interaction: discord.Interaction):
            builder = getattr(bot, "xbot_player_panel_builders", {}).get(self.key)
            if builder is None:
                await interaction.response.send_message(f"The {self.label} panel is still loading.", ephemeral=True)
                return
            await deferred_edit(interaction, lambda: builder(interaction.user.id))

    class LegacyWarButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Season & Tools", emoji="🏁", style=discord.ButtonStyle.secondary)

        async def callback(self, interaction: discord.Interaction):
            if legacy_war_builder is None:
                await interaction.response.send_message("War Season tools are still loading.", ephemeral=True)
                return
            await deferred_edit(interaction, lambda: legacy_war_builder(interaction.user.id))

    class WarPageButton(discord.ui.Button):
        LABELS = {
            "overview": ("Overview", "🏠", discord.ButtonStyle.secondary),
            "attack": ("Attack", "⚔️", discord.ButtonStyle.danger),
            "defence": ("Defence", "🛡️", discord.ButtonStyle.primary),
            "army": ("Army", "🪖", discord.ButtonStyle.success),
            "reports": ("Reports", "📜", discord.ButtonStyle.secondary),
        }

        def __init__(self, page: str, *, current=False):
            label, emoji, style = self.LABELS[page]
            super().__init__(label=label, emoji=emoji, style=style, disabled=current)
            self.page = page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            if self.page == "overview":
                view = WarCentreView(interaction.user.id)
            elif self.page == "attack":
                plan = create_plan(db, interaction.user.id)
                view = AttackPlannerView(interaction.user.id, int(plan["id"]))
            elif self.page == "defence":
                view = DefenceView(interaction.user.id)
            elif self.page == "army":
                view = ArmyPageView(interaction.user.id)
            else:
                view = ReportsView(interaction.user.id)
            await interaction.edit_original_response(view=view)

    def nav_row(current="overview"):
        return discord.ui.ActionRow(*(
            WarPageButton(page, current=page == current)
            for page in ("overview", "attack", "defence", "army", "reports")
        ))

    def overview_body(owner_id: int) -> str:
        ensure_player_state(db, owner_id)
        player = player_row(owner_id)
        state = db.execute(
            "SELECT supply,readiness,morale,defense_stance FROM player_war_settings WHERE user_id=?", (owner_id,)
        ).fetchone()
        powers = war_system.service_powers(db, owner_id)
        targets = eligible_targets(db, owner_id, get_active_war, get_alliance_for_user)
        season = war_tier.active_season(db)
        now = int(time.time())
        cooldown = max(0, setting(db, "tier7_attack_cooldown_seconds") - (now - int(player["last_attack"] or 0)))
        conflict_text = (
            f"🔥 **{len(targets)} active enemy Nation{'s' if len(targets) != 1 else ''}**"
            if targets else "🕊️ **No active enemy** · open Diplomacy to declare a bordering war"
        )
        cooldown_text = f"Ready <t:{now + cooldown}:R>" if cooldown else "Ready now"
        return (
            f"## ⚔️ {player['nation_name']} — Warfront 2.0\n"
            f"{conflict_text}\n"
            f"🎯 Attack: **{cooldown_text}** · 🏛️ Capital **{int(player['capital_health'])}/100 HP**\n\n"
            f"🪖 Land **{powers['land']:,}** · ✈️ Air **{powers['air']:,}** · ⚓ Navy **{powers['navy']:,}** Power\n"
            f"📦 Supply **{int(state['supply']):,}** · 🎯 Readiness **{int(state['readiness'])}%** · "
            f"🔥 Morale **{int(state['morale'])}%**\n"
            f"🛡️ Defence stance **{str(state['defense_stance']).title()}**"
            + (f"\n🏁 Season: **{season['name']}**" if season else "\n🏁 No active War Season")
        )

    class WarCentreView(OwnedView):
        def __init__(self, owner_id: int, notice: str = ""):
            super().__init__(owner_id)
            container = discord.ui.Container(accent_color=discord.Color.dark_red())
            container.add_item(discord.ui.TextDisplay(overview_body(owner_id)))
            if notice:
                container.add_item(discord.ui.TextDisplay(f"-# {notice}"))
            container.add_item(nav_row("overview"))
            container.add_item(discord.ui.ActionRow(
                ExternalPanelButton("city", "City", "🏙️"),
                ExternalPanelButton("diplomacy", "Diplomacy", "🕊️", discord.ButtonStyle.primary),
                LegacyWarButton(),
                LobbyButton(),
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(
                "### Fast play\nUse **Attack** for a planned battle or **Quick Attack** inside the planner. "
                "Every result stays in this message and can return here."
            ))
            self.add_item(container)

    class TargetNationSelect(discord.ui.Select):
        def __init__(
            self, plan_id: int, rows, selected_id: int | None,
            target_page=0, territory_page=0, force_page=0,
        ):
            if rows:
                options = []
                for row in rows:
                    nation_name = str(row["nation_name"] or _player_label(row))
                    player_label = _player_label(row)
                    description = (
                        f"Player: {player_label}" if player_label != nation_name else "Active enemy Nation"
                    )
                    options.append(discord.SelectOption(
                        label=nation_name[:100], value=str(row["user_id"]),
                        description=description[:100], emoji="🏳️",
                        default=selected_id is not None and int(row["user_id"]) == int(selected_id),
                    ))
                placeholder = "1 · Choose an active enemy Nation"
                disabled = False
            else:
                options = [discord.SelectOption(
                    label="No active enemy Nation", value="none",
                    description="Open Diplomacy and declare war first", emoji="🕊️",
                )]
                placeholder = "1 · No active enemy — open Diplomacy"
                disabled = True
            super().__init__(
                placeholder=placeholder, options=options,
                min_values=1, max_values=1, disabled=disabled,
            )
            self.plan_id = plan_id
            self.target_page, self.territory_page, self.force_page = target_page, territory_page, force_page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            target_id = int(self.values[0])
            allowed = {int(row["user_id"]) for row in eligible_targets(
                db, interaction.user.id, get_active_war, get_alliance_for_user
            )}
            if target_id not in allowed:
                await interaction.edit_original_response(
                    view=AttackPlannerView(
                        interaction.user.id, self.plan_id,
                        "That war is no longer active. Refresh Diplomacy or choose another enemy.",
                        target_page=self.target_page,
                        territory_page=self.territory_page, force_page=self.force_page,
                    )
                )
                return
            update_plan_target(db, self.plan_id, target_id)
            await interaction.edit_original_response(view=AttackPlannerView(
                interaction.user.id, self.plan_id,
                target_page=self.target_page,
                territory_page=0, force_page=self.force_page,
            ))

    class TerritorySelect(discord.ui.Select):
        def __init__(self, plan_id: int, rows, selected_code: str | None, territory_page=0, force_page=0, target_page=0):
            options = [discord.SelectOption(
                label=str(row["territory_name"])[:100],
                value=str(row["territory_code"]),
                description=("Capital siege" if row["is_capital"] else f"Land Lv {int(row['level'])}")[:100],
                default=str(row["territory_code"]) == str(selected_code),
            ) for row in rows]
            super().__init__(placeholder="2 · Choose the Land to attack", options=options, min_values=1, max_values=1)
            self.plan_id, self.territory_page, self.force_page = plan_id, territory_page, force_page
            self.target_page = target_page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            update_plan_territory(db, self.plan_id, self.values[0])
            await interaction.edit_original_response(view=AttackPlannerView(
                interaction.user.id, self.plan_id,
                target_page=self.target_page,
                territory_page=self.territory_page, force_page=self.force_page,
            ))

    class ForceSelect(discord.ui.Select):
        def __init__(self, plan_id: int, units, territory_page=0, force_page=0, target_page=0):
            selected = set(_selected_unit_ids(db, plan_id))
            options = [discord.SelectOption(
                label=f"{row['name']} ×{int(row['quantity']):,}"[:100],
                value=str(row["id"]),
                description=f"{str(row['branch']).title()} · {int(row['power'])} Power each"[:100],
                default=int(row["id"]) in selected,
            ) for row in units]
            super().__init__(
                placeholder="3 · Select one or many unit models",
                options=options, min_values=0, max_values=len(options),
            )
            self.plan_id, self.territory_page, self.force_page = plan_id, territory_page, force_page
            self.target_page = target_page
            self.visible_ids = {int(row["id"]) for row in units}

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            current = set(_selected_unit_ids(db, self.plan_id))
            selected_here = {int(value) for value in self.values}
            set_plan_units(db, self.plan_id, sorted((current - self.visible_ids) | selected_here))
            await interaction.edit_original_response(view=AttackPlannerView(
                interaction.user.id, self.plan_id,
                target_page=self.target_page,
                territory_page=self.territory_page, force_page=self.force_page,
            ))

    class ModeSelect(discord.ui.Select):
        def __init__(self, plan_id: int, current: str, territory_page=0, force_page=0, target_page=0):
            options = []
            for key, (label, _emoji) in MODE_LABELS.items():
                mode = mode_config(db, key)
                options.append(discord.SelectOption(
                    label=label, value=key,
                    description=f"{mode['force_percent']}% force · {mode['supply_cost']} Supply"[:100],
                    default=key == current,
                ))
            super().__init__(placeholder="4 · Choose an attack mode", options=options)
            self.plan_id, self.territory_page, self.force_page = plan_id, territory_page, force_page
            self.target_page = target_page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            update_plan_mode(db, self.plan_id, self.values[0])
            await interaction.edit_original_response(view=AttackPlannerView(
                interaction.user.id, self.plan_id,
                target_page=self.target_page,
                territory_page=self.territory_page, force_page=self.force_page,
            ))

    class PlannerPageButton(discord.ui.Button):
        def __init__(
            self, plan_id: int, territory_page: int, force_page: int, *,
            target_page: int = 0,
            kind: str, new_page: int, label: str, emoji: str, disabled=False,
        ):
            super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, disabled=disabled)
            self.plan_id, self.territory_page, self.force_page = plan_id, territory_page, force_page
            self.target_page = target_page
            self.kind, self.new_page = kind, new_page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            target_page = self.new_page if self.kind == "target" else self.target_page
            territory_page = self.new_page if self.kind == "territory" else self.territory_page
            force_page = self.new_page if self.kind == "force" else self.force_page
            await interaction.edit_original_response(view=AttackPlannerView(
                interaction.user.id, self.plan_id,
                target_page=target_page,
                territory_page=territory_page, force_page=force_page,
            ))

    def planner_body(plan, notice="") -> str:
        target = player_row(int(plan["defender_id"])) if plan["defender_id"] else None
        territory = db.execute(
            "SELECT territory_name FROM map_territories WHERE territory_code=?", (plan["territory_code"],)
        ).fetchone() if plan["territory_code"] else None
        mode = mode_config(db, plan["mode"])
        selected_ids = set(_selected_unit_ids(db, int(plan["id"])))
        deployment_rows = [
            row for row in war_system.units_for_player(db, int(plan["attacker_id"]), enabled_only=True)
            if int(row["id"]) in selected_ids and int(row["quantity"] or 0) > 0
        ]
        deployment_units = sum(
            min(
                int(row["quantity"]),
                max(1, int(row["quantity"]) * int(mode["force_percent"]) // 100),
            )
            for row in deployment_rows
        )
        body = (
            "## 🎯 Attack Planner\n"
            f"🏳️ Target: **{_player_label(target) if target else 'Not selected'}**\n"
            f"🗺️ Objective: **{territory['territory_name'] if territory else 'Not selected'}**\n"
            f"{mode['emoji']} Mode: **{mode['label']}** · deploys **{mode['force_percent']}%** per selected model\n"
            f"🪖 Selected: **{len(deployment_rows):,} models · {deployment_units:,} units**\n"
            f"📦 Cost on launch: **{mode['supply_cost']:,} Supply**"
        )
        preview_raw = str(plan["preview_json"] or "{}")
        try:
            preview = json.loads(preview_raw)
        except (json.JSONDecodeError, TypeError):
            preview = {}
        if not isinstance(preview, dict):
            preview = {}
        if preview.get("attacker_score") is not None:
            colour = "🟢" if int(preview["attacker_win_chance"]) >= 55 else "🟡" if int(preview["attacker_win_chance"]) >= 40 else "🔴"
            attack_range = preview.get("power_range", {}).get("attacker", [0, 0])
            defence_range = preview.get("power_range", {}).get("defender", [0, 0])
            detail = preview.get("defence", {})
            body += (
                f"\n\n### Battle preview\n{colour} Estimated win chance: **{preview['attacker_win_chance']}%**\n"
                f"⚔️ Attack range **{int(attack_range[0]):,}–{int(attack_range[1]):,}**\n"
                f"🛡️ Defence range **{int(defence_range[0]):,}–{int(defence_range[1]):,}**\n"
                f"🌍 {str(detail.get('terrain','plains')).title()} · Fortification Lv {int(detail.get('fortification_level',0))}"
            )
        if notice:
            body += f"\n\n-# {notice}"
        return body

    class PreviewButton(discord.ui.Button):
        def __init__(self, plan_id: int, territory_page=0, force_page=0, target_page=0):
            super().__init__(label="Preview", emoji="📊", style=discord.ButtonStyle.primary)
            self.plan_id, self.territory_page, self.force_page = plan_id, territory_page, force_page
            self.target_page = target_page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            _preview, errors = preview_plan(db, self.plan_id, get_active_war, get_alliance_for_user)
            notice = " · ".join(errors) if errors else "Preview updated. No resources were spent."
            await interaction.edit_original_response(view=AttackPlannerView(
                interaction.user.id, self.plan_id, notice,
                target_page=self.target_page,
                territory_page=self.territory_page, force_page=self.force_page,
            ))

    class LaunchButton(discord.ui.Button):
        def __init__(self, plan_id: int, territory_page=0, force_page=0, target_page=0):
            super().__init__(label="Launch Attack", emoji="🚀", style=discord.ButtonStyle.danger)
            self.plan_id, self.territory_page, self.force_page = plan_id, territory_page, force_page
            self.target_page = target_page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            success, report, errors = resolve_plan(db, self.plan_id, get_active_war, get_alliance_for_user)
            if success:
                view = BattleReportView(interaction.user.id, int(report["id"]))
            else:
                view = AttackPlannerView(
                    interaction.user.id, self.plan_id, " · ".join(errors),
                    target_page=self.target_page,
                    territory_page=self.territory_page, force_page=self.force_page,
                )
            await interaction.edit_original_response(view=view)

    class QuickAttackButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Quick Attack", emoji="⚡", style=discord.ButtonStyle.success)

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            plan, errors = quick_plan(db, interaction.user.id, get_active_war, get_alliance_for_user)
            if plan is not None:
                success, report, errors = resolve_plan(db, int(plan["id"]), get_active_war, get_alliance_for_user)
            else:
                success, report = False, None
            if success:
                view = BattleReportView(interaction.user.id, int(report["id"]))
            else:
                replacement = create_plan(db, interaction.user.id)
                view = AttackPlannerView(interaction.user.id, int(replacement["id"]), " · ".join(errors))
            await interaction.edit_original_response(view=view)

    class AttackPlannerView(OwnedView):
        def __init__(
            self, owner_id: int, plan_id: int, notice: str = "", *,
            target_page: int = 0, territory_page: int = 0, force_page: int = 0,
        ):
            super().__init__(owner_id)
            plan = active_plan(plan_id)
            if plan is None or plan["status"] != "draft" or int(plan["expires_at"]) <= int(time.time()):
                previous_target = int(plan["defender_id"]) if plan is not None and plan["defender_id"] else None
                previous_mode = str(plan["mode"]) if plan is not None else "standard"
                plan = create_plan(db, owner_id, previous_target, previous_mode)
                plan_id = int(plan["id"])
                notice = (notice + " " if notice else "") + "A fresh battle plan was opened safely."
                target_page = territory_page = force_page = 0
            container = discord.ui.Container(accent_color=discord.Color.red())
            container.add_item(discord.ui.TextDisplay(planner_body(plan, notice)))
            targets = eligible_targets(db, owner_id, get_active_war, get_alliance_for_user)
            target_pages = max(1, (len(targets) + 24) // 25)
            target_page = max(0, min(int(target_page), target_pages - 1))
            target_rows = targets[target_page * 25:(target_page + 1) * 25]
            container.add_item(discord.ui.ActionRow(TargetNationSelect(
                plan_id, target_rows, plan["defender_id"], target_page, territory_page, force_page,
            )))
            if target_pages > 1:
                container.add_item(discord.ui.ActionRow(
                    PlannerPageButton(
                        plan_id, territory_page, force_page, target_page=target_page, kind="target",
                        new_page=target_page - 1, label=f"Enemies {target_page + 1}/{target_pages}",
                        emoji="◀️", disabled=target_page <= 0,
                    ),
                    PlannerPageButton(
                        plan_id, territory_page, force_page, target_page=target_page, kind="target",
                        new_page=target_page + 1, label="Next Enemies", emoji="▶️",
                        disabled=target_page >= target_pages - 1,
                    ),
                ))
            territories = []
            if plan["defender_id"]:
                territories = attackable_territories(db, owner_id, int(plan["defender_id"]))
                if territories:
                    territory_pages = max(1, (len(territories) + 24) // 25)
                    territory_page = max(0, min(int(territory_page), territory_pages - 1))
                    territory_rows = territories[territory_page * 25:(territory_page + 1) * 25]
                    container.add_item(discord.ui.ActionRow(TerritorySelect(
                        plan_id, territory_rows, plan["territory_code"], territory_page, force_page, target_page,
                    )))
                    if territory_pages > 1:
                        container.add_item(discord.ui.ActionRow(
                            PlannerPageButton(
                                plan_id, territory_page, force_page, kind="territory",
                                target_page=target_page,
                                new_page=territory_page - 1, label=f"Land {territory_page + 1}/{territory_pages}",
                                emoji="◀️", disabled=territory_page <= 0,
                            ),
                            PlannerPageButton(
                                plan_id, territory_page, force_page, kind="territory",
                                target_page=target_page,
                                new_page=territory_page + 1, label="Next Land", emoji="▶️",
                                disabled=territory_page >= territory_pages - 1,
                            ),
                        ))
            positive_units = [row for row in war_system.units_for_player(db, owner_id, enabled_only=True) if int(row["quantity"] or 0) > 0]
            if positive_units:
                force_pages = max(1, (len(positive_units) + 24) // 25)
                force_page = max(0, min(int(force_page), force_pages - 1))
                force_rows = positive_units[force_page * 25:(force_page + 1) * 25]
                container.add_item(discord.ui.ActionRow(ForceSelect(
                    plan_id, force_rows, territory_page, force_page, target_page,
                )))
                if force_pages > 1:
                    container.add_item(discord.ui.ActionRow(
                        PlannerPageButton(
                            plan_id, territory_page, force_page, kind="force",
                            target_page=target_page,
                            new_page=force_page - 1, label=f"Units {force_page + 1}/{force_pages}",
                            emoji="◀️", disabled=force_page <= 0,
                        ),
                        PlannerPageButton(
                            plan_id, territory_page, force_page, kind="force",
                            target_page=target_page,
                            new_page=force_page + 1, label="Next Units", emoji="▶️",
                            disabled=force_page >= force_pages - 1,
                        ),
                    ))
            container.add_item(discord.ui.ActionRow(ModeSelect(
                plan_id, plan["mode"], territory_page, force_page, target_page,
            )))
            container.add_item(discord.ui.ActionRow(
                PreviewButton(plan_id, territory_page, force_page, target_page),
                LaunchButton(plan_id, territory_page, force_page, target_page), QuickAttackButton(), WarCentreButton()
            ))
            self.add_item(container)

    class DefenceTerritorySelect(discord.ui.Select):
        def __init__(self, rows, selected_code, page=0):
            options = [discord.SelectOption(
                label=str(row["territory_name"])[:100], value=str(row["territory_code"]),
                description=f"{str(row['terrain']).title()} · Fortification Lv {int(row['fortification_level'])}"[:100],
                default=str(row["territory_code"]) == str(selected_code),
            ) for row in rows]
            super().__init__(placeholder="Select a Land to defend", options=options)
            self.page = page

        async def callback(self, interaction: discord.Interaction):
            await deferred_edit(interaction, lambda: DefenceView(
                interaction.user.id, self.values[0], page=self.page,
            ))

    class FortifyButton(discord.ui.Button):
        def __init__(self, territory_code: str, page=0):
            super().__init__(label="Fortify Selected", emoji="🏰", style=discord.ButtonStyle.primary)
            self.territory_code, self.page = territory_code, page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            _success, message = fortify_territory(db, interaction.user.id, self.territory_code)
            await interaction.edit_original_response(view=DefenceView(
                interaction.user.id, self.territory_code, message, page=self.page,
            ))

    class BatchFortifyButton(discord.ui.Button):
        def __init__(self, selected_code=None, page=0):
            super().__init__(label="Fortify Weak Lands", emoji="🏗️", style=discord.ButtonStyle.success)
            self.selected_code, self.page = selected_code, page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            _success, message = batch_fortify(db, interaction.user.id)
            await interaction.edit_original_response(view=DefenceView(
                interaction.user.id, self.selected_code, notice=message, page=self.page,
            ))

    class GarrisonButton(discord.ui.Button):
        def __init__(self, current: int, selected_code=None, page=0):
            super().__init__(label=f"Garrison {current}%", emoji="🪖", style=discord.ButtonStyle.secondary)
            self.current, self.selected_code, self.page = current, selected_code, page

        async def callback(self, interaction: discord.Interaction):
            levels = (50, 75, 100)
            next_value = levels[(levels.index(self.current) + 1) % len(levels)] if self.current in levels else 100
            await interaction.response.defer()
            save_defence_profile(db, interaction.user.id, garrison_percent=next_value)
            await interaction.edit_original_response(view=DefenceView(
                interaction.user.id, self.selected_code,
                notice=f"Garrison commitment changed to {next_value}%.", page=self.page,
            ))

    class StanceButton(discord.ui.Button):
        def __init__(self, current: str, selected_code=None, page=0):
            super().__init__(label=f"Stance: {current.title()}", emoji="🛡️", style=discord.ButtonStyle.secondary)
            self.current, self.selected_code, self.page = current, selected_code, page

        async def callback(self, interaction: discord.Interaction):
            stances = ("balanced", "fortified", "aggressive")
            next_value = stances[(stances.index(self.current) + 1) % len(stances)] if self.current in stances else "balanced"
            await interaction.response.defer()
            db.execute("UPDATE player_war_settings SET defense_stance=? WHERE user_id=?", (next_value, interaction.user.id))
            db.commit()
            await interaction.edit_original_response(view=DefenceView(
                interaction.user.id, self.selected_code,
                notice=f"Defence stance changed to {next_value.title()}.", page=self.page,
            ))

    class ProfileToggleButton(discord.ui.Button):
        def __init__(self, field: str, enabled: bool, selected_code=None, page=0):
            label = "Auto Reinforce" if field == "auto_reinforce" else "Capital Priority"
            emoji = "🔄" if field == "auto_reinforce" else "🏛️"
            super().__init__(
                label=f"{label}: {'On' if enabled else 'Off'}", emoji=emoji,
                style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
            )
            self.field, self.enabled, self.selected_code, self.page = field, enabled, selected_code, page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            kwargs = {self.field: int(not self.enabled)}
            save_defence_profile(db, interaction.user.id, **kwargs)
            await interaction.edit_original_response(view=DefenceView(
                interaction.user.id, self.selected_code,
                notice=f"{self.label.split(':')[0]} {'enabled' if not self.enabled else 'disabled'}.",
                page=self.page,
            ))

    class DefencePageButton(discord.ui.Button):
        def __init__(self, page: int, label: str, emoji: str, disabled=False):
            super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, disabled=disabled)
            self.page = page

        async def callback(self, interaction: discord.Interaction):
            await deferred_edit(interaction, lambda: DefenceView(interaction.user.id, page=self.page))

    def defence_body(owner_id: int, rows, selected_code, notice="") -> str:
        profile = defence_profile(db, owner_id)
        state = db.execute(
            "SELECT defense_stance,supply,readiness FROM player_war_settings WHERE user_id=?", (owner_id,)
        ).fetchone()
        selected = next((row for row in rows if row["territory_code"] == selected_code), rows[0] if rows else None)
        enemies = eligible_targets(db, owner_id, get_active_war, get_alliance_for_user)
        enemy_power = max((war_system.branch_power(db, int(row["user_id"]), "land") for row in enemies), default=0)
        own_power = max(1, war_system.branch_power(db, owner_id, "land"))
        risk = "Low" if not enemies or own_power >= enemy_power * 1.25 else "Medium" if own_power >= enemy_power * .75 else "High"
        weakest = min(rows, key=lambda row: int(row["fortification_level"])) if rows else None
        recommendation = (
            f"Fortify **{weakest['territory_name']}** next."
            if weakest and int(weakest["fortification_level"]) < setting(db, "tier7_fortification_max_level")
            else "Your mapped Lands have reached the current fortification limit."
        )
        body = (
            "## 🛡️ National Defence Centre\n"
            f"Threat level: **{risk}** · {len(enemies)} active enemy Nation{'s' if len(enemies) != 1 else ''}\n"
            f"🪖 Garrison **{int(profile['garrison_percent'])}%** · 🛡️ {str(state['defense_stance']).title()} stance\n"
            f"📦 Supply **{int(state['supply']):,}** · 🎯 Readiness **{int(state['readiness'])}%**\n"
            f"🔄 Auto Reinforce **{'On' if profile['auto_reinforce'] else 'Off'}** · "
            f"🏛️ Capital Priority **{'On' if profile['capital_priority'] else 'Off'}**\n\n"
        )
        if selected:
            body += (
                f"### Selected Land\n🗺️ **{selected['territory_name']}**\n"
                f"🌍 {str(selected['terrain']).title()} · 🏰 Fortification Lv {int(selected['fortification_level'])}"
            )
        body += f"\n\n💡 {recommendation}"
        if notice:
            body += f"\n-# {notice}"
        return body

    class DefenceView(OwnedView):
        def __init__(
            self, owner_id: int, selected_code: str | None = None, notice: str = "", *, page: int = 0,
        ):
            super().__init__(owner_id)
            ensure_player_state(db, owner_id)
            rows = db.execute(
                """SELECT t.*,d.terrain,d.fortification_level FROM map_territories t
                   JOIN tier7_territory_defence d ON d.territory_code=t.territory_code
                   WHERE t.owner_user_id=? ORDER BY d.fortification_level,t.is_capital DESC,t.territory_name""",
                (owner_id,),
            ).fetchall()
            pages = max(1, (len(rows) + 24) // 25)
            page = max(0, min(int(page), pages - 1))
            page_rows = rows[page * 25:(page + 1) * 25]
            if selected_code not in {row["territory_code"] for row in page_rows}:
                selected_code = page_rows[0]["territory_code"] if page_rows else None
            profile = defence_profile(db, owner_id)
            state = db.execute("SELECT defense_stance FROM player_war_settings WHERE user_id=?", (owner_id,)).fetchone()
            container = discord.ui.Container(accent_color=discord.Color.blue())
            container.add_item(discord.ui.TextDisplay(defence_body(owner_id, rows, selected_code, notice)))
            if page_rows:
                container.add_item(discord.ui.ActionRow(DefenceTerritorySelect(page_rows, selected_code, page)))
                if pages > 1:
                    container.add_item(discord.ui.ActionRow(
                        DefencePageButton(page - 1, f"Land {page + 1}/{pages}", "◀️", page <= 0),
                        DefencePageButton(page + 1, "Next Land", "▶️", page >= pages - 1),
                    ))
                container.add_item(discord.ui.ActionRow(
                    FortifyButton(selected_code, page), BatchFortifyButton(selected_code, page),
                    GarrisonButton(int(profile["garrison_percent"]), selected_code, page),
                    StanceButton(str(state["defense_stance"]), selected_code, page),
                ))
            container.add_item(discord.ui.ActionRow(
                ProfileToggleButton("auto_reinforce", bool(profile["auto_reinforce"]), selected_code, page),
                ProfileToggleButton("capital_priority", bool(profile["capital_priority"]), selected_code, page),
                WarCentreButton(), LobbyButton(),
            ))
            self.add_item(container)

    class RecruitButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Recruit", emoji="🪖", style=discord.ButtonStyle.success)

        async def callback(self, interaction: discord.Interaction):
            builder = getattr(bot, "xbot_army_recruit_builder", None)
            if builder is None:
                await interaction.response.send_message("The Recruit Centre is still loading.", ephemeral=True)
                return
            await deferred_edit(interaction, lambda: builder(interaction.user.id))

    class SupplyModal(discord.ui.Modal, title="Buy Military Supply"):
        def __init__(self, owner_id: int, source_message):
            super().__init__(timeout=300)
            self.owner_id, self.source_message = owner_id, source_message
            self.amount = discord.ui.TextInput(label="Supply amount", placeholder="Example: 100", min_length=1, max_length=7)
            self.add_item(self.amount)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                requested = int(str(self.amount.value).strip())
            except ValueError:
                await interaction.response.send_message("Enter a whole number.", ephemeral=True)
                return
            if requested <= 0:
                await interaction.response.send_message("Supply amount must be at least 1.", ephemeral=True)
                return
            # Acknowledge the modal before SQLite work.  On a phone the
            # Dashboard or backup process may briefly hold the database lock;
            # deferring first prevents Discord Unknown Interaction (10062).
            await interaction.response.defer()
            ensure_player_state(db, self.owner_id)
            state = db.execute("SELECT supply FROM player_war_settings WHERE user_id=?", (self.owner_id,)).fetchone()
            player = player_row(self.owner_id)
            amount = min(requested, max(0, war_tier.setting(db, "war_max_supply") - int(state["supply"])))
            cost = amount * war_tier.setting(db, "war_supply_cost")
            if amount <= 0 or int(player["money"]) < cost:
                await self.source_message.edit(view=ArmyPageView(
                    self.owner_id, "Supply is full or you do not have enough War Credits."
                ))
                return
            db.execute("UPDATE players SET money=money-? WHERE user_id=?", (cost, self.owner_id))
            db.execute("UPDATE player_war_settings SET supply=supply+? WHERE user_id=?", (amount, self.owner_id))
            db.commit()
            await self.source_message.edit(view=ArmyPageView(
                self.owner_id, f"Bought {amount:,} Supply for {cost:,} War Credits."
            ))

    class BuySupplyButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Buy Supply", emoji="📦", style=discord.ButtonStyle.primary)

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.send_modal(SupplyModal(interaction.user.id, interaction.message))

    class ArmyActionButton(discord.ui.Button):
        def __init__(self, action: str):
            label, emoji = (("Prepare", "🎯") if action == "prepare" else ("Rally", "🔥"))
            super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary)
            self.action = action

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            ensure_player_state(db, interaction.user.id)
            state = db.execute(
                "SELECT supply,readiness,morale FROM player_war_settings WHERE user_id=?", (interaction.user.id,)
            ).fetchone()
            if self.action == "prepare":
                cost = war_tier.setting(db, "war_prepare_supply_cost")
                if int(state["readiness"]) >= 100:
                    notice = "Readiness is already 100%."
                elif int(state["supply"]) < cost:
                    notice = f"Prepare needs {cost:,} Supply."
                else:
                    gain = min(war_tier.setting(db, "war_readiness_per_prepare"), 100 - int(state["readiness"]))
                    db.execute(
                        "UPDATE player_war_settings SET supply=supply-?,readiness=readiness+? WHERE user_id=?",
                        (cost, gain, interaction.user.id),
                    )
                    db.commit()
                    notice = f"Readiness +{gain}% · Supply -{cost:,}."
            else:
                cost = war_tier.setting(db, "war_rally_supply_cost")
                if int(state["morale"]) >= 100:
                    notice = "Morale is already 100%."
                elif int(state["supply"]) < cost:
                    notice = f"Rally needs {cost:,} Supply."
                else:
                    gain = min(war_tier.setting(db, "war_rally_morale_gain"), 100 - int(state["morale"]))
                    db.execute(
                        "UPDATE player_war_settings SET supply=supply-?,morale=morale+? WHERE user_id=?",
                        (cost, gain, interaction.user.id),
                    )
                    db.commit()
                    notice = f"Morale +{gain}% · Supply -{cost:,}."
            await interaction.edit_original_response(view=ArmyPageView(interaction.user.id, notice))

    class ArmyPageView(OwnedView):
        def __init__(self, owner_id: int, notice: str = ""):
            super().__init__(owner_id)
            ensure_player_state(db, owner_id)
            state = db.execute(
                "SELECT supply,readiness,morale FROM player_war_settings WHERE user_id=?", (owner_id,)
            ).fetchone()
            powers = war_system.service_powers(db, owner_id)
            counts = {row["branch"]: int(row["amount"]) for row in db.execute(
                """SELECT u.branch,COALESCE(SUM(p.quantity),0) amount FROM player_war_units p
                   JOIN war_unit_types u ON u.id=p.unit_type_id WHERE p.user_id=? AND u.enabled=1 GROUP BY u.branch""",
                (owner_id,),
            ).fetchall()}
            body = (
                "## 🪖 Armed Forces & Logistics\n"
                f"🪖 Land **{counts.get('land',0):,} units · {powers['land']:,} Power**\n"
                f"✈️ Air **{counts.get('air',0):,} units · {powers['air']:,} Power**\n"
                f"⚓ Navy **{counts.get('navy',0):,} units · {powers['navy']:,} Power**\n\n"
                f"📦 Supply **{int(state['supply']):,}/{war_tier.setting(db,'war_max_supply'):,}** · "
                f"🎯 Readiness **{int(state['readiness'])}%** · 🔥 Morale **{int(state['morale'])}%**"
            )
            if notice:
                body += f"\n-# {notice}"
            container = discord.ui.Container(accent_color=discord.Color.green())
            container.add_item(discord.ui.TextDisplay(body))
            container.add_item(discord.ui.ActionRow(
                RecruitButton(), BuySupplyButton(), ArmyActionButton("prepare"), ArmyActionButton("rally"), WarCentreButton()
            ))
            container.add_item(discord.ui.ActionRow(
                ExternalPanelButton("city", "City", "🏙️"),
                ExternalPanelButton("diplomacy", "Diplomacy", "🕊️"),
                LobbyButton(),
            ))
            self.add_item(container)

    def report_body(report) -> str:
        try:
            detail = json.loads(report["details_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        attacker = player_name(int(report["attacker_id"]))
        defender = player_name(int(report["defender_id"]))
        winner = player_name(int(report["winner_id"]))
        fronts = detail.get("fronts", {})
        reward = (
            f"🗺️ Land captured: **{report['territory_name']}**" if report["land_captured"]
            else f"🏛️ Capital damage: **{int(report['capital_damage'])} HP**" if report["capital_damage"]
            else "🛡️ No territory changed hands."
        )
        if int(report["credits_captured"]):
            reward += f"\n💰 Captured **{int(report['credits_captured']):,} War Credits**"
        return (
            f"## 📜 Battle Report #{int(report['battle_id'])}\n"
            f"⚔️ **{attacker}** vs **{defender}**\n"
            f"🗺️ {report['territory_name']} · {str(report['terrain']).title()} · {str(report['mode']).title()}\n\n"
            f"### Three fronts\n✈️ Air: **{str(fronts.get('air','none')).title()}** · "
            f"⚓ Navy: **{str(fronts.get('navy','none')).title()}**\n"
            f"🪖 Final Land Power: **{int(report['attacker_score']):,} vs {int(report['defender_score']):,}**\n"
            f"🏆 Winner: **{winner}**\n\n{reward}\n\n"
            f"Attacker losses: {detail.get('attacker_loss_text','No units lost')}\n"
            f"Defender losses: {detail.get('defender_loss_text','No units lost')}"
        )

    class AttackAgainButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Attack Again", emoji="⚔️", style=discord.ButtonStyle.danger)

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            plan = create_plan(db, interaction.user.id)
            await interaction.edit_original_response(view=AttackPlannerView(interaction.user.id, int(plan["id"])))

    class BattleReportView(OwnedView):
        def __init__(self, owner_id: int, report_id: int):
            super().__init__(owner_id)
            report = db.execute("SELECT * FROM tier7_battle_reports WHERE id=?", (report_id,)).fetchone()
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay(
                report_body(report) if report else "## 📜 Battle Report\nThis report is no longer available."
            ))
            container.add_item(discord.ui.ActionRow(
                AttackAgainButton(), ReportListButton(), WarCentreButton(), LobbyButton()
            ))
            self.add_item(container)

    class ReportSelect(discord.ui.Select):
        def __init__(self, rows):
            options = [discord.SelectOption(
                label=f"#{int(row['battle_id'])} · {row['territory_name']}"[:100], value=str(row["id"]),
                description=f"{row['attacker_name']} vs {row['defender_name']}"[:100],
            ) for row in rows]
            super().__init__(placeholder="Open a full battle report", options=options)

        async def callback(self, interaction: discord.Interaction):
            await deferred_edit(interaction, lambda: BattleReportView(interaction.user.id, int(self.values[0])))

    class ReportPageButton(discord.ui.Button):
        def __init__(self, page: int, label: str, emoji: str, disabled=False):
            super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, disabled=disabled)
            self.page = page

        async def callback(self, interaction: discord.Interaction):
            await deferred_edit(interaction, lambda: ReportsView(interaction.user.id, self.page))

    class ReportListButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="All Reports", emoji="📚", style=discord.ButtonStyle.primary)

        async def callback(self, interaction: discord.Interaction):
            await deferred_edit(interaction, lambda: ReportsView(interaction.user.id))

    class ReportsView(OwnedView):
        def __init__(self, owner_id: int, page: int = 0):
            super().__init__(owner_id)
            rows, pages = reports_for_player(db, owner_id, page)
            page = max(0, min(page, pages - 1))
            lines = []
            for row in rows:
                won = int(row["winner_id"]) == owner_id
                opponent = row["defender_name"] if int(row["attacker_id"]) == owner_id else row["attacker_name"]
                lines.append(
                    f"{'🏆' if won else '🛡️'} **#{int(row['battle_id'])} {'Victory' if won else 'Defeat'}** · "
                    f"vs {opponent}\n> {row['territory_name']} · <t:{int(row['created_at'])}:R>"
                )
            body = "## 📚 War Reports\n" + ("\n".join(lines) if lines else "No Tier 7 battles yet. Open Attack to begin.")
            body += f"\n\n-# Page {page + 1}/{pages} · reports are permanent and can be opened below."
            container = discord.ui.Container(accent_color=discord.Color.dark_gold())
            container.add_item(discord.ui.TextDisplay(body))
            if rows:
                container.add_item(discord.ui.ActionRow(ReportSelect(rows)))
            container.add_item(discord.ui.ActionRow(
                ReportPageButton(page - 1, "Previous", "◀️", page <= 0),
                ReportPageButton(page + 1, "Next", "▶️", page >= pages - 1),
                WarCentreButton(), LobbyButton(),
            ))
            self.add_item(container)

    bot.xbot_player_panel_builders = builders
    bot.xbot_player_panel_builders["war"] = lambda owner_id: WarCentreView(owner_id)
    bot.xbot_tier7_attack_builder = lambda owner_id, target_id=None: AttackPlannerView(
        owner_id, int(create_plan(db, owner_id, target_id)["id"])
    )
    bot.xbot_tier7_defence_builder = lambda owner_id: DefenceView(owner_id)
    bot.xbot_tier7_army_builder = lambda owner_id: ArmyPageView(owner_id)
    bot.xbot_tier7_reports_builder = lambda owner_id: ReportsView(owner_id)
    bot.xbot_tier7_health = lambda: health_report(db)
    bot.xbot_tier7_repair = lambda: repair(db)

    # /war may be a server-only command while the old /attack is global.
    # war_tier may have registered /war globally on an older installation.
    # Remove both scopes so setup_hook cannot copy a legacy global command
    # back over this immediate guild version during synchronisation.
    bot.tree.remove_command("war")
    if guild is not None:
        bot.tree.remove_command("war", guild=guild)
    bot.tree.remove_command("attack")

    @bot.tree.command(name="war", description="Open the X BOT Warfront 2.0 Centre", **player_command_kwargs)
    async def war_command(interaction: discord.Interaction):
        await interaction.response.defer()
        create_player(interaction.user)
        ensure_player_state(db, interaction.user.id)
        await interaction.edit_original_response(view=WarCentreView(interaction.user.id))

    @bot.tree.command(name="attack", description="Open the Attack Planner or choose a target", **player_command_kwargs)
    @app_commands.describe(target="Optional enemy Nation to preselect")
    async def attack_command(interaction: discord.Interaction, target: Optional[discord.Member] = None):
        if target is not None and (target.bot or target.id == interaction.user.id):
            await interaction.response.send_message("Choose another player's Nation.", ephemeral=True)
            return
        await interaction.response.defer()
        create_player(interaction.user)
        notice = ""
        target_id = None
        if target is not None:
            allowed = {int(row["user_id"]) for row in eligible_targets(
                db, interaction.user.id, get_active_war, get_alliance_for_user
            )}
            if player_row(target.id) is None:
                notice = "That player has not created a Nation yet."
            elif target.id not in allowed:
                notice = "This target is not in an active war with you. Open Diplomacy first."
            else:
                target_id = target.id
        plan = create_plan(db, interaction.user.id, target_id)
        await interaction.edit_original_response(view=AttackPlannerView(interaction.user.id, int(plan["id"]), notice))
