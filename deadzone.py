"""Deadzone inactivity, role demotion, and resurrection system for X BOT."""
import json
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks

import leveling
import xbot_ui

DEFAULTS = {
    "deadzone_enabled": "1",
    "deadzone_days": "7",
    "deadzone_role_id": "1551839505168859196",
    "deadzone_guest_role_id": "1524715220365217842",
    "deadzone_crypt_channel_id": "0",
    "deadzone_lounge_channel_id": "0",
    "deadzone_notification_channel_id": "0",
    "deadzone_revive_bonus_cash": "100000",
    "deadzone_revive_bonus_xc": "150",
    "deadzone_revive_bonus_xp": "100",
    "deadzone_scavenge_cooldown": "72000",
    "deadzone_rescue_reward_xc": "100",
    "deadzone_haunt_reward_xc": "30",
    "deadzone_haunt_cooldown": "7200",
    "deadzone_party_duration": "300",
    "deadzone_party_reward_cash": "2000",
}

RESURRECTION_QUOTES = [
    "Welcome back to the squad, soldier!",
    "Death was just a temporary setback. Welcome back to the fight!",
    "The Deadzone couldn't hold you down! Good to have you back alive.",
    "Look who clawed their way out of the crypt! Welcome back!",
    "Cryo-stasis deactivated. Grab your weapons and jump back in!",
    "Legends never truly die—they just take a tactical nap. Welcome back!",
    "Back from the cold abyss and ready for action!",
    "The living missed you, operative! Time to make some noise.",
    "Coffin shattered, spirit unbroken. Welcome back to the squad!",
    "Rise and shine! Your comrades need you on the frontlines.",
    "You conquered the silence of the Deadzone. Welcome back to life!",
    "War calls once more. Glad to see you back on your feet, warrior!",
]

GHOST_HAUNT_QUOTES = [
    "From beneath the frozen crypt, chains rattle as a ghostly voice cries out for salvation...",
    "A freezing mist creeps across the room... a lost soul from the Deadzone reaches out from the shadow realm!",
    "The tombstone quivers! An ethereal whisper echoes through the halls: 'Don't leave me behind in the cold!'",
    "A spectral chill grips the server. The phantom of an old comrade demands a rescue operation!",
    "The coffin lid creaks open slightly in the fog. A restless spirit is calling their living teammates!",
    "Static crackles across the comms... 'Can anyone hear me? I am shivering in the cryo-tomb!'",
    "A glowing ghostly apparition points toward #general: 'Chat with me to thaw my stasis!'",
]


_bot = None
_db = None
active_parties = {}


