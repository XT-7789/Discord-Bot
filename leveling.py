"""X BOT Beta 1.4A activity XP, ranks, rewards, and announcements."""
import math
import random
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import tasks

import xbot_ui

REGULAR_MUSIC_ROLE_ID = 1505437186219311236
PREMIUM_MUSIC_ROLE_ID = 1526237128093339848

DEFAULTS = {
    "xp_enabled": "1", "xp_message_enabled": "1", "xp_message_min": "15", "xp_message_max": "25",
    "xp_message_cooldown": "60", "xp_voice_enabled": "1", "xp_voice_per_minute": "5",
    "xp_ignored_channel_ids": "", "xp_ignored_role_ids": "", "xp_announcement_enabled": "1",
    "xp_announcement_channel_id": "0", "xp_announcement_template": "🎉 **LEVEL UP!** {mention} reached **Level {level}**! {reward}",
    "xp_message_cash_min": "100", "xp_message_cash_max": "300",
    "xp_message_lucky_chance_percent": "5", "xp_message_lucky_xc_min": "1", "xp_message_lucky_xc_max": "5",
}

def initialise(db):
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))
    db.execute("""CREATE TABLE IF NOT EXISTS xp_profiles(
        user_id INTEGER PRIMARY KEY,total_xp INTEGER NOT NULL DEFAULT 0,weekly_xp INTEGER NOT NULL DEFAULT 0,
        level INTEGER NOT NULL DEFAULT 1,message_xp INTEGER NOT NULL DEFAULT 0,voice_xp INTEGER NOT NULL DEFAULT 0,
        last_message_xp INTEGER NOT NULL DEFAULT 0,last_voice_xp INTEGER NOT NULL DEFAULT 0,last_week_key TEXT NOT NULL DEFAULT '')""")
    db.execute("""CREATE TABLE IF NOT EXISTS xp_rewards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,level INTEGER NOT NULL,role_id TEXT NOT NULL DEFAULT '',name TEXT NOT NULL,
        emoji TEXT NOT NULL DEFAULT '🏅',reward_mode TEXT NOT NULL DEFAULT 'exclusive',announcement TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,UNIQUE(level,role_id))""")
    reward_columns = {row["name"] for row in db.execute("PRAGMA table_info(xp_rewards)")}
    for name, definition in {
        "item_id": "INTEGER", "item_quantity": "INTEGER NOT NULL DEFAULT 0",
        "xc": "INTEGER NOT NULL DEFAULT 0", "war_credits": "INTEGER NOT NULL DEFAULT 0",
        "xcrystals": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in reward_columns:
            db.execute(f"ALTER TABLE xp_rewards ADD COLUMN {name} {definition}")
    profile_columns = {row["name"] for row in db.execute("PRAGMA table_info(xp_profiles)")}
    for name, definition in {
        "current_streak": "INTEGER NOT NULL DEFAULT 0", "best_streak": "INTEGER NOT NULL DEFAULT 0",
        "last_active_day": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in profile_columns:
            db.execute(f"ALTER TABLE xp_profiles ADD COLUMN {name} {definition}")
    db.execute("""CREATE TABLE IF NOT EXISTS streak_rewards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,days INTEGER NOT NULL UNIQUE,item_id INTEGER,
        item_quantity INTEGER NOT NULL DEFAULT 0,xc INTEGER NOT NULL DEFAULT 0,
        war_credits INTEGER NOT NULL DEFAULT 0,xcrystals INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1
    )""")
    seed_rewards = [
        (2, "Member", "👤", "permanent"), (5, "Music", "🎵", "permanent"),
        (7, "Active", "✨", "exclusive"), (10, "Elite", "⭐", "exclusive"),
        (20, "Senior", "🏆", "exclusive"), (40, "Mythic", "💗", "exclusive"),
        (60, "Titan", "👑", "exclusive"), (80, "Ascendant", "🚀", "exclusive"),
        (100, "Legend", "💎", "exclusive"),
    ]
    definition_marker = db.execute("SELECT value FROM economy_settings WHERE key='beta_1_4a_reward_definitions_seeded'").fetchone()
    if definition_marker is None:
        for level, name, emoji, reward_mode in seed_rewards:
            if db.execute("SELECT 1 FROM xp_rewards WHERE level=? AND name=? COLLATE NOCASE", (level, name)).fetchone() is None:
                db.execute("INSERT INTO xp_rewards(level,role_id,name,emoji,reward_mode) VALUES(?,'',?,?,?)", (level, name, emoji, reward_mode))
        db.execute("INSERT INTO economy_settings(key,value) VALUES('beta_1_4a_reward_definitions_seeded','1')")
    role_marker = db.execute("SELECT value FROM economy_settings WHERE key='beta_1_4a_reward_roles_seeded'").fetchone()
    if role_marker is None:
        reward_roles = {
            2: "1505437941647015986", 5: "1505437186219311236", 7: "1524719900785119354",
            10: "1522478703819362334", 20: "1526851111128928307", 40: "1526851320118509648",
            60: "1534198146807234722", 80: "1534198492656959616", 100: "1534198526571970701",
        }
        for level, role_id in reward_roles.items():
            db.execute("UPDATE xp_rewards SET role_id=? WHERE level=? AND role_id=''", (role_id, level))
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES('verification_guest_role_id','1524715220365217842')")
        db.execute("INSERT INTO economy_settings(key,value) VALUES('beta_1_4a_reward_roles_seeded','1')")
    for command_name in ("setlevel", "setannouncement", "announcementshow", "setannouncementchat"):
        db.execute("INSERT OR IGNORE INTO command_permissions(command_name,access_mode) VALUES(?,'admin')", (command_name,))
        db.execute("UPDATE command_permissions SET access_mode='admin' WHERE command_name=?", (command_name,))
    db.commit()

def setting(db, key):
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS[key]

def level_for_xp(xp):
    return max(1, int(math.sqrt(max(0, xp - 35) / 20)) + 1)

def xp_for_level(level):
    return 0 if level <= 1 else (level - 1) ** 2 * 20 + 35

def _ids(value):
    return {part.strip() for part in str(value or "").split(",") if part.strip()}

def profile(db, user_id):
    row = db.execute("SELECT * FROM xp_profiles WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        db.execute("INSERT INTO xp_profiles(user_id,last_week_key) VALUES(?,strftime('%Y-W%W','now'))", (user_id,))
        db.commit()
        row = db.execute("SELECT * FROM xp_profiles WHERE user_id=?", (user_id,)).fetchone()
    return row

def _ensure_economy_player(db, member):
    db.execute("""INSERT OR IGNORE INTO players(user_id,nation_name,capital_name)
        VALUES(?,?,?)""", (member.id, f"{member.display_name}'s Nation", f"{member.display_name} Capital"))
    db.execute("UPDATE players SET display_name=? WHERE user_id=?", (member.display_name, member.id))

def _grant_value_reward(db, member, reward, reason):
    """Grant configured currency/item values without replacing any existing settings."""
    _ensure_economy_player(db, member)
    xc = max(0, int(reward["xc"] or 0)); war = max(0, int(reward["war_credits"] or 0)); crystals = max(0, int(reward["xcrystals"] or 0))
    db.execute("UPDATE players SET xc=xc+?,money=money+?,xcrystals=xcrystals+? WHERE user_id=?", (xc, war, crystals, member.id))
    item_id = reward["item_id"]; quantity = max(0, int(reward["item_quantity"] or 0))
    if item_id and quantity and db.execute("SELECT 1 FROM items WHERE id=? AND enabled=1", (item_id,)).fetchone():
        db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
            ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""", (member.id, item_id, quantity))
    details = []
    if xc: details.append(f"{xc} XC")
    if war: details.append(f"{war} War Credits")
    if crystals: details.append(f"{crystals} XCrystals")
    if item_id and quantity: details.append(f"{quantity} item(s) #{item_id}")
    if details:
        db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)", (member.id, reason, ", ".join(details), int(time.time())))
    return details

def _update_activity_streak(db, member):
    row = profile(db, member.id); today = int(time.time() // 86400)
    if row["last_active_day"] == today:
        return row["current_streak"], []
    streak = row["current_streak"] + 1 if row["last_active_day"] == today - 1 else 1
    best = max(row["best_streak"], streak)
    db.execute("UPDATE xp_profiles SET current_streak=?,best_streak=?,last_active_day=? WHERE user_id=?", (streak, best, today, member.id))
    reward = db.execute("SELECT * FROM streak_rewards WHERE enabled=1 AND days=?", (streak,)).fetchone()
    details = _grant_value_reward(db, member, reward, f"activity_streak_{streak}") if reward else []
    return streak, details

async def _apply_rewards(db, member, old_level, new_level):
    if not isinstance(member, discord.Member) or new_level <= old_level:
        return []
    earned = db.execute("SELECT * FROM xp_rewards WHERE enabled=1 AND level>? AND level<=? ORDER BY level", (old_level, new_level)).fetchall()
    exclusive = [row for row in db.execute("SELECT * FROM xp_rewards WHERE enabled=1 AND reward_mode='exclusive' AND role_id!='' AND level<=? ORDER BY level", (new_level,)).fetchall()]
    newest = exclusive[-1] if exclusive else None
    exclusive_ids = {int(row["role_id"]) for row in exclusive}
    remove = [role for role in member.roles if role.id in exclusive_ids and (newest is None or role.id != int(newest["role_id"]))]
    add = []
    for row in earned:
        _grant_value_reward(db, member, row, f"level_reward_{row['level']}")
        if not row["role_id"] or (row["reward_mode"] == "exclusive" and (newest is None or row["id"] != newest["id"])):
            continue
        role = member.guild.get_role(int(row["role_id"]))
        if role and role not in member.roles:
            add.append(role)
    # Release SQLite before waiting for Discord role API requests.
    db.commit()
    try:
        if remove: await member.remove_roles(*remove, reason="X BOT level title upgrade")
        if add: await member.add_roles(*add, reason="X BOT level reward")
        if new_level >= 2:
            guest_setting = db.execute("SELECT value FROM economy_settings WHERE key='verification_guest_role_id'").fetchone()
            guest = member.guild.get_role(int(guest_setting["value"])) if guest_setting and guest_setting["value"].isdigit() else None
            if guest and guest in member.roles: await member.remove_roles(guest, reason="Reached X BOT Level 2")
    except discord.HTTPException:
        pass
    return earned

async def sync_reward_roles(db, member, level):
    """Make configured reward roles exactly match a member's current level."""
    if not isinstance(member, discord.Member): return
    rows = db.execute("SELECT * FROM xp_rewards WHERE enabled=1 AND role_id!='' ORDER BY level", ()).fetchall()
    permanent = [row for row in rows if row["reward_mode"] == "permanent" and row["level"] <= level]
    titles = [row for row in rows if row["reward_mode"] == "exclusive" and row["level"] <= level]
    desired_ids = {int(row["role_id"]) for row in permanent}
    if titles: desired_ids.add(int(titles[-1]["role_id"]))
    configured_ids = {int(row["role_id"]) for row in rows}

    # If member holds Premium Music, regular Music is superseded and must be removed
    if any(role.id == PREMIUM_MUSIC_ROLE_ID for role in member.roles):
        desired_ids.discard(REGULAR_MUSIC_ROLE_ID)
        configured_ids.add(REGULAR_MUSIC_ROLE_ID)

    remove = [role for role in member.roles if role.id in configured_ids and role.id not in desired_ids]
    add = [member.guild.get_role(role_id) for role_id in desired_ids if member.guild.get_role(role_id) and member.guild.get_role(role_id) not in member.roles]
    try:
        if remove: await member.remove_roles(*remove, reason="X BOT level synchronization")
        if add: await member.add_roles(*add, reason="X BOT level synchronization")
        if level >= 2:
            guest_setting = db.execute("SELECT value FROM economy_settings WHERE key='verification_guest_role_id'").fetchone()
            guest = member.guild.get_role(int(guest_setting["value"])) if guest_setting and guest_setting["value"].isdigit() else None
            if guest and guest in member.roles: await member.remove_roles(guest, reason="Reached X BOT Level 2")
    except discord.HTTPException:
        pass


async def sync_guild_member_levels(bot, db, guild):
    """Scan all members in a guild and align database XP profiles with their highest Discord level roles."""
    if not guild:
        return 0

    reward_rows = db.execute("SELECT level, role_id FROM xp_rewards WHERE enabled=1 AND role_id!=''").fetchall()
    role_to_level = {int(r["role_id"]): int(r["level"]) for r in reward_rows if str(r["role_id"]).isdigit()}

    updated_count = 0
    for member in guild.members:
        if member.bot:
            continue

        member_role_ids = {r.id for r in member.roles}
        held_levels = [role_to_level[rid] for rid in member_role_ids if rid in role_to_level]
        if not held_levels:
            continue

        max_role_level = max(held_levels)
        prof = profile(db, member.id)
        current_level = int(prof["level"] if prof else 1)

        if max_role_level > current_level:
            target_xp = xp_for_level(max_role_level)
            db.execute(
                """INSERT INTO xp_profiles(user_id, level, total_xp) VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                level=MAX(level, excluded.level),
                total_xp=MAX(total_xp, excluded.total_xp)""",
                (member.id, max_role_level, target_xp),
            )
            updated_count += 1
            await sync_reward_roles(db, member, max_role_level)

    db.commit()
    return updated_count

async def _announce(bot, db, member, new_level, rewards):
    if setting(db, "xp_announcement_enabled") != "1": return
    reward_text = ", ".join(f"{r['emoji']} **{r['name']}**" for r in rewards) or ""
    custom = next((r["announcement"] for r in reversed(rewards) if r["announcement"]), "")
    text = (custom or setting(db, "xp_announcement_template")).replace("{mention}", member.mention).replace("{user}", member.display_name).replace("{level}", str(new_level)).replace("{reward}", reward_text)
    channel_id = int(setting(db, "xp_announcement_channel_id") or 0)
    channel = bot.get_channel(channel_id) if channel_id else member.guild.system_channel
    if channel:
        try: await channel.send(view=xbot_ui.success(f"🎉 Level {new_level} Announcement", text))
        except discord.HTTPException: pass

async def grant_xp(bot, db, member, amount, source):
    if amount <= 0: return
    row = profile(db, member.id); old_level = row["level"]; week_key = time.strftime("%Y-W%W", time.gmtime())
    weekly = 0 if row["last_week_key"] != week_key else row["weekly_xp"]
    total = row["total_xp"] + amount; new_level = level_for_xp(total)
    field = "message_xp" if source == "message" else "voice_xp"; timestamp = "last_message_xp" if source == "message" else "last_voice_xp"
    db.execute(f"UPDATE xp_profiles SET total_xp=?,weekly_xp=?,level=?,{field}={field}+?,{timestamp}=?,last_week_key=? WHERE user_id=?", (total, weekly + amount, new_level, amount, int(time.time()), week_key, member.id))
    _update_activity_streak(db, member); db.commit()
    if new_level > old_level:
        rewards = await _apply_rewards(db, member, old_level, new_level)
        await _announce(bot, db, member, new_level, rewards)

async def handle_message(bot, db, message):
    if not message.guild or message.author.bot or setting(db, "xp_enabled") != "1" or setting(db, "xp_message_enabled") != "1": return
    if str(message.channel.id) in _ids(setting(db, "xp_ignored_channel_ids")): return
    if {str(role.id) for role in message.author.roles} & _ids(setting(db, "xp_ignored_role_ids")): return
    row = profile(db, message.author.id); now = int(time.time())
    if now - row["last_message_xp"] < int(setting(db, "xp_message_cooldown")): return
    low = int(setting(db, "xp_message_min")); high = max(low, int(setting(db, "xp_message_max")))
    await grant_xp(bot, db, message.author, random.randint(low, high), "message")

    # Award Chat Cash Drop
    _ensure_economy_player(db, message.author)
    cash_low = int(setting(db, "xp_message_cash_min") or 100)
    cash_high = max(cash_low, int(setting(db, "xp_message_cash_max") or 300))
    cash_reward = random.randint(cash_low, cash_high)
    db.execute("UPDATE players SET money=money+? WHERE user_id=?", (cash_reward, message.author.id))

    # 5% Lucky Drop (1~5 XC)
    lucky_chance = int(setting(db, "xp_message_lucky_chance_percent") or 5)
    if lucky_chance > 0 and random.randint(1, 100) <= lucky_chance:
        xc_low = int(setting(db, "xp_message_lucky_xc_min") or 1)
        xc_high = max(xc_low, int(setting(db, "xp_message_lucky_xc_max") or 5))
        xc_reward = random.randint(xc_low, xc_high)
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (xc_reward, message.author.id))
        db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
                   (message.author.id, "chat_lucky_drop", f"+{xc_reward} XC (Chat Lucky Drop)", int(time.time())))
        try:
            await message.add_reaction("🪙")
        except (discord.HTTPException, discord.Forbidden):
            pass

    db.commit()

def start_voice_task(bot, db):
    if not voice_xp_loop.is_running():
        voice_xp_loop.bot = bot; voice_xp_loop.db = db; voice_xp_loop.start()

@tasks.loop(minutes=1)
async def voice_xp_loop():
    bot = voice_xp_loop.bot; db = voice_xp_loop.db
    if setting(db, "xp_enabled") != "1" or setting(db, "xp_voice_enabled") != "1": return
    ignored_channels = _ids(setting(db, "xp_ignored_channel_ids")); ignored_roles = _ids(setting(db, "xp_ignored_role_ids")); amount = int(setting(db, "xp_voice_per_minute"))
    for guild in bot.guilds:
        for channel in guild.voice_channels:
            if str(channel.id) in ignored_channels: continue
            for member in channel.members:
                if member.bot or not member.voice or member.voice.self_deaf or member.voice.afk: continue
                if {str(role.id) for role in member.roles} & ignored_roles: continue
                await grant_xp(bot, db, member, amount, "voice")

@voice_xp_loop.before_loop
async def before_voice_xp():
    await voice_xp_loop.bot.wait_until_ready()

def register_commands(bot, db, is_council_or_admin=None):
    @bot.tree.command(name="rank", description="View your X BOT activity rank")
    @app_commands.describe(member="Leave empty to view your own rank")
    async def rank(interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user; row = profile(db, target.id); db.commit()
        server_rank = db.execute("SELECT COUNT(*)+1 rank FROM xp_profiles WHERE total_xp>?", (row["total_xp"],)).fetchone()["rank"]
        current = xp_for_level(row["level"]); nxt = xp_for_level(row["level"] + 1); progress = row["total_xp"] - current
        body = f"🏆 **Server Rank:** #{server_rank}\n⭐ **Level:** {row['level']}\n📈 **EXP:** {row['total_xp']:,} · {progress:,}/{nxt-current:,} to next level\n🔥 Activity Streak: **{row['current_streak']} days** · Best: **{row['best_streak']}**\n💬 Message EXP: **{row['message_xp']:,}** · 🔊 Voice EXP: **{row['voice_xp']:,}**\n📅 Weekly EXP: **{row['weekly_xp']:,}**"
        await interaction.response.send_message(view=xbot_ui.panel(f"⭐ {target.display_name}'s Rank", body, colour=discord.Color.gold()))

    @bot.tree.command(name="level", description="Check your own or another user's activity level and XP")
    @app_commands.describe(member="Member whose level to check (leave empty for yourself)")
    async def level(interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        row = profile(db, target.id)
        db.commit()
        server_rank = db.execute("SELECT COUNT(*)+1 rank FROM xp_profiles WHERE total_xp>?", (row["total_xp"],)).fetchone()["rank"]
        current = xp_for_level(row["level"])
        nxt = xp_for_level(row["level"] + 1)
        progress = max(0, row["total_xp"] - current)
        needed = max(1, nxt - current)
        pct = min(100, int((progress / needed) * 100))

        unlocked = db.execute(
            "SELECT name, emoji FROM xp_rewards WHERE enabled=1 AND level<=? ORDER BY level DESC LIMIT 1",
            (row["level"],),
        ).fetchone()
        title_text = f"{unlocked['emoji']} {unlocked['name']}" if unlocked else "👤 Novice / Guest"

        next_reward = db.execute(
            "SELECT level, name, emoji FROM xp_rewards WHERE enabled=1 AND level>? ORDER BY level ASC LIMIT 1",
            (row["level"],),
        ).fetchone()
        next_text = f"Level {next_reward['level']} ({next_reward['emoji']} {next_reward['name']})" if next_reward else "Max Level Reached! 👑"

        dz_status = "🟢 Active"
        try:
            dz_row = db.execute("SELECT is_in_deadzone FROM deadzone_members WHERE user_id=?", (target.id,)).fetchone()
            if dz_row and dz_row["is_in_deadzone"]:
                dz_status = "💀 In Deadzone"
        except sqlite3.OperationalError:
            pass

        body = (
            f"⭐ **Current Level:** **Level {row['level']}** ({title_text})\n"
            f"🏆 **Server Rank:** #{server_rank}\n"
            f"📈 **Total EXP:** **{row['total_xp']:,} XP**\n"
            f"📊 **Progress to Level {row['level'] + 1}:** {progress:,} / {needed:,} XP (`{pct}%`)\n"
            f"🎁 **Next Unlock:** {next_text}\n"
            f"🔥 **Activity Streak:** **{row['current_streak']} days** (Best: {row['best_streak']})\n"
            f"📡 **Status:** {dz_status}\n\n"
            f"💬 Messages: `{row['message_xp']:,} XP` · 🔊 Voice: `{row['voice_xp']:,} XP`"
        )
        await interaction.response.send_message(
            view=xbot_ui.panel(f"⭐ {target.display_name}'s Level & XP", body, colour=discord.Color.gold())
        )

    @bot.tree.command(name="level_leaderboard", description="View the X BOT activity leaderboard")
    @app_commands.choices(period=[app_commands.Choice(name="All Time", value="total_xp"), app_commands.Choice(name="Weekly", value="weekly_xp")])
    async def level_leaderboard(interaction: discord.Interaction, period: app_commands.Choice[str] | None = None):
        column = period.value if period else "total_xp"
        rows = db.execute(f"SELECT x.* FROM xp_profiles x ORDER BY x.{column} DESC LIMIT 10").fetchall(); medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i<3 else f'**#{i+1}**'} <@{r['user_id']}> · Level **{r['level']}** · {r[column]:,} EXP" for i, r in enumerate(rows)]
        await interaction.response.send_message(view=xbot_ui.panel("🏆 X BOT Level Leaderboard", "\n".join(lines) or "No activity XP has been earned yet.", colour=discord.Color.gold(), footer="Weekly XP resets automatically by calendar week."))

    @bot.tree.command(name="level_rewards", description="View X BOT level reward roles")
    async def level_rewards(interaction: discord.Interaction):
        rows = db.execute("SELECT * FROM xp_rewards WHERE enabled=1 ORDER BY level").fetchall()
        lines = []
        for r in rows:
            rewards = []
            if r["role_id"]: rewards.append(f"<@&{r['role_id']}>")
            if r["item_id"] and r["item_quantity"]: rewards.append(f"{r['item_quantity']}x item #{r['item_id']}")
            if r["xc"]: rewards.append(f"{r['xc']} XC")
            if r["war_credits"]: rewards.append(f"{r['war_credits']} War Credits")
            if r["xcrystals"]: rewards.append(f"{r['xcrystals']} XCrystals")
            lines.append(f"{r['emoji']} **Level {r['level']} — {r['name']}** · " + (", ".join(rewards) or "No configured payout"))
        await interaction.response.send_message(view=xbot_ui.panel("🎁 X BOT Level Rewards", "\n".join(lines) or "No level rewards are configured yet.", colour=discord.Color.purple()))

    @bot.tree.command(name="setlevel", description="Admin: set a member's X BOT activity level")
    @app_commands.default_permissions(administrator=True)
    async def setlevel(interaction: discord.Interaction, member: discord.Member, level: app_commands.Range[int, 1, 1000]):
        total = xp_for_level(level); profile(db, member.id)
        db.execute("UPDATE xp_profiles SET total_xp=?,level=? WHERE user_id=?", (total, level, member.id)); db.commit()
        await sync_reward_roles(db, member, level)
        await interaction.response.send_message(view=xbot_ui.success("⭐ Level Updated", f"{member.mention} is now **Level {level}** with **{total:,} XP**."), ephemeral=True)

    @bot.tree.command(name="level_sync", description="Admin: Sync all members' database XP levels with their Discord roles")
    async def level_sync_command(interaction: discord.Interaction):
        can_run = False
        if getattr(interaction.user, "guild_permissions", None) and interaction.user.guild_permissions.administrator:
            can_run = True
        elif is_council_or_admin and is_council_or_admin(interaction):
            can_run = True

        if not can_run:
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators can synchronize levels."), ephemeral=True)
            return

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        count = await sync_guild_member_levels(bot, db, interaction.guild)
        await interaction.followup.send(f"✅ **Level Synchronization Complete!** Checked all members and updated `{count}` profiles to match their highest Discord level roles.", ephemeral=True)

    @bot.tree.command(name="setannouncement", description="Admin: set the default or a reward-specific level announcement")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(level="Use 0 for the default announcement", message="Supports {mention}, {user}, {level}, and {reward}")
    async def setannouncement(interaction: discord.Interaction, level: app_commands.Range[int, 0, 1000], message: str):
        if level == 0:
            db.execute("INSERT INTO economy_settings(key,value) VALUES('xp_announcement_template',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (message,))
        else:
            row = db.execute("SELECT id FROM xp_rewards WHERE level=? ORDER BY id LIMIT 1", (level,)).fetchone()
            if row is None:
                await interaction.response.send_message(view=xbot_ui.danger("Reward Not Found", f"No reward exists at Level **{level}**. Create it in the Dashboard first."), ephemeral=True); return
            db.execute("UPDATE xp_rewards SET announcement=? WHERE id=?", (message, row["id"]))
        db.commit(); await interaction.response.send_message(view=xbot_ui.success("📣 Announcement Saved", f"Updated the **{'default' if level==0 else f'Level {level}'}** announcement."), ephemeral=True)

    @bot.tree.command(name="announcementshow", description="Admin: show configured level announcements")
    @app_commands.default_permissions(administrator=True)
    async def announcementshow(interaction: discord.Interaction):
        rows = db.execute("SELECT level,emoji,name,announcement FROM xp_rewards WHERE enabled=1 ORDER BY level").fetchall()
        lines = [f"{r['emoji']} **Level {r['level']} — {r['name']}**\n{r['announcement'] or '*Uses the default announcement*'}" for r in rows]
        body = f"**Default**\n{setting(db,'xp_announcement_template')}\n\n" + "\n\n".join(lines)
        await interaction.response.send_message(view=xbot_ui.panel("📣 Level Announcements", body, colour=discord.Color.teal()), ephemeral=True)

    @bot.tree.command(name="setannouncementchat", description="Admin: choose the channel for level announcements")
    @app_commands.default_permissions(administrator=True)
    async def setannouncementchat(interaction: discord.Interaction, channel: discord.TextChannel):
        db.execute("INSERT INTO economy_settings(key,value) VALUES('xp_announcement_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(channel.id),)); db.commit()
        await interaction.response.send_message(view=xbot_ui.success("📣 Announcement Channel", f"Level announcements will be sent to {channel.mention}."), ephemeral=True)
