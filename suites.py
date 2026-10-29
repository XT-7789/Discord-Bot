"""X BOT Automated Private Suites System with Level 10+ gating and Admin One-Click Approval."""

import json
import re
import sqlite3
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks

import leveling
import xbot_ui

DEFAULT_SETTINGS = {
    "suite_enabled": "1",
    "suite_admin_channel_id": "1552329383681986633",
    "suite_min_level": "10",
    "suite_senior_level": "20",
    "suite_category_id": "1527538020700389498",
}

_bot: Optional[discord.Client] = None
_db: Optional[sqlite3.Connection] = None


def setting(db, key: str) -> str:
    try:
        row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return DEFAULT_SETTINGS.get(key, "0")
        if hasattr(row, "keys"):
            return str(row["value"])
        return str(row[0])
    except Exception:
        return DEFAULT_SETTINGS.get(key, "0")


def set_setting(db, key: str, value: str):
    db.execute(
        "INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    db.commit()


def initialise(db):
    """Ensure database schema for Private Suites exists."""
    for key, val in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, val))

    db.execute(
        "UPDATE economy_settings SET value='1527538020700389498' WHERE key='suite_category_id' AND (value='0' OR value='' OR value IS NULL)"
    )
    db.commit()

    db.execute("""CREATE TABLE IF NOT EXISTS suite_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        user_name TEXT NOT NULL,
        user_level INTEGER NOT NULL,
        custom_name TEXT NOT NULL,
        purpose TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        admin_id INTEGER NOT NULL DEFAULT 0,
        admin_message_id INTEGER NOT NULL DEFAULT 0,
        voice_channel_id INTEGER NOT NULL DEFAULT 0,
        text_channel_id INTEGER NOT NULL DEFAULT 0,
        duration_str TEXT NOT NULL DEFAULT 'lifetime',
        expires_at INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        reviewed_at INTEGER NOT NULL DEFAULT 0
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS suite_members (
        suite_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        PRIMARY KEY (suite_id, user_id)
    )""")
    db.commit()


