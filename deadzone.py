"""Deadzone inactivity, role demotion, and resurrection system for X BOT."""
import json
import random
import sqlite3
import time
import traceback
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks

import leveling
import xbot_ui

DEFAULT_GENERAL_CHANNEL_ID = 1524716540988231820
DEFAULT_NOTIF_CHANNEL_ID = 1526521131048370217
LOUNGE_TEXT_CHANNEL_IDS = {
    1538463993910403074,  # Lounge 1
    1544726174083846244,  # Lounge 2
    1551191838780559460,  # Lounge 3
    1544735099877064865,  # Lounge 4
    1544735123596120125,  # Lounge 5
}

REGULAR_MUSIC_ROLE_ID = 1505437186219311236
PREMIUM_MUSIC_ROLE_ID = 1526237128093339848

DEFAULTS = {
    "deadzone_enabled": "1",
    "deadzone_days": "7",
    "deadzone_role_id": "1551839505168859196",
    "deadzone_guest_role_id": "1524715220365217842",
    "deadzone_crypt_channel_id": "0",
    "deadzone_lounge_channel_id": "0",
    "deadzone_notification_channel_id": "1526521131048370217",
    "deadzone_party_channel_id": "1524716540988231820",
    "deadzone_revive_bonus_cash": "100000",
    "deadzone_revive_bonus_xc": "150",
    "deadzone_revive_bonus_xp": "100",
    "deadzone_scavenge_cooldown": "72000",
    "deadzone_rescue_reward_cash": "50000",
    "deadzone_rescue_reward_xc": "250",
    "deadzone_rescue_reward_xp": "150",
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

    # Migration: Update existing database entries that have old deprecated defaults
    db.execute("UPDATE economy_settings SET value='250' WHERE key='deadzone_rescue_reward_xc' AND value in ('50', '100')")
    db.execute("UPDATE economy_settings SET value='100' WHERE key='deadzone_revive_bonus_xp' AND value='50'")
    db.execute("UPDATE economy_settings SET value='50000' WHERE key='deadzone_rescue_reward_cash' AND value in ('0', '')")
    db.execute("UPDATE economy_settings SET value='150' WHERE key='deadzone_rescue_reward_xp' AND value in ('0', '')")
    db.commit()

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
            uid = r[0] if isinstance(r, tuple) else r["user_id"]
            msg_xp = r[1] if isinstance(r, tuple) else (r["last_message_xp"] or 0)
            vc_xp = r[2] if isinstance(r, tuple) else (r["last_voice_xp"] or 0)
            latest = max(msg_xp or 0, vc_xp or 0)
            ts = latest if latest > 0 else now
            db.execute(
                "INSERT OR IGNORE INTO deadzone_members(user_id, last_active_at) VALUES(?,?)",
                (uid, ts),
            )
    except Exception:
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
    try:
        row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return DEFAULTS.get(key, "0")
        if hasattr(row, "keys"):
            return str(row["value"])
        return str(row[0])
    except Exception:
        return DEFAULTS.get(key, "0")


async def get_party_channel_async(bot, db, guild=None):
    try:
        cid_val = setting(db, "deadzone_party_channel_id") or setting(db, "general_channel_id") or str(DEFAULT_GENERAL_CHANNEL_ID)
        cid = int(cid_val) if str(cid_val).isdigit() else DEFAULT_GENERAL_CHANNEL_ID
    except Exception:
        cid = DEFAULT_GENERAL_CHANNEL_ID

    ch = getattr(bot, "get_channel", lambda _id: None)(cid) if cid and hasattr(bot, "get_channel") else None
    if not ch and bot and cid and hasattr(bot, "fetch_channel"):
        try:
            ch = await bot.fetch_channel(cid)
        except Exception:
            ch = None

    if not ch and guild:
        if hasattr(guild, "get_channel"):
            try:
                ch = guild.get_channel(cid)
            except Exception:
                ch = None
        if not ch and hasattr(guild, "fetch_channel"):
            try:
                ch = await guild.fetch_channel(cid)
            except Exception:
                ch = None

    # Fallback: Find text channel named "general" or "chat" in guild
    if not ch and guild and hasattr(guild, "text_channels"):
        for c in guild.text_channels:
            if c.name.lower() in ("general", "chat", "main-chat", "💬-general"):
                ch = c
                break

    return ch or getattr(guild, "system_channel", None)


def get_party_channel(bot, db, guild=None):
    try:
        cid_val = setting(db, "deadzone_party_channel_id") or setting(db, "general_channel_id") or str(DEFAULT_GENERAL_CHANNEL_ID)
        cid = int(cid_val) if str(cid_val).isdigit() else DEFAULT_GENERAL_CHANNEL_ID
    except Exception:
        cid = DEFAULT_GENERAL_CHANNEL_ID
    ch = getattr(bot, "get_channel", lambda _id: None)(cid) if cid and hasattr(bot, "get_channel") else None
    if not ch and guild and hasattr(guild, "get_channel"):
        try:
            ch = guild.get_channel(cid)
        except Exception:
            ch = None
    if not ch and guild and hasattr(guild, "text_channels"):
        for c in guild.text_channels:
            if c.name.lower() in ("general", "chat", "main-chat", "💬-general"):
                ch = c
                break
    return ch or getattr(guild, "system_channel", None)


def get_notif_channel(bot, db, guild=None):
    try:
        cid_val = setting(db, "deadzone_notification_channel_id") or str(DEFAULT_NOTIF_CHANNEL_ID)
        cid = int(cid_val) if str(cid_val).isdigit() else DEFAULT_NOTIF_CHANNEL_ID
    except Exception:
        cid = DEFAULT_NOTIF_CHANNEL_ID
    ch = getattr(bot, "get_channel", lambda _id: None)(cid) if cid and hasattr(bot, "get_channel") else None
    if not ch and guild and hasattr(guild, "get_channel"):
        try:
            ch = guild.get_channel(cid)
        except Exception:
            ch = None
    return ch or getattr(guild, "system_channel", None)



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
    has_premium_music = any(r.id == PREMIUM_MUSIC_ROLE_ID for r in member.roles) or (PREMIUM_MUSIC_ROLE_ID in saved_role_ids)
    roles_to_add = []
    for rid in saved_role_ids:
        if has_premium_music and rid == REGULAR_MUSIC_ROLE_ID:
            continue
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
        try:
            await leveling.grant_xp(bot, db, member, bonus_xp, "message")
        except Exception as xp_err:
            print(f"[REVIVE] grant_xp warning: {xp_err}")

    now = int(time.time())
    db.execute(
        """UPDATE deadzone_members
        SET is_in_deadzone=0, last_active_at=?, resurrections_count=resurrections_count+1, saved_roles='[]', thaw_count=0
        WHERE user_id=?""",
        (now, member.id),
    )
    db.commit()

    party_channel = await get_party_channel_async(bot, db, getattr(member, "guild", None))
    party_duration = int(setting(db, "deadzone_party_duration") or 300)
    party_reward = int(setting(db, "deadzone_party_reward_cash") or 2000)

    # Announce resurrection technical alert in notification channel (bot-notifications)
    notif_channel = get_notif_channel(bot, db, getattr(member, "guild", None))
    if notif_channel:
        welcome_quote = random.choice(RESURRECTION_QUOTES)
        party_callout = ""
        target_party_ch = party_channel or notif_channel
        if target_party_ch and party_duration > 0:
            party_callout = (
                f"\n\n🎊 **Resurrection Welcome Party (5 Minutes):**\n"
                f"A celebration party has begun in {target_party_ch.mention}!\n"
                f"Chat there within the next 5 minutes to claim your **💵 +${party_reward:,} Cash** welcome bonus!"
            )
        embed = discord.Embed(
            title="⚡ [RESURRECTION ALERT]",
            description=(
                f"🎉 {member.mention} **has broken out of their coffin and returned to the living!**\n\n"
                f"🛡️ **Status Restored:** Member, Music, and Level {level} perks are active.\n"
                f"🎁 **Survival Bonus:** Received `💵 +{bonus_cash:,} Cash`, `🪙 +{bonus_xc} XC`, and `⭐ +{bonus_xp} XP`!{party_callout}\n\n"
                f"*{welcome_quote}*"
            ),
            color=0x2ECC71,
        )
        embed.set_footer(text="X BOT · Deadzone Division")
        try:
            await notif_channel.send(embed=embed)
        except (discord.HTTPException, discord.Forbidden):
            try:
                await notif_channel.send(
                    f"⚡ **[RESURRECTION ALERT]** 🎉 {member.mention} has returned to the living! Perks restored.\n"
                    f"Survival Bonus: `+${bonus_cash:,} Cash`, `+{bonus_xc} XC`, `+{bonus_xp} XP`.{party_callout}"
                )
            except Exception:
                pass

    # Start 5-minute Resurrection Welcome Party in general channel (Picture 1)
    target_party_ch = party_channel or notif_channel
    if target_party_ch and party_duration > 0 and getattr(member, "guild", None):
        allowed_channels = {target_party_ch.id, *LOUNGE_TEXT_CHANNEL_IDS}
        if notif_channel:
            allowed_channels.add(notif_channel.id)
        active_parties[member.guild.id] = {
            "expires_at": time.time() + party_duration,
            "revived_user_id": member.id,
            "revived_name": member.display_name,
            "channel_id": target_party_ch.id,
            "allowed_channel_ids": allowed_channels,
            "reward_cash": party_reward,
            "claimed_users": set(),
        }
        party_embed = discord.Embed(
            title="🎊 [WELCOME PARTY STARTED — 5 MINUTES]",
            description=(
                f"A celebration party has started for {member.mention}!\n\n"
                f"💬 **Chat in {target_party_ch.mention}** within the next **5 minutes** to claim your **💵 {party_reward:,} Cash** welcome bonus!\n"
                f"-# One claim per member · Say hi and celebrate their return!"
            ),
            color=0xF1C40F,
        )
        party_embed.set_footer(text="X BOT · Deadzone Division · Welcome Party")
        party_posted = False
        try:
            await target_party_ch.send(embed=party_embed)
            party_posted = True
        except (discord.HTTPException, discord.Forbidden):
            try:
                await target_party_ch.send(
                    f"🎊 **[WELCOME PARTY STARTED — 5 MINUTES]**\n"
                    f"A celebration party has started for {member.mention}!\n"
                    f"💬 Chat in this channel within 5 minutes to claim your **💵 {party_reward:,} Cash** welcome bonus!"
                )
                party_posted = True
            except Exception as e:
                print(f"[PARTY] Error posting party embed to {target_party_ch}: {e}")

        # If sending to primary party channel failed, fallback to notif_channel
        if not party_posted and notif_channel and notif_channel.id != target_party_ch.id:
            try:
                await notif_channel.send(embed=party_embed)
            except Exception:
                pass

    return True


def build_deadzone_board_embed(db=None):
    """Build the official crypt announcement embed displayed with the revival button."""
    database = db or _db
    bonus_cash = int(setting(database, "deadzone_revive_bonus_cash") or 100000) if database else 100000
    bonus_xc = int(setting(database, "deadzone_revive_bonus_xc") or 150) if database else 150
    bonus_xp = int(setting(database, "deadzone_revive_bonus_xp") or 100) if database else 100

    rescue_cash = int(setting(database, "deadzone_rescue_reward_cash") or 50000) if database else 50000
    rescue_xc = int(setting(database, "deadzone_rescue_reward_xc") or 250) if database else 250
    rescue_xp = int(setting(database, "deadzone_rescue_reward_xp") or 150) if database else 150

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
            "1. **Condition 1 (Thaw Out):**\n"
            "   • **Chat:** Send **5 messages** in text channels to melt your seal, **OR**\n"
            "   • **Lounge Voice:** Hang out in **Lounge 1~5 VC** (defrosts **+1** every 3 minutes)!\n"
            "2. **Condition 2 (Teammate Rescue):**\n"
            "   • Once thawed (**5/5**), have an active comrade rescue you with:\n"
            "     **`/deadzone rescue member:@you`** or click **`[ 🤝 Rescue Teammate ]`** below.\n\n"
            "🎁 **Resurrection Rewards (For You):**\n"
            "• Instant restoration of **Member**, **Music** *(or Premium Music)*, and all earned **Level rank tags**.\n"
            f"• **`+${bonus_cash:,} Cash`** & **`+{bonus_xc} XC`** survival bonus added to your balance!\n"
            f"• **`+{bonus_xp} XP`** activity boost!\n\n"
            "💰 **Hero Rescue Bounty (For Rescuer):**\n"
            f"• Rescuer receives **`+${rescue_cash:,} Cash`**, **`+{rescue_xc} XC`**, and **`+{rescue_xp} XP`** bounty!"
        ),
        color=0x4A4D52,
    )
    embed.set_footer(text="X BOT · Deadzone Division · 1+2 Respawn Protocol")
    return embed