def initialise(db):
    """Initialise database tables and default configuration settings for Deadzone."""
    global _db
    _db = db
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))

    db.execute("""CREATE TABLE IF NOT EXISTS deadzone_members (
        user_id INTEGER PRIMARY KEY,
        last_active_at INTEGER NOT NULL DEFAULT 0,
        is_in_deadzone INTEGER NOT NULL DEFAULT 0,
        deadzone_entered_at INTEGER NOT NULL DEFAULT 0,
        resurrections_count INTEGER NOT NULL DEFAULT 0,
        saved_roles TEXT NOT NULL DEFAULT '[]',
        thaw_count INTEGER NOT NULL DEFAULT 0,
        last_haunt_at INTEGER NOT NULL DEFAULT 0
    )""")
    try:
        db.execute("ALTER TABLE deadzone_members ADD COLUMN thaw_count INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE deadzone_members ADD COLUMN last_haunt_at INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass

    db.execute("""CREATE TABLE IF NOT EXISTS deadzone_scavenge (
        user_id INTEGER PRIMARY KEY,
        last_scavenge_at INTEGER NOT NULL DEFAULT 0,
        total_scavenged_xc INTEGER NOT NULL DEFAULT 0
    )""")

    # Seed initial activity timestamps from xp_profiles if available
    try:
        rows = db.execute("SELECT user_id, last_message_xp, last_voice_xp FROM xp_profiles").fetchall()
        now = int(time.time())
        for r in rows:
            latest = max(r["last_message_xp"] or 0, r["last_voice_xp"] or 0)
            ts = latest if latest > 0 else now
            db.execute(
                "INSERT OR IGNORE INTO deadzone_members(user_id, last_active_at) VALUES(?,?)",
                (r["user_id"], ts),
            )
    except sqlite3.OperationalError:
        pass

    db.execute("""CREATE TABLE IF NOT EXISTS command_permissions (
        command_name TEXT PRIMARY KEY,
        access_mode TEXT NOT NULL
    )""")
    for command_name in ("deadzone_send", "deadzone_restore", "deadzone_scan"):
        db.execute("INSERT OR IGNORE INTO command_permissions(command_name,access_mode) VALUES(?,'admin')", (command_name,))
        db.execute("UPDATE command_permissions SET access_mode='admin' WHERE command_name=?", (command_name,))

    db.commit()


def setting(db, key):
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS.get(key, "0")


def touch_activity(db, user_id: int):
    """Record that a user has been active right now."""
    now = int(time.time())
    db.execute(
        """INSERT INTO deadzone_members(user_id, last_active_at) VALUES(?,?)
        ON CONFLICT(user_id) DO UPDATE SET last_active_at=excluded.last_active_at""",
        (user_id, now),
    )
    db.commit()


def member_status(db, user_id: int):
    row = db.execute("SELECT * FROM deadzone_members WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        now = int(time.time())
        db.execute("INSERT INTO deadzone_members(user_id, last_active_at) VALUES(?,?)", (user_id, now))
        db.commit()
        row = db.execute("SELECT * FROM deadzone_members WHERE user_id=?", (user_id,)).fetchone()
    return row


def _get_privilege_role_ids(db):
    """IDs of Member, Music, and all level rewards that should be stripped upon demotion."""
    rows = db.execute("SELECT role_id FROM xp_rewards WHERE enabled=1 AND role_id!=''").fetchall()
    role_ids = {int(r["role_id"]) for r in rows if str(r["role_id"]).isdigit()}
    role_ids.update({
        1505437941647015986,  # Member
        1505437186219311236,  # Music
        1524719900785119354,  # Active
        1522478703819362334,  # Elite
        1526851111128928307,  # Senior
        1526851320118509648,  # Mythic
        1534198146807234722,  # Titan
        1534198492656959616,  # Ascendant
        1534198526571970701,  # Legend
    })
    return role_ids


async def demote_to_deadzone(bot, db, member: discord.Member, reason: str = "Inactive for 7 days"):
    """Strip Member, Music, and Level roles, assign Deadzone + Guest roles, and record status."""
    if not isinstance(member, discord.Member) or member.bot:
        return False

    status = member_status(db, member.id)
    if status and status["is_in_deadzone"]:
        return False

    deadzone_role_id = int(setting(db, "deadzone_role_id") or 0)

    privilege_role_ids = _get_privilege_role_ids(db)
    roles_to_remove = [r for r in member.roles if r.id in privilege_role_ids]
    saved_ids = [r.id for r in roles_to_remove]

    roles_to_add = []
    if deadzone_role_id:
        dz_role = member.guild.get_role(deadzone_role_id)
        if dz_role and dz_role not in member.roles:
            roles_to_add.append(dz_role)

    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason=f"X BOT Deadzone Demotion: {reason}")
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason=f"X BOT Deadzone Demotion: {reason}")
    except discord.HTTPException:
        pass

    now = int(time.time())
    db.execute(
        """UPDATE deadzone_members
        SET is_in_deadzone=1, deadzone_entered_at=?, saved_roles=?, thaw_count=0
        WHERE user_id=?""",
        (now, json.dumps(saved_ids), member.id),
    )
    db.commit()

    # Post notification in notification channel (or crypt channel as fallback)
    notif_channel_id = int(setting(db, "deadzone_notification_channel_id") or 0)
    crypt_channel_id = int(setting(db, "deadzone_crypt_channel_id") or 0)
    target_channel_id = notif_channel_id or crypt_channel_id
    channel = bot.get_channel(target_channel_id) if target_channel_id else None
    if channel:
        embed = discord.Embed(
            title="🪦 [TOMBSTONE ERECTED]",
            description=(
                f"**Operative:** {member.mention}\n"
                f"**Status:** Cryo-Stasis / Demoted to Deadzone\n"
                f"**Reason:** {reason}\n"
                f"⚠️ *Member, Music, and Rank perks revoked. Assigned Deadzone status.*\n\n"
                f"*\"May they rest in peace... until they shatter their coffin.\"*"
            ),
            color=0x4A4D52,
        )
        embed.set_footer(text="X BOT · Deadzone Division · Click the pinned Break Out button above or chat in general to revive")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    return True