def get_active_suite_by_host(db, user_id: int):
    return db.execute(
        "SELECT * FROM suite_requests WHERE user_id=? AND status='approved' ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def get_pending_request_by_user(db, user_id: int):
    return db.execute(
        "SELECT * FROM suite_requests WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def get_suite_by_channel(db, channel_id: int):
    return db.execute(
        "SELECT * FROM suite_requests WHERE status='approved' AND (voice_channel_id=? OR text_channel_id=?)",
        (channel_id, channel_id),
    ).fetchone()


async def handle_suite_request_start(interaction: discord.Interaction):
    """Validate member eligibility and launch the suite application modal."""
    global _db
    if not _db:
        await interaction.response.send_message("Database unavailable.", ephemeral=True)
        return

    # Level requirement check (Min Level 10)
    prof = leveling.profile(_db, interaction.user.id)
    level = int(prof["level"] if prof else 1)
    min_level = int(setting(_db, "suite_min_level") or 10)

    if level < min_level:
        await interaction.response.send_message(
            f"🔒 **Level {min_level}+ Required for Private Suites**\n"
            f"Private Suites are exclusive custom sanctuaries for **Level {min_level}+ (⭐ Elite)** members.\n"
            f"• Your Current Level: **Level {level}**\n"
            f"• Progress: Chat in text channels, hang out in Voice Lounges, and complete `/daily` to reach Level {min_level}!",
            ephemeral=True,
        )
        return

    # Check existing active or pending suite
    pending = get_pending_request_by_user(_db, interaction.user.id)
    if pending:
        await interaction.response.send_message(
            f"⏳ **Application Pending**\n"
            f"You already have a pending Private Suite request for **\"{pending['custom_name']}\"** under review by server administration.",
            ephemeral=True,
        )
        return

    active = get_active_suite_by_host(_db, interaction.user.id)
    if active:
        await interaction.response.send_message(
            f"👑 **You already have an active Private Suite!**\n"
            f"• Suite Name: **{active['custom_name']}**\n"
            f"• Text Channel: <#{active['text_channel_id']}>\n"
            f"• Voice Channel: <#{active['voice_channel_id']}>\n"
            f"Each member may host only one active Private Suite.",
            ephemeral=True,
        )
        return

    modal = PrivateSuiteRequestModal(level)
    await interaction.response.send_modal(modal)


class PrivateSuiteRequestModal(discord.ui.Modal):
    """Modal for Level 10+ members to specify custom suite name and purpose."""
    def __init__(self, user_level: int):
        super().__init__(title="👑 Request Private Suite")
        self.user_level = user_level

        self.name_input = discord.ui.TextInput(
            label="Custom Suite Name",
            placeholder="e.g. Gamer Haven, The Void, Chill Lounge…",
            min_length=2,
            max_length=32,
            required=True,
        )
        self.add_item(self.name_input)

        self.purpose_input = discord.ui.TextInput(
            label="Suite Purpose & Activities",
            placeholder="e.g. Long-term squad gaming base, stream lounge, study sessions with friends…",
            min_length=5,
            max_length=200,
            style=discord.TextStyle.paragraph,
            required=True,
        )
        self.add_item(self.purpose_input)

    async def on_submit(self, interaction: discord.Interaction):
        global _bot, _db
        await interaction.response.defer(ephemeral=True)

        user = interaction.user
        custom_name = self.name_input.value.strip()
        purpose = self.purpose_input.value.strip()
        now = int(time.time())

        # Save pending request into database
        cursor = _db.execute(
            """INSERT INTO suite_requests (user_id, user_name, user_level, custom_name, purpose, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (user.id, user.display_name, self.user_level, custom_name, purpose, now),
        )
        _db.commit()
        request_id = cursor.lastrowid

        # Determine admin channel
        admin_cid = int(setting(_db, "suite_admin_channel_id") or 0)
        admin_channel = interaction.guild.get_channel(admin_cid) if interaction.guild and admin_cid else None

        senior_level = int(setting(_db, "suite_senior_level") or 20)
        is_senior = self.user_level >= senior_level

        # Check if Level 40+ Mythic member for Instant Auto-Approval
        MYTHIC_AUTO_LEVEL = 40
        if self.user_level >= MYTHIC_AUTO_LEVEL and interaction.guild:
            duration_label = "Permanent (Mythic Lv.40+ VIP Privilege)"
            expires_at = 0
            try:
                bot_user = _bot.user if _bot and _bot.user else interaction.guild.me
                bot_id = bot_user.id if bot_user else 0
                text_ch, voice_ch = await provision_suite_channels(
                    guild=interaction.guild,
                    applicant=user,
                    request_id=request_id,
                    custom_name=custom_name,
                    duration_label=duration_label,
                    expires_at=expires_at,
                    approver_id=bot_id,
                    approver_mention="⚡ **Mythic VIP Auto-Approval**",
                    admin_note=f"Instant auto-approval unlocked for Level {self.user_level} Mythic+ member.",
                )

                if admin_channel:
                    try:
                        admin_embed = discord.Embed(
                            title=f"⚡ [PRIVATE SUITE AUTO-APPROVED · #{request_id}]",
                            description=(
                                f"👤 **Host:** {user.mention} (`{user.display_name}` · `{user.id}`)\n"
                                f"🎖️ **Rank Standing:** 💗 **LEVEL {self.user_level} MYTHIC+ (VIP Instant Approval)**\n\n"
                                f"🏷️ **Suite Name:** `{custom_name}`\n"
                                f"📝 **Purpose & Activities:**\n>>> {purpose}\n\n"
                                f"🔊 **Voice Channel:** {voice_ch.mention}\n"
                                f"💬 **Text Channel:** {text_ch.mention}\n"
                                f"⚡ **Status:** Automatically provisioned instantly."
                            ),
                            color=0xFF69B4,
                        )
                        admin_embed.set_footer(text=f"X BOT · Private Suite System · #{request_id}")
                        await admin_channel.send(
                            content=f"⚡ **Mythic Auto-Approval: Private Suite #{request_id} created for {user.mention} (Lv.{self.user_level})!**",
                            embed=admin_embed,
                        )
                    except Exception:
                        pass

                await interaction.followup.send(
                    f"🎉 **Mythic VIP Instant Auto-Approval!**\n"
                    f"Because you are **Level {self.user_level} (Mythic+)**, your Private Suite **\"{custom_name}\"** was approved and created instantly without waiting for admin review!\n\n"
                    f"💬 **Text Channel:** {text_ch.mention}\n"
                    f"🔊 **Voice Channel:** {voice_ch.mention}\n\n"
                    f"Head over to your suite to invite your squad!",
                    ephemeral=True,
                )
                return
            except Exception as err:
                print(f"[Suite Auto-Approval Error] {err}")

        # Build notification embed for admins
        if is_senior:
            color = 0xF1C40F  # Gold
            badge = f"🌟 **LEVEL {self.user_level} SENIOR VETERAN (Lv.20+)**\n-# ⚡ **Fast-Track Recommended:** Trusted veteran member with high loyalty."
        else:
            color = 0x3498DB  # Blue
            badge = f"⭐ **LEVEL {self.user_level} ELITE MEMBER (Lv.10+)**"

        embed = discord.Embed(
            title=f"👑 [PRIVATE SUITE REQUEST · #{request_id}]",
            description=(
                f"👤 **Applicant:** {user.mention} (`{user.display_name}` · `{user.id}`)\n"
                f"🎖️ **Rank Standing:** {badge}\n\n"
                f"🏷️ **Custom Suite Name:** `{custom_name}`\n"
                f"📝 **Purpose & Activities:**\n>>> {purpose}\n\n"
                f"📅 **Submitted:** <t:{now}:f> (<t:{now}:R>)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Click **[Approve Suite]** to dynamically create channels and assign ownership, or **[Reject]** to decline."
            ),
            color=color,
        )
        embed.set_footer(text=f"X BOT · Private Suite System · Request #{request_id}")

        review_view = SuiteAdminReviewView(request_id)

        msg = None
        if admin_channel:
            try:
                msg = await admin_channel.send(
                    content=f"🚨 **New Private Suite Application from {user.mention} (Level {self.user_level})!**",
                    embed=embed,
                    view=review_view,
                )
                _db.execute("UPDATE suite_requests SET admin_message_id=? WHERE id=?", (msg.id, request_id))
                _db.commit()
            except discord.HTTPException as err:
                pass

        await interaction.followup.send(
            f"✅ **Private Suite Request Submitted Successfully!**\n"
            f"• Suite Name: **{custom_name}**\n"
            f"• Applicant Level: **Level {self.user_level}**\n"
            f"• Status: ⏳ Sent to Server Administration for review.\n\n"
            f"You will receive a notification as soon as an Administrator reviews your request.",
            ephemeral=True,
        )


class SuiteAdminReviewView(discord.ui.View):
    """Interactive persistent view posted in the admin review channel."""
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.request_id = int(request_id)

        btn_approve = discord.ui.Button(
            label="Approve Suite",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"xbot:suite:approve:{self.request_id}",
        )
        btn_approve.callback = self.on_approve
        self.add_item(btn_approve)

        btn_reject = discord.ui.Button(
            label="Reject",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"xbot:suite:reject:{self.request_id}",
        )
        btn_reject.callback = self.on_reject
        self.add_item(btn_reject)

    async def on_approve(self, interaction: discord.Interaction):
        # Admin permission check
        if not getattr(interaction.user, "guild_permissions", None) or not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🔒 Only Administrators can approve Private Suites.", ephemeral=True)
            return

        modal = SuiteApproveModal(self.request_id)
        await interaction.response.send_modal(modal)

    async def on_reject(self, interaction: discord.Interaction):
        if not getattr(interaction.user, "guild_permissions", None) or not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🔒 Only Administrators can reject Private Suites.", ephemeral=True)
            return

        modal = SuiteRejectModal(self.request_id)
        await interaction.response.send_modal(modal)


async def provision_suite_channels(
    guild: discord.Guild,
    applicant: discord.Member,
    request_id: int,
    custom_name: str,
    duration_label: str,
    expires_at: int,
    approver_id: int,
    approver_mention: str,
    admin_note: str = "",
):
    global _db
    now = int(time.time())

    cat_id = int(setting(_db, "suite_category_id") or 1527538020700389498)
    target_category = guild.get_channel(cat_id) if cat_id else None

    if not target_category:
        import lounges
        info = lounges.LOUNGES.get(1)
        if info:
            lounge1_ch = guild.get_channel(info["text_id"])
            if lounge1_ch and lounge1_ch.category:
                target_category = lounge1_ch.category

    if not target_category:
        for cat in guild.categories:
            if "suite" in cat.name.lower():
                target_category = cat
                break

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        applicant: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            connect=True,
            speak=True,
            embed_links=True,
            attach_files=True,
        ),
    }

    clean_name = re.sub(r"[^\w\s-]", "", custom_name).strip() or f"suite-{request_id}"

    voice_ch = await guild.create_voice_channel(
        name=f"???{clean_name}",
        category=target_category,
        overwrites=overwrites,
        reason=f"X BOT Private Suite for {applicant.display_name}",
    )
    text_ch = await guild.create_text_channel(
        name=f"???{clean_name.lower().replace(' ', '-')}",
        category=target_category,
        overwrites=overwrites,
        reason=f"X BOT Private Suite for {applicant.display_name}",
    )

    _db.execute(
        """UPDATE suite_requests
        SET status='approved', admin_id=?, voice_channel_id=?, text_channel_id=?, duration_str=?, expires_at=?, reviewed_at=?
        WHERE id=?""",
        (approver_id, voice_ch.id, text_ch.id, duration_label, expires_at, now, request_id),
    )
    _db.commit()

    welcome_embed = discord.Embed(
        title=f"?? Welcome to your Private Suite: {custom_name}!",
        description=(
            f"?? {applicant.mention}, **your Private Suite application has been approved by {approver_mention}!**\n\n"
            f"??? **Suite Name:** `{custom_name}`\n"
            f"? **Duration:** `{duration_label}`\n"
            f"?? **Voice Channel:** {voice_ch.mention}\n"
            f"?? **Text Channel:** {text_ch.mention}\n"
            + (f"?? **Note:** {admin_note}\n" if admin_note else "")
            + "\n????????????????????????\n"
            f"??? **Suite Host Controls:**\n"
            f"? Use the buttons below to invite your squadmates or remove members.\n"
            f"? Only invited members will be granted access to your private suite."
        ),
        color=0x2ECC71,
    )
    welcome_embed.set_footer(text=f"X BOT ? Private Suite #{request_id}")

    host_view = SuiteHostControlView(request_id)
    try:
        await text_ch.send(content=f"{applicant.mention} ?? **Your Private Suite is ready!**", embed=welcome_embed, view=host_view)
    except Exception:
        pass

    return text_ch, voice_ch


class SuiteApproveModal(discord.ui.Modal):
    """Modal for administrator to specify suite duration and optional notes."""
    def __init__(self, request_id: int):
        super().__init__(title="Approve Private Suite")
        self.request_id = request_id

        self.duration_input = discord.ui.TextInput(
            label="Duration (e.g. lifetime, 7d, 14d, 30d)",
            default="lifetime",
            placeholder="lifetime or number of days like 7d, 30d",
            max_length=20,
            required=True,
        )
        self.add_item(self.duration_input)

        self.note_input = discord.ui.TextInput(
            label="Admin Welcome Note / Terms (Optional)",
            placeholder="e.g. Approved for squad gaming. Enjoy your suite!",
            max_length=150,
            required=False,
        )
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction):
        global _bot, _db
        await interaction.response.defer(ephemeral=True)

        req = _db.execute("SELECT * FROM suite_requests WHERE id=?", (self.request_id,)).fetchone()
        if not req or req["status"] != "pending":
            await interaction.followup.send("?? This request has already been processed or no longer exists.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Guild unavailable.", ephemeral=True)
            return

        now = int(time.time())
        duration_text = self.duration_input.value.strip().lower()
        admin_note = self.note_input.value.strip()

        # Parse duration
        expires_at = 0
        match = re.match(r"^(\d+)\s*d(ays)?$", duration_text)
        if match:
            days = int(match.group(1))
            expires_at = now + days * 86400
            duration_label = f"{days} Days (<t:{expires_at}:R>)"
        else:
            duration_label = "Permanent (Lifetime)"

        applicant = guild.get_member(req["user_id"])
        if not applicant:
            try:
                applicant = await guild.fetch_member(req["user_id"])
            except discord.HTTPException:
                pass

        if not applicant:
            await interaction.followup.send("?? Applicant is no longer in the Discord server.", ephemeral=True)
            return

        try:
            text_ch, voice_ch = await provision_suite_channels(
                guild=guild,
                applicant=applicant,
                request_id=self.request_id,
                custom_name=req["custom_name"],
                duration_label=duration_label,
                expires_at=expires_at,
                approver_id=interaction.user.id,
                approver_mention=interaction.user.mention,
                admin_note=admin_note,
            )
        except discord.HTTPException as err:
            await interaction.followup.send(f"? Failed to create channels: {err}", ephemeral=True)
            return

        # Update the original admin review message
        try:
            if interaction.message:
                updated_embed = interaction.message.embeds[0]
                updated_embed.color = 0x2ECC71
                updated_embed.add_field(
                    name="Status",
                    value=f"? **APPROVED** by {interaction.user.mention} (<t:{now}:R>)\n? Voice: {voice_ch.mention}\n? Text: {text_ch.mention}\n? Duration: `{duration_label}`",
                    inline=False,
                )
                await interaction.message.edit(embed=updated_embed, view=None)
        except Exception:
            pass

        # Notify applicant in DM
        try:
            dm_embed = discord.Embed(
                title=f"?? Private Suite Approved: {req['custom_name']}!",
                description=(
                    f"Great news! Your request for **\"{req['custom_name']}\"** was approved by **{interaction.user.display_name}**.\n\n"
                    f"? ?? **Voice Channel:** {voice_ch.mention}\n"
                    f"? ?? **Text Channel:** {text_ch.mention}\n"
                    f"? ? **Duration:** `{duration_label}`\n\n"
                    f"Head over to your text channel to manage your squad and invite your friends!"
                ),
                color=0x2ECC71,
            )
            await applicant.send(embed=dm_embed)
        except Exception:
            pass

        await interaction.followup.send(
            f"? **Private Suite #{self.request_id} Approved!**\n"
            f"? Channels Created: {text_ch.mention} & {voice_ch.mention}\n"
            f"? Host: {applicant.mention}\n"
            f"? Duration: `{duration_label}`",
            ephemeral=True,
        )


class SuiteRejectModal(discord.ui.Modal):
    """Modal for administrator to decline a suite request with reason."""
    def __init__(self, request_id: int):
        super().__init__(title="Reject Private Suite")
        self.request_id = request_id

        self.reason_input = discord.ui.TextInput(
            label="Reason for Rejection",
            placeholder="e.g. Inappropriate suite name, maximum server capacity reached, please resubmit…",
            max_length=150,
            required=True,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        global _bot, _db
        await interaction.response.defer(ephemeral=True)

        req = _db.execute("SELECT * FROM suite_requests WHERE id=?", (self.request_id,)).fetchone()
        if not req or req["status"] != "pending":
            await interaction.followup.send("⚠️ This request has already been processed or no longer exists.", ephemeral=True)
            return

        reason = self.reason_input.value.strip()
        now = int(time.time())

        _db.execute(
            "UPDATE suite_requests SET status='rejected', admin_id=?, reviewed_at=? WHERE id=?",
            (interaction.user.id, now, self.request_id),
        )
        _db.commit()

        # Update the original admin review message
        try:
            if interaction.message:
                updated_embed = interaction.message.embeds[0]
                updated_embed.color = 0xE74C3C
                updated_embed.add_field(
                    name="Status",
                    value=f"❌ **REJECTED** by {interaction.user.mention} (<t:{now}:R>)\n• Reason: `{reason}`",
                    inline=False,
                )
                await interaction.message.edit(embed=updated_embed, view=None)
        except Exception:
            pass

        # Notify applicant
        guild = interaction.guild
        applicant = guild.get_member(req["user_id"]) if guild else None
        if applicant:
            try:
                dm_embed = discord.Embed(
                    title="⚠️ Private Suite Request Declined",
                    description=(
                        f"Your request for the Private Suite **\"{req['custom_name']}\"** was reviewed and declined by server administration.\n\n"
                        f"**Reason:**\n>>> {reason}\n\n"
                        f"You may submit a revised application once ready."
                    ),
                    color=0xE74C3C,
                )
                await applicant.send(embed=dm_embed)
            except Exception:
                pass

        await interaction.followup.send(f"❌ **Private Suite #{self.request_id} has been rejected.**", ephemeral=True)


class SuiteHostControlView(discord.ui.View):
    """Control view pinned in the suite's private text channel for the host."""
    def __init__(self, suite_id: int):
        super().__init__(timeout=None)
        self.suite_id = int(suite_id)

        btn_invite = discord.ui.Button(
            label="Invite Member",
            emoji="➕",
            style=discord.ButtonStyle.primary,
            custom_id=f"xbot:suite:invite:{self.suite_id}",
        )
        btn_invite.callback = self.on_invite
        self.add_item(btn_invite)

        btn_kick = discord.ui.Button(
            label="Remove Member",
            emoji="➖",
            style=discord.ButtonStyle.secondary,
            custom_id=f"xbot:suite:kick:{self.suite_id}",
        )
        btn_kick.callback = self.on_kick
        self.add_item(btn_kick)

    async def on_invite(self, interaction: discord.Interaction):
        global _db
        suite = _db.execute("SELECT * FROM suite_requests WHERE id=?", (self.suite_id,)).fetchone()
        if not suite or suite["status"] != "approved":
            await interaction.response.send_message("Suite not found or no longer active.", ephemeral=True)
            return

        if interaction.user.id != suite["user_id"] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🔒 Only the Suite Host can invite members.", ephemeral=True)
            return

        view = SuiteMemberSelectView(self.suite_id, mode="invite")
        await interaction.response.send_message("👥 **Select a member to invite to your Suite:**", view=view, ephemeral=True)

    async def on_kick(self, interaction: discord.Interaction):
        global _db
        suite = _db.execute("SELECT * FROM suite_requests WHERE id=?", (self.suite_id,)).fetchone()
        if not suite or suite["status"] != "approved":
            await interaction.response.send_message("Suite not found or no longer active.", ephemeral=True)
            return

        if interaction.user.id != suite["user_id"] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🔒 Only the Suite Host can remove members.", ephemeral=True)
            return

        view = SuiteMemberSelectView(self.suite_id, mode="kick")
        await interaction.response.send_message("🚫 **Select a member to remove from your Suite:**", view=view, ephemeral=True)


class SuiteMemberSelectView(discord.ui.View):
    """View containing UserSelect to add or remove members from the suite."""
    def __init__(self, suite_id: int, mode: str = "invite"):
        super().__init__(timeout=120)
        self.suite_id = suite_id
        self.mode = mode

        self.select_user = discord.ui.UserSelect(
            placeholder=f"Select member to {mode}…",
            min_values=1,
            max_values=1,
        )
        self.select_user.callback = self.on_select
        self.add_item(self.select_user)

    async def on_select(self, interaction: discord.Interaction):
        global _db
        target = self.select_user.values[0]
        if target.id == interaction.user.id:
            await interaction.response.send_message("You cannot select yourself.", ephemeral=True)
            return

        suite = _db.execute("SELECT * FROM suite_requests WHERE id=?", (self.suite_id,)).fetchone()
        if not suite or suite["status"] != "approved":
            await interaction.response.send_message("Suite is no longer active.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Guild unavailable.", ephemeral=True)
            return

        tc = guild.get_channel(suite["text_channel_id"])
        vc = guild.get_channel(suite["voice_channel_id"])

        if self.mode == "invite":
            overwrite = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                connect=True,
                speak=True,
                embed_links=True,
                attach_files=True,
            )
            if tc:
                try: await tc.set_permissions(target, overwrite=overwrite)
                except discord.HTTPException: pass
            if vc:
                try: await vc.set_permissions(target, overwrite=overwrite)
                except discord.HTTPException: pass

            _db.execute("INSERT OR IGNORE INTO suite_members (suite_id, user_id) VALUES (?, ?)", (self.suite_id, target.id))
            _db.commit()

            if tc:
                try: await tc.send(f"👋 {target.mention} was added to the suite by {interaction.user.mention}!")
                except Exception: pass

            await interaction.response.send_message(f"✅ {target.mention} has been added to your suite!", ephemeral=True)

        else:  # kick
            if tc:
                try: await tc.set_permissions(target, overwrite=None)
                except discord.HTTPException: pass
            if vc:
                try:
                    await vc.set_permissions(target, overwrite=None)
                    if hasattr(target, "voice") and target.voice and target.voice.channel == vc:
                        await target.move_to(None)
                except discord.HTTPException:
                    pass

            _db.execute("DELETE FROM suite_members WHERE suite_id=? AND user_id=?", (self.suite_id, target.id))
            _db.commit()

            if tc:
                try: await tc.send(f"🚪 {target.mention} was removed from the suite.")
                except Exception: pass

            await interaction.response.send_message(f"🚫 {target.mention} has been removed from your suite.", ephemeral=True)


async def delete_suite_channels(bot: discord.Client, db, guild: discord.Guild, suite_id: int, reason: str = "Admin deletion"):
    """Delete the voice and text channels of an approved suite and mark it deleted."""
    row = db.execute("SELECT * FROM suite_requests WHERE id=?", (suite_id,)).fetchone()
    if not row:
        return False

    if guild:
        tc = guild.get_channel(row["text_channel_id"])
        vc = guild.get_channel(row["voice_channel_id"])
        if tc:
            try: await tc.delete(reason=reason)
            except discord.HTTPException: pass
        if vc:
            try: await vc.delete(reason=reason)
            except discord.HTTPException: pass

    db.execute("UPDATE suite_requests SET status='deleted' WHERE id=?", (suite_id,))
    db.execute("DELETE FROM suite_members WHERE suite_id=?", (suite_id,))
    db.commit()
    return True
