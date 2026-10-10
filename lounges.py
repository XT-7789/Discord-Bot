"""X BOT Dynamic Lounge Reservation & Auto-Clear System.

Manages 5 dedicated server Lounges (Text + Voice channels):
- Members can request/reserve a lounge with custom duration and privacy (Public/Private).
- Host receives an interactive control hub in the lounge text channel to invite/kick members,
  toggle privacy, extend time, and end session early.
- When expired: automatically evicts VC members, purges text chat for privacy, resets permissions,
  and re-opens the lounge.
- Provides a persistent Public Lounge Lobby Panel for easy one-click booking.
"""

import asyncio
import json
import random
import time
from typing import Optional, Set

import discord
from discord import app_commands
from discord.ext import tasks

import deadzone
import leveling

# Configuration for the 5 server lounges
LOUNGES = {
    1: {"name": "Lounge 1", "text_id": 1538463993910403074, "vc_id": 1538463656201945158},
    2: {"name": "Lounge 2", "text_id": 1544726174083846244, "vc_id": 1544727470224179280},
    3: {"name": "Lounge 3", "text_id": 1551191838780559460, "vc_id": 1544729661567795210},
    4: {"name": "Lounge 4", "text_id": 1544735099877064865, "vc_id": 1544735358728540270},
    5: {"name": "Lounge 5", "text_id": 1544735123596120125, "vc_id": 1544735436436410388},
}

LOUNGE_TEXT_CHANNEL_IDS: Set[int] = {info["text_id"] for info in LOUNGES.values()}
LOUNGE_VC_CHANNEL_IDS: Set[int] = {info["vc_id"] for info in LOUNGES.values()}

DEFAULT_SETTINGS = {
    "lounge_enabled": "1",
    "lounge_default_duration_mins": "60",
    "lounge_max_duration_mins": "300",  # 5 hours maximum session with extensions
    "lounge_cooldown_seconds": "900",  # 15 minutes cooldown after hosting
    "lounge_lobby_channel_id": "0",
    "lounge_lobby_message_id": "0",
    "verification_member_role_id": "1505437941647015986",
    "lounge_empty_timeout_seconds": "90",
    "lounge_empty_grace_seconds": "180",
}

_bot = None
_db = None
_user_vc_duration = {}
_user_dz_thaw_seconds = {}
_squad_ping_cooldowns = {}
_lounge_squad_time = {}
_lounge_empty_since = {}
_lounge_empty_warned = {}
_lounge_session_rewards = {}


def setting(db, key):
    try:
        row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return DEFAULT_SETTINGS.get(key, "0")
        if hasattr(row, "keys"):
            return str(row["value"])
        return str(row[0])
    except Exception:
        return DEFAULT_SETTINGS.get(key, "0")


def set_setting(db, key, value):
    db.execute(
        "INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    db.commit()


def initialise(db):
    """Ensure database tables for server lounges exist."""
    db.execute("""
    CREATE TABLE IF NOT EXISTS server_lounges (
        lounge_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        text_channel_id INTEGER NOT NULL,
        vc_channel_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'available',
        host_user_id INTEGER DEFAULT 0,
        host_name TEXT DEFAULT '',
        privacy TEXT NOT NULL DEFAULT 'private',
        reserved_at INTEGER DEFAULT 0,
        expires_at INTEGER DEFAULT 0,
        invited_user_ids TEXT DEFAULT '[]',
        warned_5m INTEGER DEFAULT 0,
        warned_1m INTEGER DEFAULT 0,
        control_message_id INTEGER DEFAULT 0
    )
    """)

    for lid, info in LOUNGES.items():
        db.execute(
            """INSERT OR IGNORE INTO server_lounges(lounge_id, name, text_channel_id, vc_channel_id, status)
            VALUES(?,?,?,?,'available')""",
            (lid, info["name"], info["text_id"], info["vc_id"]),
        )

    for key, val in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, val))

    # Ensure max duration is set to 300 minutes (5 hours)
    db.execute("INSERT INTO economy_settings(key,value) VALUES('lounge_max_duration_mins','300') ON CONFLICT(key) DO UPDATE SET value='300'")

    db.commit()


def get_lounge_row(db, lounge_id: int):
    row = db.execute("SELECT * FROM server_lounges WHERE lounge_id=?", (lounge_id,)).fetchone()
    return dict(row) if row else None


def get_all_lounges(db):
    rows = db.execute("SELECT * FROM server_lounges ORDER BY lounge_id ASC").fetchall()
    return [dict(r) for r in rows]


def get_active_lounge_by_host(db, user_id: int):
    row = db.execute("SELECT * FROM server_lounges WHERE host_user_id=? AND status='occupied'", (user_id,)).fetchone()
    return dict(row) if row else None


def get_lounge_by_channel(db, channel_id: int):
    row = db.execute(
        "SELECT * FROM server_lounges WHERE text_channel_id=? OR vc_channel_id=?",
        (channel_id, channel_id),
    ).fetchone()
    return dict(row) if row else None


# ---------- Discord Channel Overwrite / Permission Logic ----------

async def apply_lounge_permissions(guild: discord.Guild, lounge_id: int, host_member: discord.Member, privacy: str, invited_ids: list):
    """Set up permissions for text and voice channels based on host and privacy mode."""
    info = LOUNGES.get(lounge_id)
    if not info or not guild:
        return

    text_ch = guild.get_channel(info["text_id"])
    vc_ch = guild.get_channel(info["vc_id"])

    # 1. Base overwrite for @everyone
    if privacy == "private":
        # Private: hidden/locked from public
        text_everyone = discord.PermissionOverwrite(view_channel=False, send_messages=False, read_message_history=False)
        vc_everyone = discord.PermissionOverwrite(view_channel=False, connect=False, speak=False)
    else:
        # Public: open to all server members
        text_everyone = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        vc_everyone = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)

    if text_ch:
        try:
            await text_ch.set_permissions(guild.default_role, overwrite=text_everyone)
            # Host full access
            await text_ch.set_permissions(
                host_member,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, embed_links=True,
                    attach_files=True, read_message_history=True, mention_everyone=False
                ),
            )
            # Invited members access
            for uid in invited_ids:
                m = guild.get_member(uid)
                if m:
                    await text_ch.set_permissions(
                        m,
                        overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                    )
        except discord.HTTPException:
            pass

    if vc_ch:
        try:
            await vc_ch.set_permissions(guild.default_role, overwrite=vc_everyone)
            # Host full access
            await vc_ch.set_permissions(
                host_member,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, connect=True, speak=True,
                    stream=True, use_voice_activation=True, move_members=True
                ),
            )
            # Invited members access
            for uid in invited_ids:
                m = guild.get_member(uid)
                if m:
                    await vc_ch.set_permissions(
                        m,
                        overwrite=discord.PermissionOverwrite(
                            view_channel=True, connect=True, speak=True, stream=True, use_voice_activation=True
                        ),
                    )
        except discord.HTTPException:
            pass


async def reset_lounge_permissions(guild: discord.Guild, lounge_id: int):
    """Reset text and VC permissions to standard default (available to everyone)."""
    info = LOUNGES.get(lounge_id)
    if not info or not guild:
        return

    text_ch = guild.get_channel(info["text_id"])
    vc_ch = guild.get_channel(info["vc_id"])

    if text_ch:
        try:
            for target in list(text_ch.overwrites.keys()):
                if isinstance(target, (discord.Member, discord.User)):
                    await text_ch.set_permissions(target, overwrite=None)
            # Idle state (no active booking): completely hidden from everyone until booked by a host
            await text_ch.set_permissions(
                guild.default_role,
                overwrite=discord.PermissionOverwrite(view_channel=False, send_messages=False, read_message_history=False),
            )
        except discord.HTTPException:
            pass

    if vc_ch:
        try:
            for target in list(vc_ch.overwrites.keys()):
                if isinstance(target, (discord.Member, discord.User)):
                    await vc_ch.set_permissions(target, overwrite=None)
            # Idle state (no active booking): completely hidden from everyone until booked by a host
            await vc_ch.set_permissions(
                guild.default_role,
                overwrite=discord.PermissionOverwrite(view_channel=False, connect=False, speak=False),
            )
        except discord.HTTPException:
            pass