async def revive_member(bot, db, member: discord.Member, triggered_by: str = "message"):
    """Remove Deadzone role, restore Member/Music/Level roles, award bonus, and announce."""
    if not isinstance(member, discord.Member):
        return False

    status = member_status(db, member.id)
    if not status or not status["is_in_deadzone"]:
        return False

    deadzone_role_id = int(setting(db, "deadzone_role_id") or 0)
    if deadzone_role_id:
        dz_role = member.guild.get_role(deadzone_role_id)
        if dz_role and dz_role in member.roles:
            try:
                await member.remove_roles(dz_role, reason="X BOT Deadzone Resurrection")
            except discord.HTTPException:
                pass

    # 1. Parse saved roles from deadzone_members
    saved_roles_raw = status["saved_roles"] if status and "saved_roles" in status.keys() else "[]"
    try:
        saved_role_ids = [int(x) for x in json.loads(saved_roles_raw)] if saved_roles_raw else []
    except Exception:
        saved_role_ids = []

    # 2. Check if user held any level roles before demotion
    # If the user held a level role (e.g. Level 7 Active, Level 10 Elite) before demotion,
    # preserve that level so leveling.sync_reward_roles won't strip their level title!
    prof = leveling.profile(db, member.id)
    current_level = int(prof["level"] if prof else 1)
    max_role_level = current_level

    reward_rows = db.execute("SELECT level, role_id FROM xp_rewards WHERE enabled=1 AND role_id!=''").fetchall()
    role_to_level = {int(r["role_id"]): int(r["level"]) for r in reward_rows if str(r["role_id"]).isdigit()}
    for rid in saved_role_ids:
        if rid in role_to_level and role_to_level[rid] > max_role_level:
            max_role_level = role_to_level[rid]

    if max_role_level > current_level:
        target_xp = leveling.xp_for_level(max_role_level)
        db.execute(
            """INSERT INTO xp_profiles(user_id, level, total_xp) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
            level=MAX(level, excluded.level),
            total_xp=MAX(total_xp, excluded.total_xp)""",
            (member.id, max_role_level, target_xp),
        )
        db.commit()
        current_level = max_role_level

    # 3. Restore all saved roles directly to the member
    roles_to_add = []
    for rid in saved_role_ids:
        role = member.guild.get_role(rid)
        if role and role not in member.roles and role not in roles_to_add:
            roles_to_add.append(role)

    # Always ensure default verified Member role is given back upon revival
    member_role_id = int(setting(db, "verification_member_role_id") or 1505437941647015986)
    m_role = member.guild.get_role(member_role_id)
    if m_role and m_role not in member.roles and m_role not in roles_to_add:
        roles_to_add.append(m_role)

    if roles_to_add:
        try:
            await member.add_roles(*roles_to_add, reason="X BOT Deadzone Resurrection - Restoring saved roles")
        except discord.HTTPException:
            pass

    # 4. Synchronize reward roles matching their restored level
    await leveling.sync_reward_roles(db, member, current_level)
    level = current_level

    # Award revival bonus
    bonus_cash = int(setting(db, "deadzone_revive_bonus_cash") or 100000)
    bonus_xc = int(setting(db, "deadzone_revive_bonus_xc") or 150)
    bonus_xp = int(setting(db, "deadzone_revive_bonus_xp") or 100)

    if bonus_cash > 0:
        db.execute("UPDATE players SET money=money+? WHERE user_id=?", (bonus_cash, member.id))
    if bonus_xc > 0:
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (bonus_xc, member.id))
    if bonus_xp > 0:
        await leveling.grant_xp(bot, db, member, bonus_xp, "message")

    now = int(time.time())
    db.execute(
        """UPDATE deadzone_members
        SET is_in_deadzone=0, last_active_at=?, resurrections_count=resurrections_count+1, saved_roles='[]', thaw_count=0
        WHERE user_id=?""",
        (now, member.id),
    )
    db.commit()

    # Announce resurrection in notification channel or lounge
    notif_channel_id = int(setting(db, "deadzone_notification_channel_id") or 0)
    lounge_channel_id = int(setting(db, "deadzone_lounge_channel_id") or 0)
    target_id = notif_channel_id or lounge_channel_id

    target_channel = (bot.get_channel(target_id) if target_id and hasattr(bot, "get_channel") else None) or getattr(member.guild, "system_channel", None)
    if target_channel is None and getattr(member.guild, "text_channels", None):
        target_channel = member.guild.text_channels[0]

    welcome_quote = random.choice(RESURRECTION_QUOTES)
    embed = discord.Embed(
        title="⚡ [RESURRECTION ALERT]",
        description=(
            f"🎉 {member.mention} **has broken out of their coffin and returned to the living!**\n\n"
            f"🛡️ **Status Restored:** Member, Music, and Level {level} perks are active.\n"
            f"🎁 **Survival Bonus:** Received `💵 +{bonus_cash:,} Cash`, `🪙 +{bonus_xc} XC`, and `⭐ +{bonus_xp} XP`!\n\n"
            f"*{welcome_quote}*"
        ),
        color=0x2ECC71,
    )
    embed.set_footer(text="X BOT · Deadzone Division")

    if target_channel:
        try:
            await target_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # Start 5-minute Resurrection Welcome Party in target_channel
    party_duration = int(setting(db, "deadzone_party_duration") or 300)
    party_reward = int(setting(db, "deadzone_party_reward_cash") or 2000)
    if target_channel and party_duration > 0 and getattr(member, "guild", None):
        active_parties[member.guild.id] = {
            "expires_at": time.time() + party_duration,
            "revived_user_id": member.id,
            "revived_name": member.display_name,
            "channel_id": target_channel.id,
            "reward_cash": party_reward,
            "claimed_users": set(),
        }
        party_embed = discord.Embed(
            title="🎊 [WELCOME PARTY STARTED — 5 MINUTES]",
            description=(
                f"A celebration party has started for {member.mention}!\n\n"
                f"💬 **Chat in {target_channel.mention}** within the next **5 minutes** to claim your **💵 {party_reward:,} Cash** welcome bonus!\n"
                f"-# One claim per member · Say hi and celebrate their return!"
            ),
            color=0xF1C40F,
        )
        party_embed.set_footer(text="X BOT · Deadzone Division · Welcome Party")
        try:
            await target_channel.send(embed=party_embed)
        except discord.HTTPException:
            pass

    return True