async def update_crypt_board(bot, db):
    """Update existing crypt announcement message if recorded in economy_settings."""
    channel_id = int(setting(db, "deadzone_crypt_channel_id") or 0)
    message_id = int(setting(db, "deadzone_crypt_message_id") or 0)
    if not channel_id or not message_id:
        return False
    channel = bot.get_channel(channel_id)
    if not channel:
        return False
    try:
        msg = await channel.fetch_message(message_id)
        if msg:
            embed = build_deadzone_board_embed(db)
            await msg.edit(embed=embed, view=DeadzoneReviveView())
            return True
    except (discord.HTTPException, discord.NotFound):
        pass
    return False


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
            allowed_channels = party.get("allowed_channel_ids", {party["channel_id"]})
            if message.channel.id in allowed_channels and message.author.id != party["revived_user_id"]:
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
                            f"*(Rescuers receive **+$50,000 Cash, +250 XC & +150 XP** for pulling you out of the crypt!)*"
                        ),
                        color=0x3498DB,
                    )
                    embed.set_footer(text="X BOT · Deadzone Division · 1+2 Respawn Protocol")
                    notif_channel = get_notif_channel(bot or _bot, db, message.guild)
                    # Picture 2: Do NOT post to general; post to notif_channel
                    if notif_channel:
                        await notif_channel.send(embed=embed)
                    elif message.channel.id != DEFAULT_GENERAL_CHANNEL_ID:
                        await message.channel.send(embed=embed)
                    await message.add_reaction("🧊")
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
    @app_commands.describe(member="Member currently sleeping in the Deadzone (optional: defaults to yourself)")
    async def dz_wake(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        if not isinstance(target, discord.Member) and interaction.guild:
            target = interaction.guild.get_member(target.id) or target

        status = member_status(db, target.id)
        if not status or not status["is_in_deadzone"]:
            msg = f"{target.mention} is not in the Deadzone! They are already active." if member else "You are not in the Deadzone! You are already active."
            await interaction.response.send_message(msg, ephemeral=True)
            return

        touch_activity(db, interaction.user.id)

        # If Admin or Staff: directly wake up and revive them!
        if is_council_or_admin(interaction):
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            try:
                success = await revive_member(bot, db, target, triggered_by="admin_wake")
                if success:
                    await interaction.followup.send(f"⚡ **Wake Up Successful!** Revived {target.mention} from Deadzone. All Member, Music, and Level perks restored.", ephemeral=True)
                else:
                    await interaction.followup.send(f"⚠️ Failed to revive {target.mention}.", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"⚠️ Error while reviving {target.mention}: {e}", ephemeral=True)
            return

        # For regular members:
        thaw = status["thaw_count"] if "thaw_count" in status.keys() else 0
        if target.id == interaction.user.id:
            await interaction.response.send_message(
                f"🧊 **You are resting in the Deadzone crypt!**\n"
                f"Thaw progress: **{thaw}/5 messages**.\n"
                f"Chat **{max(0, 5 - thaw)} more time(s)** in #general or Lounges to thaw, then have a comrade rescue you with `/deadzone rescue`!",
                ephemeral=True,
            )
            return

        try:
            await target.send(
                f"📢 **WAKE UP CALL FROM {interaction.user.display_name}!**\n"
                f"You are resting in the Deadzone in **{interaction.guild.name}**.\n"
                f"Thaw progress: **{thaw}/5 messages**.\n"
                f"Post {max(0, 5 - thaw)} more message(s) in chat to thaw, then have a comrade rescue you with `/deadzone rescue`!"
            )
            await interaction.response.send_message(f"🔔 Sent a direct wake-up call to {target.mention} (Thaw progress: {thaw}/5)!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"📢 {target.mention}, wake up! {interaction.user.mention} is calling you from #general! Send messages in chat to thaw ({thaw}/5) then get rescued!",
            )
        except Exception:
            await interaction.response.send_message(f"🔔 Sent a direct wake-up call to {target.mention}!", ephemeral=True)

    @deadzone_group.command(name="rescue", description="Rescue a thawed teammate from the Deadzone and earn Hero rewards")
    @app_commands.describe(member="Thawed teammate sleeping in Deadzone (optional: auto-targets thawed member if left empty)")
    async def dz_rescue(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        my_status = member_status(db, interaction.user.id)
        if my_status and my_status["is_in_deadzone"]:
            await interaction.response.send_message("💀 You cannot rescue others while trapped in the Deadzone yourself! Thaw out first.", ephemeral=True)
            return

        target_member = member
        if target_member is None:
            # Auto-detect any thawed comrade (is_in_deadzone=1 and thaw_count>=5)
            ready_sleepers = db.execute(
                "SELECT user_id, thaw_count FROM deadzone_members WHERE is_in_deadzone=1 AND thaw_count>=5 ORDER BY thaw_count DESC"
            ).fetchall()
            valid_sleepers = [r for r in ready_sleepers if r["user_id"] != interaction.user.id]

            for s_row in valid_sleepers:
                uid = s_row["user_id"]
                m = interaction.guild.get_member(uid)
                if not m:
                    try:
                        m = await interaction.guild.fetch_member(uid)
                    except (discord.NotFound, discord.HTTPException):
                        m = None
                if m:
                    target_member = m
                    break

            if not target_member:
                all_sleepers = db.execute("SELECT user_id, thaw_count FROM deadzone_members WHERE is_in_deadzone=1").fetchall()
                valid_all = [r for r in all_sleepers if r["user_id"] != interaction.user.id]
                if not valid_all:
                    await interaction.response.send_message("🎉 No comrades are currently trapped in the Deadzone! Everyone is active.", ephemeral=True)
                    return

                lines = [f"• <@{r['user_id']}> (Thaw progress: **{min(r['thaw_count'] if 'thaw_count' in r.keys() else 0, 5)}/5** messages)" for r in valid_all[:10]]
                await interaction.response.send_message(
                    "🧊 **No comrades are fully thawed yet!**\n"
                    "Comrades must chat in channels to reach **5/5 thaw** before they can be rescued:\n\n"
                    + "\n".join(lines),
                    ephemeral=True,
                )
                return
        else:
            if interaction.user.id == target_member.id:
                await interaction.response.send_message("❌ You cannot rescue yourself! A living comrade must pull you out of the crypt.", ephemeral=True)
                return

            target_status = member_status(db, target_member.id)
            if not target_status or not target_status["is_in_deadzone"]:
                await interaction.response.send_message(f"{target_member.mention} is not in the Deadzone! They are already active.", ephemeral=True)
                return

            thaw_count = target_status["thaw_count"] if "thaw_count" in target_status.keys() else 0
            if thaw_count < 5:
                await interaction.response.send_message(
                    f"🧊 {target_member.mention} is still frozen in cryo-stasis (**{thaw_count}/5 messages**).\n"
                    f"They need to send **{5 - thaw_count} more message(s)** in chat before you can rescue them!",
                    ephemeral=True,
                )
                return

        # Target member confirmed and ready to be rescued
        await interaction.response.defer(ephemeral=False)

        try:
            success = await revive_member(bot, db, target_member, triggered_by=f"rescued_by_{interaction.user.id}")
            if not success:
                await interaction.followup.send(f"⚠️ Failed to rescue {target_member.mention}. They may have already been revived.")
                return

            reward_cash = int(setting(db, "deadzone_rescue_reward_cash") or 50000)
            reward_xc = int(setting(db, "deadzone_rescue_reward_xc") or 250)
            reward_xp = int(setting(db, "deadzone_rescue_reward_xp") or 150)
            leveling._ensure_economy_player(db, interaction.user)
            db.execute("UPDATE players SET xc = xc + ?, money = money + ? WHERE user_id = ?", (reward_xc, reward_cash, interaction.user.id))
            touch_activity(db, interaction.user.id)
            db.commit()

            try:
                await leveling.grant_xp(bot, db, interaction.user, reward_xp, "rescue_comrade")
            except Exception as xp_err:
                print(f"[DZ RESCUE] grant_xp warning: {xp_err}")

            embed = discord.Embed(
                title="🤝 [RESCUE OPERATION COMPLETE]",
                description=(
                    f"🎉 {interaction.user.mention} **bravely pulled** {target_member.mention} **out of the Deadzone crypt!**\n\n"
                    f"🛡️ **{target_member.mention}** has returned to the living! Perks and rank tags restored.\n\n"
                    f"💰 **Hero Rescue Rewards:**\n"
                    f"• 💵 **+${reward_cash:,} Cash**\n"
                    f"• 🪙 **+{reward_xc} XC**\n"
                    f"• ⭐ **+{reward_xp} XP**"
                ),
                color=0x2ECC71,
            )
            embed.set_footer(text="X BOT · Deadzone Division · 1+2 Respawn System")

            notif_ch = get_notif_channel(bot, db, interaction.guild)
            if notif_ch and notif_ch.id != interaction.channel_id:
                try:
                    await notif_ch.send(embed=embed)
                except (discord.HTTPException, discord.Forbidden):
                    pass

            try:
                await interaction.followup.send(embed=embed)
            except (discord.HTTPException, discord.Forbidden):
                fallback_msg = (
                    f"🤝 **[RESCUE OPERATION COMPLETE]**\n"
                    f"🎉 {interaction.user.mention} **bravely pulled** {target_member.mention} **out of the Deadzone crypt!**\n\n"
                    f"🛡️ **{target_member.mention}** has returned to the living! Perks and rank tags restored.\n"
                    f"💰 **Hero Rescue Rewards:** `+${reward_cash:,} Cash`, `+{reward_xc} XC`, `+{reward_xp} XP`"
                )
                await interaction.followup.send(fallback_msg)

        except Exception as err:
            traceback.print_exc()
            await interaction.followup.send(f"⚠️ An error occurred while executing the rescue: `{err}`")

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

    @deadzone_group.command(name="set_party_channel", description="Admin: Set the channel where Welcome Back Parties are held")
    @app_commands.describe(channel="Text channel for Welcome Back Parties (e.g. #general)")
    async def dz_set_party_channel(interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators can configure the party channel."), ephemeral=True)
            return

        db.execute(
            "INSERT INTO economy_settings(key, value) VALUES('deadzone_party_channel_id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(channel.id),)
        )
        db.commit()
        await interaction.response.send_message(
            f"✅ **Party Channel Configured:** Welcome Back Parties will now be held in {channel.mention} (`{channel.id}`).",
            ephemeral=True
        )

    @deadzone_group.command(name="channels", description="Staff: View current Deadzone channel configuration")
    async def dz_channels(interaction: discord.Interaction):
        p_cid = setting(db, "deadzone_party_channel_id") or str(DEFAULT_GENERAL_CHANNEL_ID)
        n_cid = setting(db, "deadzone_notification_channel_id") or str(DEFAULT_NOTIF_CHANNEL_ID)
        c_cid = setting(db, "deadzone_crypt_channel_id") or "Not configured"

        party_mention = f"<#{p_cid}>" if p_cid.isdigit() else p_cid
        notif_mention = f"<#{n_cid}>" if n_cid.isdigit() else n_cid
        crypt_mention = f"<#{c_cid}>" if c_cid.isdigit() else c_cid

        msg = (
            f"### ⚙️ Deadzone Channel Configuration:\n"
            f"• 🎊 **Party Channel (Welcome Back Party):** {party_mention} (`{p_cid}`)\n"
            f"• 📢 **Notification Channel (Alerts):** {notif_mention} (`{n_cid}`)\n"
            f"• ⚰️ **Crypt Channel (Status Board):** {crypt_mention} (`{c_cid}`)\n\n"
            f"-# Use `/deadzone set_party_channel #channel` to change the Welcome Party channel."
        )
        await interaction.response.send_message(msg, ephemeral=True)

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