async def clear_and_reopen_lounge(bot: discord.Client, db, guild: discord.Guild, lounge_id: int, reason: str = "Time limit expired"):
    """Core reset engine: disconnects VC members, purges chat, resets permissions, and marks open."""
    info = LOUNGES.get(lounge_id)
    if not info:
        return False

    # 1. Evict any members remaining in the VC channel
    if guild:
        vc = guild.get_channel(info["vc_id"])
        if vc and hasattr(vc, "members"):
            for m in list(vc.members):
                try:
                    await m.move_to(None)
                except Exception:
                    pass

    # 2. Purge messages and unpin in the text channel
    if guild:
        tc = guild.get_channel(info["text_id"])
        if tc:
            try:
                for p in await tc.pins():
                    try:
                        await p.unpin()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                await tc.purge(limit=100)
            except Exception:
                pass

    # 3. Reset Discord permission overwrites
    if guild:
        await reset_lounge_permissions(guild, lounge_id)

    # 4. Update database state
    db.execute(
        """UPDATE server_lounges
        SET status='available', host_user_id=0, host_name='', privacy='private',
            reserved_at=0, expires_at=0, invited_user_ids='[]', warned_5m=0, warned_1m=0, control_message_id=0
        WHERE lounge_id=?""",
        (lounge_id,),
    )
    db.commit()
    _lounge_squad_time.pop(lounge_id, None)
    _lounge_empty_since.pop(lounge_id, None)
    _lounge_empty_warned.pop(lounge_id, None)
    session_rewards = _lounge_session_rewards.pop(lounge_id, None)

    # 5. Announce session settlement & clean reopen in the text channel
    if guild:
        tc = guild.get_channel(info["text_id"])
        if tc:
            if session_rewards:
                total_cash = sum(s["cash"] for s in session_rewards.values())
                total_xc = sum(s["xc"] for s in session_rewards.values())
                total_xp = sum(s["xp"] for s in session_rewards.values())
                breakdown = [
                    f"• **{s['name']}** ({s['minutes']}m in VC): **+${s['cash']:,} Cash**, **+{s['xc']:,} XC**, **+{s['xp']:,} XP**"
                    for s in sorted(session_rewards.values(), key=lambda x: x["cash"], reverse=True)
                ]
                summary_embed = discord.Embed(
                    title="📊 [LOUNGE SESSION VOICE SETTLEMENT]",
                    description=(
                        f"🎙️ **Session Earnings Summary:**\n"
                        f"💰 **Total Cash: +${total_cash:,}** · 💎 **Total XC: +{total_xc:,}** · ⭐ **Total XP: +{total_xp:,}**\n\n"
                        f"**Participant Breakdown:**\n" + "\n".join(breakdown[:15]) +
                        f"\n\n-# All rewards have been deposited directly into participants' accounts."
                    ),
                    color=0xF1C40F,
                )
                summary_embed.set_footer(text="X BOT · Lounge Voice Activity Protocol")
                try:
                    await tc.send(embed=summary_embed)
                except Exception:
                    pass

            embed = discord.Embed(
                title=f"🟢 [{info['name'].upper()} IS NOW OPEN]",
                description=(
                    f"✨ **This lounge has been thoroughly sanitized and reopened!**\n\n"
                    f"🛋️ **How to Reserve:**\n"
                    f"• Visit the Lounge Lobby panel, or run `/lounge menu`\n"
                    f"• Choose your session duration and privacy setting\n"
                    f"• Invite your friends and squadmates for private chats & voice sessions!\n\n"
                    f"-# Reset reason: `{reason}`"
                ),
                color=0x2ECC71,
            )
            embed.set_footer(text="X BOT · Lounge Management Service")
            try:
                await tc.send(embed=embed)
            except Exception:
                pass

    # 6. Update the public lobby message if configured
    await refresh_lobby_message(bot, db, guild)
    return True


# ---------- Lounge UI Views & Embeds ----------

def build_lobby_embed(db):
    """Build the public real-time Lounge Lobby status board."""
    lounges_list = get_all_lounges(db)
    now = int(time.time())

    lines = []
    available_count = 0

    for l in lounges_list:
        lid = l["lounge_id"]
        name = l["name"]
        status = l["status"]
        text_id = l["text_channel_id"]
        vc_id = l["vc_channel_id"]

        if status == "occupied":
            rem = max(0, l["expires_at"] - now)
            mins = rem // 60
            host_text = f"<@{l['host_user_id']}>"
            priv_badge = "🔒 Private" if l["privacy"] == "private" else "🌐 Public"

            # Check if currently empty / pending auto-clean
            empty_info = ""
            if lid in _lounge_empty_since:
                empty_timeout = int(setting(db, "lounge_empty_timeout_seconds") or 90)
                reset_time = _lounge_empty_since[lid] + empty_timeout
                empty_info = f"\n   ⚠️ *VC Empty — auto-resetting <t:{reset_time}:R>*"

            lines.append(
                f"🔴 **{name}** · **Occupied** by {host_text} ({priv_badge})\n"
                f"   ⏳ Remaining: `{mins} mins` (<t:{l['expires_at']}:R>)\n"
                f"   Channels: <#{text_id}> · <#{vc_id}>{empty_info}"
            )
        else:
            available_count += 1
            lines.append(
                f"🟢 **{name}** · **Available**\n"
                f"   ✨ Ready for reservation · <#{text_id}> · <#{vc_id}>"
            )

    desc = (
        f"### 🛋️ Welcome to Server Lounges & Suites\n"
        f"Reserve an instant private lounge for squad gaming and study sessions, or apply for an exclusive long-term Private Suite.\n\n"
        f"📊 **Live Status:** `{available_count}/5 Lounges Free`\n\n"
        + "\n\n".join(lines)
        + "\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"### 📜 Booking Rules & Access\n"
        f"> 🛡️ **Eligibility:** Requires <@&1505437941647015986> (`Level 2+` or verified Member)\n"
        f"> ⏱️ **Duration:** Initial 30m ~ 3h booking · Extendable up to 5h max\n"
        f"> 🔒 **Host Controls:** Whitelist friends, kick trolls, toggle Private/Public & call Squads\n"
        f"> 🧹 **Auto-Clean:** Bot automatically clears chat, disconnects VC & resets permissions on expiry\n\n"
        f"### 🎁 Voice Perks & Activity Rewards\n"
        f"> 💰 **Voice Rewards:** Active chatters in VC earn **+$1,000 Cash, +10 XC & +25 XP** every 5 mins!\n"
        f"> 🧊 **Cryo-Thaw:** Deadzone sleepers defrost **+1** every 3 minutes in Lounge VC!\n\n"
        f"### 👑 Exclusive Private Suites (Level 10+)\n"
        f"> Want a permanent, custom-named sanctuary? Level 10+ Elite members can click **[👑 Request Private Suite]** below to apply to Server Administration!"
    )

    embed = discord.Embed(
        title="🛋️ [X BOT SERVER LOUNGES · RESERVATION LOBBY]",
        description=desc,
        color=0x5865F2 if available_count > 0 else 0xE74C3C,
    )
    embed.set_footer(text="X BOT · Select an action button below to proceed")
    return embed


async def refresh_lobby_message(bot: discord.Client, db, guild: discord.Guild = None):
    """Refresh the public lounge lobby board if posted in a channel."""
    if not bot:
        return
    cid = int(setting(db, "lounge_lobby_channel_id") or 0)
    mid = int(setting(db, "lounge_lobby_message_id") or 0)
    if not cid or not mid:
        return

    channel = bot.get_channel(cid)
    if not channel and guild:
        channel = guild.get_channel(cid)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(mid)
        if msg:
            embed = build_lobby_embed(db)
            await msg.edit(embed=embed, view=LoungeLobbyView())
    except Exception:
        pass