def build_deadzone_board_embed():
    """Build the official crypt announcement embed displayed with the revival button."""
    embed = discord.Embed(
        title="💀 [THE DEADZONE CRYPT]",
        description=(
            "### ⚠️ Cryo-Stasis & Inactivity Notice\n"
            "Members who remain inactive for **7 days** without sending messages or joining voice channels "
            "are placed into cryogenic slumber here in the **Deadzone**.\n\n"
            "**Demotion Penalties Applied:**\n"
            "• **Member** & **Music** perks are temporarily revoked.\n"
            "• All **Level rank tags** (`Active`, `Elite`, `Senior`, etc.) are hidden.\n"
            "• Tag assigned: **Deadzone**.\n\n"
            "───\n\n"
            "### ⚡ HOW TO RESURRECT (1+2 RESPAWN PROTOCOL):\n"
            "1. **Condition 1 (Thaw Out):** Send **5 chat messages** (e.g. in `#general`) to melt your cryo-stasis seal.\n"
            "2. **Condition 2 (Teammate Rescue):** Once thawed (5/5), have an active comrade rescue you with **`/deadzone rescue member:@you`** or click **`[ 🤝 Rescue Teammate ]`** below.\n\n"
            "🎁 **Resurrection Rewards:**\n"
            "• Instant restoration of **Member**, **Music**, and all earned **Level rank tags**.\n"
            "• **`+150 XC`** survival bonus added to your balance.\n"
            "• **`+50 XP`** activity boost!\n"
            "• Rescuer receives a **`+50 XC`** bounty for pulling you out!"
        ),
        color=0x4A4D52,
    )
    embed.set_footer(text="X BOT · Deadzone Division · 1+2 Respawn Protocol")
    return embed


