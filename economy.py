"""X BOT Beta 1.4B economy, community, and configurable war module."""
import random
import os
import sqlite3
import time
import tier8
import unicodedata
from pathlib import Path
from typing import Optional

import discord
import xbot_ui
import war_system
from discord import app_commands
from discord.ext import tasks


DEFAULT_SETTINGS = {
    "starting_xc": "250",
    "work_cooldown": "60",  # beta testing: one minute
    "collect_cooldown": "60",
    "land_income_per_land": "100",
    "work_crystal_chance": "2",
    "mine_cooldown": "60",
    "mine_crystal_chance": "1",
    "daily_reward": "50",
    "daily_cooldown": "86400",
    "transfer_min": "1",
    "transfer_max": "10000",
    "transfer_tax_percent": "0",
    "exchange_xc_to_war_percent": "100000",
    "exchange_war_to_xc_percent": "1",
    "economy_shop_enabled": "1",
    "job_drop_base_chance": "6",
    "job_drop_tenure_multiplier": "1",
    "job_drop_max_chance": "25",
    "job_keep_recommendation": "1",
    "job_keep_apology": "1",
    "job_inactivity_enabled": "1",
    "job_warning_days": "7",
    "job_fire_days": "14",
    "job_log_channel_id": "0",
    "army_recruit_default_category_id": "0",
    "army_recruit_default_sort": "manual",
    "mining_energy_enabled": "1", "mining_max_energy": "100",
    "mining_energy_regen_amount": "5", "mining_energy_regen_seconds": "1800",
    "mining_starter_pickaxe_enabled": "1", "mining_collection_xc_reward": "500",
    "mining_collection_xcrystal_reward": "5",
    "version": "Beta 1.4B",
}


def safe_discord_component_emoji(value: str | None, fallback: str = "⚔️") -> str:
    """Return one Discord-safe unicode emoji for a button/select option.

    Display emoji may contain combinations such as ``🇬🇧🛡️``. Discord accepts
    those in text, but a component emoji field only accepts one emoji and will
    reject the complete interaction response otherwise.
    """
    value = (value or "").strip()
    if not value:
        return fallback
    codepoints = [ord(char) for char in value]
    regional = [cp for cp in codepoints if 0x1F1E6 <= cp <= 0x1F1FF]
    ignored = {0x200D, 0x20E3, 0xFE0E, 0xFE0F}
    bases = [
        cp for cp in codepoints
        if cp not in ignored
        and not 0x1F3FB <= cp <= 0x1F3FF
        and unicodedata.category(chr(cp)) not in {"Mn", "Me"}
    ]
    if regional:
        return value if len(regional) == 2 and len(bases) == 2 else fallback
    return value if len(bases) == 1 else fallback


def text_setting(db: sqlite3.Connection, key: str, fallback: str) -> str:
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else fallback