def build_host_control_embed(lounge_data: dict):
    """Build the host control panel embed inside the lounge text channel."""
    name = lounge_data["name"]
    expires_at = lounge_data["expires_at"]
    privacy = lounge_data["privacy"]
    host_id = lounge_data["host_user_id"]

    try:
        invited = json.loads(lounge_data.get("invited_user_ids") or "[]")
    except Exception:
        invited = []

    invited_mentions = ", ".join(f"<@{uid}>" for uid in invited) if invited else "*None yet (use Invite button below)*"
    priv_display = "🔒 **Private** (Only Host & Invited members can view/join)" if privacy == "private" else "🌐 **Public** (Open to all server members)"

    embed = discord.Embed(
        title=f"👑 [{name.upper()} HOST CONTROL HUB]",
        description=(
            f"Welcome to your private lounge, <@{host_id}>!\n\n"
            f"⏳ **Session Expiry:** <t:{expires_at}:R> (<t:{expires_at}:t>)\n"
            f"🛡️ **Privacy Status:** {priv_display}\n"
            f"👥 **Invited Members:** {invited_mentions}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎮 **Host Management Actions:**\n"
            f"• **➕ Invite Member:** Grant instant Text & VC access + send invitation DM\n"
            f"• **👢 Kick Member:** Revoke access and disconnect from VC immediately\n"
            f"• **🔒 Toggle Privacy:** Switch between Private and Public on the fly\n"
            f"• **⏱️ Extend (+30m):** Add extra time (up to max limit)\n"
            f"• **🚪 End Session:** Wrap up early to clean chat and release the room\n\n"
            f"-# ⚠️ When time expires, text chat is automatically purged and VC members disconnected."
        ),
        color=0x9B59B6,
    )
    embed.set_footer(text="X BOT · Lounge Host Hub · Session Active")
    return embed


# ---------- Interactive UI Views ----------