class DeadzoneReviveView(discord.ui.View):
    """Persistent UI view with Break Out status and Rescue buttons."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⚡ Break Out Status", style=discord.ButtonStyle.success, custom_id="xbot:deadzone:breakout", emoji="⚰️")
    async def breakout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Only members in the server can use this button.", ephemeral=True)
            return

        db = getattr(interaction.client, "db", None) or _db
        if not db:
            await interaction.response.send_message("Database unavailable. Please try again shortly.", ephemeral=True)
            return

        status = member_status(db, interaction.user.id)
        if not status or not status["is_in_deadzone"]:
            await interaction.response.send_message("🟢 You are not in the Deadzone. You are already alive and active!", ephemeral=True)
            return

        thaw_count = status["thaw_count"] if "thaw_count" in status.keys() else 0
        if thaw_count < 5:
            await interaction.response.send_message(
                f"🧊 **Cryo-Stasis Thaw Progress: {thaw_count}/5 messages sent**\n\n"
                f"Your coffin is frozen shut! Follow the **1+2 Respawn Protocol**:\n"
                f"1. **Thaw out:** Send **{5 - thaw_count} more message(s)** in any chat channel (e.g. `#general`).\n"
                f"2. **Get rescued:** Once thawed (5/5), have an active comrade rescue you with `/deadzone rescue member:{interaction.user.mention}` (they earn **+50 XC**!).",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ **Cryo-Stasis Thaw Complete (5/5 messages)!**\n\n"
            f"Your seal is broken, but you still need a hand up from the crypt!\n"
            f"Ask any active teammate to run:\n"
            f"**`/deadzone rescue member:{interaction.user.mention}`**\n"
            f"*(They will be awarded **+50 XC** for rescuing you!)*",
            ephemeral=True,
        )

    @discord.ui.button(label="🤝 Rescue Teammate", style=discord.ButtonStyle.primary, custom_id="xbot:deadzone:rescue_btn", emoji="🤝")
    async def rescue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Only members in the server can use this button.", ephemeral=True)
            return

        db = getattr(interaction.client, "db", None) or _db
        if not db:
            await interaction.response.send_message("Database unavailable. Please try again shortly.", ephemeral=True)
            return

        my_status = member_status(db, interaction.user.id)
        if my_status and my_status["is_in_deadzone"]:
            await interaction.response.send_message("💀 You cannot rescue others while trapped in the Deadzone yourself! Thaw out and get rescued first.", ephemeral=True)
            return

        ready_sleepers = db.execute("SELECT user_id FROM deadzone_members WHERE is_in_deadzone=1 AND thaw_count>=5").fetchall()
        if not ready_sleepers:
            total_sleepers = db.execute("SELECT COUNT(*) as c FROM deadzone_members WHERE is_in_deadzone=1").fetchone()["c"]
            await interaction.response.send_message(
                f"ℹ️ There are **{total_sleepers}** sleeper(s) in the Deadzone, but none have finished thawing (5/5 messages) yet.\n"
                f"Tell them to chat in `#general` to thaw out, then use `/deadzone rescue @member` once they reach 5/5!",
                ephemeral=True,
            )
            return

        lines = [f"• <@{r['user_id']}> (Thaw: 5/5 ✅)" for r in ready_sleepers[:10]]
        await interaction.response.send_message(
            f"### 🤝 Operatives Ready for Rescue:\n"
            + "\n".join(lines)
            + f"\n\nRun **`/deadzone rescue member:@user`** to pull them out and earn **+100 XC**!",
            ephemeral=True,
        )


async def handle_message(bot, db, message: discord.Message):
    """Listen for chat messages to update activity timestamps, track cryo-thaw, and award welcome party bonuses."""
    if not message.guild or message.author.bot:
        return

    if setting(db, "deadzone_enabled") != "1":
        return

    touch_activity(db, message.author.id)

    # Check Resurrection Welcome Party participation
    guild_id = getattr(message.guild, "id", None)
    if guild_id and guild_id in active_parties:
        party = active_parties[guild_id]
        now = time.time()
        if now <= party["expires_at"]:
            if message.channel.id == party["channel_id"] and message.author.id != party["revived_user_id"]:
                if message.author.id not in party["claimed_users"]:
                    party["claimed_users"].add(message.author.id)
                    cash_reward = party["reward_cash"]
                    db.execute("UPDATE players SET money=money+? WHERE user_id=?", (cash_reward, message.author.id))
                    db.commit()
                    try:
                        await message.add_reaction("🎉")
                    except (discord.HTTPException, discord.Forbidden):
                        pass
        else:
            del active_parties[guild_id]

    status = member_status(db, message.author.id)
    if status and status["is_in_deadzone"]:
        current_thaw = status["thaw_count"] if "thaw_count" in status.keys() else 0
        if current_thaw < 5:
            new_thaw = current_thaw + 1
            db.execute("UPDATE deadzone_members SET thaw_count=? WHERE user_id=?", (new_thaw, message.author.id))
            db.commit()
            try:
                if new_thaw == 5:
                    embed = discord.Embed(
                        title="🧊 [CRYO-THAW COMPLETE (5/5)]",
                        description=(
                            f"🎉 {message.author.mention} **has fully melted their cryo-stasis seal!**\n\n"
                            f"🤝 **Next Step (Condition 2):** An active comrade can now run:\n"
                            f"`/deadzone rescue member:{message.author.mention}`\n\n"
                            f"*(Rescuers receive a **+100 XC bounty** for pulling you out of the crypt!)*"
                        ),
                        color=0x3498DB,
                    )
                    embed.set_footer(text="X BOT · Deadzone Division · 1+2 Respawn Protocol")
                    await message.channel.send(embed=embed)
                else:
                    await message.add_reaction("🔥")
            except (discord.HTTPException, discord.Forbidden):
                pass


async def scan_guild_inactivity(bot, db, guild: discord.Guild):
    """Scan all members in a guild and demote those inactive past the configured days."""
    if setting(db, "deadzone_enabled") != "1":
        return []

    days = int(setting(db, "deadzone_days") or 7)
    threshold_seconds = days * 86400
    now = int(time.time())

    demoted = []
    for member in guild.members:
        if member.bot or member.guild_permissions.administrator:
            continue

        status = member_status(db, member.id)
        if status["is_in_deadzone"]:
            continue

        if (now - status["last_active_at"]) >= threshold_seconds:
            success = await demote_to_deadzone(bot, db, member, reason=f"Inactive for {days}+ days")
            if success:
                demoted.append(member)

    return demoted


def start_deadzone_task(bot, db):
    """Start periodic task checking for inactive members."""
    global _bot, _db
    _bot = bot
    _db = db
    if not deadzone_check_loop.is_running():
        deadzone_check_loop.bot = bot
        deadzone_check_loop.db = db
        deadzone_check_loop.start()


@tasks.loop(hours=6)
async def deadzone_check_loop():
    bot = deadzone_check_loop.bot
    db = deadzone_check_loop.db
    if setting(db, "deadzone_enabled") != "1":
        return

    for guild in bot.guilds:
        try:
            await scan_guild_inactivity(bot, db, guild)
        except Exception as e:
            print(f"Error in deadzone check for {guild.name}: {e}")


@deadzone_check_loop.before_loop
async def before_deadzone_loop():
    await deadzone_check_loop.bot.wait_until_ready()


def register_commands(bot, db, is_council_or_admin, STAFF_COMMAND_KWARGS):
    """Register Deadzone slash commands for both members and administrators."""
    global _bot, _db
    _bot = bot
    _db = db

    deadzone_group = app_commands.Group(name="deadzone", description="X BOT Deadzone & Crypt operations")

    @deadzone_group.command(name="status", description="Check the status of the Deadzone crypt")
    async def dz_status(interaction: discord.Interaction):
        total_dz = db.execute("SELECT COUNT(*) as c FROM deadzone_members WHERE is_in_deadzone=1").fetchone()["c"]
        my_status = member_status(db, interaction.user.id)
        days = int(setting(db, "deadzone_days") or 7)

        now = int(time.time())
        inactive_hours = (now - my_status["last_active_at"]) // 3600

        state_text = "💀 In Deadzone" if my_status["is_in_deadzone"] else "🟢 Alive & Active"

        embed = discord.Embed(
            title="💀 [DEADZONE CRYPT STATUS]",
            description=(
                f"**Inactivity Rule:** {days} days of inactivity strips Member & Level perks.\n"
                f"**Current Sleepers in Deadzone:** `{total_dz}` members\n\n"
                f"**Your Status:** {state_text}\n"
                f"**Your Inactive Time:** `{inactive_hours}` hours\n"
                f"**Resurrections Record:** `{my_status['resurrections_count']}` times\n"
            ),
            color=0x7289DA if not my_status["is_in_deadzone"] else 0x4A4D52,
        )
        embed.set_footer(text="Use /deadzone scavenge to search the crypt for loot")
        await interaction.response.send_message(embed=embed)

    @deadzone_group.command(name="scavenge", description="Daily: search the Deadzone ruins for scrap metal and XC")
    async def dz_scavenge(interaction: discord.Interaction):
        user_id = interaction.user.id
        status = member_status(db, user_id)
        if status["is_in_deadzone"]:
            await interaction.response.send_message("💀 You cannot scavenge while trapped in the Deadzone! Wake up first.", ephemeral=True)
            return

        cooldown = int(setting(db, "deadzone_scavenge_cooldown") or 72000)
        now = int(time.time())
        row = db.execute("SELECT * FROM deadzone_scavenge WHERE user_id=?", (user_id,)).fetchone()

        if row and (now - row["last_scavenge_at"]) < cooldown:
            rem = cooldown - (now - row["last_scavenge_at"])
            hours = rem // 3600
            mins = (rem % 3600) // 60
            await interaction.response.send_message(f"⏳ The crypt has already been scavenged. Return in **{hours}h {mins}m**.", ephemeral=True)
            return

        sleepers = db.execute("SELECT user_id FROM deadzone_members WHERE is_in_deadzone=1").fetchall()
        reward_xc = random.randint(35, 85)

        db.execute(
            """INSERT INTO deadzone_scavenge(user_id, last_scavenge_at, total_scavenged_xc)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
            last_scavenge_at=excluded.last_scavenge_at,
            total_scavenged_xc=total_scavenged_xc+excluded.total_scavenged_xc""",
            (user_id, now, reward_xc),
        )
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (reward_xc, user_id))
        touch_activity(db, user_id)
        db.commit()

        target_name = "an abandoned coffin"
        if sleepers:
            random_sleeper = random.choice(sleepers)["user_id"]
            target_name = f"<@{random_sleeper}>'s tombstone"

        embed = discord.Embed(
            title="🎒 [SCAVENGE EXPEDITION SUCCESSFUL]",
            description=(
                f"You ventured into the misty ruins of the Deadzone and searched near **{target_name}**.\n\n"
                f"💰 **Loot Retrieved:** `+{reward_xc} XC`\n"
                f"*(Bonus safely deposited into your treasury!)*"
            ),
            color=0xF1C40F,
        )
        embed.set_footer(text="X BOT · Crypt Explorations")
        await interaction.response.send_message(embed=embed)

    @deadzone_group.command(name="haunt", description="Deadzone: Send an eerie cry from the crypt to summon living comrades")
    @app_commands.describe(target="Optional comrade you want to summon / haunt")
    async def dz_haunt(interaction: discord.Interaction, target: Optional[discord.Member] = None):
        user_id = interaction.user.id
        status = member_status(db, user_id)
        if not status or not status["is_in_deadzone"]:
            await interaction.response.send_message(
                "☀️ **You are still among the living!** Only spirits trapped in the Deadzone crypt can haunt the server.\n"
                "Use `/deadzone scavenge` or rescue a frozen comrade with `/deadzone rescue`!",
                ephemeral=True,
            )
            return

        cooldown = int(setting(db, "deadzone_haunt_cooldown") or 7200)
        now = int(time.time())
        last_haunt = status["last_haunt_at"] if "last_haunt_at" in status.keys() else 0

        if (now - last_haunt) < cooldown:
            rem = cooldown - (now - last_haunt)
            mins = rem // 60
            secs = rem % 60
            await interaction.response.send_message(
                f"⏳ **Your spiritual energy is recovering.** You can haunt the living again in **{mins}m {secs}s**.",
                ephemeral=True,
            )
            return

        reward_xc = int(setting(db, "deadzone_haunt_reward_xc") or 30)
        db.execute(
            "UPDATE deadzone_members SET last_haunt_at=?, last_active_at=? WHERE user_id=?",
            (now, now, user_id),
        )
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (reward_xc, user_id))
        touch_activity(db, user_id)
        db.commit()

        thaw = status["thaw_count"] if "thaw_count" in status.keys() else 0
        quote = random.choice(GHOST_HAUNT_QUOTES)
        rescue_reward = int(setting(db, "deadzone_rescue_reward_xc") or 100)

        target_text = f"🎯 **Summoning Target:** {target.mention}\n" if target else ""
        embed = discord.Embed(
            title="👻 [GHOST TRANSMISSION · CRY FROM THE CRYPT]",
            description=(
                f"🕯️ *A sudden supernatural chill sweeps through the server...*\n\n"
                f"💀 **{interaction.user.mention} is haunting from the Deadzone crypt!**\n"
                f"> *\"{quote}\"*\n\n"
                f"{target_text}"
                f"🧊 **Cryo-Stasis Thaw Progress:** `{thaw}/5 messages`\n\n"
                f"📜 **How to Break the Curse:**\n"
                f"1. {interaction.user.mention} must chat in **#general** to thaw ({max(0, 5 - thaw)} more message(s) needed).\n"
                f"2. A living comrade runs `/deadzone rescue {interaction.user.mention}` to pull them out and claim **+{rescue_reward} XC** hero bounty!\n\n"
                f"🪙 *The ghostly wandering yielded `+{reward_xc} XC` spectral scrap into {interaction.user.mention}'s treasury.*"
            ),
            color=0x9B59B6,
        )
        embed.set_footer(text="X BOT · Deadzone Supernatural Frequency · 1+2 Respawn Protocol")

        notif_channel_id = int(setting(db, "deadzone_notification_channel_id") or 0)
        lounge_channel_id = int(setting(db, "deadzone_lounge_channel_id") or 0)
        dest_id = notif_channel_id or lounge_channel_id
        dest_channel = interaction.guild.get_channel(dest_id) if (dest_id and interaction.guild) else (interaction.guild.system_channel if interaction.guild else None)

        if dest_channel and dest_channel.id != interaction.channel_id:
            try:
                await dest_channel.send(content=f"🔔 {target.mention}" if target else None, embed=embed)
                await interaction.response.send_message(f"👻 **Haunting Successful!** Your ghostly voice manifested in {dest_channel.mention} (Earned `+{reward_xc} XC`)!", ephemeral=True)
                return
            except discord.HTTPException:
                pass

        await interaction.response.send_message(content=f"🔔 {target.mention}" if target else None, embed=embed)

    @deadzone_group.command(name="wake", description="Wake up or revive a sleeping member from the Deadzone")
    @app_commands.describe(member="Member currently sleeping in the Deadzone")
    async def dz_wake(interaction: discord.Interaction, member: discord.Member):
        status = member_status(db, member.id)
        if not status or not status["is_in_deadzone"]:
            await interaction.response.send_message(f"{member.mention} is not in the Deadzone! They are already active.", ephemeral=True)
            return

        touch_activity(db, interaction.user.id)

        # If Admin or Staff: directly wake up and revive them!
        if is_council_or_admin(interaction):
            await interaction.response.defer(ephemeral=True)
            success = await revive_member(bot, db, member, triggered_by="admin_wake")
            if success:
                await interaction.followup.send(f"⚡ **Wake Up Successful!** Revived {member.mention} from Deadzone. All Member, Music, and Level perks restored.", ephemeral=True)
            else:
                await interaction.followup.send(f"⚠️ Failed to revive {member.mention}.", ephemeral=True)
            return

        # For regular members: send wake-up notification/DM
        thaw = status["thaw_count"] if "thaw_count" in status.keys() else 0
        try:
            await member.send(
                f"📢 **WAKE UP CALL FROM {interaction.user.display_name}!**\n"
                f"You are resting in the Deadzone in **{interaction.guild.name}**.\n"
                f"Thaw progress: **{thaw}/5 messages**.\n"
                f"Post {max(0, 5 - thaw)} more message(s) in chat to thaw, then have a comrade rescue you with `/deadzone rescue`!"
            )
            await interaction.response.send_message(f"🔔 Sent a direct wake-up call to {member.mention} (Thaw progress: {thaw}/5)!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"📢 {member.mention}, wake up! {interaction.user.mention} is calling you from #general! Send messages in chat to thaw ({thaw}/5) then get rescued!",
            )

    @deadzone_group.command(name="rescue", description="Rescue a thawed teammate from the Deadzone and earn +50 XC")
    @app_commands.describe(member="Thawed teammate currently sleeping in the Deadzone")
    async def dz_rescue(interaction: discord.Interaction, member: discord.Member):
        if interaction.user.id == member.id:
            await interaction.response.send_message("❌ You cannot rescue yourself! A living comrade must pull you out of the crypt.", ephemeral=True)
            return

        my_status = member_status(db, interaction.user.id)
        if my_status and my_status["is_in_deadzone"]:
            await interaction.response.send_message("💀 You cannot rescue others while trapped in the Deadzone yourself!", ephemeral=True)
            return

        target_status = member_status(db, member.id)
        if not target_status or not target_status["is_in_deadzone"]:
            await interaction.response.send_message(f"{member.mention} is not in the Deadzone! They are already active.", ephemeral=True)
            return

        thaw_count = target_status["thaw_count"] if "thaw_count" in target_status.keys() else 0
        if thaw_count < 5:
            await interaction.response.send_message(
                f"🧊 {member.mention} is still frozen in cryo-stasis (**{thaw_count}/5 messages**).\n"
                f"They need to send **{5 - thaw_count} more message(s)** in chat before you can rescue them!",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        success = await revive_member(bot, db, member, triggered_by=f"rescued_by_{interaction.user.id}")
        if not success:
            await interaction.followup.send(f"⚠️ Failed to rescue {member.mention}.", ephemeral=True)
            return

        reward_xc = int(setting(db, "deadzone_rescue_reward_xc") or 100)
        db.execute("UPDATE players SET xc = xc + ? WHERE user_id = ?", (reward_xc, interaction.user.id))
        touch_activity(db, interaction.user.id)
        db.commit()

        embed = discord.Embed(
            title="🤝 [RESCUE OPERATION COMPLETE]",
            description=(
                f"🎉 {interaction.user.mention} **bravely pulled** {member.mention} **out of the Deadzone crypt!**\n\n"
                f"🛡️ **{member.mention}** has returned to the living! Perks and rank tags restored.\n"
                f"💰 **Hero Reward:** {interaction.user.mention} received `+{reward_xc} XC` for the successful rescue!"
            ),
            color=0x2ECC71,
        )
        embed.set_footer(text="X BOT · Deadzone Division · 1+2 Respawn System")
        await interaction.followup.send(embed=embed)

    @deadzone_group.command(name="restore", description="Admin: Restore a member from Deadzone and restore all perks")
    @app_commands.describe(member="Member to restore from Deadzone")
    async def dz_restore(interaction: discord.Interaction, member: discord.Member):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators can restore members."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        success = await revive_member(bot, db, member, triggered_by="admin_restore")
        if success:
            await interaction.followup.send(f"⚡ **Resurrection Successful!** Restored {member.mention} from Deadzone. All Member, Music, and Rank perks restored.", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ {member.mention} is not in the Deadzone.", ephemeral=True)

    @deadzone_group.command(name="revive", description="Admin: Revive a member from the Deadzone and restore all perks")
    @app_commands.describe(member="Member to revive from Deadzone")
    async def dz_revive(interaction: discord.Interaction, member: discord.Member):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators can revive members."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        success = await revive_member(bot, db, member, triggered_by="admin_revive")
        if success:
            await interaction.followup.send(f"⚡ **Resurrection Successful!** Revived {member.mention} from Deadzone. All Member, Music, and Rank perks restored.", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ {member.mention} is not in the Deadzone.", ephemeral=True)

    @deadzone_group.command(name="send", description="Admin: Demote an inactive member to Deadzone")
    @app_commands.describe(member="Member to demote", reason="Reason for demotion")
    async def dz_send(interaction: discord.Interaction, member: discord.Member, reason: str = "Admin decision"):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators can demote members."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        success = await demote_to_deadzone(bot, db, member, reason=reason)
        if success:
            await interaction.followup.send(f"✅ Successfully demoted {member.mention} to Deadzone. Member/Music/Rank roles removed.", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ Could not demote {member.mention} (already in Deadzone or bot).", ephemeral=True)

    @deadzone_group.command(name="set_channel", description="Admin: Set notification or crypt channel for Deadzone")
    @app_commands.describe(
        notification_channel="Channel where tombstones and resurrection alerts are posted",
        crypt_channel="Channel where the Deadzone board and Break Out button live",
    )
    async def dz_set_channel(
        interaction: discord.Interaction,
        notification_channel: Optional[discord.TextChannel] = None,
        crypt_channel: Optional[discord.TextChannel] = None,
    ):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators can configure channels."), ephemeral=True)
            return

        updates = []
        if notification_channel:
            db.execute("INSERT INTO economy_settings(key,value) VALUES('deadzone_notification_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(notification_channel.id),))
            updates.append(f"📢 **Notification Channel:** {notification_channel.mention}")
        if crypt_channel:
            db.execute("INSERT INTO economy_settings(key,value) VALUES('deadzone_crypt_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(crypt_channel.id),))
            updates.append(f"🪦 **Crypt Channel:** {crypt_channel.mention}")
            try:
                embed = build_deadzone_board_embed()
                await crypt_channel.send(embed=embed, view=DeadzoneReviveView())
                updates.append("⚡ *Resurrection board automatically posted in crypt channel!*")
            except discord.HTTPException:
                pass

        if not updates:
            await interaction.response.send_message("Please choose at least one channel to set.", ephemeral=True)
            return

        db.commit()
        await interaction.response.send_message("✅ **Deadzone Channels Updated:**\n" + "\n".join(updates), ephemeral=True)

    # Top-level standalone staff commands (accessible from staff_tools Admin Panel and slash)
    @bot.tree.command(name="deadzone_send", description="Admin: Demote an inactive member to Deadzone", **STAFF_COMMAND_KWARGS)
    @app_commands.describe(member="Member to demote", reason="Reason for demotion")
    async def deadzone_send(interaction: discord.Interaction, member: discord.Member, reason: str = "Admin panel decision"):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators and Staff can manage Deadzone members."), ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        success = await demote_to_deadzone(bot, db, member, reason=reason)
        if success:
            await interaction.followup.send(f"✅ Successfully demoted {member.mention} to Deadzone. Member/Music/Rank roles removed.", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ Could not demote {member.mention} (already in Deadzone or bot).", ephemeral=True)

    @bot.tree.command(name="deadzone_restore", description="Admin: Restore a member from Deadzone", **STAFF_COMMAND_KWARGS)
    @app_commands.describe(member="Member to restore")
    async def deadzone_restore(interaction: discord.Interaction, member: discord.Member):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators and Staff can manage Deadzone members."), ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        success = await revive_member(bot, db, member, triggered_by="admin")
        if success:
            await interaction.followup.send(f"✅ Successfully restored {member.mention} from Deadzone. All privileges restored.", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ {member.mention} is not in the Deadzone.", ephemeral=True)

    @bot.tree.command(name="deadzone_scan", description="Admin: Scan guild and demote members inactive for 7+ days", **STAFF_COMMAND_KWARGS)
    async def deadzone_scan(interaction: discord.Interaction):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators can trigger inactivity scans."), ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        demoted = await scan_guild_inactivity(bot, db, interaction.guild)
        names = ", ".join(m.display_name for m in demoted) if demoted else "None"
        await interaction.followup.send(f"🔍 **Deadzone Scan Complete.** Demoted `{len(demoted)}` members: {names}", ephemeral=True)

    bot.tree.add_command(deadzone_group)
