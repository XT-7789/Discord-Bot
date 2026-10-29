"""Update xp_rewards table with finalized level role design."""
import sqlite3

DB = "xwar.db"

# Final level reward design
# (level, role_name, role_id)
# role_ids from discord_resource_cache confirmed
LEVEL_ROLES = [
    (2,   "👤 Member",    1505437941647015986),
    (5,   "🎵 Music",     1505437186219311236),
    (10,  "⭐ Elite",     1522478703819362334),
    (20,  "🏆 Senior",    1526851111128928307),
    (40,  "💗 Mythic",    1526851320118509648),
    (60,  "👑 Titan",     1534198146807234722),
    (80,  "🌌 Ascendant", 1534198492656959616),
    (100, "💠 Legend",    1534198526571970701),
]

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("Current xp_rewards:")
for row in c.execute("SELECT level, role_name, role_id, xc_reward, xp_bonus FROM xp_rewards ORDER BY level"):
    print(f"  Lv{row['level']:>3}: {row['role_name']} | role={row['role_id']}")

print()
print("Applying updates...")

for level, role_name, role_id in LEVEL_ROLES:
    existing = c.execute("SELECT id FROM xp_rewards WHERE level=?", (level,)).fetchone()
    if existing:
        c.execute(
            "UPDATE xp_rewards SET role_name=?, role_id=?, xc_reward=0, xp_bonus=0, item_reward=0, enabled=1 WHERE level=?",
            (role_name, role_id, level)
        )
        print(f"  UPDATED Lv{level}: {role_name}")
    else:
        c.execute(
            "INSERT INTO xp_rewards(level, role_name, role_id, xc_reward, xp_bonus, item_reward, enabled) VALUES(?,?,?,0,0,0,1)",
            (level, role_name, role_id)
        )
        print(f"  INSERTED Lv{level}: {role_name}")

# Remove streak roles from discord_resource_cache (they are deleted from Discord)
deleted_ids = [1536374663263821904, 1536374678715899955, 1536374745933545563]
for rid in deleted_ids:
    c.execute("DELETE FROM discord_resource_cache WHERE resource_id=?", (rid,))
    print(f"  REMOVED cached role {rid} (deleted streak role)")

conn.commit()
conn.close()

print()
print("Done! Final xp_rewards:")
conn2 = sqlite3.connect(DB)
conn2.row_factory = sqlite3.Row
for row in conn2.execute("SELECT level, role_name, role_id, enabled FROM xp_rewards ORDER BY level"):
    print(f"  Lv{row['level']:>3}: {row['role_name']} | role={row['role_id']} | enabled={row['enabled']}")
conn2.close()