class LoungeLobbyView(discord.ui.View):
    """Persistent public view for the Lounge Lobby."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Request Lounge (1~3h)", emoji="🛎️", style=discord.ButtonStyle.primary, row=0, custom_id="lounge_lobby_request")
    async def request_lounge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _db
        if not _db:
            await interaction.response.send_message("Database unavailable. Please try again shortly.", ephemeral=True)
            return

        # Check Member role requirement
        val = setting(_db, "verification_member_role_id")
        member_role_id = int(val) if val and val.isdigit() and int(val) > 0 else 1505437941647015986
        user_roles = getattr(interaction.user, "roles", [])
        is_admin = getattr(interaction.user, "guild_permissions", None) and interaction.user.guild_permissions.administrator
        has_member = any(r.id == member_role_id for r in user_roles) or is_admin

        # Check Deadzone stasis
        dz_status = deadzone.member_status(_db, interaction.user.id)
        if dz_status and dz_status["is_in_deadzone"]:
            await interaction.response.send_message(
                "💀 **Access Denied:** You are currently trapped in cryogenic stasis in the **Deadzone**!\n"
                "You must thaw out and have a comrade rescue you back to life before requesting a private lounge.",
                ephemeral=True,
            )
            return

        if not has_member:
            await interaction.response.send_message(
                f"🔒 **Member Role Required:**\n"
                f"You need the <@&{member_role_id}> role (Level 2+ or verified Member) to request a Lounge!\n"
                f"Please verify or participate in chat to unlock Lounge booking.",
                ephemeral=True,
            )
            return

        # Check if user already hosts a lounge
        active = get_active_lounge_by_host(_db, interaction.user.id)
        if active:
            info = LOUNGES.get(active["lounge_id"], {})
            await interaction.response.send_message(
                f"⚠️ You are already hosting **{active['name']}**!\n"
                f"Go to your active lounge: <#{info.get('text_id')}>\n"
                f"You can only host one lounge at a time.",
                ephemeral=True,
            )
            return

        # Check for free lounges
        all_l = get_all_lounges(_db)
        free_lounges = [l for l in all_l if l["status"] == "available"]
        if not free_lounges:
            await interaction.response.send_message(
                "❌ **All 5 Lounges are currently occupied!**\n"
                "Please check back shortly or check the remaining timers in the Lobby board.",
                ephemeral=True,
            )
            return

        view = LoungeBookingSelectView(free_lounges)
        await interaction.response.send_message("🛋️ **Select your Lounge & Booking Details:**", view=view, ephemeral=True)

    @discord.ui.button(label="Request Private Suite (Lv.10+)", emoji="👑", style=discord.ButtonStyle.success, row=0, custom_id="lounge_lobby_request_suite")
    async def request_suite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        import suites
        await suites.handle_suite_request_start(interaction)

    @discord.ui.button(label="My Active Lounge", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="lounge_lobby_mystatus")
    async def my_status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _db
        if not _db:
            await interaction.response.send_message("Database unavailable.", ephemeral=True)
            return

        active = get_active_lounge_by_host(_db, interaction.user.id)
        if not active:
            await interaction.response.send_message("ℹ️ You do not currently host an active lounge.", ephemeral=True)
            return

        now = int(time.time())
        rem = max(0, active["expires_at"] - now) // 60
        info = LOUNGES.get(active["lounge_id"], {})
        await interaction.response.send_message(
            f"👑 **Your Active Reservation:**\n"
            f"• Lounge: **{active['name']}**\n"
            f"• Time Remaining: `{rem} mins` (<t:{active['expires_at']}:R>)\n"
            f"• Text Channel: <#{info.get('text_id')}>\n"
            f"• Voice Channel: <#{info.get('vc_id')}>",
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh Lobby", emoji="🔄", style=discord.ButtonStyle.secondary, row=1, custom_id="lounge_lobby_refresh")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _db
        if not _db:
            await interaction.response.send_message("Database unavailable.", ephemeral=True)
            return

        embed = build_lobby_embed(_db)
        await interaction.response.edit_message(embed=embed, view=self)


class LoungeBookingSelectView(discord.ui.View):
    """View presenting lounge selection and duration options to the requester."""
    def __init__(self, free_lounges):
        super().__init__(timeout=120)
        self.free_lounges = free_lounges

        options = [
            discord.SelectOption(
                label=l["name"],
                value=str(l["lounge_id"]),
                description=f"Reserve {l['name']} for your squad",
                emoji="🛋️",
            )
            for l in free_lounges
        ]

        self.select_lounge = discord.ui.Select(
            placeholder="Step 1: Choose an available Lounge…",
            options=options,
            row=0,
        )
        self.select_lounge.callback = self.on_lounge_selected
        self.add_item(self.select_lounge)

        self.chosen_lounge_id = int(free_lounges[0]["lounge_id"])
        self.chosen_duration = 180
        self.chosen_privacy = "private"

        duration_options = [
            discord.SelectOption(label="30 Minutes", value="30", emoji="⚡"),
            discord.SelectOption(label="1 Hour", value="60", emoji="⏱️"),
            discord.SelectOption(label="2 Hours", value="120", emoji="⏳"),
            discord.SelectOption(label="3 Hours (Recommended Initial Max)", value="180", default=True, emoji="🕒"),
        ]
        self.select_duration = discord.ui.Select(
            placeholder="Step 2: Choose session duration…",
            options=duration_options,
            row=1,
        )
        self.select_duration.callback = self.on_duration_selected
        self.add_item(self.select_duration)

        privacy_options = [
            discord.SelectOption(label="Private (Host + Invited Only)", value="private", default=True, emoji="🔒"),
            discord.SelectOption(label="Public (Open to All Members)", value="public", emoji="🌐"),
        ]
        self.select_privacy = discord.ui.Select(
            placeholder="Step 3: Choose privacy mode…",
            options=privacy_options,
            row=2,
        )
        self.select_privacy.callback = self.on_privacy_selected
        self.add_item(self.select_privacy)

        self.confirm_btn = discord.ui.Button(
            label="Confirm & Reserve Lounge",
            style=discord.ButtonStyle.success,
            emoji="✅",
            row=3,
        )
        self.confirm_btn.callback = self.on_confirm
        self.add_item(self.confirm_btn)

    async def on_lounge_selected(self, interaction: discord.Interaction):
        self.chosen_lounge_id = int(self.select_lounge.values[0])
        await interaction.response.defer()

    async def on_duration_selected(self, interaction: discord.Interaction):
        self.chosen_duration = int(self.select_duration.values[0])
        await interaction.response.defer()

    async def on_privacy_selected(self, interaction: discord.Interaction):
        self.chosen_privacy = self.select_privacy.values[0]
        await interaction.response.defer()

    async def on_confirm(self, interaction: discord.Interaction):
        global _bot, _db
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if not _db:
            await interaction.followup.send("Database error.", ephemeral=True)
            return

        # Double check Member role
        val = setting(_db, "verification_member_role_id")
        member_role_id = int(val) if val and val.isdigit() and int(val) > 0 else 1505437941647015986
        user_roles = getattr(interaction.user, "roles", [])
        is_admin = getattr(interaction.user, "guild_permissions", None) and interaction.user.guild_permissions.administrator
        if not is_admin and not any(r.id == member_role_id for r in user_roles):
            await interaction.followup.send("🔒 You need the Member role to reserve a lounge.", ephemeral=True)
            return

        # Double check if lounge is still free
        current = get_lounge_row(_db, self.chosen_lounge_id)
        if not current or current["status"] != "available":
            await interaction.followup.send(
                "⚠️ This lounge was just reserved by someone else! Please pick another.",
                ephemeral=True,
            )
            return

        now = int(time.time())
        expires_at = now + (self.chosen_duration * 60)

        # Reserve in database
        _db.execute(
            """UPDATE server_lounges
            SET status='occupied', host_user_id=?, host_name=?, privacy=?,
                reserved_at=?, expires_at=?, invited_user_ids='[]', warned_5m=0, warned_1m=0
            WHERE lounge_id=?""",
            (interaction.user.id, interaction.user.display_name, self.chosen_privacy, now, expires_at, self.chosen_lounge_id),
        )
        _db.commit()

        # Apply channel permissions
        guild = interaction.guild
        info = LOUNGES[self.chosen_lounge_id]
        if guild:
            await apply_lounge_permissions(guild, self.chosen_lounge_id, interaction.user, self.chosen_privacy, [])

            # Post Host Control Panel in the lounge text channel
            tc = guild.get_channel(info["text_id"])
            if tc:
                lounge_data = get_lounge_row(_db, self.chosen_lounge_id)
                embed = build_host_control_embed(lounge_data)
                view = LoungeHostControlView()
                try:
                    ctrl_msg = await tc.send(
                        content=f"🎉 {interaction.user.mention} **Your lounge session has begun!**",
                        embed=embed,
                        view=view,
                    )
                    try:
                        await ctrl_msg.pin(reason="Lounge Host Control Panel")
                    except Exception:
                        pass
                    _db.execute("UPDATE server_lounges SET control_message_id=? WHERE lounge_id=?", (ctrl_msg.id, self.chosen_lounge_id))
                    _db.commit()
                except Exception:
                    pass

        # Update lobby board
        await refresh_lobby_message(_bot, _db, guild)

        await interaction.followup.send(
            f"🎉 **{info['name']} reserved successfully!**\n"
            f"• Mode: **{self.chosen_privacy.title()}**\n"
            f"• Duration: **{self.chosen_duration} minutes** (Expires <t:{expires_at}:R>)\n"
            f"• Enter your lounge: <#{info['text_id']}> (VC: <#{info['vc_id']}>)\n"
            f"Check the pinned host panel in the text chat to invite members!",
            ephemeral=True,
        )


class LoungeHostControlView(discord.ui.View):
    """Persistent control hub posted in the lounge text chat for the Host."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Invite Member", emoji="➕", style=discord.ButtonStyle.primary, custom_id="lounge_host_invite")
    async def invite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _db
        lounge = get_lounge_by_channel(_db, interaction.channel_id)
        if not lounge or lounge["status"] != "occupied":
            await interaction.response.send_message("❌ This lounge is not currently active.", ephemeral=True)
            return

        if interaction.user.id != lounge["host_user_id"] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🔒 Only the Lounge Host can invite members.", ephemeral=True)
            return

        view = LoungeMemberSelectView(lounge["lounge_id"], action="invite")
        await interaction.response.send_message("Select a member to invite to your Lounge:", view=view, ephemeral=True)

    @discord.ui.button(label="Kick Member", emoji="👢", style=discord.ButtonStyle.secondary, custom_id="lounge_host_kick")
    async def kick_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _db
        lounge = get_lounge_by_channel(_db, interaction.channel_id)
        if not lounge or lounge["status"] != "occupied":
            await interaction.response.send_message("❌ This lounge is not currently active.", ephemeral=True)
            return

        if interaction.user.id != lounge["host_user_id"] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🔒 Only the Lounge Host can kick members.", ephemeral=True)
            return

        try:
            invited = json.loads(lounge.get("invited_user_ids") or "[]")
        except Exception:
            invited = []

        if not invited:
            await interaction.response.send_message("ℹ️ You have not invited any members yet.", ephemeral=True)
            return

        view = LoungeMemberSelectView(lounge["lounge_id"], action="kick", invited_ids=invited)
        await interaction.response.send_message("Select a member to remove from your Lounge:", view=view, ephemeral=True)

    @discord.ui.button(label="Toggle Privacy", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="lounge_host_toggle")
    async def toggle_privacy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _bot, _db
        lounge = get_lounge_by_channel(_db, interaction.channel_id)
        if not lounge or lounge["status"] != "occupied":
            await interaction.response.send_message("❌ This lounge is not currently active.", ephemeral=True)
            return

        if interaction.user.id != lounge["host_user_id"] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🔒 Only the Lounge Host can toggle privacy.", ephemeral=True)
            return

        new_privacy = "public" if lounge["privacy"] == "private" else "private"
        _db.execute("UPDATE server_lounges SET privacy=? WHERE lounge_id=?", (new_privacy, lounge["lounge_id"]))
        _db.commit()

        try:
            invited = json.loads(lounge.get("invited_user_ids") or "[]")
        except Exception:
            invited = []

        # Update host panel embed immediately to acknowledge interaction
        updated_lounge = get_lounge_row(_db, lounge["lounge_id"])
        embed = build_host_control_embed(updated_lounge)
        await interaction.response.edit_message(embed=embed, view=self)

        if interaction.guild:
            await apply_lounge_permissions(interaction.guild, lounge["lounge_id"], interaction.user, new_privacy, invited)

        await refresh_lobby_message(_bot, _db, interaction.guild)
        await interaction.followup.send(
            f"🛡️ Privacy mode switched to **{new_privacy.upper()}**!",
            ephemeral=True,
        )

    @discord.ui.button(label="Extend (+30m)", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="lounge_host_extend")
    async def extend_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _bot, _db
        lounge = get_lounge_by_channel(_db, interaction.channel_id)
        if not lounge or lounge["status"] != "occupied":
            await interaction.response.send_message("❌ This lounge is not currently active.", ephemeral=True)
            return

        if interaction.user.id != lounge["host_user_id"] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🔒 Only the Lounge Host can extend the session.", ephemeral=True)
            return

        max_duration = int(setting(_db, "lounge_max_duration_mins") or 300) * 60
        reserved_at = lounge["reserved_at"]
        current_expiry = lounge["expires_at"]
        new_expiry = current_expiry + 1800  # +30 minutes

        # Check if user has unlimited extension bypass (Administrator or VIP Lounge Pass owner)
        is_admin = getattr(interaction.user, "guild_permissions", None) and interaction.user.guild_permissions.administrator
        has_vip_pass = False
        if not is_admin:
            vip_item = _db.execute("SELECT id FROM items WHERE (name='VIP Lounge Pass' OR effect='vip_lounge')").fetchone()
            if vip_item:
                owned = _db.execute("SELECT quantity FROM inventories WHERE user_id=? AND item_id=? AND quantity>0", (interaction.user.id, vip_item["id"])).fetchone()
                has_vip_pass = bool(owned)

        has_unlimited_extension = is_admin or has_vip_pass

        if not has_unlimited_extension and (new_expiry - reserved_at) > max_duration:
            await interaction.response.send_message(
                f"⚠️ Cannot extend further! Standard session limit is **{max_duration // 60} minutes (5 Hours)**.\n"
                f"-# 💡 Server Administrators and members holding a **VIP Lounge Pass** enjoy unlimited extensions!",
                ephemeral=True,
            )
            return

        _db.execute("UPDATE server_lounges SET expires_at=?, warned_5m=0, warned_1m=0 WHERE lounge_id=?", (new_expiry, lounge["lounge_id"]))
        _db.commit()

        updated_lounge = get_lounge_row(_db, lounge["lounge_id"])
        embed = build_host_control_embed(updated_lounge)
        await interaction.response.edit_message(embed=embed, view=self)

        await refresh_lobby_message(_bot, _db, interaction.guild)
        total_mins = (new_expiry - reserved_at) // 60
        vip_tag = " [👑 Unlimited VIP/Admin Extension]" if has_unlimited_extension else " (Standard Max: 5 Hours)"
        await interaction.followup.send(
            f"⏳ **Session extended by +30 minutes!**\n"
            f"• Current Total Session: **{total_mins} mins**{vip_tag}\n"
            f"• New Expiry: <t:{new_expiry}:R> (<t:{new_expiry}:t>)",
            ephemeral=True,
        )

    @discord.ui.button(label="End Session & Clean", emoji="🚪", style=discord.ButtonStyle.danger, custom_id="lounge_host_end")
    async def end_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _bot, _db
        lounge = get_lounge_by_channel(_db, interaction.channel_id)
        if not lounge or lounge["status"] != "occupied":
            await interaction.response.send_message("❌ This lounge is not currently active.", ephemeral=True)
            return

        if interaction.user.id != lounge["host_user_id"] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🔒 Only the Lounge Host or an Admin can end this session.", ephemeral=True)
            return

        await interaction.response.send_message("🧹 **Ending session and initiating auto-clean...**", ephemeral=True)
        await clear_and_reopen_lounge(_bot, _db, interaction.guild, lounge["lounge_id"], reason=f"Ended early by host {interaction.user.display_name}")

    @discord.ui.button(label="Ping Squad (LFG)", emoji="📢", style=discord.ButtonStyle.primary, custom_id="lounge_host_squad_ping", row=2)
    async def ping_squad_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        global _db
        lounge = get_lounge_by_channel(_db, interaction.channel_id)
        if not lounge or lounge["status"] != "occupied":
            await interaction.response.send_message("❌ This lounge is not currently active.", ephemeral=True)
            return

        is_admin = getattr(interaction.user, "guild_permissions", None) and interaction.user.guild_permissions.administrator
        if interaction.user.id != lounge["host_user_id"] and not is_admin:
            await interaction.response.send_message("🔒 Only the Lounge Host or an Admin can ping a squad.", ephemeral=True)
            return

        now = int(time.time())
        last_ping = _squad_ping_cooldowns.get(lounge["lounge_id"], 0)
        if now - last_ping < 300:
            rem = 300 - (now - last_ping)
            await interaction.response.send_message(f"⏳ Squad ping is on cooldown! You can broadcast again in **{rem}s**.", ephemeral=True)
            return

        view = LoungeSquadPingSelectView(lounge["lounge_id"])
        await interaction.response.send_message("📢 **Select Game Squad to Rally:**", view=view, ephemeral=True)