def initialise(db: sqlite3.Connection) -> None:
    """Create the configurable economy data without resetting existing players."""
    version_row = db.execute(
        "SELECT value FROM economy_settings WHERE key='version'"
    ).fetchone() if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='economy_settings'"
    ).fetchone() else None
    old_version = version_row["value"] if version_row else "pre-beta"
    if old_version != "Beta 1.4B":
        database_file = Path(db.execute("PRAGMA database_list").fetchone()[2])
        if database_file.exists():
            backup_dir = database_file.parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            backup_file = backup_dir / "xwar-before-beta-1.4B.db"
            if not backup_file.exists():
                backup_db = sqlite3.connect(backup_file)
                db.backup(backup_db)
                backup_db.close()
    columns = {row["name"] for row in db.execute("PRAGMA table_info(players)")}
    for name, definition in {
        "display_name": "TEXT NOT NULL DEFAULT ''",
        "xc": "INTEGER NOT NULL DEFAULT 250",
        "xcrystals": "INTEGER NOT NULL DEFAULT 0",
        "job_id": "INTEGER",
        "last_work": "INTEGER NOT NULL DEFAULT 0",
        "last_daily": "INTEGER NOT NULL DEFAULT 0",
        "work_day": "INTEGER NOT NULL DEFAULT 0",
        "work_shifts_today": "INTEGER NOT NULL DEFAULT 0",
        "equipped_pickaxe_id": "INTEGER",
        "job_started_at": "INTEGER NOT NULL DEFAULT 0",
        "job_warning_sent": "INTEGER NOT NULL DEFAULT 0",
        "mining_level": "INTEGER NOT NULL DEFAULT 1", "mining_exp": "INTEGER NOT NULL DEFAULT 0",
        "mining_energy": "INTEGER NOT NULL DEFAULT 100", "mining_energy_updated": "INTEGER NOT NULL DEFAULT 0",
        "mining_area_id": "INTEGER", "total_mines": "INTEGER NOT NULL DEFAULT 0",
        "rare_mining_finds": "INTEGER NOT NULL DEFAULT 0", "last_mine_at": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in columns:
            db.execute(f"ALTER TABLE players ADD COLUMN {name} {definition}")

    db.execute("""
        CREATE TABLE IF NOT EXISTS economy_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            description TEXT NOT NULL,
            min_salary INTEGER NOT NULL,
            max_salary INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)
    job_columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
    for name, definition in {
        "emoji": "TEXT NOT NULL DEFAULT '💼'",
        "shifts_per_day": "INTEGER NOT NULL DEFAULT 1",
        "requirement_item_id": "INTEGER",
        "requirement_quantity": "INTEGER NOT NULL DEFAULT 0",
        "required_role_id": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in job_columns:
            db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
    db.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            description TEXT NOT NULL,
            emoji TEXT NOT NULL DEFAULT '📦',
            category TEXT NOT NULL DEFAULT 'misc',
            price INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'xc',
            sell_price INTEGER NOT NULL DEFAULT 0,
            stock INTEGER NOT NULL DEFAULT -1,
            effect TEXT NOT NULL DEFAULT 'none',
            effect_value INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)
    item_columns = {row["name"] for row in db.execute("PRAGMA table_info(items)")}
    for name, definition in {
        "aliases": "TEXT NOT NULL DEFAULT ''",
        "sellable": "INTEGER NOT NULL DEFAULT 1",
        "tradeable": "INTEGER NOT NULL DEFAULT 0",
        "category_id": "INTEGER",
        "shop_visible": "INTEGER NOT NULL DEFAULT 1",
        "mine_weight": "INTEGER NOT NULL DEFAULT 0",
        "mine_min_yield": "INTEGER NOT NULL DEFAULT 1",
        "mine_max_yield": "INTEGER NOT NULL DEFAULT 1",
        "pickaxe_power": "INTEGER NOT NULL DEFAULT 1", "pickaxe_luck": "INTEGER NOT NULL DEFAULT 0",
        "pickaxe_yield_bonus": "INTEGER NOT NULL DEFAULT 0", "pickaxe_cooldown_reduction": "INTEGER NOT NULL DEFAULT 0",
        "pickaxe_required_level": "INTEGER NOT NULL DEFAULT 1",
    }.items():
        if name not in item_columns:
            db.execute(f"ALTER TABLE items ADD COLUMN {name} {definition}")
    db.execute("""
        CREATE TABLE IF NOT EXISTS item_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL UNIQUE COLLATE NOCASE,
            emoji TEXT NOT NULL DEFAULT '📦',
            position INTEGER NOT NULL DEFAULT 99,
            enabled INTEGER NOT NULL DEFAULT 1,
            system_category INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS inventories (
            user_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, item_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS economy_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS job_incentive_pool (
            item_id INTEGER PRIMARY KEY,
            amount INTEGER NOT NULL DEFAULT 1,
            weight INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(item_id) REFERENCES items(id)
        )
    """)
    db.execute("""CREATE TABLE IF NOT EXISTS mining_areas(
        id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        emoji TEXT NOT NULL DEFAULT '⛏️',description TEXT NOT NULL DEFAULT '',required_level INTEGER NOT NULL DEFAULT 1,
        cooldown_seconds INTEGER NOT NULL DEFAULT 60,energy_cost INTEGER NOT NULL DEFAULT 10,
        exp_min INTEGER NOT NULL DEFAULT 10,exp_max INTEGER NOT NULL DEFAULT 20,
        crystal_chance INTEGER NOT NULL DEFAULT 0,position INTEGER NOT NULL DEFAULT 10,enabled INTEGER NOT NULL DEFAULT 1
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS mining_area_drops(
        area_id INTEGER NOT NULL,item_id INTEGER NOT NULL,weight INTEGER NOT NULL DEFAULT 1,
        min_yield INTEGER NOT NULL DEFAULT 1,max_yield INTEGER NOT NULL DEFAULT 1,rare INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(area_id,item_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS mining_collection_rewards(
        user_id INTEGER PRIMARY KEY,claimed_at INTEGER NOT NULL
    )""")
    for key, value in DEFAULT_SETTINGS.items():
        db.execute(
            "INSERT OR IGNORE INTO economy_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    db.execute(
        "INSERT INTO economy_settings(key,value) VALUES('version','Beta 1.4B') ON CONFLICT(key) DO UPDATE SET value='Beta 1.4B'"
    )
    db.execute("UPDATE economy_settings SET value='100000' WHERE key='exchange_xc_to_war_percent'")
    db.execute("UPDATE economy_settings SET value='1' WHERE key='exchange_war_to_xc_percent' AND value='100'")
    categories = [
        ("Work & Career", "💼", 1, 0), ("Consumable", "🎟️", 2, 0),
        ("Utility & Protection", "🧰", 3, 0), ("Materials & Resources", "⛏️", 4, 0),
        ("Gear", "⚒️", 5, 0), ("War", "⚔️", 6, 0),
        ("Collectables", "🏆", 7, 0), ("Misc", "📦", 99, 1),
    ]
    db.executemany("INSERT OR IGNORE INTO item_categories(label,emoji,position,system_category) VALUES(?,?,?,?)", categories)
    # Match old item labels to the new editable category library.
    category_map = {"utility": "Utility & Protection", "war": "War", "collectible": "Collectables", "gear": "Gear", "material": "Materials & Resources", "job": "Work & Career", "misc": "Misc"}
    for old_label, new_label in category_map.items():
        db.execute("UPDATE items SET category_id=(SELECT id FROM item_categories WHERE label=?) WHERE category=? AND category_id IS NULL", (new_label, old_label))
    if db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"] == 0:
        db.executemany(
            "INSERT INTO jobs (name, description, min_salary, max_salary) VALUES (?, ?, ?, ?)",
            [
                ("Recruit", "Entry-level work for the X BOT economy.", 80, 120),
                ("Miner", "Processes supplies and earns steady XC.", 120, 180),
                ("Trader", "Negotiates deals for a higher XC salary.", 180, 260),
            ],
        )
    if db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"] == 0:
        db.executemany(
            """INSERT INTO items(name,description,emoji,category,price,currency,sell_price,stock,effect,effect_value) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                ("XC Voucher", "Redeem for 50 XC.", "🎟️", "utility", 60, "xc", 20, -1, "xc_reward", 50),
                ("Capital Repair Kit", "Repairs 25 Capital HP.", "🧰", "war", 250, "xc", 75, -1, "capital_repair", 25),
                ("War Supply Crate", "Redeem for 200 War Credits.", "📦", "war", 350, "xc", 100, -1, "war_credits", 200),
                ("X BOT Trophy", "A collectible from the Beta era.", "🏆", "collectible", 2, "xcrystals", 0, 25, "collectible", 0),
            ],
        )
    # Default catalog. Every record is editable in the Dashboard. Sapphire is deliberately not seeded or changed.
    catalog = [
        # name, description, emoji, category, price, effect, power/weight, shop visible, sell price, mine min/max
        ("Basic Pickaxe", "A reliable starter pickaxe for mining.", "⛏️", "Gear", 1, "mine_tool", 0, 1, 0, 1, 1),
        ("Iron Pickaxe", "A stronger pickaxe with an extra mining bonus.", "⛏️", "Gear", 600, "mine_tool", 15, 1, 180, 1, 1),
        ("Gold Pickaxe", "A high-quality pickaxe with an improved mining bonus.", "⛏️", "Gear", 5000, "mine_tool", 30, 1, 1500, 1, 1),
        ("Diamond Pickaxe", "A powerful pickaxe for serious miners.", "⛏️", "Gear", 20000, "mine_tool", 50, 1, 6000, 1, 1),
        ("X Pickaxe", "An elite pickaxe forged for experienced miners.", "⛏️", "Gear", 100000, "mine_tool", 75, 1, 30000, 1, 1),
        ("GuardVest", "Future protection against robbery attempts.", "🛡️", "Utility & Protection", 500, "none", 0, 1, 150, 1, 1),
        ("Reinforced Gear", "Future protection while mining.", "🧰", "Utility & Protection", 700, "none", 0, 1, 200, 1, 1),
        ("Hunter Gear", "Future protection while hunting.", "🔫", "Utility & Protection", 700, "none", 0, 1, 200, 1, 1),
        ("Aqua Gear", "Future protection while fishing.", "🦺", "Utility & Protection", 700, "none", 0, 1, 200, 1, 1),
        ("Lucky Charm", "A lucky charm reserved for future games.", "🍀", "Utility & Protection", 350, "none", 0, 1, 100, 1, 1),
        ("Jail Pass", "Reserved for the future Jail system.", "🔓", "Utility & Protection", 1000, "none", 0, 1, 300, 1, 1),
        ("Clover", "A lucky charm reserved for future gambling games.", "🍀", "Consumable", 250, "none", 0, 1, 75, 1, 1),
        ("Lottery Ticket", "Reserved for the future X BOT Lottery.", "🎟️", "Consumable", 100, "none", 0, 1, 25, 1, 1),
        ("Balloon", "Reserved for a future Balloon Pop game.", "🎈", "Consumable", 50, "none", 0, 1, 10, 1, 1),
        ("Safe Pass", "Reserved for the future robbery system.", "🎫", "Consumable", 300, "none", 0, 1, 90, 1, 1),
        ("Spin Token", "Reserved for a future spin game.", "🌈", "Consumable", 150, "none", 0, 1, 40, 1, 1),
        ("Event Ticket", "A ticket reserved for X BOT events.", "🎟️", "Consumable", 250, "none", 0, 1, 0, 1, 1),
        ("Event Trophy", "A commemorative trophy for X BOT events.", "🏆", "Collectables", 0, "collectible", 0, 0, 0, 1, 1),
        ("Trophy", "A special trophy for achievements and challenges.", "🏆", "Collectables", 0, "collectible", 0, 0, 0, 1, 1),
        ("Legacy Basic Pickaxe", "A rare collectible from the first X BOT testing era.", "⛏️", "Collectables", 0, "collectible", 0, 0, 0, 1, 1),
        ("Stone", "A common stone found while mining.", "🪨", "Materials & Resources", 0, "crafting_material", 50, 0, 2, 1, 3),
        ("Coal", "A common fuel resource found deep underground.", "⚫", "Materials & Resources", 0, "crafting_material", 35, 0, 4, 1, 2),
        ("Copper", "A common metal resource used for crafting and trade.", "🟠", "Materials & Resources", 0, "crafting_material", 25, 0, 7, 1, 2),
        ("Iron", "A durable metal resource commonly found while mining.", "🔩", "Materials & Resources", 0, "crafting_material", 18, 0, 12, 1, 2),
        ("Silver", "A valuable metal resource less common than gold.", "⚪", "Materials & Resources", 0, "crafting_material", 12, 0, 18, 1, 1),
        ("Gold", "A valuable precious metal sought after by miners.", "🟡", "Materials & Resources", 0, "crafting_material", 8, 0, 30, 1, 1),
        ("Amethyst", "A beautiful purple crystal uncommon while mining.", "🟣", "Materials & Resources", 0, "crafting_material", 6, 0, 45, 1, 1),
        ("Ruby", "A rare red gemstone known for beauty and value.", "🔴", "Materials & Resources", 0, "crafting_material", 4, 0, 65, 1, 1),
        ("Emerald", "A rare green gemstone valued for beauty and rarity.", "🟢", "Materials & Resources", 0, "crafting_material", 3, 0, 85, 1, 1),
        ("Obsidian", "A dark volcanic stone formed from cooled lava.", "⚫", "Materials & Resources", 0, "crafting_material", 2, 0, 110, 1, 1),
        ("Diamond", "An extremely rare gemstone found while mining.", "💎", "Materials & Resources", 0, "crafting_material", 1, 0, 180, 1, 1),
    ]
    for name, description, emoji, category, price, effect, effect_value, shop_visible, sell_price, mine_weight, mine_max_yield in catalog:
        db.execute("""INSERT OR IGNORE INTO items(name,description,emoji,category,category_id,price,currency,sell_price,stock,effect,effect_value,enabled,sellable,tradeable,shop_visible,mine_weight,mine_min_yield,mine_max_yield)
        VALUES(?,?,?,?,(SELECT id FROM item_categories WHERE label=?),?,'xc',?,-1,?,?,1,?,?,?, ?,1,?)""", (name, description, emoji, category.lower(), category, price, sell_price, effect, effect_value, 1 if sell_price else 0, 0, shop_visible, mine_weight if effect == "crafting_material" else 0, mine_max_yield))
    # A non-economic collectible for the winner of the first War Campaign.
    # It is deliberately hidden from the shop and cannot be sold or traded.
    db.execute("""INSERT INTO items(name,description,emoji,category,category_id,price,currency,sell_price,stock,effect,effect_value,enabled,sellable,tradeable,shop_visible)
        SELECT 'Season 1 Champion Trophy','Awarded to the champion of X BOT War Season 1.','🏆','collectable',
        (SELECT id FROM item_categories WHERE label='Collectables'),0,'xc',0,-1,'collectible',0,1,0,0,0
        WHERE NOT EXISTS(SELECT 1 FROM items WHERE name='Season 1 Champion Trophy' COLLATE NOCASE)""")
    db.execute("""UPDATE items SET description='A reliable starter pickaxe for mining.',price=1,effect='mine_tool',
        enabled=1,shop_visible=1,category_id=(SELECT id FROM item_categories WHERE label='Gear')
        WHERE name='Basic Pickaxe' AND description='Configurable starter tool for the future mining system.'""")
    # Upgrade the earlier item-library materials to real mineable materials without replacing admin-set weights.
    db.execute("""UPDATE items SET effect='crafting_material', enabled=1, shop_visible=0,
        category_id=(SELECT id FROM item_categories WHERE label='Materials & Resources'),
        mine_weight=CASE name WHEN 'Stone' THEN 50 WHEN 'Coal' THEN 35 WHEN 'Copper' THEN 25 WHEN 'Iron' THEN 18
            WHEN 'Silver' THEN 12 WHEN 'Gold' THEN 8 WHEN 'Amethyst' THEN 6 WHEN 'Ruby' THEN 4
            WHEN 'Emerald' THEN 3 WHEN 'Obsidian' THEN 2 WHEN 'Diamond' THEN 1 ELSE mine_weight END,
        mine_min_yield=1,
        mine_max_yield=CASE name WHEN 'Stone' THEN 3 WHEN 'Coal' THEN 2 WHEN 'Copper' THEN 2 WHEN 'Iron' THEN 2 ELSE 1 END
        WHERE name IN ('Stone','Coal','Copper','Iron','Silver','Gold','Amethyst','Ruby','Emerald','Obsidian','Diamond')
        AND mine_weight=0""")
    # Mining rewards must be usable immediately: old item-library records may
    # predate sell-back prices, so enable selling without overwriting an admin's
    # custom non-zero price from the Dashboard.
    db.execute("""UPDATE items SET sellable=1,
        sell_price=CASE WHEN sell_price<=0 THEN CASE name
            WHEN 'Stone' THEN 2 WHEN 'Coal' THEN 4 WHEN 'Copper' THEN 7 WHEN 'Iron' THEN 12
            WHEN 'Silver' THEN 18 WHEN 'Gold' THEN 30 WHEN 'Amethyst' THEN 45 WHEN 'Ruby' THEN 65
            WHEN 'Emerald' THEN 85 WHEN 'Obsidian' THEN 110 WHEN 'Diamond' THEN 180 ELSE sell_price END
        ELSE sell_price END
        WHERE name IN ('Stone','Coal','Copper','Iron','Silver','Gold','Amethyst','Ruby','Emerald','Obsidian','Diamond')""")
    for item_name, item_effect, description in [
        ("Lottery Ticket", "lottery_ticket", "Use this item to enter the active X BOT Lottery round."),
        ("Spin Token", "spin_token", "Use this token for one X BOT prize-wheel spin without an XC bet."),
        ("Balloon", "balloon_token", "Use this item for one Balloon Pop game without an XC bet."),
    ]:
        db.execute("UPDATE items SET effect=?,description=? WHERE name=? AND effect='none'", (item_effect, description, item_name))
    # Department careers and their configurable incentive documents.
    db.execute("""INSERT INTO items(name,description,emoji,category,category_id,price,currency,sell_price,stock,effect,effect_value,enabled,sellable,tradeable,shop_visible)
        SELECT 'Letter of Recommendation','Required approval letter for department careers.','📜','work',(SELECT id FROM item_categories WHERE label='Work & Career'),0,'xc',0,-1,'none',0,1,0,0,0
        WHERE NOT EXISTS(SELECT 1 FROM items WHERE name='Letter of Recommendation' COLLATE NOCASE)""")
    db.execute("""INSERT INTO items(name,description,emoji,category,category_id,price,currency,sell_price,stock,effect,effect_value,enabled,sellable,tradeable,shop_visible)
        SELECT 'Work Pass','Career incentive document used to unlock department jobs, reapply, or take vacation.','🎟️','work',(SELECT id FROM item_categories WHERE label='Work & Career'),0,'xc',0,-1,'job_bonus',0,1,0,0,0
        WHERE NOT EXISTS(SELECT 1 FROM items WHERE name='Work Pass' COLLATE NOCASE)""")
    department_jobs = [
        ("Maintenance Department", "Maintain server operations and complete scheduled department work.", "⚠️", 30, 30, 4, "1526826551927115826"),
        ("Public Relations Department", "Represent the community and coordinate public communications.", "📣", 30, 30, 4, "1527254483875270716"),
        ("Security Department", "Protect community operations and support server security.", "🛡️", 30, 30, 4, "1519587111450185769"),
        ("Department Manager/Commissioner", "Manage department staff, standards and daily operations.", "🔨", 80, 80, 2, "1536978781137412116"),
        ("Assistant Director", "Coordinate senior department leadership and strategic work.", "🔨", 100, 100, 2, "1531235529608138913"),
        ("Board of Executive Directors", "Provide executive oversight for X BOT departments.", "⚒️", 150, 150, 2, "1531206560267632760"),
        ("X Council", "Provide senior council leadership and community oversight.", "🏛️", 300, 300, 1, "1523954152701431848"),
        ("Administration", "Lead X BOT administration and executive server operations.", "🏢", 500, 500, 1, "1531176720097476708"),
    ]
    db.executemany("""INSERT INTO jobs(name,description,emoji,min_salary,max_salary,shifts_per_day,required_role_id,requirement_item_id,requirement_quantity,enabled)
        VALUES(?,?,?,?,?,?,?,(SELECT id FROM items WHERE name='Work Pass'),1,1) ON CONFLICT(name) DO UPDATE SET description=excluded.description,emoji=excluded.emoji,
        min_salary=excluded.min_salary,max_salary=excluded.max_salary,shifts_per_day=excluded.shifts_per_day,
        required_role_id=excluded.required_role_id,requirement_item_id=excluded.requirement_item_id,requirement_quantity=1,enabled=1""", department_jobs)
    db.execute("UPDATE jobs SET enabled=0 WHERE name IN ('Recruit','Miner','Trader')")
    # Work Pass replaced the old recommendation letter. Remove unusable copies while preserving logs/history.
    old_letter = db.execute("SELECT id FROM items WHERE name='Letter of Recommendation' COLLATE NOCASE").fetchone()
    if old_letter:
        db.execute("DELETE FROM inventories WHERE item_id=?", (old_letter["id"],))
        db.execute("UPDATE items SET enabled=0,shop_visible=0,sellable=0,tradeable=0 WHERE id=?", (old_letter["id"],))
    work_pass = db.execute("SELECT id FROM items WHERE name='Work Pass' COLLATE NOCASE").fetchone()
    clover = db.execute("SELECT id FROM items WHERE name='Clover' COLLATE NOCASE").fetchone()
    if work_pass:
        for key in ("job_recommendation_item_id", "job_apology_item_id", "job_vacation_item_id"):
            db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, str(work_pass["id"])))
        db.execute("INSERT OR IGNORE INTO job_incentive_pool(item_id,amount,weight,enabled) VALUES(?,1,99,1)", (work_pass["id"],))
    if clover:
        db.execute("INSERT OR IGNORE INTO job_incentive_pool(item_id,amount,weight,enabled) VALUES(?,1,1,1)", (clover["id"],))
    # X BOT is now a War Game + Casino bot, not a company/job economy.
    # Retire the legacy job data every startup so it cannot return after a restart.
    retired_job_items = [row["id"] for row in db.execute(
        "SELECT id FROM items WHERE name IN ('Work Pass','Letter of Recommendation')"
    ).fetchall()]
    if retired_job_items:
        marks = ",".join("?" for _ in retired_job_items)
        db.execute(f"DELETE FROM inventories WHERE item_id IN ({marks})", retired_job_items)
        db.execute(f"DELETE FROM job_incentive_pool WHERE item_id IN ({marks})", retired_job_items)
        db.execute(f"DELETE FROM items WHERE id IN ({marks})", retired_job_items)
    db.execute("UPDATE players SET job_id=NULL,job_started_at=0,job_warning_sent=0,work_shifts_today=0")
    db.execute("DELETE FROM jobs")
    areas = [
        ("Surface Mine","🪨","A safe starting mine for stone, coal and copper.",1,60,8,10,18,0,1),
        ("Iron Depths","🔩","Deeper tunnels rich in iron and silver.",6,75,10,18,28,1,2),
        ("Golden Cavern","🟡","A dangerous cavern containing precious metals and gems.",16,90,12,28,42,2,3),
        ("Crystal Abyss","💎","The deepest mine with the rarest X BOT resources.",31,120,15,45,70,5,4),
    ]
    db.executemany("""INSERT OR IGNORE INTO mining_areas(name,emoji,description,required_level,cooldown_seconds,energy_cost,exp_min,exp_max,crystal_chance,position)
        VALUES(?,?,?,?,?,?,?,?,?,?)""", areas)
    drop_map = {"Surface Mine": ["Stone","Coal","Copper"], "Iron Depths": ["Coal","Copper","Iron","Silver"],
        "Golden Cavern": ["Iron","Silver","Gold","Amethyst","Ruby"], "Crystal Abyss": ["Gold","Amethyst","Ruby","Emerald","Obsidian","Diamond"]}
    for area_name, item_names in drop_map.items():
        area = db.execute("SELECT id FROM mining_areas WHERE name=?", (area_name,)).fetchone()
        for item_name in item_names:
            item = db.execute("SELECT id,mine_weight,mine_min_yield,mine_max_yield FROM items WHERE name=?", (item_name,)).fetchone()
            if item:
                db.execute("""INSERT OR IGNORE INTO mining_area_drops(area_id,item_id,weight,min_yield,max_yield,rare)
                    VALUES(?,?,?,?,?,?)""", (area["id"],item["id"],max(1,item["mine_weight"]),item["mine_min_yield"],item["mine_max_yield"],int(item["mine_weight"] <= 4)))
    pickaxes = [("Basic Pickaxe",1,0,0,0,1),("Iron Pickaxe",2,5,10,5,5),("Gold Pickaxe",3,10,20,10,15),
        ("Diamond Pickaxe",4,15,35,15,25),("X Pickaxe",5,25,50,25,40)]
    db.executemany("""UPDATE items SET pickaxe_power=?,pickaxe_luck=?,pickaxe_yield_bonus=?,pickaxe_cooldown_reduction=?,pickaxe_required_level=?
        WHERE name=?""", [(power,luck,yield_bonus,cooldown,level,name) for name,power,luck,yield_bonus,cooldown,level in pickaxes])
    first_area = db.execute("SELECT id FROM mining_areas WHERE enabled=1 ORDER BY required_level,position LIMIT 1").fetchone()
    if first_area:
        db.execute("UPDATE players SET mining_area_id=? WHERE mining_area_id IS NULL", (first_area["id"],))
    # A new player should never be blocked from the mining loop by needing to
    # buy a starter tool first. Give exactly one Basic Pickaxe only to players
    # who own no mining tool at all; existing equipped tools are never replaced.
    starter_pickaxe = db.execute("SELECT id FROM items WHERE name='Basic Pickaxe' COLLATE NOCASE AND effect='mine_tool'").fetchone()
    if starter_pickaxe and setting(db, "mining_starter_pickaxe_enabled"):
        starter_id = starter_pickaxe["id"]
        db.execute("""INSERT INTO inventories(user_id,item_id,quantity)
            SELECT p.user_id,?,1 FROM players p
            WHERE NOT EXISTS (
                SELECT 1 FROM inventories inv JOIN items tool ON tool.id=inv.item_id
                WHERE inv.user_id=p.user_id AND inv.quantity>0 AND tool.effect='mine_tool'
            )
            ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=MAX(inventories.quantity,1)""", (starter_id,))
        db.execute("""UPDATE players SET equipped_pickaxe_id=?
            WHERE equipped_pickaxe_id IS NULL
            AND EXISTS(SELECT 1 FROM inventories WHERE user_id=players.user_id AND item_id=? AND quantity>0)""",
            (starter_id, starter_id))
    db.execute("UPDATE players SET mining_energy_updated=? WHERE mining_energy_updated=0", (int(time.time()),))
    db.execute("UPDATE players SET job_started_at=? WHERE job_id IS NOT NULL AND job_started_at=0", (int(time.time()),))
    db.commit()


def setting(db, key: str) -> int:
    row = db.execute("SELECT value FROM economy_settings WHERE key = ?", (key,)).fetchone()
    return int(row["value"]) if row else int(DEFAULT_SETTINGS[key])


def roll_job_incentive(db: sqlite3.Connection, user_id: int, job_started_at: int, now: int | None = None):
    """Roll and grant one weighted job incentive; return (item, amount, chance)."""
    now = now or int(time.time())
    tenure_days = max(0, (now - max(0, job_started_at)) // 86400) if job_started_at else 0
    chance = min(
        setting(db, "job_drop_max_chance"),
        setting(db, "job_drop_base_chance") + tenure_days * setting(db, "job_drop_tenure_multiplier"),
    )
    if chance <= 0 or random.randint(1, 100) > chance:
        return None, 0, chance
    pool = db.execute("""SELECT p.*,i.name,i.emoji FROM job_incentive_pool p
        JOIN items i ON i.id=p.item_id WHERE p.enabled=1 AND p.weight>0 AND p.amount>0 AND i.enabled=1""").fetchall()
    if not pool:
        return None, 0, chance
    chosen = random.choices(pool, weights=[row["weight"] for row in pool], k=1)[0]
    db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
        ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
        (user_id, chosen["item_id"], chosen["amount"]))
    return chosen, chosen["amount"], chance


def log(db, user_id: int, action: str, detail: str) -> None:
    db.execute(
        "INSERT INTO economy_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
        (user_id, action, detail, int(time.time())),
    )


def process_job_inactivity(db: sqlite3.Connection, now: int | None = None):
    """Apply warning/firing rules and return events for Discord delivery."""
    if not setting(db, "job_inactivity_enabled"):
        return []
    now = now or int(time.time())
    warning_seconds = setting(db, "job_warning_days") * 86400
    fire_seconds = setting(db, "job_fire_days") * 86400
    events = []
    rows = db.execute("""SELECT p.user_id,p.display_name,p.last_work,p.job_started_at,p.job_warning_sent,j.name job_name
        FROM players p JOIN jobs j ON j.id=p.job_id WHERE p.job_id IS NOT NULL""").fetchall()
    for player in rows:
        last_active = max(player["last_work"], player["job_started_at"])
        inactive = max(0, now - last_active)
        inactive_days = inactive // 86400
        if inactive >= fire_seconds:
            db.execute("UPDATE players SET job_id=NULL,job_started_at=0,job_warning_sent=0 WHERE user_id=?", (player["user_id"],))
            log(db, player["user_id"], "job_inactivity_fired", f"Removed from {player['job_name']} after {inactive_days} inactive days")
            events.append(("fired", player["user_id"], player["display_name"], player["job_name"], inactive_days))
        elif inactive >= warning_seconds and not player["job_warning_sent"]:
            db.execute("UPDATE players SET job_warning_sent=? WHERE user_id=?", (now, player["user_id"]))
            log(db, player["user_id"], "job_inactivity_warning", f"Warning for {player['job_name']} after {inactive_days} inactive days")
            events.append(("warning", player["user_id"], player["display_name"], player["job_name"], inactive_days))
    db.commit()
    return events


def start_job_inactivity_task(bot: discord.Client, db: sqlite3.Connection) -> None:
    """Retired: Job system is inactive; no background inactivity loop needed."""
    return


def find_item(db, name: str, user_id: int | None = None):
    """Find an item by its name or a dashboard-configured alias."""
    wanted = name.strip().casefold()
    rows = db.execute("SELECT * FROM items" if user_id is None else """SELECT items.*, inventories.quantity FROM items
        INNER JOIN inventories ON inventories.item_id=items.id WHERE inventories.user_id=?""", (() if user_id is None else (user_id,))).fetchall()
    for item in rows:
        aliases = [part.strip().casefold() for part in item["aliases"].split(",") if part.strip()]
        if item["name"].casefold() == wanted or wanted in aliases:
            return item
    return None


def member_has_role(member, role_id: str) -> bool:
    if not role_id:
        return True
    wanted = str(role_id).strip().removeprefix("@").casefold()
    return any(str(role.id) == wanted or role.name.casefold() == wanted for role in getattr(member, "roles", []))


def mining_level_for_exp(exp: int) -> int:
    level = 1
    while exp >= 50 * level * (level + 1):
        level += 1
    return level


def refresh_mining_energy(db: sqlite3.Connection, user_id: int, now: int | None = None):
    now = now or int(time.time()); maximum = setting(db, "mining_max_energy")
    player = db.execute("SELECT mining_energy,mining_energy_updated FROM players WHERE user_id=?", (user_id,)).fetchone()
    if not setting(db, "mining_energy_enabled"):
        return maximum
    interval = max(1, setting(db, "mining_energy_regen_seconds")); elapsed = max(0, now - player["mining_energy_updated"])
    ticks = elapsed // interval
    if ticks:
        energy = min(maximum, player["mining_energy"] + ticks * setting(db, "mining_energy_regen_amount"))
        db.execute("UPDATE players SET mining_energy=?,mining_energy_updated=? WHERE user_id=?", (energy, player["mining_energy_updated"] + ticks * interval, user_id))
        return energy
    return min(maximum, player["mining_energy"])


def mining_collection_status(db: sqlite3.Connection, user_id: int):
    """Return every enabled mining material together with this player's owned amount."""
    return db.execute("""SELECT i.id,i.name,i.emoji,i.description,i.sell_price,
        COALESCE(inv.quantity,0) quantity
        FROM items i LEFT JOIN inventories inv ON inv.item_id=i.id AND inv.user_id=?
        WHERE i.effect='crafting_material' AND i.enabled=1 ORDER BY i.mine_weight DESC,i.name""",
        (user_id,)).fetchall()


def award_mining_collection_if_complete(db: sqlite3.Connection, user_id: int, now: int | None = None):
    """Claim the configured completion reward only once when every material was found."""
    materials = mining_collection_status(db, user_id)
    if not materials or any(row["quantity"] <= 0 for row in materials):
        return None
    already = db.execute("SELECT 1 FROM mining_collection_rewards WHERE user_id=?", (user_id,)).fetchone()
    if already:
        return None
    claimed_at = now or int(time.time())
    db.execute("INSERT INTO mining_collection_rewards(user_id,claimed_at) VALUES(?,?)", (user_id, claimed_at))
    xc_reward = max(0, setting(db, "mining_collection_xc_reward"))
    crystal_reward = max(0, setting(db, "mining_collection_xcrystal_reward"))
    db.execute("UPDATE players SET xc=xc+?,xcrystals=xcrystals+? WHERE user_id=?", (xc_reward, crystal_reward, user_id))
    log(db, user_id, "mining_collection_complete", f"All {len(materials)} materials discovered; +{xc_reward} XC, +{crystal_reward} XCrystals")
    return xc_reward, crystal_reward, len(materials)


def build_profile_view(target, db: sqlite3.Connection, create_player_fn=None) -> discord.ui.LayoutView:
    """Build an interactive LayoutView for a member's unified profile with 1-click Gamer Card & Mining Stats."""
    player = db.execute("SELECT * FROM players WHERE user_id = ?", (target.id,)).fetchone()
    if not player and create_player_fn:
        player = create_player_fn(target)
    elif not player:
        player = {
            "nation_name": f"{target.display_name}'s Nation",
            "xc": 0,
            "bank_xc": 0,
            "money": 0,
            "xcrystals": 0,
            "mining_level": 1,
            "mining_exp": 0,
        }

    item_count = 0
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inventories'").fetchone():
        row = db.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS total FROM inventories WHERE user_id = ?",
            (target.id,),
        ).fetchone()
        if row:
            item_count = row["total"]

    xp_row = db.execute("SELECT level, total_xp FROM xp_profiles WHERE user_id = ?", (target.id,)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='xp_profiles'").fetchone() else None
    level_info = f"⭐ **Level:** {xp_row['level']} ({xp_row['total_xp']:,} XP)" if xp_row else "⭐ **Level:** 1 (0 XP)"

    m_level = player["mining_level"] if "mining_level" in player.keys() else 1
    m_exp = player["mining_exp"] if "mining_exp" in player.keys() else 0
    mining_line = f"⛏️ **Mining:** Lv.{m_level} ({m_exp:,} EXP)"

    game_prof = db.execute("SELECT * FROM game_profiles WHERE user_id = ?", (target.id,)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='game_profiles'").fetchone() else None
    gaming_line = ""
    if game_prof and (game_prof["steam_id"] or game_prof["roblox_name"]):
        tags = []
        if game_prof["steam_id"]: tags.append(f"Steam: `{game_prof['steam_id']}`")
        if game_prof["roblox_name"]: tags.append(f"Roblox: `{game_prof['roblox_name']}`")
        gaming_line = f"\n🎮 **Gaming:** " + " · ".join(tags)

    view = discord.ui.LayoutView(timeout=180)
    container = discord.ui.Container(accent_color=discord.Color.blurple())
    avatar_url = target.display_avatar.url if hasattr(target, "display_avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"
    text = (f"## 👤 {target.display_name}'s X BOT Profile\n"
            f"🏳️ **Nation:** {player['nation_name']}\n"
            f"{level_info} · {mining_line}\n"
            f"🪙 **Wallet XC:** {player['xc']:,} · 🏦 **Bank XC:** {player['bank_xc']:,}\n"
            f"💵 **Cash:** {player['money']:,} · 💎 **XCrystals:** {player['xcrystals']:,}\n"
            f"🎒 **Inventory:** {item_count:,} item(s)"
            f"{gaming_line}")
    container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=discord.ui.Thumbnail(avatar_url)))

    row = discord.ui.ActionRow()
    btn_gamer = discord.ui.Button(
        label="Gamer Card",
        emoji="🎮",
        style=discord.ButtonStyle.primary,
        custom_id=f"xbot:prof:gamer:{target.id}",
    )
    btn_mining = discord.ui.Button(
        label="Mining Stats",
        emoji="⛏️",
        style=discord.ButtonStyle.secondary,
        custom_id=f"xbot:prof:mining:{target.id}",
    )
    btn_edit = discord.ui.Button(
        label="Edit Handles",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id=f"xbot:prof:edit:{target.id}",
    )

    async def on_gamer_click(b_inter: discord.Interaction):
        import gaming
        g_prof = gaming.get_game_profile(db, target.id)
        embed, card_view = gaming.build_gamer_card(target, g_prof, db, b_inter.guild)
        await b_inter.response.send_message(embed=embed, view=card_view, ephemeral=True)

    async def on_mining_click(b_inter: discord.Interaction):
        refresh_mining_energy(db, target.id)
        db.commit()
        player_row = db.execute("""SELECT p.*,a.name area_name,a.emoji area_emoji,i.name pickaxe_name FROM players p
            LEFT JOIN mining_areas a ON a.id=p.mining_area_id LEFT JOIN items i ON i.id=p.equipped_pickaxe_id WHERE p.user_id=?""", (target.id,)).fetchone()
        if not player_row:
            await b_inter.response.send_message("❌ Player not found.", ephemeral=True)
            return
        m_lvl = player_row["mining_level"]
        next_exp = 50 * m_lvl * (m_lvl + 1)
        next_area = db.execute("""SELECT name,emoji,required_level FROM mining_areas
            WHERE enabled=1 AND required_level>? ORDER BY required_level,position LIMIT 1""", (m_lvl,)).fetchone()
        tool_stats = ""
        if player_row["equipped_pickaxe_id"]:
            tool = db.execute("SELECT pickaxe_power,pickaxe_luck,pickaxe_yield_bonus,pickaxe_cooldown_reduction FROM items WHERE id=?", (player_row["equipped_pickaxe_id"],)).fetchone()
            if tool:
                tool_stats = f" · Power {tool['pickaxe_power']} · Luck {tool['pickaxe_luck']}% · Yield +{tool['pickaxe_yield_bonus']}%"
        area_goal = (f"\n🔓 **Next Area:** {next_area['emoji']} {next_area['name']} at Level {next_area['required_level']}"
                     if next_area else "\n🏆 You have unlocked every current mining area.")
        body = (f"⛏️ **Level:** {m_lvl} · **EXP:** {player_row['mining_exp']:,}/{next_exp:,}\n"
            f"⚡ **Energy:** {player_row['mining_energy']}/{setting(db,'mining_max_energy')}\n🗺️ **Area:** {player_row['area_emoji'] or '❓'} {player_row['area_name'] or 'Not selected'}\n"
            f"🛠️ **Pickaxe:** {player_row['pickaxe_name'] or 'Not equipped'}{tool_stats}\n📊 **Mining Runs:** {player_row['total_mines']:,} · **Rare Finds:** {player_row['rare_mining_finds']:,}{area_goal}")
        await b_inter.response.send_message(view=xbot_ui.panel(f"⛏️ {target.display_name}'s Mining Stats", body, colour=discord.Color.dark_gold()), ephemeral=True)

    async def on_edit_click(b_inter: discord.Interaction):
        import gaming
        existing = gaming.get_game_profile(db, b_inter.user.id)
        modal = gaming.QuickGameSetModal(db, existing)
        await b_inter.response.send_modal(modal)

    btn_gamer.callback = on_gamer_click
    btn_mining.callback = on_mining_click
    btn_edit.callback = on_edit_click

    row.add_item(btn_gamer)
    row.add_item(btn_mining)
    row.add_item(btn_edit)
    container.add_item(row)
    view.add_item(container)
    return view


def register_commands(bot, db, create_player) -> None:
    def is_economy_staff(interaction: discord.Interaction) -> bool:
        """Staff-only read access for admin/moderator economy lookups.

        Discord permission flags are preferred, but role-name matching keeps
        the command usable after the server's Council role was renamed to
        Moderator (including the older "Moterator" spelling).
        """
        if interaction.guild is None:
            return False
        if interaction.guild.owner_id == interaction.user.id:
            return True
        permissions = getattr(interaction.user, "guild_permissions", None)
        if permissions and (permissions.administrator or permissions.moderate_members):
            return True
        role_names = {str(role.name).casefold() for role in getattr(interaction.user, "roles", [])}
        return any(name in role_names for name in {"administrator", "admin", "moderator", "moterator"})

    async def admin_item_autocomplete(interaction: discord.Interaction, current: str):
        rows = db.execute("SELECT name FROM items WHERE enabled=1 AND name LIKE ? ORDER BY name LIMIT 25", (f"%{current.strip()}%",)).fetchall()
        return [app_commands.Choice(name=row["name"], value=row["name"]) for row in rows]

    async def owned_item_autocomplete(interaction: discord.Interaction, current: str):
        """Offer only items in the caller's Backpack, so no exact spelling is needed."""
        rows = db.execute("""SELECT i.name,i.emoji,inv.quantity FROM inventories inv
            JOIN items i ON i.id=inv.item_id
            WHERE inv.user_id=? AND inv.quantity>0 AND i.name LIKE ?
            ORDER BY i.name LIMIT 25""", (interaction.user.id, f"%{current.strip()}%")).fetchall()
        return [app_commands.Choice(name=f"{row['emoji']} {row['name']} ×{row['quantity']}"[:100], value=row["name"]) for row in rows]

    @bot.tree.command(name="spawn", description="Admin: give an economy item to a member")
    @app_commands.describe(user="Member receiving the item", item="Item Library name", quantity="Quantity to give", reason="Optional audit reason")
    @app_commands.autocomplete(item=admin_item_autocomplete)
    async def spawn_item(interaction: discord.Interaction, user: discord.Member, item: str, quantity: app_commands.Range[int, 1, 100000], reason: str = "Council grant"):
        target_item = find_item(db, item)
        if target_item is None or not target_item["enabled"]:
            await interaction.response.send_message(view=xbot_ui.danger("Item Not Found", "Choose an enabled item from the autocomplete list."), ephemeral=True)
            return
        create_player(user)
        db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
            ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""", (user.id, target_item["id"], quantity))
        log(db, user.id, "admin_spawn", f"{interaction.user} ({interaction.user.id}) gave {quantity}x {target_item['name']} | {reason[:200]}")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("📦 Item Spawned", f"Gave **{quantity:,}x {target_item['emoji']} {target_item['name']}** to {user.mention}.\nReason: **{reason[:200]}**\n-# This action was recorded in Economy Logs."), ephemeral=True)

    @bot.tree.command(name="remove_item", description="Admin: remove an economy item from a member")
    @app_commands.describe(user="Member losing the item", item="Item Library name", quantity="Quantity to remove", reason="Audit reason")
    @app_commands.autocomplete(item=admin_item_autocomplete)
    async def remove_admin_item(interaction: discord.Interaction, user: discord.Member, item: str, quantity: app_commands.Range[int, 1, 100000], reason: str = "Administrative correction"):
        target_item = find_item(db, item)
        owned = db.execute("SELECT quantity FROM inventories WHERE user_id=? AND item_id=?", (user.id, target_item["id"])).fetchone() if target_item else None
        if target_item is None or owned is None or owned["quantity"] < quantity:
            await interaction.response.send_message(view=xbot_ui.danger("Not Enough Items", f"{user.mention} does not own that quantity."), ephemeral=True)
            return
        db.execute("UPDATE inventories SET quantity=quantity-? WHERE user_id=? AND item_id=?", (quantity, user.id, target_item["id"]))
        db.execute("DELETE FROM inventories WHERE user_id=? AND item_id=? AND quantity<=0", (user.id, target_item["id"]))
        log(db, user.id, "admin_remove_item", f"{interaction.user} ({interaction.user.id}) removed {quantity}x {target_item['name']} | {reason[:200]}")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🗑️ Item Removed", f"Removed **{quantity:,}x {target_item['emoji']} {target_item['name']}** from {user.mention}.\n-# This action was recorded in Economy Logs."), ephemeral=True)

    @bot.tree.command(name="economy_adjust", description="Admin: securely adjust a member's X BOT currency")
    @app_commands.describe(user="Member to update", currency="Currency account", amount="Positive to give, negative to take", reason="Required audit reason")
    @app_commands.choices(currency=[app_commands.Choice(name="XC Wallet", value="xc"), app_commands.Choice(name="XC Bank", value="bank_xc"), app_commands.Choice(name="Cash", value="money"), app_commands.Choice(name="XCrystals", value="xcrystals")])
    async def economy_adjust(interaction: discord.Interaction, user: discord.Member, currency: app_commands.Choice[str], amount: app_commands.Range[int, -100000000, 100000000], reason: str):
        if amount == 0:
            try:
                import staff_panel
                panel = staff_panel.AdminPanel(bot, db, is_economy_staff, interaction.user.id, page="assets", target_user_id=user.id)
                embed = panel.build_embed()
                await interaction.response.send_message(embed=embed, view=panel, ephemeral=True)
                return
            except Exception:
                await interaction.response.send_message("Amount cannot be 0.", ephemeral=True)
                return
        player = create_player(user); column = currency.value
        if player[column] + amount < 0:
            await interaction.response.send_message(view=xbot_ui.danger("Adjustment Rejected", "This adjustment would make the balance negative."), ephemeral=True); return
        db.execute(f"UPDATE players SET {column}={column}+? WHERE user_id=?", (amount, user.id))
        log(db, user.id, "admin_currency_adjust", f"{interaction.user} ({interaction.user.id}) changed {column} by {amount:+} | {reason[:200]}")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("💰 Economy Adjusted", f"{user.mention} · **{currency.name}: {amount:+,}**\nReason: **{reason[:200]}**\n-# This action was recorded in Economy Logs."), ephemeral=True)

    @bot.tree.command(name="balance", description="View your X BOT balance, or inspect a member as staff")
    @app_commands.describe(user="Optional: staff may inspect another member")
    async def balance(interaction: discord.Interaction, user: discord.Member | None = None):
        user = user or interaction.user
        if user.id != interaction.user.id and not is_economy_staff(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators and Moderators can inspect another member's balance."), ephemeral=True)
            return
        player = create_player(user)
        item_count = db.execute("SELECT COALESCE(SUM(quantity),0) total FROM inventories WHERE user_id=?", (user.id,)).fetchone()['total']
        avatar = user.display_avatar.url
        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container(accent_color=discord.Color.teal())
        text = (f"## 💳 {user.display_name}'s X BOT Balance\n"
                f"🪙 **Wallet XC:** {player['xc']:,}\n"
                f"🏦 **Bank XC:** {player['bank_xc']:,}\n"
                f"💵 **Cash:** {player['money']:,}\n"
                f"💎 **XCrystals:** {player['xcrystals']:,}\n"
                f"🎒 **Inventory:** {item_count:,} item(s)\n"
                f"🏳️ **Nation:** {player['nation_name']}")
        container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=discord.ui.Thumbnail(avatar)))
        container.add_item(discord.ui.Separator())
        footer = "Staff lookup · `/inventory_check` inspects items." if user.id != interaction.user.id else "Open `/economy` for your full Economy Centre."
        container.add_item(discord.ui.TextDisplay(f"-# {footer}"))
        view.add_item(container)
        await interaction.response.send_message(view=view, ephemeral=True)

    @bot.tree.command(name="inventory_check", description="Staff: check a member's X BOT items")
    @app_commands.describe(user="Member whose Backpack you want to inspect")
    async def inventory_check(interaction: discord.Interaction, user: discord.Member):
        if not is_economy_staff(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators and Moderators can inspect another member's Backpack."), ephemeral=True)
            return
        create_player(user)
        rows = db.execute("""SELECT i.emoji,i.name,inv.quantity
            FROM inventories inv JOIN items i ON i.id=inv.item_id
            WHERE inv.user_id=? AND inv.quantity>0
            ORDER BY i.name LIMIT 40""", (user.id,)).fetchall()
        lines = [f"{row['emoji']} **{row['name']}** ×{row['quantity']:,}" for row in rows]
        body = "\n".join(lines) if lines else "*No items in this member's Backpack.*"
        await interaction.response.send_message(
            view=xbot_ui.panel("🎒 Staff Inventory Check", f"## {user.display_name}'s Backpack\n{body}", colour=discord.Color.teal()),
            ephemeral=True,
        )

    @bot.tree.command(name="profile", description="View your or another member's X BOT economy profile")
    @app_commands.describe(user="Member whose profile you want to inspect (optional)")
    async def profile(interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        create_player(target)
        view = build_profile_view(target, db, create_player)
        await interaction.response.send_message(view=view)

    @bot.tree.command(name="leaderboard", description="View an X BOT leaderboard")
    @app_commands.describe(category="Choose the ranking")
    @app_commands.choices(category=[
        app_commands.Choice(name="XC", value="xc"),
        app_commands.Choice(name="Bank XC", value="bank_xc"),
        app_commands.Choice(name="Net Worth XC", value="net_worth"),
        app_commands.Choice(name="Cash", value="money"),
        app_commands.Choice(name="XCrystals", value="xcrystals"),
        app_commands.Choice(name="Land", value="land"),
        app_commands.Choice(name="Military Power", value="power"),
        app_commands.Choice(name="Activity Level & XP", value="level"),
        app_commands.Choice(name="Weekly Activity XP", value="level_weekly"),
        app_commands.Choice(name="Mining Level", value="mining"),
        app_commands.Choice(name="Casino Profit", value="casino"),
        app_commands.Choice(name="Weekly Lounge Gamers", value="gaming"),
    ])
    async def leaderboard(interaction: discord.Interaction, category: app_commands.Choice[str]):
        footer_text = None
        if category.value == 'power':
            rows = db.execute("""SELECT p.nation_name,COALESCE(SUM(w.quantity*u.power),0) score FROM players p
                LEFT JOIN player_war_units w ON w.user_id=p.user_id LEFT JOIN war_unit_types u ON u.id=w.unit_type_id AND u.enabled=1
                GROUP BY p.user_id ORDER BY score DESC LIMIT 10""").fetchall()
            lines = [f"**{index}.** 🏳️ {row['nation_name']} — **{row['score']:,} Power**" for index, row in enumerate(rows, 1)]
        elif category.value == 'net_worth':
            rows = db.execute("SELECT nation_name,(xc+bank_xc) score FROM players ORDER BY score DESC LIMIT 10").fetchall()
            lines = [f"**{index}.** {row['nation_name']} — **{row['score']:,} XC**" for index, row in enumerate(rows, 1)]
        elif category.value == 'level':
            rows = db.execute("SELECT x.*, p.nation_name FROM xp_profiles x LEFT JOIN players p ON p.user_id=x.user_id ORDER BY x.total_xp DESC LIMIT 10").fetchall()
            medals = ["🥇", "🥈", "🥉"]
            lines = [f"{medals[i] if i<3 else f'**#{i+1}**'} <@{r['user_id']}> ({r['nation_name'] or 'Player'}) · Level **{r['level']}** · {r['total_xp']:,} EXP" for i, r in enumerate(rows)]
        elif category.value == 'level_weekly':
            rows = db.execute("SELECT x.*, p.nation_name FROM xp_profiles x LEFT JOIN players p ON p.user_id=x.user_id ORDER BY x.weekly_xp DESC LIMIT 10").fetchall()
            medals = ["🥇", "🥈", "🥉"]
            lines = [f"{medals[i] if i<3 else f'**#{i+1}**'} <@{r['user_id']}> ({r['nation_name'] or 'Player'}) · Level **{r['level']}** · {r['weekly_xp']:,} EXP" for i, r in enumerate(rows)]
            lines.append(
                "\n🎁 **Weekly Dividend Prize Pool (Top 10):**\n"
                "🥇 1st: **+$100,000 Cash + 250 XC**\n"
                "🥈 2nd: **+$60,000 Cash + 150 XC**\n"
                "🥉 3rd: **+$40,000 Cash + 100 XC**\n"
                "🎖️ 4th–10th: **+$20,000 Cash + 50 XC**\n"
                "-# 💰 Dividends auto-distribute Mondays at 00:00 UTC."
            )
            footer_text = "Weekly XP resets automatically every Monday at 00:00 UTC."
        elif category.value == 'mining':
            rows = db.execute("SELECT display_name,nation_name,mining_level,mining_exp,rare_mining_finds FROM players ORDER BY mining_level DESC,mining_exp DESC LIMIT 10").fetchall()
            lines = [f"**{index}.** ⛏️ **{row['display_name'] or row['nation_name'] or 'Unknown Miner'}** · Lv.{row['mining_level']} · {row['mining_exp']:,} EXP · {row['rare_mining_finds']} rare" for index, row in enumerate(rows, 1)]
        elif category.value == 'casino':
            rows = db.execute("SELECT * FROM casino_stats ORDER BY total_won-total_wagered DESC, biggest_payout DESC LIMIT 10").fetchall()
            lines = [f"**{number}.** <@{row['user_id']}> — **{row['total_won'] - row['total_wagered']:+,} XC** (Won: {row['total_won']:,} XC)" for number, row in enumerate(rows, 1)]
        elif category.value == 'gaming':
            rows = db.execute("""SELECT p.user_id, p.display_name, x.voice_xp, x.weekly_xp
                FROM xp_profiles x
                JOIN players p ON p.user_id=x.user_id
                WHERE x.weekly_xp > 0
                ORDER BY x.voice_xp DESC, x.weekly_xp DESC
                LIMIT 10""").fetchall()
            medals = ["🥇", "🥈", "🥉"]
            lines = [f"{medals[idx] if idx < 3 else f'**#{idx+1}**'} <@{r['user_id']}> — `{r['voice_xp']:,} Voice XP` (`{r['weekly_xp']:,} Weekly XP`)" for idx, r in enumerate(rows)]
        else:
            allowed = {'xc', 'bank_xc', 'money', 'xcrystals', 'land'}
            column = category.value if category.value in allowed else 'xc'
            rows = db.execute(f"SELECT nation_name,{column} score FROM players ORDER BY {column} DESC LIMIT 10").fetchall()
            lines = [f"**{index}.** {row['nation_name']} — **{row['score']:,}**" for index, row in enumerate(rows, 1)]

        body = "\n".join(lines) if lines else "No records found yet."
        await interaction.response.send_message(view=xbot_ui.panel(f"🏆 {category.name} Leaderboard", body, colour=discord.Color.gold(), footer=footer_text))


    def shop_categories():
        return db.execute("""SELECT c.* FROM item_categories c WHERE c.enabled=1 AND EXISTS(
            SELECT 1 FROM items i WHERE i.category_id=c.id AND i.enabled=1 AND i.shop_visible=1)
            ORDER BY c.position,c.label LIMIT 25""").fetchall()

    def category_items(category_id: int):
        return db.execute("""SELECT * FROM items WHERE enabled=1 AND shop_visible=1 AND category_id=?
            ORDER BY price,name""", (category_id,)).fetchall()

    class ShopMarketButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Player Market", emoji="🛒", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open `/market` for your own panel.", ephemeral=True)
                return
            builder = getattr(bot, "xbot_player_panel_builders", {}).get("market")
            if builder:
                await interaction.response.defer()
                await interaction.edit_original_response(view=builder(self.owner_id))
            else:
                await interaction.response.send_message("Player Market is loading.", ephemeral=True)

    class ShopRoleBuyButton(discord.ui.Button):
        def __init__(self, offer):
            label = f"Buy ({offer['price']:,} {'XC' if offer['currency']=='xc' else 'XCrystals'})"
            super().__init__(label=label[:80], emoji="🛍️", style=discord.ButtonStyle.success)
            self.offer_id = offer["id"]

        async def callback(self, interaction: discord.Interaction):
            offer = db.execute("SELECT * FROM role_shop WHERE id=?", (self.offer_id,)).fetchone()
            import advanced_systems
            await advanced_systems.purchase_role(interaction, db, create_player, offer)

    class ShopCategorySelect(discord.ui.Select):
        def __init__(self, owner_id: int, selected_category_id):
            options = [discord.SelectOption(label=row['label'][:100], value=str(row['id']), emoji=row['emoji'], default=str(row['id']) == str(selected_category_id)) for row in shop_categories()]
            options.append(discord.SelectOption(
                label="Discord Roles",
                value="roles",
                emoji="🎭",
                description="Purchase server Discord roles with XC",
                default=str(selected_category_id) == "roles"
            ))
            super().__init__(placeholder="Choose a shop category…", min_values=1, max_values=1, options=options)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            val = self.values[0]
            category_id = val if val == "roles" else int(val)
            view = ShopView(self.owner_id, category_id, 0)
            await interaction.response.edit_message(view=view)

    class ShopBuyButton(discord.ui.Button):
        def __init__(self, item, disabled: bool):
            super().__init__(label="View Item", style=discord.ButtonStyle.primary)
            self.item_id = item['id']
        async def callback(self, interaction):
            if interaction.user.id != self.view.owner_id:
                await interaction.response.send_message('Open your own Shop.',ephemeral=True);return
            from economy_trade_ui import TradeView
            owner,category,page=self.view.owner_id,self.view.category_id,self.view.page
            await interaction.response.edit_message(view=TradeView(bot,db,owner,'shop',self.item_id,back=lambda:ShopView(owner,category,page)))

    class ShopPageButton(discord.ui.Button):
        def __init__(self, owner_id: int, category_id, target_page: int, label: str, emoji: str, disabled: bool):
            super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.primary, disabled=disabled)
            self.owner_id, self.category_id, self.target_page = owner_id, category_id, target_page

        async def callback(self, interaction: discord.Interaction):
            view = ShopView(self.owner_id, self.category_id, self.target_page)
            await interaction.response.edit_message(view=view)

    class EconomyCentreButton(discord.ui.Button):
        """Return child Economy pages to the same player's Economy Centre."""
        def __init__(self, owner_id: int, label: str = "Economy Centre"):
            super().__init__(label=label, emoji="⬅️", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("This panel belongs to another player.", ephemeral=True)
                return
            builder = getattr(bot, "xbot_player_panel_builders", {}).get("economy")
            if builder is None:
                await interaction.response.send_message("Economy Centre is loading. Please try again.", ephemeral=True)
                return
            await interaction.response.edit_message(view=builder(self.owner_id))

    class ShopView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, category_id, page: int = 0):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            self.category_id = category_id
            self.page = page
            container = discord.ui.Container(accent_color=discord.Color.teal())
            wallet = db.execute('SELECT xc,xcrystals FROM players WHERE user_id=?', (owner_id,)).fetchone()
            player = db.execute("SELECT * FROM players WHERE user_id=?", (owner_id,)).fetchone()

            if str(category_id) == "roles":
                roles = db.execute("SELECT * FROM role_shop WHERE enabled=1 AND stock<>0 ORDER BY price,name").fetchall()
                pages = max(1, (len(roles) + 4) // 5)
                self.page = max(0, min(page, pages - 1))
                container.add_item(discord.ui.TextDisplay(
                    f"## 🎭 Discord Role Shop\n"
                    f"Wallet **{wallet['xc']:,} XC** · Crystals **{wallet['xcrystals']:,}**\n"
                    f"Unlock exclusive Discord server roles with your balance."
                ))
                container.add_item(discord.ui.Separator())
                for role_row in roles[self.page * 5:(self.page + 1) * 5]:
                    currency = "XC" if role_row["currency"] == "xc" else "XCrystals"
                    stock = "Unlimited" if role_row["stock"] < 0 else str(role_row["stock"])
                    text = f"### {role_row['emoji']} {role_row['name']}\n💰 Price: **{role_row['price']:,} {currency}** · 📦 Stock: **{stock}**\n{role_row['description'] or 'Official Discord role.'}"
                    container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=ShopRoleBuyButton(role_row)))
                    container.add_item(discord.ui.Separator())
                if not roles:
                    container.add_item(discord.ui.TextDisplay("No roles are currently for sale in the Role Shop."))
                item_count = len(roles)
            else:
                cat_id_int = int(category_id)
                items = category_items(cat_id_int)
                pages = max(1, (len(items) + 4) // 5)
                self.page = max(0, min(page, pages - 1))
                category = db.execute("SELECT * FROM item_categories WHERE id=?", (cat_id_int,)).fetchone()
                cat_label = category['label'] if category else 'Item'
                container.add_item(discord.ui.TextDisplay(f"## 🏪 {cat_label} Shop\nWallet **{wallet['xc']:,} XC** · Crystals **{wallet['xcrystals']:,}**\nChoose an item below."))
                container.add_item(discord.ui.Separator())
                for item in items[self.page * 5:(self.page + 1) * 5]:
                    balance = player['xc'] if player and item['currency'] == 'xc' else player['xcrystals'] if player else 0
                    unavailable = item['stock'] == 0 or balance < item['price']
                    stock = "Unlimited" if item['stock'] < 0 else str(item['stock'])
                    currency = "XC" if item['currency'] == 'xc' else "XCrystals"
                    text = f"### {item['emoji']} {item['name']}\n📦 Stock: **{stock}**\n💰 Price: **{item['price']} {currency}**\n{item['description'][:180]}"
                    container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=ShopBuyButton(item, unavailable)))
                    container.add_item(discord.ui.Separator())
                item_count = len(items)

            navigation = discord.ui.ActionRow(
                ShopPageButton(owner_id, category_id, 0, "", "⏪", self.page == 0),
                ShopPageButton(owner_id, category_id, self.page - 1, "", "◀️", self.page == 0),
                ShopPageButton(owner_id, category_id, self.page + 1, "", "▶️", self.page >= pages - 1),
                ShopPageButton(owner_id, category_id, pages - 1, "", "⏩", self.page >= pages - 1),
                ShopMarketButton(owner_id),
            )
            container.add_item(navigation)
            container.add_item(discord.ui.ActionRow(ShopCategorySelect(owner_id, category_id)))
            container.add_item(discord.ui.TextDisplay(f"-# Page {self.page + 1}/{pages} · {item_count} item(s) · Switch categories or visit Player Market."))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This shop menu belongs to the player who opened it. Open `/menu` for your own menu.", ephemeral=True)
            return False

    @bot.tree.command(name="shop", description="Open the interactive X BOT Economy Shop")
    async def shop(interaction: discord.Interaction):
        if not setting(db, "economy_shop_enabled"):
            await interaction.response.send_message(view=xbot_ui.warning("🏪 Economy Shop Closed", "The Economy Shop is currently closed."), ephemeral=True)
            return
        categories = shop_categories()
        first_cat = categories[0]['id'] if categories else "roles"
        if not categories and not db.execute("SELECT 1 FROM role_shop WHERE enabled=1 AND stock<>0").fetchone():
            await interaction.response.send_message("🏪 The Economy Shop is empty. An administrator can add shop-visible items or roles in the Dashboard.")
            return
        await interaction.response.defer()
        create_player(interaction.user)
        view = ShopView(interaction.user.id, first_cat)
        await interaction.edit_original_response(view=view)

    bot.xbot_player_panel_builders = getattr(bot, "xbot_player_panel_builders", {})
    bot.xbot_player_panel_builders["shop"] = lambda owner_id: ShopView(owner_id, shop_categories()[0]['id'] if shop_categories() else "roles")

    @bot.tree.command(name="inventory", description="View your X BOT inventory")
    async def inventory(interaction: discord.Interaction):
        player = create_player(interaction.user)
        rows = db.execute("""SELECT items.*, inventories.quantity FROM inventories INNER JOIN items ON items.id = inventories.item_id WHERE inventories.user_id = ? AND inventories.quantity > 0 ORDER BY items.name""", (interaction.user.id,)).fetchall()
        if not rows:
            if interaction.message is not None:
                await interaction.response.edit_message(view=xbot_ui.warning("🎒 Backpack Empty", "Your inventory is empty."))
            else:
                await interaction.response.send_message("🎒 Your inventory is empty.")
            return
        if interaction.message is not None:
            await interaction.response.edit_message(view=InventoryView(interaction.user.id))
        else:
            await interaction.response.send_message(view=InventoryView(interaction.user.id))

    @bot.tree.command(name="sell_item", description="Sell an inventory item back to X BOT")
    @app_commands.describe(name="Exact item name", amount="How many to sell")
    @app_commands.autocomplete(name=owned_item_autocomplete)
    async def sell_item(interaction: discord.Interaction, name: str, amount: int = 1):
        if amount <= 0:
            await interaction.response.send_message("❌ Amount must be greater than 0.", ephemeral=True)
            return
        player = create_player(interaction.user)
        item = find_item(db, name, interaction.user.id)
        if item is None or item['quantity'] < amount:
            await interaction.response.send_message("❌ You do not own enough of that item.", ephemeral=True)
            return
        if not item['sellable'] or item['sell_price'] <= 0:
            await interaction.response.send_message("❌ This item cannot be sold back.", ephemeral=True)
            return
        reward = item['sell_price'] * amount
        currency_column = 'xc' if item['currency'] == 'xc' else 'xcrystals'
        import economy_journey
        try:
            economy_journey.sell(db,interaction.user.id,item['id'],amount,expected=(item['sell_price'],item['currency']))
        except ValueError as error:
            await interaction.response.send_message(str(error),ephemeral=True);return
        currency = 'XC' if currency_column == 'xc' else 'XCrystals'
        result=xbot_ui.success("💰 Item Sold", f"Sold **{amount}x {item['name']}** for **{reward:,} {currency}**.")
        result.add_item(discord.ui.ActionRow(economy_journey.Entry(bot,db,interaction.user.id,'Craft Again')))
        await interaction.response.send_message(view=result)

    @bot.tree.command(name="use", description="Use an item from your inventory")
    @app_commands.describe(name="Exact item name")
    @app_commands.autocomplete(name=owned_item_autocomplete)
    async def use(interaction: discord.Interaction, name: str):
        create_player(interaction.user)
        item=find_item(db,name,interaction.user.id)
        if item is None:
            await interaction.response.send_message('You do not own that item.',ephemeral=True);return
        import economy_journey
        try:notice=economy_journey.use(db,interaction.user.id,item['id'])
        except ValueError as error:notice=str(error)
        await interaction.response.send_message(view=xbot_ui.panel('Item Use',notice))

    @bot.tree.command(name="equip", description="Equip a Pickaxe from your inventory")
    @app_commands.describe(name="Pickaxe name or an item alias")
    @app_commands.autocomplete(name=owned_item_autocomplete)
    async def equip(interaction: discord.Interaction, name: str):
        create_player(interaction.user)
        item=find_item(db,name,interaction.user.id)
        if item is None:
            await interaction.response.send_message('You do not own that item.',ephemeral=True);return
        import economy_transactions
        try:notice=economy_transactions.equip(db,interaction.user.id,item['id'])
        except ValueError as error:notice=str(error)
        await interaction.response.send_message(view=xbot_ui.panel('Equip Item',notice))



    class BackpackItemSelect(discord.ui.Select):
        def __init__(self, owner_id: int, page: int, selected_id: int | None, rows):
            options = [discord.SelectOption(
                label=f"{row['name']} × {row['quantity']}"[:100], value=str(row['id']), emoji=safe_discord_component_emoji(row['emoji'], '📦'),
                default=row['id'] == selected_id,
            ) for row in rows]
            super().__init__(placeholder="Choose an item to inspect…", options=options, min_values=1, max_values=1)
            self.owner_id, self.page = owner_id, page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=InventoryView(self.owner_id, self.page, int(self.values[0])))

    class BackpackPageButton(discord.ui.Button):
        def __init__(self, owner_id: int, page: int, target_page: int, emoji: str, disabled: bool):
            super().__init__(emoji=emoji, style=discord.ButtonStyle.primary, disabled=disabled)
            self.owner_id, self.page, self.target_page = owner_id, page, target_page

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=InventoryView(self.owner_id, self.target_page))

    class BackpackActionButton(discord.ui.Button):
        def __init__(self, action, item, disabled, page):
            labels={'use':'Use','equip':'Equip','sell':'Sell to System','list':'List for Players'}
            super().__init__(label=labels[action],style=discord.ButtonStyle.secondary,disabled=disabled)
            self.action,self.item_id,self.page=action,item['id'],page
        async def callback(self, interaction):
            owner=self.view.owner_id
            if interaction.user.id!=owner:
                await interaction.response.send_message('Open your own Backpack.',ephemeral=True);return
            if self.action=='equip':
                import economy_transactions
                try:notice=economy_transactions.equip(db,owner,self.item_id)
                except ValueError as error:notice=str(error)
                await interaction.response.edit_message(view=InventoryView(owner,self.page,self.item_id,notice=notice));return
            from economy_trade_ui import TradeView
            await interaction.response.edit_message(view=TradeView(bot,db,owner,self.action,self.item_id,back=lambda:InventoryView(owner,self.page,self.item_id)))

    class InventoryView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, page: int = 0, selected_id: int | None = None, notice: str = ""):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            all_rows = db.execute("""SELECT i.*,inv.quantity FROM inventories inv JOIN items i ON i.id=inv.item_id
                WHERE inv.user_id=? AND inv.quantity>0 ORDER BY i.name""", (owner_id,)).fetchall()
            pages = max(1, (len(all_rows) + 24) // 25)
            self.page = max(0, min(page, pages - 1))
            shown = all_rows[self.page * 25:(self.page + 1) * 25]
            selected = next((row for row in all_rows if row['id'] == selected_id), None) or (shown[0] if shown else None)
            container = discord.ui.Container(accent_color=discord.Color.blurple())
            if selected is None:
                container.add_item(discord.ui.TextDisplay("## 🎒 X BOT Backpack\nYour inventory is empty."))
                container.add_item(discord.ui.ActionRow(EconomyCentreButton(owner_id, "Economy")))
                self.add_item(container)
                return
            equipped = db.execute("SELECT equipped_pickaxe_id FROM players WHERE user_id=?", (owner_id,)).fetchone()
            equipped_text = " · **Equipped**" if equipped and equipped['equipped_pickaxe_id'] == selected['id'] else ""
            sell_value = selected['sell_price'] if selected['sellable'] else 0
            description = selected['description'] or "No description provided."
            notice_text = f"\n{notice}" if notice else ""
            container.add_item(discord.ui.TextDisplay(
                f"## 🎒 X BOT Backpack\n"
                f"**{len(all_rows)} item types** · **{sum(int(row['quantity']) for row in all_rows):,} items**\n"
                f"### {selected['emoji']} {selected['name']} × {selected['quantity']}{equipped_text}\n"
                f"{description[:300]}\n"
                f"### Sell-back Value\n## {sell_value:,} {'XC' if selected['currency']=='xc' else 'XCrystals'}\nPer item{notice_text}"
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(BackpackItemSelect(owner_id, self.page, selected['id'], shown)))
            actions=[]
            if selected['effect'] in {'war_credits','capital_repair','xc_reward'}:actions.append(BackpackActionButton('use',selected,False,self.page))
            if selected['effect']=='mine_tool':actions.append(BackpackActionButton('equip',selected,False,self.page))
            if selected['sellable'] and selected['sell_price']>0:actions.append(BackpackActionButton('sell',selected,False,self.page))
            if selected['tradeable']:actions.append(BackpackActionButton('list',selected,False,self.page))
            if actions:container.add_item(discord.ui.ActionRow(*actions))
            container.add_item(discord.ui.ActionRow(
                BackpackPageButton(owner_id, self.page, self.page - 1, "◀️", self.page == 0),
                BackpackPageButton(owner_id, self.page, self.page + 1, "▶️", self.page >= pages - 1),
                EconomyCentreButton(owner_id, "Economy"),
            ))
            container.add_item(discord.ui.TextDisplay(f"-# Page {self.page + 1}/{pages} · {len(all_rows)} different item(s) · Select an item, then use the available buttons."))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This Backpack belongs to the player who opened it. Open `/menu` for your own Backpack.", ephemeral=True)
            return False

    class MiningAreaSelect(discord.ui.Select):
        def __init__(self, owner_id: int, current_area_id: int, mining_level: int):
            self.owner_id = owner_id
            self.mining_level = mining_level
            areas = db.execute("SELECT * FROM mining_areas WHERE enabled=1 ORDER BY required_level,position").fetchall()
            options = []
            for area in areas[:25]:
                unlocked = mining_level >= area["required_level"]
                prefix = "✅ " if area["id"] == current_area_id else ("🔓 " if unlocked else f"🔒 Lv.{area['required_level']} ")
                desc = f"Cost: {area['energy_cost']}⚡ | CD: {area['cooldown_seconds']}s | XCrystal: {area['crystal_chance']}%"
                options.append(discord.SelectOption(
                    label=f"{prefix}{area['name']}"[:100],
                    value=str(area["id"]),
                    description=desc[:100],
                    emoji=safe_discord_component_emoji(area.get("emoji"), "🗺️"),
                    default=(area["id"] == current_area_id)
                ))
            super().__init__(placeholder="Select an expedition area...", options=options or [discord.SelectOption(label="No areas available", value="0")])

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("This Mining view belongs to another player.", ephemeral=True)
                return
            area_id = int(self.values[0])
            selected = db.execute("SELECT * FROM mining_areas WHERE id=? AND enabled=1", (area_id,)).fetchone()
            if not selected:
                await interaction.response.send_message("❌ Mining area not found.", ephemeral=True)
                return
            if self.mining_level < selected["required_level"]:
                await interaction.response.send_message(f"🔒 **{selected['name']}** requires Mining Level **{selected['required_level']}** (Your level: {self.mining_level}).", ephemeral=True)
                return
            db.execute("UPDATE players SET mining_area_id=? WHERE user_id=?", (selected["id"], self.owner_id))
            db.commit()
            await interaction.response.edit_message(view=MiningAreasView(self.owner_id, notice=f"✅ Switched active area to **{selected['emoji']} {selected['name']}**!"))

    class MiningAreasView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, notice: str | None = None):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            player = db.execute("SELECT * FROM players WHERE user_id=?", (owner_id,)).fetchone()
            mining_lvl = player["mining_level"] if player else 1
            curr_area_id = player["mining_area_id"] if player else 0

            container = discord.ui.Container(accent_color=discord.Color.dark_gold())
            container.add_item(discord.ui.TextDisplay("## 🗺️ Mining Areas & Expeditions"))
            if notice:
                container.add_item(discord.ui.TextDisplay(notice))
                container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

            areas = db.execute("SELECT * FROM mining_areas WHERE enabled=1 ORDER BY required_level,position").fetchall()
            lines = []
            for area in areas:
                is_active = (area["id"] == curr_area_id)
                unlocked = mining_lvl >= area["required_level"]
                status = "🟢 Active" if is_active else ("✅ Unlocked" if unlocked else f"🔒 Requires Level {area['required_level']}")
                lines.append(
                    f"### {area['emoji']} {area['name']} — {status}\n"
                    f"{area['description'] or 'No description set.'}\n"
                    f"⚡ {area['energy_cost']} Energy · ⏳ {area['cooldown_seconds']}s · 📈 {area['exp_min']}–{area['exp_max']} EXP · 💎 {area['crystal_chance']}% XCrystal"
                )
            container.add_item(discord.ui.TextDisplay("\n".join(lines) or "No Mining Areas enabled."))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(MiningAreaSelect(owner_id, curr_area_id, mining_lvl)))
            container.add_item(discord.ui.ActionRow(
                MiningHubButton("mine", "Mine Now", "⛏️", discord.ButtonStyle.success),
                MiningHubButton("hub", "Mining Hub", "⛏️", discord.ButtonStyle.secondary),
                MiningLobbyButton(),
            ))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("Open `/mining` for your own Mining Hub.", ephemeral=True)
            return False

    async def show_mining_areas(interaction: discord.Interaction):
        create_player(interaction.user)
        view = MiningAreasView(interaction.user.id)
        if interaction.message is not None:
            await interaction.response.edit_message(view=view)
        else:
            await interaction.response.send_message(view=view)

    class MiningProfileView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, body: str):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.dark_gold())
            container.add_item(discord.ui.TextDisplay(body))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(
                MiningHubButton("mine", "Mine Now", "⛏️", discord.ButtonStyle.success),
                MiningHubButton("hub", "Mining Hub", "⛏️", discord.ButtonStyle.secondary),
                MiningHubButton("inventory", "Backpack", "🎒", discord.ButtonStyle.secondary),
            ))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("Open `/mining` for your own Mining Hub.", ephemeral=True)
            return False

    async def show_mining_profile(interaction: discord.Interaction):
        create_player(interaction.user); energy = refresh_mining_energy(db, interaction.user.id); db.commit()
        player = db.execute("""SELECT p.*,a.name area_name,a.emoji area_emoji,i.name pickaxe_name FROM players p
            LEFT JOIN mining_areas a ON a.id=p.mining_area_id LEFT JOIN items i ON i.id=p.equipped_pickaxe_id WHERE p.user_id=?""", (interaction.user.id,)).fetchone()
        next_exp = 50 * player["mining_level"] * (player["mining_level"] + 1)
        next_area = db.execute("""SELECT name,emoji,required_level FROM mining_areas
            WHERE enabled=1 AND required_level>? ORDER BY required_level,position LIMIT 1""", (player["mining_level"],)).fetchone()
        tool_stats = ""
        if player["equipped_pickaxe_id"]:
            tool = db.execute("SELECT pickaxe_power,pickaxe_luck,pickaxe_yield_bonus,pickaxe_cooldown_reduction FROM items WHERE id=?", (player["equipped_pickaxe_id"],)).fetchone()
            if tool:
                tool_stats = f" · Power {tool['pickaxe_power']} · Luck {tool['pickaxe_luck']}% · Yield +{tool['pickaxe_yield_bonus']}%"
        area_goal = (f"\n🔓 **Next Area:** {next_area['emoji']} {next_area['name']} at Level {next_area['required_level']}"
                     if next_area else "\n🏆 You have unlocked every current mining area.")
        body = (f"⛏️ **Level:** {player['mining_level']} · **EXP:** {player['mining_exp']:,}/{next_exp:,}\n"
            f"⚡ **Energy:** {energy}/{setting(db,'mining_max_energy')}\n🗺️ **Area:** {player['area_emoji'] or '❓'} {player['area_name'] or 'Not selected'}\n"
            f"🛠️ **Pickaxe:** {player['pickaxe_name'] or 'Not equipped'}{tool_stats}\n📊 **Mining Runs:** {player['total_mines']:,} · **Rare Finds:** {player['rare_mining_finds']:,}{area_goal}")
        view = MiningProfileView(interaction.user.id, f"## ⛏️ {interaction.user.display_name}'s Mining Profile\n{body}")
        if interaction.message is not None:
            await interaction.response.edit_message(view=view)
        else:
            await interaction.response.send_message(view=view)

    class MiningHelpView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            player = db.execute("SELECT * FROM players WHERE user_id=?", (owner_id,)).fetchone()
            lvl = player["mining_level"] if player else 1
            body = ("**1.** Press `[ ⛏️ Mine Now ]` to collect materials from your selected area.\n"
                    "**2.** Use `/profile` (or `[ 📊 Profile ]`) to view your mining level, energy, and equipped pickaxe.\n"
                    "**3.** Press `[ 🗺️ Areas & Select ]` to switch expeditions and unlock higher tiers.\n"
                    "**4.** Press `[ 🏪 Tool Shop ]` to purchase better pickaxes, then equip them in `[ 🎒 Backpack ]`.\n"
                    "**5.** Press `[ 💰 Sell Materials ]` to quick-sell mined minerals for XC.\n"
                    "**6.** Complete your `[ 🏆 Collection ]` by finding all minerals for a massive reward!\n\n"
                    f"You start with a **Basic Pickaxe** automatically. Your Mining Level: **{lvl}**.")
            container = discord.ui.Container(accent_color=discord.Color.dark_gold())
            container.add_item(discord.ui.TextDisplay("## ⛏️ X BOT Mining Guide\n" + body))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(
                MiningHubButton("mine", "Mine Now", "⛏️", discord.ButtonStyle.success),
                MiningHubButton("hub", "Mining Hub", "⛏️", discord.ButtonStyle.secondary),
                MiningLobbyButton(),
            ))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("Open `/mining` for your own Mining Hub.", ephemeral=True)
            return False

    async def show_mining_help(interaction: discord.Interaction):
        create_player(interaction.user)
        view = MiningHelpView(interaction.user.id)
        if interaction.message is not None:
            await interaction.response.edit_message(view=view)
        else:
            await interaction.response.send_message(view=view)

    class MiningCollectionView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, body: str):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay("## 🏆 Mining Collection\n" + body))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(
                MiningHubButton("mine", "Mine Now", "⛏️", discord.ButtonStyle.success),
                MiningHubButton("hub", "Mining Hub", "⛏️", discord.ButtonStyle.secondary),
                MiningHubButton("inventory", "Backpack", "🎒", discord.ButtonStyle.secondary),
            ))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("Open `/mining` for your own Mining Hub.", ephemeral=True)
            return False

    async def show_mining_collection(interaction: discord.Interaction):
        create_player(interaction.user)
        materials = mining_collection_status(db, interaction.user.id)
        if not materials:
            view = xbot_ui.warning("⛏️ Collection Unavailable", "No mining materials are enabled yet.")
            if interaction.message is not None:
                await interaction.response.edit_message(view=view)
            else:
                await interaction.response.send_message(view=view, ephemeral=True)
            return
        found = sum(1 for item in materials if item["quantity"] > 0)
        reward = db.execute("SELECT claimed_at FROM mining_collection_rewards WHERE user_id=?", (interaction.user.id,)).fetchone()
        rows = [f"{'✅' if item['quantity'] > 0 else '⬛'} {item['emoji']} **{item['name']}** — {item['quantity']:,}" for item in materials]
        reward_text = ("🏆 **Completion Reward: claimed**" if reward else
            f"🎁 **Completion Reward:** {setting(db, 'mining_collection_xc_reward'):,} XC + {setting(db, 'mining_collection_xcrystal_reward'):,} XCrystals")
        body = (f"### Progress: **{found}/{len(materials)}** materials discovered\n"
                f"{reward_text}\n\n" + "\n".join(rows) +
                "\n\n-# Find every enabled material at least once. The reward is granted automatically after your final discovery.")
        view = MiningCollectionView(interaction.user.id, body)
        if interaction.message is not None:
            await interaction.response.edit_message(view=view)
        else:
            await interaction.response.send_message(view=view)

    @bot.tree.command(name="mine", description="Mine materials in your selected area")
    async def mine(interaction: discord.Interaction):
        player = create_player(interaction.user); now = int(time.time())
        pickaxe = db.execute("""SELECT items.*,inventories.quantity FROM items INNER JOIN inventories ON inventories.item_id=items.id
            WHERE inventories.user_id=? AND items.id=?""", (interaction.user.id, player['equipped_pickaxe_id'] or -1)).fetchone()
        if pickaxe is None or pickaxe['quantity'] <= 0 or pickaxe['effect'] != 'mine_tool':
            view=MiningSetupView(interaction.user.id,'Equip a Pickaxe to start',
                'Open Backpack, select a Pickaxe, then press Equip. If you do not own one, open Tool Shop and review its price and required level before buying.')
            if interaction.message is not None:await interaction.response.edit_message(view=view)
            else:await interaction.response.send_message(view=view)
            return
        if player["mining_level"] < pickaxe["pickaxe_required_level"]:
            view=MiningSetupView(interaction.user.id,'This Pickaxe is locked',
                f"Your Mining Level: **{player['mining_level']}** · Required: **{pickaxe['pickaxe_required_level']}**.\nOpen Backpack and equip a lower-level Pickaxe. Mining with a usable tool earns EXP.")
            if interaction.message is not None:await interaction.response.edit_message(view=view)
            else:await interaction.response.send_message(view=view)
            return
        area = db.execute("SELECT * FROM mining_areas WHERE id=? AND enabled=1", (player["mining_area_id"],)).fetchone()
        if area is None or player["mining_level"] < area["required_level"]:
            area = db.execute("SELECT * FROM mining_areas WHERE enabled=1 AND required_level<=? ORDER BY required_level,position LIMIT 1", (player["mining_level"],)).fetchone()
        if area is None:
            await interaction.response.send_message("❌ No mining area is available.", ephemeral=True); return
        cooldown = max(1, area["cooldown_seconds"] * (100 - min(90,pickaxe["pickaxe_cooldown_reduction"])) // 100)
        remaining = cooldown - (now - player["last_mine_at"])
        if remaining > 0:
            result=MiningActionResultView(interaction.user.id,'⏳ Mining is recovering',
                f"Ready <t:{now+remaining}:R>. Your progress is saved. Review crafting or material sales while you wait.",discord.Color.gold())
            if interaction.message is not None:
                await interaction.response.edit_message(view=result)
            else:
                await interaction.response.send_message(view=result)
            return
        energy = refresh_mining_energy(db, interaction.user.id, now)
        if setting(db,"mining_energy_enabled") and energy < area["energy_cost"]:
            db.commit()
            regen=setting(db,'mining_energy_regen_amount');seconds=max(1,setting(db,'mining_energy_regen_seconds'))
            recovery=f"Energy restores **{regen} every {seconds//60} min {seconds%60}s**, up to {setting(db,'mining_max_energy')}." if regen>0 else 'Automatic energy recovery is currently disabled.'
            result=MiningActionResultView(interaction.user.id,'⚡ Not enough energy',
                f"Current **{energy}** · This area needs **{area['energy_cost']}**.\n{recovery}\nChoose another area, craft from your Backpack, or review material sales.",discord.Color.gold())
            if interaction.message is not None:await interaction.response.edit_message(view=result)
            else:await interaction.response.send_message(view=result)
            return
        drops = db.execute("""SELECT d.*,i.name,i.emoji FROM mining_area_drops d JOIN items i ON i.id=d.item_id
            WHERE d.area_id=? AND i.enabled=1 AND d.weight>0""", (area["id"],)).fetchall()
        if not drops:
            await interaction.response.send_message("❌ This area has no configured drops.", ephemeral=True); return
        weights = [row["weight"] * (1 + (pickaxe["pickaxe_luck"] / 100 if row["rare"] else 0)) for row in drops]
        material = random.choices(drops, weights=weights, k=1)[0]
        amount = random.randint(material["min_yield"], material["max_yield"])
        amount += amount * pickaxe["pickaxe_yield_bonus"] // 100
        research_yield = amount * tier8.bonus(db, interaction.user.id, 'mining')
        amount += research_yield // 100 + int(random.randrange(100) < research_yield % 100)
        lucky_extra = random.randint(1,100) <= pickaxe["pickaxe_luck"]
        if lucky_extra: amount += 1
        gained_exp = random.randint(area["exp_min"], area["exp_max"]) + max(0,pickaxe["pickaxe_power"]-1)*2
        new_exp = player["mining_exp"] + gained_exp; new_level = mining_level_for_exp(new_exp)
        crystal = random.randint(1,100) <= area["crystal_chance"]
        db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id)
            DO UPDATE SET quantity=quantity+excluded.quantity""", (interaction.user.id,material["item_id"],amount))
        db.execute("""UPDATE players SET mining_exp=?,mining_level=?,mining_area_id=?,last_mine_at=?,total_mines=total_mines+1,
            rare_mining_finds=rare_mining_finds+?,mining_energy=MAX(0,mining_energy-?),xcrystals=xcrystals+? WHERE user_id=?""",
            (new_exp,new_level,area["id"],now,int(material["rare"]),area["energy_cost"] if setting(db,"mining_energy_enabled") else 0,int(crystal),interaction.user.id))
        collection_reward = award_mining_collection_if_complete(db, interaction.user.id, now)
        log(db,interaction.user.id,"mine",f"{area['name']}: {amount}x {material['name']}, +{gained_exp} EXP")
        db.commit(); level_up = f"\n🎉 **Level Up! Mining Level {new_level}**" if new_level>player["mining_level"] else ""
        extras = ("\n🍀 Pickaxe Luck: **+1 material**" if lucky_extra else "") + ("\n💎 Found **1 XCrystal**" if crystal else "")
        if collection_reward:
            extras += f"\n🏆 **Collection Complete!** +{collection_reward[0]:,} XC +{collection_reward[1]:,} XCrystals"
        body = (f"## {area['emoji']} {area['name']}\nFound **{amount}x {material['emoji']} {material['name']}**\n"
            f"🛠️ {pickaxe['name']} · Power {pickaxe['pickaxe_power']} · Luck {pickaxe['pickaxe_luck']}%\n"
            f"📈 **+{gained_exp} EXP** · Level {new_level}\n⚡ Energy: **{max(0,energy-area['energy_cost'])}/{setting(db,'mining_max_energy')}**\n"
            f"💰 Craft a product, review material sales, or mine again below.{extras}{level_up}")
        result = MiningActionResultView(
            interaction.user.id,
            "⛏️ Mining Expedition Complete",
            body,
            discord.Color.dark_gold(), material_id=material['item_id'],
        )
        if interaction.message is not None:
            await interaction.response.edit_message(view=result)
        else:
            await interaction.response.send_message(view=result)

    async def execute_sell_mined(interaction: discord.Interaction):
        """Quick-sell only mined crafting materials; tools and other items stay safe."""
        create_player(interaction.user)
        materials = db.execute("""SELECT i.id,i.name,i.emoji,i.sell_price,i.currency,inv.quantity
            FROM inventories inv JOIN items i ON i.id=inv.item_id
            WHERE inv.user_id=? AND inv.quantity>0 AND i.effect='crafting_material'
              AND i.sellable=1 AND i.sell_price>0
            ORDER BY i.name""", (interaction.user.id,)).fetchall()
        if not materials:
            warn = xbot_ui.warning(
                "⛏️ No Mining Materials to Sell",
                "You do not have any sellable mined materials. Keep materials for recipes, or use `/mine` to find more."
            )
            if interaction.message is not None:
                await interaction.response.edit_message(view=warn)
            else:
                await interaction.response.send_message(view=warn, ephemeral=True)
            return

        totals = {"xc": 0, "xcrystals": 0}
        sold_lines = []
        for item in materials:
            currency = item["currency"] if item["currency"] in totals else "xc"
            value = item["sell_price"] * item["quantity"]
            totals[currency] += value
            sold_lines.append(f"{item['quantity']}x {item['emoji']} {item['name']}")
            db.execute("DELETE FROM inventories WHERE user_id=? AND item_id=?", (interaction.user.id, item["id"]))
        if totals["xc"]:
            db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (totals["xc"], interaction.user.id))
        if totals["xcrystals"]:
            db.execute("UPDATE players SET xcrystals=xcrystals+? WHERE user_id=?", (totals["xcrystals"], interaction.user.id))
        reward_parts = []
        if totals["xc"]:
            reward_parts.append(f"**{totals['xc']:,} XC**")
        if totals["xcrystals"]:
            reward_parts.append(f"**{totals['xcrystals']:,} XCrystals**")
        log(db, interaction.user.id, "sell_mined", f"{'; '.join(sold_lines)} | +{', '.join(reward_parts)}")
        db.commit()
        body = (f"Sold **{len(materials)}** material type(s):\n"
                f"{', '.join(sold_lines)}\n\n"
                f"💰 Received {' + '.join(reward_parts)}\n"
                "-# Pickaxes, other equipment and non-mining items were not sold.")
        result = MiningActionResultView(
            interaction.user.id,
            "⛏️ Mining Materials Sold",
            body,
            discord.Color.green(),
        )
        if interaction.message is not None:
            await interaction.response.edit_message(view=result)
        else:
            await interaction.response.send_message(view=result)

    sell_mined = execute_sell_mined

    class MiningHubButton(discord.ui.Button):
        def __init__(self, action: str, label: str, emoji: str, style: discord.ButtonStyle):
            super().__init__(label=label, emoji=emoji, style=style)
            self.action = action

        async def callback(self, interaction: discord.Interaction):
            if self.action == "hub":
                await interaction.response.edit_message(view=build_mining_hub(interaction.user.id))
                return
            if self.action == "areas":
                await show_mining_areas(interaction)
                return
            if self.action == "collection":
                await show_mining_collection(interaction)
                return
            if self.action == "help":
                await show_mining_help(interaction)
                return
            if self.action == "sell":
                await execute_sell_mined(interaction)
                return
            actions = {
                "mine": mine.callback,
                "profile": show_mining_profile,
                "inventory": inventory.callback,
                "shop": shop.callback,
            }
            await actions[self.action](interaction)

    class MiningLobbyButton(discord.ui.Button):
        def __init__(self,destination='lobby'):
            super().__init__(label="Lobby", emoji="✨", style=discord.ButtonStyle.secondary)
            self.destination=destination

        async def callback(self, interaction: discord.Interaction):
            builder = getattr(bot, "xbot_player_lobby_builder", None) if self.destination=='lobby' else bot.xbot_player_panel_builders.get(self.destination)
            if builder is None:
                await interaction.response.send_message("Lobby is loading. Please try again.", ephemeral=True)
                return
            await interaction.response.edit_message(view=builder(interaction.user.id))

    class MiningSetupView(discord.ui.LayoutView):
        """Actionable setup without requiring players to type equip commands."""
        def __init__(self,owner_id,title,body):
            super().__init__(timeout=300);self.owner_id=owner_id
            box=discord.ui.Container(accent_color=discord.Color.gold())
            box.add_item(discord.ui.TextDisplay(f'## {title}\n{body}'))
            box.add_item(discord.ui.ActionRow(
                MiningHubButton('inventory','Open Backpack','🎒',discord.ButtonStyle.success),
                MiningHubButton('shop','Tool Shop','🏪',discord.ButtonStyle.secondary),
                MiningHubButton('hub','Mining Hub','⛏️',discord.ButtonStyle.secondary)))
            self.add_item(box)
        async def interaction_check(self,interaction):
            if interaction.user.id==self.owner_id:return True
            await interaction.response.send_message('Open /menu for your own panel.',ephemeral=True);return False

    class MiningActionResultView(discord.ui.LayoutView):
        """Keep a Mining result useful instead of leaving an orphan message."""
        def __init__(self, owner_id: int, title: str, body: str, colour: discord.Color, material_id=None):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            container = discord.ui.Container(accent_color=colour)
            container.add_item(discord.ui.TextDisplay(f"## {title}\n{body}"))
            goal_text,goal_ready=tier8.mining_goal(db,owner_id)
            container.add_item(discord.ui.TextDisplay(goal_text))
            container.add_item(discord.ui.Separator())
            lobby_button=MiningLobbyButton('missions' if goal_ready else 'lobby')
            if goal_ready:
                lobby_button.label='Missions · Claim Rewards'
                lobby_button.style=discord.ButtonStyle.success
            container.add_item(discord.ui.ActionRow(
                MiningHubButton("mine", "Mine Again", "⛏️", discord.ButtonStyle.success),
                __import__('economy_journey').Entry(bot,db,owner_id,'Craft',material=material_id,back=self),
                __import__('economy_journey').Entry(bot,db,owner_id,'Sell Materials',page='materials',back=self),
            ))
            container.add_item(discord.ui.ActionRow(
                MiningHubButton("hub", "Mining Hub", "🗺️", discord.ButtonStyle.secondary),
                EconomyCentreButton(owner_id, "Economy"),
            ))
            if goal_ready:
                container.add_item(discord.ui.ActionRow(lobby_button))
            container.add_item(discord.ui.TextDisplay("-# Keep materials for crafting or review a sale. Nothing is sold automatically."))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("Open `/mining` for your own Mining Hub.", ephemeral=True)
            return False

    def build_mining_hub(owner_id: int):
        energy = refresh_mining_energy(db, owner_id)
        db.commit()
        player = db.execute("SELECT * FROM players WHERE user_id=?", (owner_id,)).fetchone()
        area = db.execute("SELECT * FROM mining_areas WHERE id=? AND enabled=1", (player["mining_area_id"],)).fetchone()
        pickaxe = db.execute("SELECT * FROM items WHERE id=?", (player["equipped_pickaxe_id"],)).fetchone()
        return MiningHubView(owner_id, player, area, pickaxe, energy)

    class MiningHubView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, player, area, pickaxe, energy: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.dark_gold())
            area_text = f"{area['emoji']} **{area['name']}**" if area else "No area selected"
            pickaxe_text = f"{pickaxe['emoji']} **{pickaxe['name']}**" if pickaxe else "No Pickaxe equipped"
            container.add_item(discord.ui.TextDisplay(
                f"## ⛏️ X BOT Mining Hub\n"
                f"📈 Mining Level **{player['mining_level']}** · EXP **{player['mining_exp']:,}**\n"
                f"### ⚡ Energy\n## {energy} / {setting(db, 'mining_max_energy')}\n"
                f"### Expedition\n🗺️ {area_text}\n🛠️ {pickaxe_text}"
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(
                MiningHubButton("mine", "Mine Now", "⛏️", discord.ButtonStyle.success),
                MiningHubButton("areas", "Areas & Select", "🗺️", discord.ButtonStyle.primary),
                MiningHubButton("help", "Guide", "📖", discord.ButtonStyle.secondary),
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay('### 🎒 Equipment & Materials'))
            container.add_item(discord.ui.ActionRow(
                MiningHubButton("inventory", "Backpack", "🎒", discord.ButtonStyle.secondary),
                MiningHubButton("shop", "Tool Shop", "🏪", discord.ButtonStyle.primary),
                MiningHubButton("sell", "Sell Materials", "💰", discord.ButtonStyle.secondary),
            ))
            container.add_item(discord.ui.ActionRow(
                MiningHubButton("collection", "Collection", "🏆", discord.ButtonStyle.secondary),
                MiningHubButton("profile", "Profile", "📊", discord.ButtonStyle.secondary),
                EconomyCentreButton(owner_id, "Economy"),
                MiningLobbyButton(),
            ))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This Mining Hub belongs to the player who opened it. Open `/menu` for your own hub.", ephemeral=True)
            return False

    @bot.tree.command(name="mining", description="Open the X BOT interactive Mining Hub")
    async def mining(interaction: discord.Interaction):
        await interaction.response.defer()
        create_player(interaction.user)
        await interaction.edit_original_response(view=build_mining_hub(interaction.user.id))

    class RecruitQuantityModal(discord.ui.Modal):
        def __init__(self, unit_id: int):
            unit = db.execute("SELECT * FROM war_unit_types WHERE id=?", (unit_id,)).fetchone()
            name = unit['name']
            super().__init__(title=f"Recruit {name}"[:45])
            self.unit_id = unit_id
            self.quantity_input = discord.ui.TextInput(label="Quantity", placeholder="Enter how many units to recruit", default="1", min_length=1, max_length=7)
            self.add_item(self.quantity_input)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                amount = int(self.quantity_input.value)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message("Quantity must be a whole number greater than 0.", ephemeral=True)
                return
            unit = db.execute("SELECT * FROM war_unit_types WHERE id=? AND enabled=1", (self.unit_id,)).fetchone()
            if unit is None:
                await interaction.response.send_message(view=xbot_ui.danger("Unit Unavailable", "This unit model is no longer enabled."), ephemeral=True)
                return
            name, emoji, unit_cost = unit['name'], unit['emoji'], unit['cost']
            total = unit_cost * amount
            player = create_player(interaction.user)
            if player['money'] < total:
                await interaction.response.send_message(f"You need **{total:,} Cash**.", ephemeral=True)
                return
            await interaction.response.defer()
            await interaction.edit_original_response(view=RecruitConfirmView(interaction.user.id,unit,amount))

    class RecruitConfirmButton(discord.ui.Button):
        def __init__(self,owner_id,unit,amount):
            super().__init__(label='Confirm Recruitment',style=discord.ButtonStyle.success)
            self.owner_id,self.unit_id,self.amount,self.cost=owner_id,unit['id'],amount,int(unit['cost'])
            self.done=False
        async def callback(self,interaction):
            if interaction.user.id!=self.owner_id:
                await interaction.response.send_message('Open /recruit for your own army.',ephemeral=True)
                return
            await interaction.response.defer()
            if self.done:
                await interaction.followup.send('This recruitment was already completed.',ephemeral=True)
                return
            unit=db.execute('SELECT * FROM war_unit_types WHERE id=? AND enabled=1',(self.unit_id,)).fetchone()
            if unit is None or int(unit['cost'])!=self.cost:
                await interaction.edit_original_response(view=ArmyShopView(self.owner_id,notice='Unit or price changed. Choose again; no payment taken.'))
                return
            total=self.cost*self.amount
            changed=db.execute('UPDATE players SET money=money-? WHERE user_id=? AND money>=?',(total,self.owner_id,total))
            if not changed.rowcount:
                await interaction.edit_original_response(view=ArmyShopView(self.owner_id,notice=f'Not enough Cash. Required: {total:,}.'))
                return
            db.execute('''INSERT INTO player_war_units(user_id,unit_type_id,quantity) VALUES(?,?,?)
                ON CONFLICT(user_id,unit_type_id) DO UPDATE SET quantity=quantity+excluded.quantity''',(self.owner_id,self.unit_id,self.amount))
            log(db,self.owner_id,'recruit',f"Recruited {self.amount:,}x {unit['name']} for {total:,} Cash ({self.cost:,} each)")
            db.commit()
            self.done=True
            await interaction.edit_original_response(view=ArmyShopView(self.owner_id,notice=f"✅ Recruited {self.amount:,} × {unit['name']} · Paid {total:,} Cash"))

    class RecruitConfirmView(discord.ui.LayoutView):
        def __init__(self,owner_id,unit,amount):
            super().__init__(timeout=180)
            self.owner_id=owner_id
            container=discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay(f"## 🪖 Review Recruitment\n**{discord.utils.escape_markdown(unit['name'])}** × {amount:,}\nUnit cost **{unit['cost']:,} WC** · Total **{unit['cost']*amount:,} WC**\nAdded power **{unit['power']*amount:,}**\nNothing charged until you confirm."))
            container.add_item(discord.ui.ActionRow(RecruitConfirmButton(owner_id,unit,amount),ArmyWarCentreButton(owner_id)))
            self.add_item(container)
        async def interaction_check(self,interaction):
            return interaction.user.id==self.owner_id

    class RecruitButton(discord.ui.Button):
        def __init__(self, unit):
            name, emoji, cost = unit['name'], unit['emoji'], unit['cost']
            super().__init__(
                label="Choose Quantity",
                emoji=safe_discord_component_emoji(emoji),
                style=discord.ButtonStyle.primary,
            )
            self.unit_id = unit['id']

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.send_modal(RecruitQuantityModal(self.unit_id))

    class ArmyPageButton(discord.ui.Button):
        def __init__(self, owner_id: int, branch: str, category_id: int, target_page: int, sort_mode: str, emoji: str, disabled: bool):
            super().__init__(emoji=emoji, style=discord.ButtonStyle.primary, disabled=disabled)
            self.owner_id, self.branch, self.category_id, self.target_page, self.sort_mode = owner_id, branch, category_id, target_page, sort_mode

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=ArmyShopView(self.owner_id, self.branch, self.category_id, self.target_page, self.sort_mode))

    class ArmyCategorySelect(discord.ui.Select):
        def __init__(self, owner_id: int, branch: str, categories, selected_id: int, sort_mode: str):
            options = [discord.SelectOption(
                label=row['label'],
                value=str(row['id']),
                emoji=safe_discord_component_emoji(row['emoji']),
                default=row['id'] == selected_id,
            ) for row in categories]
            super().__init__(placeholder="Choose a unit class", options=options)
            self.owner_id, self.branch, self.sort_mode = owner_id, branch, sort_mode

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=ArmyShopView(self.owner_id, self.branch, int(self.values[0]), 0, self.sort_mode))

    class ArmyServiceButton(discord.ui.Button):
        SERVICES = {
            "land": ("Land Army", "🪖"),
            "air": ("Air Force", "✈️"),
            "navy": ("Navy", "⚓"),
        }

        def __init__(self, owner_id: int, branch: str, selected: bool, sort_mode: str):
            label, emoji = self.SERVICES[branch]
            super().__init__(label=label, emoji=emoji,
                style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
                disabled=selected)
            self.owner_id, self.branch, self.sort_mode = owner_id, branch, sort_mode

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=ArmyShopView(self.owner_id, self.branch, None, 0, self.sort_mode))

    class ArmyWarCentreButton(discord.ui.Button):
        """Return from Recruit to the same War panel instead of creating another output."""
        def __init__(self, owner_id: int):
            super().__init__(label="War Centre", emoji="⬅️", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("This recruit menu belongs to another player.", ephemeral=True)
                return
            builder = getattr(bot, "xbot_player_panel_builders", {}).get("war")
            if builder is None:
                await interaction.response.send_message("The War Centre is loading. Please try again.", ephemeral=True)
                return
            await interaction.response.edit_message(view=builder(self.owner_id))

    class ArmyShopView(discord.ui.LayoutView):
        SORT_SQL = {
            "manual": "position,name",
            "cost_low": "cost ASC,power ASC,name",
            "cost_high": "cost DESC,power DESC,name",
            "power_high": "power DESC,cost DESC,name",
            "name": "name COLLATE NOCASE",
        }

        def __init__(self, owner_id: int, branch: str = "land", category_id: int | None = None, page: int = 0, sort_mode: str | None = None, notice=None):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            if branch not in {"land", "air", "navy"}:
                branch = "land"
            player = db.execute("SELECT * FROM players WHERE user_id=?", (owner_id,)).fetchone()
            categories = db.execute("""SELECT * FROM war_unit_categories WHERE enabled=1 AND parent_branch=? AND EXISTS(
                SELECT 1 FROM war_unit_types u WHERE u.category_id=war_unit_categories.id AND u.enabled=1)
                ORDER BY position,label LIMIT 25""", (branch,)).fetchall()
            if not categories:
                container = discord.ui.Container(accent_color=discord.Color.dark_red())
                container.add_item(discord.ui.TextDisplay(f"## ⚔️ X BOT Army Recruit\nNo **{branch.title()}** unit classes are enabled yet."))
                container.add_item(discord.ui.ActionRow(*(ArmyServiceButton(owner_id,service,branch==service,sort_mode) for service in ('land','air','navy'))))
                self.add_item(container); return
            if category_id is None:
                configured_default = setting(db, "army_recruit_default_category_id")
                category_id = configured_default if any(row['id'] == configured_default for row in categories) else categories[0]['id']
            elif not any(row['id'] == category_id for row in categories):
                category_id = categories[0]['id']
            if sort_mode not in self.SORT_SQL:
                sort_mode = text_setting(db, "army_recruit_default_sort", "manual")
            if sort_mode not in self.SORT_SQL:
                sort_mode = "manual"
            category = next(row for row in categories if row['id'] == category_id)
            service_name, service_emoji = ArmyServiceButton.SERVICES[branch]
            container = discord.ui.Container(accent_color=discord.Color.dark_red())
            container.add_item(discord.ui.TextDisplay(
                f"## ⚔️ X BOT Army Recruit\n"
                f"### {service_emoji} {service_name} · {category['emoji']} {category['label']}\n"
                f"💵 **{player['money']:,} Cash** available · Choose a unit, then enter quantity."
            ))
            if notice:
                container.add_item(discord.ui.TextDisplay(notice))
            container.add_item(discord.ui.ActionRow(
                ArmyServiceButton(owner_id, "land", branch == "land", sort_mode),
                ArmyServiceButton(owner_id, "air", branch == "air", sort_mode),
                ArmyServiceButton(owner_id, "navy", branch == "navy", sort_mode),
            ))
            container.add_item(discord.ui.ActionRow(ArmyCategorySelect(owner_id, branch, categories, category_id, sort_mode)))
            container.add_item(discord.ui.Separator())
            units = db.execute(f"SELECT * FROM war_unit_types WHERE enabled=1 AND category_id=? ORDER BY {self.SORT_SQL[sort_mode]}", (category_id,)).fetchall()
            pages = max(1, (len(units) + 2) // 3)
            page = max(0, min(page, pages - 1))
            for unit in units[page * 3:(page + 1) * 3]:
                name, emoji, cost, power_value = unit['name'], unit['emoji'], unit['cost'], unit['power']
                button = RecruitButton(unit)
                owned=db.execute('SELECT quantity FROM player_war_units WHERE user_id=? AND unit_type_id=?',(owner_id,unit['id'])).fetchone()
                container.add_item(discord.ui.Section(discord.ui.TextDisplay(f"### {emoji} {discord.utils.escape_markdown(name)}\nOwned **{owned[0] if owned else 0:,}** · Power **{power_value:,}** / unit\nPrice **{cost:,} Cash** / unit"), accessory=button))
                container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(
                ArmyPageButton(owner_id, branch, category_id, 0, sort_mode, "⏪", page == 0),
                ArmyPageButton(owner_id, branch, category_id, page - 1, sort_mode, "◀️", page == 0),
                ArmyPageButton(owner_id, branch, category_id, page + 1, sort_mode, "▶️", page >= pages - 1),
                ArmyPageButton(owner_id, branch, category_id, pages - 1, sort_mode, "⏩", page >= pages - 1),
                ArmyWarCentreButton(owner_id),
            ))
            container.add_item(discord.ui.TextDisplay(f"-# {service_name} · Page {page + 1}/{pages} · Choose Recruit to enter a quantity, or return to War Centre."))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This Army Recruit menu belongs to another player. Use `/army_recruit` for your own menu.", ephemeral=True)
            return False

    # Other player panels (especially /warfront -> Operations) must use this
    # shared builder. ArmyShopView is local to economy.register_commands and
    # cannot be referenced directly from war_tier.py.
    bot.xbot_army_recruit_builder = lambda owner_id: ArmyShopView(owner_id)

    @bot.tree.command(name="army_recruit", description="Open the interactive X BOT Army Recruit menu")
    async def army_recruit(interaction: discord.Interaction):
        # Acknowledge immediately before database queries and Components V2
        # construction. Otherwise Discord expires the token with HTTP 10062.
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(thinking=True)
            except discord.NotFound:
                return
        create_player(interaction.user)
        recruit_view = ArmyShopView(interaction.user.id)
        await interaction.followup.send(view=recruit_view)

    class ArmedForcesServiceButton(discord.ui.Button):
        SERVICE_INFO = {
            "land": ("Land Army", "🪖", discord.Color.dark_red()),
            "air": ("Air Force", "✈️", discord.Color.teal()),
            "navy": ("Navy", "⚓", discord.Color.blue()),
        }

        def __init__(self, owner_id: int, branch: str, selected: bool):
            label, emoji, _ = self.SERVICE_INFO[branch]
            super().__init__(label=label, emoji=emoji,
                style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
                disabled=selected)
            self.owner_id, self.branch = owner_id, branch

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(view=ArmedForcesView(self.owner_id, self.branch))

    class ArmedUnitSelect(discord.ui.Select):
        def __init__(self,owner_id,branch,page,units):
            super().__init__(placeholder='Choose a unit to inspect',options=[discord.SelectOption(label=u['name'][:100],value=str(u['id']),description=f"Owned {u['quantity']:,} · {u['power']:,} power each") for u in units])
            self.owner_id,self.branch,self.page=owner_id,branch,page
        async def callback(self,i):
            await i.response.edit_message(view=ArmedForcesView(self.owner_id,self.branch,self.page,int(self.values[0])))

    class ArmedUnitPage(discord.ui.Button):
        def __init__(self,owner_id,branch,page,label,disabled=False):
            super().__init__(label=label,style=discord.ButtonStyle.secondary,disabled=disabled)
            self.owner_id,self.branch,self.page=owner_id,branch,page
        async def callback(self,i):
            await i.response.edit_message(view=ArmedForcesView(self.owner_id,self.branch,self.page))

    class OpenRecruitButton(discord.ui.Button):
        def __init__(self,owner_id,branch):
            super().__init__(label='Recruit',emoji='🪖',style=discord.ButtonStyle.primary)
            self.owner_id,self.branch=owner_id,branch
        async def callback(self,i):
            await i.response.edit_message(view=ArmyShopView(self.owner_id,self.branch))

    class OpenDivisionsButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Divisions", emoji="📐", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            rows = db.execute("SELECT * FROM division_templates WHERE user_id=? ORDER BY service,name", (self.owner_id,)).fetchall()
            if not rows:
                await interaction.response.send_message(view=xbot_ui.warning(
                    "📐 No Division Templates",
                    "You do not have any saved division templates yet. Create your templates in War Centre."
                ), ephemeral=True)
                return
            import war_tier
            await interaction.response.edit_message(view=war_tier.DivisionTemplateView(self.owner_id, rows, rows[0]["id"]))

    class DemobilizeButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Demobilize", emoji="🔄", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            units = db.execute("""SELECT u.name, u.emoji, p.quantity FROM player_war_units p
                JOIN war_unit_types u ON u.id=p.unit_type_id
                WHERE p.user_id=? AND p.quantity>0 ORDER BY u.name""", (self.owner_id,)).fetchall()
            if not units:
                await interaction.response.send_message("❌ You have no military units to demobilize.", ephemeral=True)
                return
            lines = [f"• {u['emoji']} **{u['name']}**: {u['quantity']:,} units" for u in units]
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🔄 Demobilize Units",
                    description="To retire units and get a 50% War Credit refund, manage your armed forces:\n\n" + "\n".join(lines),
                    color=discord.Color.dark_grey()
                ),
                ephemeral=True
            )

    class ArmedForcesView(discord.ui.LayoutView):
        """The unified replacement for /army, /airforce and /navy viewing."""
        def __init__(self, owner_id: int, branch: str = "land",page=0,selected_unit=None):
            super().__init__(timeout=300)
            # Keep the message private to the player who opened it.
            # The service buttons need this value when Discord checks clicks.
            self.owner_id = owner_id
            if branch not in {"land", "air", "navy"}:
                branch = "land"
            player = db.execute("SELECT * FROM players WHERE user_id=?", (owner_id,)).fetchone()
            if player is None:
                # The slash command always creates the player first; this is
                # only a harmless fallback for an expired/old component.
                container = discord.ui.Container(accent_color=discord.Color.dark_red())
                container.add_item(discord.ui.TextDisplay("## ⚔️ X BOT Armed Forces\nNation profile not found. Please run `/armed_forces` again."))
                self.add_item(container)
                return
            names = {
                "land": "Land Army", "air": "Air Force", "navy": "Navy",
            }
            state = db.execute("SELECT army_name,airforce_name,navy_name,armed_forces_name FROM player_war_settings WHERE user_id=?", (owner_id,)).fetchone()
            if state:
                names["land"] = state["army_name"].strip() or names["land"]
                names["air"] = state["airforce_name"].strip() or names["air"]
                names["navy"] = state["navy_name"].strip() or names["navy"]
                overall_name = state["armed_forces_name"].strip() or "Armed Forces"
            else:
                overall_name = "Armed Forces"
            info = ArmedForcesServiceButton.SERVICE_INFO[branch]
            units = db.execute("""SELECT u.*,COALESCE(p.quantity,0) quantity
                FROM war_unit_types u LEFT JOIN player_war_units p ON p.unit_type_id=u.id AND p.user_id=?
                WHERE u.enabled=1 AND u.branch=? AND COALESCE(p.quantity,0)>0
                ORDER BY u.position,u.name""", (owner_id, branch)).fetchall()
            branch_power = sum(int(unit["quantity"]) * int(unit["power"]) for unit in units)
            service_powers = {}
            for service in ("land", "air", "navy"):
                row = db.execute("""SELECT COALESCE(SUM(p.quantity*u.power),0) total
                    FROM player_war_units p JOIN war_unit_types u ON u.id=p.unit_type_id
                    WHERE p.user_id=? AND u.enabled=1 AND u.branch=?""", (owner_id, service)).fetchone()
                service_powers[service] = int(row["total"])
            container = discord.ui.Container(accent_color=info[2])
            container.add_item(discord.ui.TextDisplay(
                f"## ⚔️ {player['nation_name']} — {overall_name}\n"
                f"Total Power **{sum(service_powers.values()):,}** · Cash **{player['money']:,}**\n\n"
                f"### {info[1]} {names[branch]}\n"
                f"💥 **{branch_power:,} Service Power** · Units **{sum(int(u['quantity']) for u in units):,}**\n\n"
                f"🪖 **{names['land']}** — {service_powers['land']:,} power\n"
                f"✈️ **{names['air']}** — {service_powers['air']:,} power\n"
                f"⚓ **{names['navy']}** — {service_powers['navy']:,} power"
            ))
            container.add_item(discord.ui.ActionRow(
                ArmedForcesServiceButton(owner_id, "land", branch == "land"),
                ArmedForcesServiceButton(owner_id, "air", branch == "air"),
                ArmedForcesServiceButton(owner_id, "navy", branch == "navy"),
                ArmyWarCentreButton(owner_id),
            ))
            container.add_item(discord.ui.Separator())
            if units:
                pages=(len(units)+24)//25
                page=max(0,min(page,pages-1))
                container.add_item(discord.ui.TextDisplay(f'### Unit Details\n{len(units)} models · Page {page+1}/{pages}'))
                container.add_item(discord.ui.ActionRow(ArmedUnitSelect(owner_id,branch,page,units[page*25:(page+1)*25])))
                if pages>1:
                    container.add_item(discord.ui.ActionRow(ArmedUnitPage(owner_id,branch,page-1,'Previous',page==0),ArmedUnitPage(owner_id,branch,page+1,'Next',page==pages-1)))
                selected=next((u for u in units if u['id']==selected_unit),None)
                if selected:
                    container.add_item(discord.ui.TextDisplay(f"**{discord.utils.escape_markdown(selected['name'])}**\nOwned **{selected['quantity']:,}** · Unit Power **{selected['power']:,}**\nTotal Power **{selected['quantity']*selected['power']:,}**\n{discord.utils.escape_markdown(selected['description'][:250])}"))
            else:
                container.add_item(discord.ui.TextDisplay(f"### No {names[branch]} units yet\nOpen Recruit to choose your first unit."))
            container.add_item(discord.ui.ActionRow(
                OpenRecruitButton(owner_id, branch),
                OpenDivisionsButton(owner_id),
                DemobilizeButton(owner_id),
            ))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This Armed Forces menu belongs to another player.", ephemeral=True)
            return False

    bot.xbot_armed_forces_builder = lambda owner_id: ArmedForcesView(owner_id)

    # Discord's global command catalogue is already full, therefore this
    # player command is registered directly to X Community like the staff
    # tools. It remains available to every member of this server.
    _guild_id = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
    _armed_forces_kwargs = {"guild": discord.Object(id=_guild_id)} if _guild_id else {}

    @bot.tree.command(name="armed_forces", description="View your Land Army, Air Force and Navy", **_armed_forces_kwargs)
    async def armed_forces(interaction: discord.Interaction):
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(thinking=True)
            except discord.NotFound:
                return
        create_player(interaction.user)
        await interaction.followup.send(view=ArmedForcesView(interaction.user.id, "land"))

    class ExchangeModal(discord.ui.Modal):
        def __init__(self, direction: str):
            self.direction = direction
            source = "XC" if direction == "xc_to_war" else "Cash"
            super().__init__(title=f"Exchange {source}")
            self.amount_input = discord.ui.TextInput(label=f"{source} amount", placeholder="Enter amount", min_length=1, max_length=12)
            self.add_item(self.amount_input)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                amount = int(self.amount_input.value)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message("Amount must be a whole number greater than 0.", ephemeral=True)
                return
            player = create_player(interaction.user)
            if self.direction == "xc_to_war":
                source_column, target_column = "xc", "money"
                source_name, target_name = "XC", "Cash"
                rate = setting(db, "exchange_xc_to_war_percent")
                gross_received = amount * rate // 100
                spent = amount
            else:
                source_column, target_column = "money", "xc"
                source_name, target_name = "Cash", "XC"
                gross_received = amount // 1000
                spent = gross_received * 1000

            if gross_received <= 0:
                await interaction.response.send_message("This amount is too small for the current exchange rate (minimum 1,000 Cash).", ephemeral=True)
                return
            if player[source_column] < spent:
                await interaction.response.send_message(f"You do not have enough **{source_name}**.", ephemeral=True)
                return

            # 6% transaction tax if amount > 1000
            tax_rate = 6 if amount > 1000 else 0
            tax = gross_received * tax_rate // 100
            received = gross_received - tax
            if received <= 0:
                await interaction.response.send_message("This amount is too small after exchange tax.", ephemeral=True)
                return

            db.execute(f"UPDATE players SET {source_column}={source_column}-?, {target_column}={target_column}+? WHERE user_id=?", (spent, received, interaction.user.id))
            log(db, interaction.user.id, "exchange", f"{spent} {source_name} -> {received} {target_name} (tax {tax})")
            db.commit()
            tax_note = f"\n-# 🏛️ Exchange tax (6% on amounts > 1,000): **{tax:,} {target_name}** deducted." if tax > 0 else ""
            await interaction.response.send_message(view=xbot_ui.success("🔄 Exchange Complete", f"**{spent:,} {source_name}** → **{received:,} {target_name}**{tax_note}"), ephemeral=True)

    class ExchangeButton(discord.ui.Button):
        def __init__(self, direction: str):
            label = "XC → Cash" if direction == "xc_to_war" else "Cash → XC"
            super().__init__(label=label, emoji="🔄", style=discord.ButtonStyle.primary)
            self.direction = direction

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.send_modal(ExchangeModal(self.direction))

    class ExchangeView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            player = db.execute("SELECT * FROM players WHERE user_id=?", (owner_id,)).fetchone()
            xc_rate = setting(db, "exchange_xc_to_war_percent")
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay(f"## 🔄 X BOT Currency Exchange\n🪙 XC: **{player['xc']:,}**\n💵 Cash: **{player['money']:,}**"))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"**Exchange Rates**\n🪙 1 XC → **{max(1, xc_rate // 100):,} Cash**\n💵 1,000 Cash → **1 XC**\n-# 🏛️ Transactions over 1,000 have a 6% tax."))
            container.add_item(discord.ui.ActionRow(ExchangeButton("xc_to_war"), ExchangeButton("war_to_xc"), EconomyCentreButton(owner_id, "Economy")))
            container.add_item(discord.ui.TextDisplay("-# XCrystals cannot be exchanged."))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("Open `/menu` to use your own exchange menu.", ephemeral=True)
            return False

    @bot.tree.command(name="exchange", description="Exchange XC and Cash")
    async def exchange(interaction: discord.Interaction):
        create_player(interaction.user)
        await interaction.response.send_message(view=ExchangeView(interaction.user.id))

    # The Economy Centre is intentionally the only player entry command.  These
    # factories let its buttons replace that same Discord message with a child
    # panel instead of invoking a slash command and creating another output.
    def _mining_panel(owner_id: int):
        energy = refresh_mining_energy(db, owner_id)
        db.commit()
        player = db.execute("SELECT * FROM players WHERE user_id=?", (owner_id,)).fetchone()
        area = db.execute("SELECT * FROM mining_areas WHERE id=? AND enabled=1", (player['mining_area_id'],)).fetchone()
        pickaxe = db.execute("SELECT * FROM items WHERE id=?", (player['equipped_pickaxe_id'],)).fetchone()
        return MiningHubView(owner_id, player, area, pickaxe, energy)

    def _shop_panel(owner_id: int):
        categories = shop_categories()
        if not categories:
            return xbot_ui.warning("🏪 Economy Shop", "The Economy Shop is empty right now.")
        return ShopView(owner_id, categories[0]['id'])

    bot.xbot_player_panel_builders = getattr(bot, "xbot_player_panel_builders", {})
    bot.xbot_mine_button_builder = lambda: MiningHubButton('mine','Mine Now','⛏️',discord.ButtonStyle.success)
    bot.xbot_player_panel_builders.update({
        "shop": _shop_panel,
        "inventory": lambda owner_id: InventoryView(owner_id),
        "mining": _mining_panel,
        "exchange": lambda owner_id: ExchangeView(owner_id),
        "army": lambda owner_id: ArmedForcesView(owner_id),
        "recruit": lambda owner_id: ArmyShopView(owner_id),
    })
