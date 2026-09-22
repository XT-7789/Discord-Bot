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
    "deadzone_revive_bonus_xc": "150",
    "deadzone_revive_bonus_xp": "50",
    "deadzone_scavenge_cooldown": "72000",
}


_bot = None
_db = None


def initialise(db):
    """Initialise database tables and default configuration settings for Deadzone."""
    global _db
    _db = db
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))

    db.execute("""CREATE TABLE IF NOT EXISTS deadzone_members (
        user_id INTEGER PRIMARY KEY,
        last_active_at INTEGER NOT NULL,
        is_in_deadzone INTEGER NOT NULL DEFAULT 0,
        deadzone_entered_at INTEGER NOT NULL DEFAULT 0,
        resurrections_count INTEGER NOT NULL DEFAULT 0,
        saved_roles TEXT NOT NULL DEFAULT '[]'
    )""")

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
    for command_name in ("deadzone_send", "deadzone_restore", "deadzone_scan", "deadzone_post"):
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
    guest_role_id = int(setting(db, "deadzone_guest_role_id") or 0)

    privilege_role_ids = _get_privilege_role_ids(db)
    roles_to_remove = [r for r in member.roles if r.id in privilege_role_ids]
    saved_ids = [r.id for r in roles_to_remove]

    roles_to_add = []
    if deadzone_role_id:
        dz_role = member.guild.get_role(deadzone_role_id)
        if dz_role and dz_role not in member.roles:
            roles_to_add.append(dz_role)

    if guest_role_id:
        g_role = member.guild.get_role(guest_role_id)
        if g_role and g_role not in member.roles:
            roles_to_add.append(g_role)

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
        SET is_in_deadzone=1, deadzone_entered_at=?, saved_roles=?
        WHERE user_id=?""",
        (now, json.dumps(saved_ids), member.id),
    )
    db.commit()

    # Post notification in crypt channel if configured
    crypt_channel_id = int(setting(db, "deadzone_crypt_channel_id") or 0)
    channel = bot.get_channel(crypt_channel_id) if crypt_channel_id else None
    if channel:
        embed = discord.Embed(
            title="🪦 [TOMBSTONE ERECTED]",
            description=(
                f"**Operative:** {member.mention}\n"
                f"**Status:** Cryo-Stasis / Demoted to Deadzone\n"
                f"**Reason:** {reason}\n"
                f"⚠️ *Member, Music, and Rank perks revoked. Assigned Guest status.*\n\n"
                f"*\"May they rest in peace... until they shatter their coffin.\"*"
            ),
            color=0x4A4D52,
        )
        embed.set_footer(text="X BOT · Deadzone Division · Click the pinned Break Out button above or chat in lounge to revive")
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

    # Retrieve member's level and restore reward roles (Member, Music, Level tags)
    prof = leveling.profile(db, member.id)
    level = prof["level"]
    await leveling.sync_reward_roles(db, member, level)

    # Award revival bonus
    bonus_xc = int(setting(db, "deadzone_revive_bonus_xc") or 0)
    bonus_xp = int(setting(db, "deadzone_revive_bonus_xp") or 0)

    if bonus_xc > 0:
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (bonus_xc, member.id))
    if bonus_xp > 0:
        await leveling.grant_xp(bot, db, member, bonus_xp, "message")

    now = int(time.time())
    db.execute(
        """UPDATE deadzone_members
        SET is_in_deadzone=0, last_active_at=?, resurrections_count=resurrections_count+1, saved_roles='[]'
        WHERE user_id=?""",
        (now, member.id),
    )
    db.commit()

    # Announce resurrection in lounge or crypt
    lounge_channel_id = int(setting(db, "deadzone_lounge_channel_id") or 0)

    target_channel = None
    if lounge_channel_id:
        target_channel = bot.get_channel(lounge_channel_id)
    if not target_channel:
        target_channel = member.guild.system_channel

    embed = discord.Embed(
        title="⚡ [RESURRECTION ALERT]",
        description=(
            f"🎉 {member.mention} **has broken out of their coffin and returned to the living!**\n\n"
            f"🛡️ **Status Restored:** Member, Music, and Level {level} perks are active.\n"
            f"🎁 **Survival Bonus:** Received `+{bonus_xc} XC` and `+{bonus_xp} XP`!\n\n"
            f"*Welcome back to the squad, soldier!*"
        ),
        color=0x2ECC71,
    )
    embed.set_footer(text="X BOT · Deadzone Division")

    if target_channel:
        try:
            await target_channel.send(embed=embed)
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
            "• Status reduced to **Guest**.\n\n"
            "───\n\n"
            "### ⚡ HOW TO RESURRECT & RESTORE PERKS:\n"
            "1. Click the green **`[ ⚡ Break Out of Coffin ]`** button below.\n"
            "2. **OR** simply post any message in a public channel like `#lounge`.\n\n"
            "🎁 **Resurrection Bonus:**\n"
            "• Instant restoration of **Member**, **Music**, and all earned **Level rank tags**.\n"
            "• **`+150 XC`** survival bonus added to your balance.\n"
            "• **`+50 XP`** activity boost!"
        ),
        color=0x4A4D52,
    )
    embed.set_footer(text="X BOT · Deadzone Division · Click the button below to revive anytime")
    return embed


class DeadzoneReviveView(discord.ui.View):
    """Persistent UI view with a Break Out button."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⚡ Break Out of Coffin", style=discord.ButtonStyle.success, custom_id="xbot:deadzone:breakout", emoji="⚰️")
    async def breakout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Only members in the server can use this button.", ephemeral=True)
            return

        db = getattr(interaction.client, "db", None) or _db
        bot = interaction.client or _bot
        if not db:
            await interaction.response.send_message("Database unavailable. Please try again shortly.", ephemeral=True)
            return

        status = member_status(db, interaction.user.id)
        if not status or not status["is_in_deadzone"]:
            await interaction.response.send_message("You are not in the Deadzone. You are already alive and kicking!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await revive_member(bot, db, interaction.user, triggered_by="button")
        await interaction.followup.send("⚡ **Resurrection successful!** Your Member and Level perks have been restored. Welcome back!", ephemeral=True)


async def handle_message(bot, db, message: discord.Message):
    """Listen for chat messages to update activity timestamps and trigger resurrection."""
    if not message.guild or message.author.bot:
        return

    if setting(db, "deadzone_enabled") != "1":
        return

    touch_activity(db, message.author.id)

    status = member_status(db, message.author.id)
    if status and status["is_in_deadzone"]:
        if isinstance(message.author, discord.Member):
            await revive_member(bot, db, message.author, triggered_by="message")


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

        state_text = "💀 In Deadzone (Demoted to Guest)" if my_status["is_in_deadzone"] else "🟢 Alive & Active"

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

    @deadzone_group.command(name="wake", description="Remind a sleeping friend to return and break out of the Deadzone")
    @app_commands.describe(member="Member currently sleeping in the Deadzone")
    async def dz_wake(interaction: discord.Interaction, member: discord.Member):
        status = member_status(db, member.id)
        if not status or not status["is_in_deadzone"]:
            await interaction.response.send_message(f"{member.mention} is not in the Deadzone! They are already alive.", ephemeral=True)
            return

        touch_activity(db, interaction.user.id)

        try:
            await member.send(
                f"📢 **WAKE UP CALL!**\n"
                f"Your teammate {interaction.user.mention} is calling for you in **{interaction.guild.name}**!\n"
                f"You are currently resting in the Deadzone. Post a message or click the **Break Out** button to restore your Member and Level perks!"
            )
            await interaction.response.send_message(f"🔔 Sent a direct wake-up call to {member.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"📢 {member.mention}, wake up! {interaction.user.mention} is calling you from the living lounge! Type a message to revive!",
            )

    @deadzone_group.command(name="post", description="Admin: Post the official Deadzone resurrection board with the Break Out button")
    @app_commands.describe(channel="Channel to post the board in (defaults to current channel)")
    async def dz_post(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators can post the Deadzone board."), ephemeral=True)
            return

        dest = channel or interaction.channel
        if not isinstance(dest, discord.TextChannel):
            await interaction.response.send_message(view=xbot_ui.danger("Invalid Channel", "Please choose a valid text channel."), ephemeral=True)
            return

        embed = build_deadzone_board_embed()
        await dest.send(embed=embed, view=DeadzoneReviveView())
        db.execute(
            "INSERT INTO economy_settings(key,value) VALUES('deadzone_crypt_channel_id',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(dest.id),),
        )
        db.commit()
        await interaction.response.send_message(f"✅ Deadzone Crypt resurrection board posted in {dest.mention} and saved as the active crypt channel!", ephemeral=True)

    # Top-level standalone staff commands (accessible from staff_tools Admin Panel and slash)
    @bot.tree.command(name="deadzone_send", description="Admin: Demote an inactive member to Deadzone", **STAFF_COMMAND_KWARGS)
    @app_commands.describe(member="Member to demote", reason="Reason for demotion")
    async def deadzone_send(interaction: discord.Interaction, member: discord.Member, reason: str = "Admin panel decision"):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators and Staff can manage Deadzone members."), ephemeral=True)
            return

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

        await interaction.response.defer(ephemeral=True)
        demoted = await scan_guild_inactivity(bot, db, interaction.guild)
        names = ", ".join(m.display_name for m in demoted) if demoted else "None"
        await interaction.followup.send(f"🔍 **Deadzone Scan Complete.** Demoted `{len(demoted)}` members: {names}", ephemeral=True)

    @bot.tree.command(name="deadzone_post", description="Admin: Post the Deadzone board with the Break Out button", **STAFF_COMMAND_KWARGS)
    @app_commands.describe(channel="Channel to post the board in (defaults to current channel)")
    async def deadzone_post(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators can post the Deadzone board."), ephemeral=True)
            return

        dest = channel or interaction.channel
        if not isinstance(dest, discord.TextChannel):
            await interaction.response.send_message(view=xbot_ui.danger("Invalid Channel", "Please choose a valid text channel."), ephemeral=True)
            return

        embed = build_deadzone_board_embed()
        await dest.send(embed=embed, view=DeadzoneReviveView())
        db.execute(
            "INSERT INTO economy_settings(key,value) VALUES('deadzone_crypt_channel_id',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(dest.id),),
        )
        db.commit()
        await interaction.response.send_message(f"✅ Deadzone Crypt resurrection board posted in {dest.mention} and saved as the active crypt channel!", ephemeral=True)

    bot.tree.add_command(deadzone_group)