class LoungeSquadPingSelectView(discord.ui.View):
    """View allowing host to select game category for LFG broadcast."""
    def __init__(self, lounge_id: int):
        super().__init__(timeout=60)
        self.lounge_id = lounge_id

        options = [
            discord.SelectOption(label="Steam Squad", value="steam", emoji="🎮", description="Broadcast to Steam gamers"),
            discord.SelectOption(label="Roblox Squad", value="roblox", emoji="🟥", description="Broadcast to Roblox gamers"),
            discord.SelectOption(label="Mobile Squad", value="mobile", emoji="📱", description="Broadcast to Mobile gamers"),
            discord.SelectOption(label="General Squad", value="general", emoji="👥", description="Broadcast general community LFG"),
        ]

        self.select_cat = discord.ui.Select(
            placeholder="Step 1: Choose game category to ping…",
            options=options,
        )
        self.select_cat.callback = self.on_select
        self.add_item(self.select_cat)

    async def on_select(self, interaction: discord.Interaction):
        cat = self.select_cat.values[0]
        modal = LoungeSquadPingModal(self.lounge_id, cat)
        await interaction.response.send_modal(modal)


class LoungeSquadPingModal(discord.ui.Modal):
    """Modal to specify game title and squad party details."""
    def __init__(self, lounge_id: int, category: str):
        cat_names = {"steam": "Steam", "roblox": "Roblox", "mobile": "Mobile", "general": "General"}
        super().__init__(title=f"Rally {cat_names.get(category, 'Gaming')} Squad")
        self.lounge_id = lounge_id
        self.category = category

        self.activity_input = discord.ui.TextInput(
            label="Game & Activity Details",
            placeholder="e.g. Valorant Ranked 5v5 (Need 2), Bedwars, Lethal Company…",
            default="Looking for gamers to squad up in voice! Jump in!",
            max_length=150,
            required=True,
        )
        self.add_item(self.activity_input)

        self.slots_input = discord.ui.TextInput(
            label="Spots Open",
            placeholder="e.g. 2, 3, or Full Party",
            default="Any",
            max_length=20,
            required=False,
        )
        self.add_item(self.slots_input)

    async def on_submit(self, interaction: discord.Interaction):
        global _bot, _db
        await interaction.response.defer(ephemeral=True)

        lounge = get_lounge_row(_db, self.lounge_id)
        if not lounge or lounge["status"] != "occupied":
            await interaction.followup.send("❌ This lounge is no longer active.", ephemeral=True)
            return

        _squad_ping_cooldowns[self.lounge_id] = int(time.time())
        info = LOUNGES.get(self.lounge_id)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Guild unavailable.", ephemeral=True)
            return

        import gaming
        role_map = {
            "steam": ("game_role_steam_id", "game_channel_steam_id", "Steam Squad", 0x1B2838),
            "roblox": ("game_role_roblox_id", "game_channel_roblox_id", "Roblox Squad", 0xE74C3C),
            "mobile": ("game_role_mobile_id", "game_channel_mobile_id", "Mobile Squad", 0x2ECC71),
        }

        role_mention = ""
        target_channel = None

        if self.category in role_map:
            role_key, ch_key, cat_label, color_val = role_map[self.category]
            role_id = int(gaming.setting(_db, role_key) or 0)
            ch_id = int(gaming.setting(_db, ch_key) or 0)
            if role_id:
                role_mention = f"<@&{role_id}> "
            if ch_id:
                target_channel = guild.get_channel(ch_id)
        else:
            cat_label = "Gaming Squad"
            color_val = 0x5865F2

        if not target_channel:
            notif_id = int(setting(_db, "deadzone_notification_channel_id") or 0)
            target_channel = guild.get_channel(notif_id) or interaction.channel

        privacy = lounge.get("privacy", "private")
        priv_badge = "🔒 Private Lounge (Click Jump In to enter!)" if privacy == "private" else "🌐 Public Lounge (Open to All)"

        host_profile = _db.execute("SELECT * FROM game_profiles WHERE user_id=?", (interaction.user.id,)).fetchone()
        host_handle_line = ""
        if host_profile:
            if self.category == "steam" and host_profile["steam_id"]:
                host_handle_line = f"\n🎮 **Host Steam Code:** `{host_profile['steam_id']}`"
            elif self.category == "roblox" and host_profile["roblox_name"]:
                host_handle_line = f"\n🟥 **Host Roblox User:** `{host_profile['roblox_name']}`"

        embed = discord.Embed(
            title=f"🎮 [SQUAD RALLY · {cat_label.upper()}]",
            description=(
                f"📢 {interaction.user.mention} **is rallying a squad in {lounge['name']}!**\n\n"
                f"🎯 **Game / Activity:** {self.activity_input.value}\n"
                f"👥 **Spots Open:** `{self.slots_input.value}`\n"
                f"🔊 **Voice Channel:** <#{info['vc_id']}>\n"
                f"🛡️ **Access:** {priv_badge}"
                f"{host_handle_line}\n\n"
                f"🎁 **Voice Perks:** Active chatters in Lounge VC earn **+$1,000 Cash, +10 XC & +25 XP** every 5 mins!\n"
                f"📦 **Supply Drop:** 30+ min squad sessions earn a **Gamer Supply Drop** (+$10,000 Cash, +25 XC, +50 XP)!"
            ),
            color=color_val,
        )
        embed.set_footer(text=f"X BOT · Lounge #{self.lounge_id} Squad Rally · Click [Jump In] to join")

        join_view = LoungeSquadJoinView(self.lounge_id)
        try:
            await target_channel.send(content=f"{role_mention}🚨 **Squad Rally in {lounge['name']}!**", embed=embed, view=join_view)
            await interaction.followup.send(f"✅ **Squad rally broadcasted in {target_channel.mention}!** Members can click to join your lounge.", ephemeral=True)
        except discord.HTTPException as err:
            await interaction.followup.send(f"⚠️ Failed to post rally in {target_channel.mention}: {err}", ephemeral=True)


