"""Update xp_rewards table with finalized level role design."""
import sqlite3
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

DB = "xwar.db"

# (level, name, emoji, role_id)
LEVEL_ROLES = [
    (2,   "Member",    "👤", 1505437941647015986),
    (5,   "Music",     "🎵", 1505437186219311236),
    (10,  "Elite",     "⭐", 1522478703819362334),
    (20,  "Senior",    "🏆", 1526851111128928307),
    (40,  "Mythic",    "💗", 1526851320118509648),
    (60,  "Titan",     "👑", 1534198146807234722),
    (80,  "Ascendant", "🌌", 1534198492656959616),
    (100, "Legend",    "💠", 1534198526571970701),
]

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Disable any roles not in LEVEL_ROLES (e.g. Lv 7)
active_levels = [item[0] for item in LEVEL_ROLES]
c.execute(f"UPDATE xp_rewards SET enabled=0 WHERE level NOT IN ({','.join('?'*len(active_levels))})", active_levels)

for level, name, emoji, role_id in LEVEL_ROLES:
    existing = c.execute("SELECT id FROM xp_rewards WHERE level=?", (level,)).fetchone()
    if existing:
        c.execute(
            "UPDATE xp_rewards SET name=?, emoji=?, role_id=?, xc=0, war_credits=0, item_id=NULL, item_quantity=0, enabled=1 WHERE level=?",
            (name, emoji, role_id, level)
        )
        print(f"Updated Lv{level}: {name} ({emoji}) role={role_id}")
    else:
        c.execute(
            "INSERT INTO xp_rewards(level, name, emoji, role_id, enabled, xc, war_credits, item_quantity, reward_mode) VALUES(?,?,?,?,1,0,0,0,'role')",
            (level, name, emoji, role_id)
        )
        print(f"Inserted Lv{level}: {name} ({emoji}) role={role_id}")

# Remove streak roles from discord_resource_cache
deleted_ids = [1536374663263821904, 1536374678715899955, 1536374745933545563]
for rid in deleted_ids:
    c.execute("DELETE FROM discord_resource_cache WHERE resource_id=?", (rid,))
    print(f"Cleaned deleted role cache: {rid}")

# Disable streak_rewards table rewards
try:
    c.execute("UPDATE streak_rewards SET enabled=0")
except Exception:
    pass

conn.commit()
print("\nFinal xp_rewards:")
for row in c.execute("SELECT level, name, emoji, role_id, enabled FROM xp_rewards ORDER BY level"):
    print(f"  Lv{row['level']:>3}: {row['emoji']} {row['name']} | role={row['role_id']} | enabled={row['enabled']}")
conn.close()