class LoungeSquadJoinView(discord.ui.View):
    """Persistent view attached to Squad Rally broadcasts allowing instant access."""
    def __init__(self, lounge_id: int):
        super().__init__(timeout=None)
        self.lounge_id = int(lounge_id)
        button = discord.ui.Button(
            label="Jump In / Join Squad",
            emoji="🚀",
            style=discord.ButtonStyle.success,
            custom_id=f"xbot:lounge:joinsquad:{self.lounge_id}",
        )
        button.callback = self.on_join
        self.add_item(button)

    async def on_join(self, interaction: discord.Interaction):
        global _bot, _db
        if not _db:
            await interaction.response.send_message("Database unavailable.", ephemeral=True)
            return

        lounge = get_lounge_row(_db, self.lounge_id)
        if not lounge or lounge["status"] != "occupied":
            await interaction.response.send_message("⚠️ This lounge session has ended or is no longer occupied.", ephemeral=True)
            return

        # Check deadzone
        dz_status = deadzone.member_status(_db, interaction.user.id)
        if dz_status and dz_status["is_in_deadzone"]:
            await interaction.response.send_message("💀 You are currently in the Deadzone. Please thaw and revive to join squads.", ephemeral=True)
            return

        info = LOUNGES.get(self.lounge_id)
        if not info:
            await interaction.response.send_message("Lounge configuration error.", ephemeral=True)
            return

        guild = interaction.guild
        user = interaction.user

        try:
            invited = json.loads(lounge.get("invited_user_ids") or "[]")
        except Exception:
            invited = []

        is_host = user.id == lounge["host_user_id"]
        already_invited = user.id in invited

        if not is_host and not already_invited:
            invited.append(user.id)
            _db.execute("UPDATE server_lounges SET invited_user_ids=? WHERE lounge_id=?", (json.dumps(invited), self.lounge_id))
            _db.commit()

            # Apply permission overwrites
            if guild:
                tc = guild.get_channel(info["text_id"])
                vc = guild.get_channel(info["vc_id"])
                overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True, speak=True)
                if tc:
                    try: await tc.set_permissions(user, overwrite=overwrite)
                    except discord.HTTPException: pass
                if vc:
                    try: await vc.set_permissions(user, overwrite=overwrite)
                    except discord.HTTPException: pass

                # Notify lounge text chat
                if tc:
                    try:
                        await tc.send(f"👋 {user.mention} **joined the squad from the LFG rally!** Welcome them in!")
                    except Exception:
                        pass

        await interaction.response.send_message(
            f"🎉 **Welcome to the squad!** You now have access to **{lounge['name']}**.\n"
            f"• 🔊 Voice Channel: <#{info['vc_id']}>\n"
            f"• 💬 Text Channel: <#{info['text_id']}>",
            ephemeral=True,
        )


class LoungeMemberSelectView(discord.ui.View):
    """View containing a UserSelect to invite or kick members."""
    def __init__(self, lounge_id: int, action: str, invited_ids: list = None):
        super().__init__(timeout=60)
        self.lounge_id = lounge_id
        self.action = action
        self.invited_ids = invited_ids or []

        self.user_select = discord.ui.UserSelect(
            placeholder=f"Select a member to {action}…",
            min_values=1,
            max_values=1,
        )
        self.user_select.callback = self.on_select
        self.add_item(self.user_select)

    async def on_select(self, interaction: discord.Interaction):
        global _bot, _db
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        target = self.user_select.values[0]
        lounge = get_lounge_row(_db, self.lounge_id)
        if not lounge or lounge["status"] != "occupied":
            await interaction.followup.send("❌ Lounge is no longer active.", ephemeral=True)
            return

        try:
            invited = json.loads(lounge.get("invited_user_ids") or "[]")
        except Exception:
            invited = []

        guild = interaction.guild
        info = LOUNGES[self.lounge_id]
        tc = guild.get_channel(info["text_id"]) if guild else None
        vc = guild.get_channel(info["vc_id"]) if guild else None

        if self.action == "invite":
            if target.id == interaction.user.id:
                await interaction.followup.send("❌ You are already the host!", ephemeral=True)
                return
            if target.id in invited:
                await interaction.followup.send(f"⚠️ {target.mention} is already invited.", ephemeral=True)
                return

            invited.append(target.id)
            _db.execute("UPDATE server_lounges SET invited_user_ids=? WHERE lounge_id=?", (json.dumps(invited), self.lounge_id))
            _db.commit()

            # Apply permissions to target member
            if tc:
                try:
                    await tc.set_permissions(target, overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True))
                except Exception:
                    pass
            if vc:
                try:
                    await vc.set_permissions(target, overwrite=discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True))
                except Exception:
                    pass

            # Notify in text channel
            if tc:
                try:
                    await tc.send(f"👋 {target.mention} was invited to the Lounge by {interaction.user.mention}!")
                except Exception:
                    pass

            # Send friendly DM to target
            try:
                await target.send(
                    f"💌 **Lounge Invitation!**\n"
                    f"{interaction.user.mention} has invited you to **{info['name']}** in **{guild.name}**!\n"
                    f"• Text: <#{info['text_id']}>\n"
                    f"• Voice: <#{info['vc_id']}>"
                )
            except Exception:
                pass

            await interaction.followup.send(f"✅ Successfully invited {target.mention} to **{info['name']}**!", ephemeral=True)

        elif self.action == "kick":
            if target.id in invited:
                invited.remove(target.id)
            _db.execute("UPDATE server_lounges SET invited_user_ids=? WHERE lounge_id=?", (json.dumps(invited), self.lounge_id))
            _db.commit()

            # Remove permissions
            if tc:
                try:
                    await tc.set_permissions(target, overwrite=None)
                except Exception:
                    pass
            if vc:
                try:
                    await vc.set_permissions(target, overwrite=None)
                    # Disconnect if currently in VC
                    if hasattr(target, "voice") and target.voice and target.voice.channel and target.voice.channel.id == vc.id:
                        await target.move_to(None)
                except Exception:
                    pass

            await interaction.followup.send(f"👢 Removed {target.mention} from **{info['name']}**.", ephemeral=True)

        # Update host panel embed in text channel
        if tc and lounge.get("control_message_id"):
            try:
                ctrl_msg = await tc.fetch_message(lounge["control_message_id"])
                if ctrl_msg:
                    updated_lounge = get_lounge_row(_db, self.lounge_id)
                    embed = build_host_control_embed(updated_lounge)
                    await ctrl_msg.edit(embed=embed, view=LoungeHostControlView())
            except Exception:
                pass


# ---------- Background Timer Loop ----------

@tasks.loop(seconds=30)
async def lounge_check_loop():
    """Background task running every 30 seconds to manage warnings, expiry, and auto-cleanup."""
    global _bot, _db
    if not _bot or not _db:
        return
    if setting(_db, "lounge_enabled") != "1":
        return

    now = int(time.time())
    active_lounges = _db.execute("SELECT * FROM server_lounges WHERE status='occupied'").fetchall()

    for row in active_lounges:
        l = dict(row)
        lid = l["lounge_id"]
        expires_at = l["expires_at"]
        rem = expires_at - now

        info = LOUNGES.get(lid)
        if not info:
            continue

        guild = _bot.get_channel(info["text_id"]).guild if _bot.get_channel(info["text_id"]) else None

        # 5 minute warning
        if 0 < rem <= 300 and l["warned_5m"] == 0:
            _db.execute("UPDATE server_lounges SET warned_5m=1 WHERE lounge_id=?", (lid,))
            _db.commit()
            if guild:
                tc = guild.get_channel(info["text_id"])
                if tc:
                    try:
                        await tc.send(f"⏳ **5-Minute Warning:** This lounge session will expire <t:{expires_at}:R>! Use the host panel to extend or wrap up.")
                    except Exception:
                        pass

        # 1 minute warning
        if 0 < rem <= 60 and l["warned_1m"] == 0:
            _db.execute("UPDATE server_lounges SET warned_1m=1 WHERE lounge_id=?", (lid,))
            _db.commit()
            if guild:
                tc = guild.get_channel(info["text_id"])
                if tc:
                    try:
                        await tc.send(f"⚠️ **1-Minute Final Warning:** Wrapping up! In 60 seconds, members will be disconnected, chat purged, and the lounge reopened.")
                    except Exception:
                        pass

        # Expired -> Auto-clean & reopen!
        if rem <= 0:
            if guild:
                await clear_and_reopen_lounge(_bot, _db, guild, lid, reason="Time limit reached")
            continue

        # Check empty lounge auto-cleanup
        empty_timeout = int(setting(_db, "lounge_empty_timeout_seconds") or 90)
        empty_grace = int(setting(_db, "lounge_empty_grace_seconds") or 180)

        vc = _bot.get_channel(info["vc_id"]) if _bot else None
        active_vc_members = [m for m in getattr(vc, "members", []) if not getattr(m, "bot", False)] if vc else []

        initial_grace_passed = (now - l.get("reserved_at", 0)) >= empty_grace

        if initial_grace_passed and len(active_vc_members) == 0:
            if lid not in _lounge_empty_since:
                _lounge_empty_since[lid] = now
                _lounge_empty_warned[lid] = False

            empty_elapsed = now - _lounge_empty_since[lid]

            # Send a warning in the text channel once when detected empty
            if not _lounge_empty_warned.get(lid, False):
                _lounge_empty_warned[lid] = True
                if guild:
                    tc = guild.get_channel(info["text_id"])
                    if tc:
                        try:
                            empty_limit_time = now + max(0, empty_timeout - empty_elapsed)
                            await tc.send(
                                f"⚠️ **Empty Lounge Alert:** No members detected in the voice channel!\n"
                                f"This lounge will automatically reset and reopen <t:{empty_limit_time}:R> if nobody rejoins."
                            )
                        except Exception:
                            pass

            if empty_elapsed >= empty_timeout:
                _lounge_empty_since.pop(lid, None)
                _lounge_empty_warned.pop(lid, None)
                if guild:
                    await clear_and_reopen_lounge(_bot, _db, guild, lid, reason="Auto-cleared (lounge was empty)")
                continue
        else:
            if lid in _lounge_empty_since:
                _lounge_empty_since.pop(lid, None)
                if _lounge_empty_warned.get(lid, False):
                    if guild:
                        tc = guild.get_channel(info["text_id"])
                        if tc:
                            try:
                                await tc.send("🟢 **Activity Restored:** Members detected in the lounge. Auto-cleanup cancelled.")
                            except Exception:
                                pass
                _lounge_empty_warned.pop(lid, None)

    # ---------- Lounge VC Rewards & Deadzone Thaw Protocol ----------
    current_vc_members = set()
    for lid, info in LOUNGES.items():
        vc_id = info["vc_id"]
        tc_id = info["text_id"]
        vc = _bot.get_channel(vc_id)
        if not vc or not hasattr(vc, "members") or not vc.members:
            continue

        tc = _bot.get_channel(tc_id)

        for member in vc.members:
            if getattr(member, "bot", False):
                continue
            vstate = getattr(member, "voice", None)
            if vstate and (vstate.self_deaf or vstate.afk):
                continue

            uid = member.id
            current_vc_members.add(uid)
            _user_vc_duration[uid] = _user_vc_duration.get(uid, 0) + 30
            _user_dz_thaw_seconds[uid] = _user_dz_thaw_seconds.get(uid, 0) + 30

            # Keep active in deadzone activity tracker so VC chatter is never marked inactive
            deadzone.touch_activity(_db, uid)

            # 1. Deadzone VC Thaw: Every 3 minutes (180s) in Lounge VC melts +1 cryo-stasis seal
            status = deadzone.member_status(_db, uid)
            if status and status["is_in_deadzone"]:
                if _user_dz_thaw_seconds[uid] >= 180:
                    _user_dz_thaw_seconds[uid] = 0
                    current_thaw = status["thaw_count"] if "thaw_count" in status.keys() else 0
                    if current_thaw < 5:
                        new_thaw = current_thaw + 1
                        _db.execute("UPDATE deadzone_members SET thaw_count=? WHERE user_id=?", (new_thaw, uid))
                        _db.commit()
                        if tc:
                            try:
                                if new_thaw == 5:
                                    embed = discord.Embed(
                                        title="🧊 [CRYO-THAW COMPLETE (5/5)]",
                                        description=(
                                            f"🎉 {member.mention} **has fully melted their cryo-stasis seal in {vc.mention}!**\n\n"
                                            f"🤝 **Next Step (Condition 2):** An active comrade can now run:\n"
                                            f"`/deadzone rescue member:{member.mention}`\n\n"
                                            f"*(Rescuers receive **+$50,000 Cash, +250 XC & +150 XP** for pulling you out of the crypt!)*"
                                        ),
                                        color=0x3498DB,
                                    )
                                    embed.set_footer(text="X BOT · Deadzone Division · Lounge Voice Protocol")
                                    await tc.send(embed=embed)
                            except Exception:
                                pass

            # 2. Lounge Voice Activity Rewards: Every 5 minutes (300s) of active voice chat
            if _user_vc_duration[uid] >= 300:
                _user_vc_duration[uid] = 0
                leveling._ensure_economy_player(_db, member)
                # Award +$1,000 Cash and +10 XC
                _db.execute("UPDATE players SET money=money+1000, xc=xc+10 WHERE user_id=?", (uid,))
                _db.commit()
                # Award +25 XP
                await leveling.grant_xp(_bot, _db, member, 25, "lounge_voice")

                # Accumulate session rewards silently (no chat spam!)
                lounge_stats = _lounge_session_rewards.setdefault(lid, {})
                user_stats = lounge_stats.setdefault(uid, {
                    "name": member.display_name,
                    "cash": 0,
                    "xc": 0,
                    "xp": 0,
                    "minutes": 0,
                })
                user_stats["name"] = member.display_name
                user_stats["cash"] += 1000
                user_stats["xc"] += 10
                user_stats["xp"] += 25
                user_stats["minutes"] += 5

        # 3. Squad Playtime & Gamer Supply Drop: 30+ minutes (1800s) of squad voice chat (2+ members)
        human_members = [
            m for m in vc.members
            if not getattr(m, "bot", False) and not (getattr(m, "voice", None) and (getattr(m, "voice").self_deaf or getattr(m, "voice").afk))
        ]
        if len(human_members) >= 2:
            _lounge_squad_time[lid] = _lounge_squad_time.get(lid, 0) + 30
            if _lounge_squad_time[lid] >= 1800:
                _lounge_squad_time[lid] = 0
                now_ts = int(time.time())
                for sm in human_members:
                    leveling._ensure_economy_player(_db, sm)
                    _db.execute("UPDATE players SET money=money+10000, xc=xc+25 WHERE user_id=?", (sm.id,))
                    _db.execute(
                        "INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
                        (sm.id, "gamer_supply_drop", "+$10,000 Cash, +25 XC, +50 XP (30m Squad VC Drop)", now_ts),
                    )
                    await leveling.grant_xp(_bot, _db, sm, 50, "gamer_supply_drop")
                _db.commit()

                if tc:
                    try:
                        pings = " ".join(sm.mention for sm in human_members)
                        squad_embed = discord.Embed(
                            title="📦 [GAMER SUPPLY DROP DELIVERED!]",
                            description=(
                                f"🎖️ **Outstanding Squadwork!** The squad in **{info['name']}** has maintained active voice chat for **30+ minutes**!\n\n"
                                f"🎁 **Supply Drop Rewarded to Active Squadmates:**\n"
                                f"• 💵 **+$10,000 Cash**\n"
                                f"• 🪙 **+25 XC**\n"
                                f"• ⭐ **+50 XP**\n\n"
                                f"Squadmates rewarded: {pings}\n"
                                f"-# Keep gaming and voice chatting together for more supply drops!"
                            ),
                            color=discord.Color.gold(),
                        )
                        squad_embed.set_footer(text="X BOT · Gaming Squad Perks · Drops deliver every 30 mins")
                        await tc.send(embed=squad_embed)
                    except Exception:
                        pass

    # Clean up tracking for members who disconnected from all lounge VCs
    for uid in list(_user_vc_duration.keys()):
        if uid not in current_vc_members:
            _user_vc_duration.pop(uid, None)
            _user_dz_thaw_seconds.pop(uid, None)


async def handle_voice_state_update(bot: discord.Client, db, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Handle instant empty detection when members leave a lounge voice channel."""
    if not bot or not db or getattr(member, "bot", False):
        return

    # Check if member left a lounge VC
    if before.channel and before.channel.id in LOUNGE_VC_CHANNEL_IDS and (after.channel is None or after.channel.id != before.channel.id):
        lid = next((k for k, v in LOUNGES.items() if v["vc_id"] == before.channel.id), None)
        if lid:
            l = get_lounge_row(db, lid)
            if l and l["status"] == "occupied":
                active_members = [m for m in getattr(before.channel, "members", []) if not getattr(m, "bot", False)]
                if len(active_members) == 0:
                    now = int(time.time())
                    if lid not in _lounge_empty_since:
                        _lounge_empty_since[lid] = now
                        _lounge_empty_warned[lid] = True
                        empty_timeout = int(setting(db, "lounge_empty_timeout_seconds") or 90)
                        tc = bot.get_channel(LOUNGES[lid]["text_id"])
                        if tc:
                            try:
                                await tc.send(
                                    f"⚠️ **Empty Lounge Alert:** Everyone has left the voice channel!\n"
                                    f"This lounge will automatically reset and reopen <t:{now + empty_timeout}:R> if nobody rejoins."
                                )
                            except Exception:
                                pass

    # Check if member joined a lounge VC
    if after.channel and after.channel.id in LOUNGE_VC_CHANNEL_IDS and (before.channel is None or before.channel.id != after.channel.id):
        lid = next((k for k, v in LOUNGES.items() if v["vc_id"] == after.channel.id), None)
        if lid and lid in _lounge_empty_since:
            _lounge_empty_since.pop(lid, None)
            if _lounge_empty_warned.pop(lid, False):
                tc = bot.get_channel(LOUNGES[lid]["text_id"])
                if tc:
                    try:
                        await tc.send(f"🟢 {member.mention} **rejoined the voice channel!** Auto-cleanup cancelled.")
                    except Exception:
                        pass


def start_lounge_loop(bot: discord.Client, db):
    """Start the lounge auto-clear background task."""
    global _bot, _db
    _bot = bot
    _db = db
    if not lounge_check_loop.is_running():
        lounge_check_loop.start()


@lounge_check_loop.before_loop
async def before_lounge_check():
    if _bot:
        await _bot.wait_until_ready()
        await refresh_lobby_message(_bot, _db)


# ---------- Slash Commands ----------

def register_commands(bot: discord.Client, db, is_council_or_admin, STAFF_COMMAND_KWARGS):
    """Register `/lounge` slash commands for members and administrators."""
    global _bot, _db
    _bot = bot
    _db = db

    @bot.tree.command(name="lounge", description="Open the Lounge Lobby & Reservation Panel")
    async def lounge(interaction: discord.Interaction):
        embed = build_lobby_embed(db)
        await interaction.response.send_message(embed=embed, view=LoungeLobbyView(), ephemeral=True)

    lounge_admin = app_commands.Group(name="lounge_admin", description="Admin: Manage Lounges & Private Suites")

    @lounge_admin.command(name="post_lobby", description="Admin: Post persistent Lounge Lobby panel in a channel")
    @app_commands.describe(channel="Channel where the Lounge Lobby panel will live (optional: defaults to here)")
    async def lounge_post_lobby(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("🔒 Only Administrators can post the Lounge Lobby.", ephemeral=True)
            return

        target_ch = channel or interaction.channel
        embed = build_lobby_embed(db)
        view = LoungeLobbyView()

        msg = await target_ch.send(embed=embed, view=view)
        set_setting(db, "lounge_lobby_channel_id", str(target_ch.id))
        set_setting(db, "lounge_lobby_message_id", str(msg.id))

        await interaction.response.send_message(f"✅ **Lounge Lobby successfully posted in {target_ch.mention}!**", ephemeral=True)

    @lounge_admin.command(name="clear", description="Admin: Force clear and reopen a lounge immediately")
    @app_commands.describe(lounge_number="Lounge number 1 to 5 to clear and reopen")
    @app_commands.choices(lounge_number=[
        app_commands.Choice(name=f"Lounge {i}", value=i) for i in range(1, 6)
    ])
    async def lounge_clear(interaction: discord.Interaction, lounge_number: int):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("🔒 Only Administrators can force-clear lounges.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        success = await clear_and_reopen_lounge(bot, db, interaction.guild, lounge_number, reason=f"Admin clear by {interaction.user.display_name}")
        if success:
            await interaction.followup.send(f"✅ **Lounge {lounge_number}** has been purged, permissions reset, and reopened!", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ Failed to clear Lounge {lounge_number}.", ephemeral=True)

    @lounge_admin.command(name="clear_all", description="Admin: Force clear and reopen all 5 lounges")
    async def lounge_clear_all(interaction: discord.Interaction):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("🔒 Only Administrators can force-clear lounges.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        for i in range(1, 6):
            await clear_and_reopen_lounge(bot, db, interaction.guild, i, reason=f"Admin clear_all by {interaction.user.display_name}")

        await interaction.followup.send("✅ **All 5 Lounges have been purged, permissions reset, and reopened!**", ephemeral=True)

    @lounge_admin.command(name="delete_suite", description="Admin: Delete a private suite and its channels")
    @app_commands.describe(suite_id="The ID of the private suite application")
    async def lounge_delete_suite(interaction: discord.Interaction, suite_id: int):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("🔒 Only Administrators can delete Private Suites.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        import suites
        success = await suites.delete_suite_channels(bot, db, interaction.guild, suite_id, reason=f"Deleted by admin {interaction.user.display_name}")
        if success:
            await interaction.followup.send(f"✅ **Private Suite #{suite_id}** and its channels have been deleted.", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ Suite #{suite_id} not found.", ephemeral=True)

    bot.tree.add_command(lounge_admin, **STAFF_COMMAND_KWARGS)
