"""Gaming Zone suite: Steam, Roblox, and Mobile game roles, LFG party finder, and game profiles."""
import time
from typing import Optional

import discord
from discord import app_commands

import xbot_ui

DEFAULT_SETTINGS = {
    "game_role_steam_id": "1552259861654413312",
    "game_role_valorant_id": "1554759111281745962",
    "game_role_roblox_id": "1552259987667943515",
    "game_role_minecraft_id": "1554759163442237440",
    "game_role_mlbb_id": "1554759232270639216",
    "game_role_genshin_id": "1554759280194883615",
    "game_channel_steam_id": "1552259761607671908",
    "game_channel_roblox_id": "1552237657424265236",
    "game_channel_mobile_id": "1552237704878620722",
    "game_channel_valorant_id": "0",
    "game_channel_minecraft_id": "0",
    "game_channel_mlbb_id": "0",
    "game_channel_genshin_id": "0",
    "device_role_pc_id": "1554759037914976317",
    "device_role_mobile_id": "1552259988691488818",
    "device_role_console_id": "1554758965454315520",
    "lfg_team_reward_cash": "500",
}

GAME_CONFIG = {
    "steam": {
        "name": "Steam",
        "emoji": "🎮",
        "role_key": "game_role_steam_id",
        "channel_key": "game_channel_steam_id",
        "color": discord.Color.dark_blue(),
    },
    "valorant": {
        "name": "Valorant",
        "emoji": "🎯",
        "role_key": "game_role_valorant_id",
        "channel_key": "game_channel_valorant_id",
        "color": discord.Color.from_rgb(255, 70, 85),
    },
    "roblox": {
        "name": "Roblox",
        "emoji": "🟥",
        "role_key": "game_role_roblox_id",
        "channel_key": "game_channel_roblox_id",
        "color": discord.Color.red(),
    },
    "minecraft": {
        "name": "Minecraft",
        "emoji": "🟩",
        "role_key": "game_role_minecraft_id",
        "channel_key": "game_channel_minecraft_id",
        "color": discord.Color.dark_green(),
    },
    "mlbb": {
        "name": "MLBB",
        "emoji": "🏆",
        "role_key": "game_role_mlbb_id",
        "channel_key": "game_channel_mlbb_id",
        "color": discord.Color.gold(),
    },
    "genshin": {
        "name": "Genshin",
        "emoji": "✨",
        "role_key": "game_role_genshin_id",
        "channel_key": "game_channel_genshin_id",
        "color": discord.Color.teal(),
    },
}

DEVICE_CONFIG = {
    "pc": {
        "name": "PC",
        "emoji": "💻",
        "role_key": "device_role_pc_id",
        "color": discord.Color.blue(),
    },
    "mobile": {
        "name": "Mobile",
        "emoji": "📱",
        "role_key": "device_role_mobile_id",
        "color": discord.Color.green(),
    },
    "console": {
        "name": "Console",
        "emoji": "🎮",
        "role_key": "device_role_console_id",
        "color": discord.Color.purple(),
    },
}


def setting(db, key: str) -> str:
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else DEFAULT_SETTINGS.get(key, "0")


def initialise(db) -> None:
    """Create database tables and seed default settings for Gaming Zone."""
    for key, value in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key, value) VALUES(?, ?)", (key, value))
        if value != "0":
            db.execute("UPDATE economy_settings SET value=? WHERE key=? AND (value='0' OR value='')", (value, key))

    db.execute("""CREATE TABLE IF NOT EXISTS game_profiles (
        user_id INTEGER PRIMARY KEY,
        steam_id TEXT NOT NULL DEFAULT '',
        roblox_name TEXT NOT NULL DEFAULT '',
        mobile_games TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL DEFAULT 0
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS lfg_parties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        host_id INTEGER NOT NULL,
        game TEXT NOT NULL,
        party_size INTEGER NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        channel_id INTEGER NOT NULL DEFAULT 0,
        message_id INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS lfg_members (
        party_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        joined_at INTEGER NOT NULL,
        PRIMARY KEY (party_id, user_id)
    )""")
    db.commit()


def get_game_profile(db, user_id: int):
    return db.execute("SELECT * FROM game_profiles WHERE user_id=?", (user_id,)).fetchone()


def save_game_profile(db, user_id: int, steam_id: str, roblox_name: str, mobile_games: str) -> None:
    now = int(time.time())
    db.execute(
        """INSERT INTO game_profiles (user_id, steam_id, roblox_name, mobile_games, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
        steam_id=excluded.steam_id,
        roblox_name=excluded.roblox_name,
        mobile_games=excluded.mobile_games,
        updated_at=excluded.updated_at""",
        (user_id, steam_id.strip(), roblox_name.strip(), mobile_games.strip(), now),
    )
    db.commit()


class GameRolesButton(discord.ui.Button):
    def __init__(self, game_key: str, row: Optional[int] = None):
        cfg = GAME_CONFIG[game_key]
        styles = {
            "steam": discord.ButtonStyle.primary,
            "valorant": discord.ButtonStyle.danger,
            "roblox": discord.ButtonStyle.danger,
            "minecraft": discord.ButtonStyle.success,
            "mlbb": discord.ButtonStyle.primary,
            "genshin": discord.ButtonStyle.secondary,
            "mobile": discord.ButtonStyle.success,
        }
        style = styles.get(game_key, discord.ButtonStyle.primary)
        super().__init__(
            label=f"{cfg['emoji']} {cfg['name']}",
            style=style,
            custom_id=f"xbot_game_role_{game_key}",
            row=row,
        )
        self.game_key = game_key

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        db = getattr(interaction.client, "xbot_db", None) or interaction.client.tree.xbot_db
        role_id_str = setting(db, GAME_CONFIG[self.game_key]["role_key"])
        role_id = int(role_id_str) if role_id_str.isdigit() else 0

        role = interaction.guild.get_role(role_id) if role_id else None
        if not role:
            # Fallback search by name if role was recreated or not yet mapped in DB
            target_name = GAME_CONFIG[self.game_key]["name"].lower()
            role = discord.utils.find(
                lambda r: target_name in r.name.lower() or self.game_key.lower() in r.name.lower(),
                interaction.guild.roles,
            )

        if not role:
            await interaction.response.send_message(
                f"❌ Role for **{GAME_CONFIG[self.game_key]['name']}** was not found on this server. Please contact an admin.",
                ephemeral=True,
            )
            return

        member = interaction.user
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="X BOT Gaming self-assign removal")
                await interaction.response.send_message(
                    f"🗑️ Removed role **{role.name}**.", ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Bot lacks permissions to manage your roles. Ensure the bot role is above the game roles.",
                    ephemeral=True,
                )
        else:
            try:
                await member.add_roles(role, reason="X BOT Gaming self-assign addition")
                await interaction.response.send_message(
                    f"✅ Added role **{role.name}**! You can now access gaming channels and receive LFG pings.",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Bot lacks permissions to assign this role. Ensure the bot role is above the game roles.",
                    ephemeral=True,
                )


class DeviceRolesButton(discord.ui.Button):
    def __init__(self, device_key: str, row: Optional[int] = None):
        cfg = DEVICE_CONFIG[device_key]
        super().__init__(
            label=f"{cfg['emoji']} {cfg['name']}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"xbot_device_role_{device_key}",
            row=row,
        )
        self.device_key = device_key

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        db = getattr(interaction.client, "xbot_db", None) or interaction.client.tree.xbot_db
        role_id_str = setting(db, DEVICE_CONFIG[self.device_key]["role_key"])
        role_id = int(role_id_str) if role_id_str.isdigit() else 0

        role = interaction.guild.get_role(role_id) if role_id else None
        if not role:
            role = discord.utils.find(
                lambda r: r.name.lower() == self.device_key.lower() or f"{self.device_key} player" in r.name.lower() or f"{self.device_key} gamer" in r.name.lower(),
                interaction.guild.roles,
            )

        if not role:
            await interaction.response.send_message(
                f"❌ Role for **{DEVICE_CONFIG[self.device_key]['name']}** was not found on this server. Please contact an admin.",
                ephemeral=True,
            )
            return

        member = interaction.user
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="X BOT Device self-assign removal")
                await interaction.response.send_message(
                    f"🗑️ Removed device role **{role.name}**.", ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Bot lacks permissions to manage your roles. Ensure the bot role is above the device roles.",
                    ephemeral=True,
                )
        else:
            try:
                await member.add_roles(role, reason="X BOT Device self-assign addition")
                await interaction.response.send_message(
                    f"✅ Added device role **{role.name}**!",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Bot lacks permissions to assign this role. Ensure the bot role is above the device roles.",
                    ephemeral=True,
                )


class UnifiedGamingRolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        # Row 0: Devices (3 buttons)
        for key in ("pc", "mobile", "console"):
            self.add_item(DeviceRolesButton(key, row=0))
        # Row 1: Games Set 1 (3 buttons)
        for key in ("steam", "valorant", "roblox"):
            self.add_item(GameRolesButton(key, row=1))
        # Row 2: Games Set 2 (3 buttons)
        for key in ("minecraft", "mlbb", "genshin"):
            self.add_item(GameRolesButton(key, row=2))


GameRolesView = UnifiedGamingRolesView
NewUserOnboardingView = UnifiedGamingRolesView


def build_gaming_roles_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎮 [COMMUNITY & GAMING ROLES]",
        description=(
            "Welcome! Select your **gaming devices** and **favorite games** below.\n"
            "This unlocks game-specific chat channels, LFG squad notifications, and customizes your server profile!\n\n"
            "🖥️ **Select Devices (Row 1):**\n"
            "• `💻 PC` · PC / Desktop Gamers\n"
            "• `📱 Mobile` · Smartphone / Tablet Gamers\n"
            "• `🎮 Console` · PS5 / Xbox / Switch Gamers\n\n"
            "🎯 **Select Games (Row 2 & 3):**\n"
            "• `🎮 Steam` · Steam Titles & PC Gaming\n"
            "• `🎯 Valorant` · Riot Games & Competitive FPS\n"
            "• `🟥 Roblox` · Roblox Games & Community\n"
            "• `🟩 Minecraft` · Survival, Creative & Multiplayer\n"
            "• `🏆 MLBB` · Mobile Legends: Bang Bang\n"
            "• `✨ Genshin` · Genshin Impact & Co-op\n\n"
            "-# 💡 Click any button to toggle the role on or off at any time."
        ),
        color=0x3498DB,
    )
    embed.set_footer(text="X BOT · Gaming Community & Onboarding · Click to toggle roles")
    return embed


build_onboarding_embed = build_gaming_roles_embed


class LFGPartyView(discord.ui.View):
    def __init__(self, party_id: int, host_id: int, max_size: int, db):
        super().__init__(timeout=1800)  # 30 minutes lobby timeout
        self.party_id = party_id
        self.host_id = host_id
        self.max_size = max_size
        self.db = db

    def get_members(self):
        rows = self.db.execute(
            "SELECT user_id FROM lfg_members WHERE party_id=? ORDER BY joined_at",
            (self.party_id,),
        ).fetchall()
        return [r["user_id"] for r in rows]

    def build_embed(self, game: str, note: str, status: str = "open"):
        members = self.get_members()
        count = len(members)

        cfg = GAME_CONFIG.get(game.lower())
        color = cfg["color"] if cfg else discord.Color.purple()
        emoji = cfg["emoji"] if cfg else "🎮"

        if status == "full":
            status_text = "🎉 **STATUS: PARTY FULL! Ready to play!**"
            accent_color = discord.Color.gold()
        elif status == "cancelled":
            status_text = "❌ **STATUS: LOBBY CANCELLED**"
            accent_color = discord.Color.dark_grey()
        else:
            status_text = f"🟢 **STATUS: RECRUITING** ({count}/{self.max_size} Players)"
            accent_color = color

        member_lines = []
        for idx, uid in enumerate(members, start=1):
            tag = " 👑 *(Host)*" if uid == self.host_id else ""
            member_lines.append(f"`{idx}.` <@{uid}>{tag}")

        embed = discord.Embed(
            title=f"{emoji} [LFG PARTY] {game.title()} Squad",
            description=(
                f"{status_text}\n\n"
                f"📝 **Mission Note:** {note or 'Come play together!'}\n\n"
                f"👥 **Squad Roster ({count}/{self.max_size}):**\n"
                + "\n".join(member_lines)
                + (f"\n\n🎁 *All squad members receive +500 Cash upon full team!*" if status != "cancelled" else "")
            ),
            color=accent_color,
        )
        embed.set_footer(text="X BOT · Gaming Zone LFG")
        return embed

    @discord.ui.button(label="Join Squad", emoji="⚔️", style=discord.ButtonStyle.success)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        party = self.db.execute("SELECT * FROM lfg_parties WHERE id=?", (self.party_id,)).fetchone()
        if not party or party["status"] != "open":
            await interaction.response.send_message("This LFG party is no longer open.", ephemeral=True)
            return

        members = self.get_members()
        if interaction.user.id in members:
            await interaction.response.send_message("You are already in this squad!", ephemeral=True)
            return

        if len(members) >= self.max_size:
            await interaction.response.send_message("This squad is already full!", ephemeral=True)
            return

        now = int(time.time())
        self.db.execute(
            "INSERT INTO lfg_members (party_id, user_id, joined_at) VALUES (?, ?, ?)",
            (self.party_id, interaction.user.id, now),
        )
        self.db.commit()

        updated_members = self.get_members()
        is_full = len(updated_members) >= self.max_size

        if is_full:
            self.db.execute("UPDATE lfg_parties SET status='full' WHERE id=?", (self.party_id,))
            reward_cash = int(setting(self.db, "lfg_team_reward_cash") or 500)
            for uid in updated_members:
                self.db.execute("UPDATE players SET money=money+? WHERE user_id=?", (reward_cash, uid))
            self.db.commit()

            for item in self.children:
                item.disabled = True

            embed = self.build_embed(party["game"], party["note"], status="full")
            await interaction.response.edit_message(embed=embed, view=self)

            pings = " ".join(f"<@{uid}>" for uid in updated_members)
            await interaction.followup.send(
                f"📢 {pings} **The squad is FULL!** Grab your gear and hop into voice/game! (+{reward_cash:,} Cash team-up reward sent to all!)"
            )
        else:
            embed = self.build_embed(party["game"], party["note"], status="open")
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Leave", emoji="🚪", style=discord.ButtonStyle.secondary)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        party = self.db.execute("SELECT * FROM lfg_parties WHERE id=?", (self.party_id,)).fetchone()
        if not party or party["status"] != "open":
            await interaction.response.send_message("This squad is no longer active.", ephemeral=True)
            return

        if interaction.user.id == self.host_id:
            await interaction.response.send_message(
                "As the squad host, use **Cancel Squad** to close the lobby.", ephemeral=True
            )
            return

        members = self.get_members()
        if interaction.user.id not in members:
            await interaction.response.send_message("You are not in this squad.", ephemeral=True)
            return

        self.db.execute("DELETE FROM lfg_members WHERE party_id=? AND user_id=?", (self.party_id, interaction.user.id))
        self.db.commit()

        embed = self.build_embed(party["game"], party["note"], status="open")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Cancel Squad", emoji="❌", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_admin = getattr(interaction.user, "guild_permissions", None) and interaction.user.guild_permissions.administrator
        if interaction.user.id != self.host_id and not is_admin:
            await interaction.response.send_message("Only the squad host or an admin can cancel this lobby.", ephemeral=True)
            return

        self.db.execute("UPDATE lfg_parties SET status='cancelled' WHERE id=?", (self.party_id,))
        self.db.commit()

        for item in self.children:
            item.disabled = True

        party = self.db.execute("SELECT * FROM lfg_parties WHERE id=?", (self.party_id,)).fetchone()
        embed = self.build_embed(party["game"], party["note"], status="cancelled")
        await interaction.response.edit_message(embed=embed, view=self)


import json


class GamerCardView(discord.ui.View):
    """Interactive Gamer Card view with copy buttons, lounge invite, and edit handles."""
    def __init__(self, target_user: discord.User | discord.Member, profile, db):
        super().__init__(timeout=180)
        self.target_user = target_user
        self.profile = profile
        self.db = db

        steam_code = profile["steam_id"] if profile and profile["steam_id"] else ""
        roblox_name = profile["roblox_name"] if profile and profile["roblox_name"] else ""

        if steam_code:
            btn_steam = discord.ui.Button(
                label="Copy Steam Code",
                emoji="🎮",
                style=discord.ButtonStyle.primary,
                custom_id=f"xbot:game:steam:{target_user.id}",
            )
            btn_steam.callback = self.on_copy_steam
            self.add_item(btn_steam)

        if roblox_name:
            btn_roblox = discord.ui.Button(
                label="Copy Roblox User",
                emoji="🟥",
                style=discord.ButtonStyle.danger,
                custom_id=f"xbot:game:roblox:{target_user.id}",
            )
            btn_roblox.callback = self.on_copy_roblox
            self.add_item(btn_roblox)

        btn_invite = discord.ui.Button(
            label="Invite to Lounge",
            emoji="🚀",
            style=discord.ButtonStyle.success,
            custom_id=f"xbot:game:invite:{target_user.id}",
        )
        btn_invite.callback = self.on_invite_lounge
        self.add_item(btn_invite)

        btn_edit = discord.ui.Button(
            label="Edit Handles",
            emoji="⚙️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"xbot:game:edit:{target_user.id}",
        )
        btn_edit.callback = self.on_edit_handles
        self.add_item(btn_edit)

        btn_profile = discord.ui.Button(
            label="Economy Profile",
            emoji="👤",
            style=discord.ButtonStyle.secondary,
            custom_id=f"xbot:game:prof:{target_user.id}",
        )
        btn_profile.callback = self.on_view_profile
        self.add_item(btn_profile)

    async def on_view_profile(self, interaction: discord.Interaction):
        import economy
        view = economy.build_profile_view(self.target_user, self.db)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def on_copy_steam(self, interaction: discord.Interaction):
        steam_code = self.profile["steam_id"] if self.profile and self.profile["steam_id"] else ""
        if not steam_code:
            await interaction.response.send_message("No Steam ID or Friend Code registered.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"📋 **Steam ID / Friend Code for {self.target_user.display_name}:**\n`{steam_code}`\n\n*(Copy and paste into Steam to add as friend!)*",
            ephemeral=True,
        )

    async def on_copy_roblox(self, interaction: discord.Interaction):
        roblox_name = self.profile["roblox_name"] if self.profile and self.profile["roblox_name"] else ""
        if not roblox_name:
            await interaction.response.send_message("No Roblox username registered.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"📋 **Roblox Username for {self.target_user.display_name}:**\n`{roblox_name}`\n\n*(Copy and search on Roblox to connect!)*",
            ephemeral=True,
        )

    async def on_invite_lounge(self, interaction: discord.Interaction):
        import lounges
        active = lounges.get_active_lounge_by_host(self.db, interaction.user.id)
        if not active:
            await interaction.response.send_message(
                "⚠️ You do not currently host an active Lounge. Reserve a Lounge in the lobby first, then invite your squadmates!",
                ephemeral=True,
            )
            return

        lounge_id = active["lounge_id"]
        info = lounges.LOUNGES.get(lounge_id)
        try:
            invited = json.loads(active.get("invited_user_ids") or "[]")
        except Exception:
            invited = []

        if self.target_user.id not in invited:
            invited.append(self.target_user.id)
            self.db.execute("UPDATE server_lounges SET invited_user_ids=? WHERE lounge_id=?", (json.dumps(invited), lounge_id))
            self.db.commit()

            guild = interaction.guild
            if guild and info:
                tc = guild.get_channel(info["text_id"])
                vc = guild.get_channel(info["vc_id"])
                overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True, speak=True)
                if tc:
                    try: await tc.set_permissions(self.target_user, overwrite=overwrite)
                    except discord.HTTPException: pass
                if vc:
                    try: await vc.set_permissions(self.target_user, overwrite=overwrite)
                    except discord.HTTPException: pass
                if tc:
                    try: await tc.send(f"👋 {self.target_user.mention} **was invited to the squad by {interaction.user.mention}!**")
                    except Exception: pass

        await interaction.response.send_message(
            f"✅ **Invited {self.target_user.mention} to your active lounge ({active['name']})!**\n"
            f"• Voice Channel: <#{info['vc_id']}>\n"
            f"• Text Channel: <#{info['text_id']}>",
            ephemeral=True,
        )

    async def on_edit_handles(self, interaction: discord.Interaction):
        existing = get_game_profile(self.db, interaction.user.id)
        modal = QuickGameSetModal(self.db, existing)
        await interaction.response.send_modal(modal)


class QuickGameSetModal(discord.ui.Modal):
    """Modal to edit gaming handles quickly."""
    def __init__(self, db, existing=None):
        super().__init__(title="Edit Gaming Handles")
        self.db = db

        self.steam_input = discord.ui.TextInput(
            label="Steam Friend Code / ID",
            placeholder="e.g. 123456789 or custom vanity URL",
            default=existing["steam_id"] if existing and existing["steam_id"] else "",
            max_length=60,
            required=False,
        )
        self.add_item(self.steam_input)

        self.roblox_input = discord.ui.TextInput(
            label="Roblox Username",
            placeholder="e.g. BloxPlayer_99",
            default=existing["roblox_name"] if existing and existing["roblox_name"] else "",
            max_length=50,
            required=False,
        )
        self.add_item(self.roblox_input)

        self.mobile_input = discord.ui.TextInput(
            label="Mobile Games / Tags",
            placeholder="e.g. Wild Rift, PUBG Mobile, Genshin",
            default=existing["mobile_games"] if existing and existing["mobile_games"] else "",
            max_length=80,
            required=False,
        )
        self.add_item(self.mobile_input)

    async def on_submit(self, interaction: discord.Interaction):
        s = self.steam_input.value.strip()
        r = self.roblox_input.value.strip()
        m = self.mobile_input.value.strip()

        save_game_profile(self.db, interaction.user.id, s, r, m)
        await interaction.response.send_message(
            f"✅ **Gaming Handles Updated!**\n"
            f"• 🎮 **Steam:** `{s or 'Not set'}`\n"
            f"• 🟥 **Roblox:** `{r or 'Not set'}`\n"
            f"• 📱 **Mobile:** `{m or 'Not set'}`",
            ephemeral=True,
        )


def build_gamer_card(target: discord.User | discord.Member, profile, db, guild: Optional[discord.Guild] = None):
    """Build the Gamer Card embed and interactive GamerCardView for a player."""
    held_roles = []
    if guild and isinstance(target, discord.Member):
        for key, cfg in GAME_CONFIG.items():
            role_id_str = setting(db, cfg["role_key"])
            if role_id_str.isdigit():
                role = guild.get_role(int(role_id_str))
                if role and role in target.roles:
                    held_roles.append(f"{cfg['emoji']} {role.name}")

    roles_text = " · ".join(held_roles) if held_roles else "No gaming roles selected"
    steam_text = f"`{profile['steam_id']}`" if profile and profile["steam_id"] else "*Not set*"
    roblox_text = f"`{profile['roblox_name']}`" if profile and profile["roblox_name"] else "*Not set*"
    mobile_text = f"`{profile['mobile_games']}`" if profile and profile["mobile_games"] else "*Not set*"

    embed = discord.Embed(
        title=f"🎮 {target.display_name}'s Gamer Card",
        description=(
            f"🏷️ **Game Roles:** {roles_text}\n\n"
            f"🎮 **Steam ID / Friend Code:** {steam_text}\n"
            f"🟥 **Roblox User:** {roblox_text}\n"
            f"📱 **Mobile Games:** {mobile_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Click the buttons below to copy codes or invite this player to your Lounge squad!"
        ),
        color=discord.Color.blurple(),
    )
    avatar_url = target.display_avatar.url if hasattr(target, "display_avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"
    embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text="X BOT · Gaming Card · Click [Edit Handles] to update your tags")

    card_view = GamerCardView(target, profile, db)
    return embed, card_view


def register_commands(bot, db, is_council_or_admin=None, staff_kwargs=None) -> None:
    """Register all slash commands and listeners for the Gaming Zone."""
    bot.xbot_db = db
    bot.add_view(GameRolesView())
    bot.add_view(NewUserOnboardingView())

    gaming_group = app_commands.Group(name="gaming", description="X BOT Gaming Zone commands")

    async def _handle_lfg(
        interaction: discord.Interaction,
        game: app_commands.Choice[str],
        party_size: int,
        note: str,
    ):
        if not interaction.guild:
            await interaction.response.send_message("LFG parties can only be formed in a server.", ephemeral=True)
            return

        now = int(time.time())
        cursor = db.execute(
            "INSERT INTO lfg_parties (host_id, game, party_size, note, channel_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (interaction.user.id, game.value, party_size, note[:200], interaction.channel_id, now),
        )
        party_id = cursor.lastrowid
        db.execute(
            "INSERT INTO lfg_members (party_id, user_id, joined_at) VALUES (?, ?, ?)",
            (party_id, interaction.user.id, now),
        )
        db.commit()

        view = LFGPartyView(party_id, interaction.user.id, party_size, db)
        embed = view.build_embed(game.value, note[:200], status="open")

        # Mention the corresponding game role if configured
        role_mention = ""
        cfg = GAME_CONFIG.get(game.value)
        if cfg and interaction.guild:
            role_id_str = setting(db, cfg["role_key"])
            if role_id_str.isdigit() and int(role_id_str) > 0:
                role = interaction.guild.get_role(int(role_id_str))
                if role:
                    role_mention = f"{role.mention} "

        content = f"📢 {role_mention}{interaction.user.mention} is looking for a **{game.name}** squad!" if role_mention else f"📢 {interaction.user.mention} is looking for a **{game.name}** squad!"
        await interaction.response.send_message(content=content, embed=embed, view=view)

    lfg_choices = [
        app_commands.Choice(name="Valorant", value="valorant"),
        app_commands.Choice(name="Roblox", value="roblox"),
        app_commands.Choice(name="Steam", value="steam"),
        app_commands.Choice(name="Minecraft", value="minecraft"),
        app_commands.Choice(name="MLBB", value="mlbb"),
        app_commands.Choice(name="Genshin Impact", value="genshin"),
        app_commands.Choice(name="Mobile", value="mobile"),
        app_commands.Choice(name="Other", value="other"),
    ]

    @gaming_group.command(name="lfg", description="Find teammates and start an LFG gaming party")
    @app_commands.describe(
        game="Game or platform you want to play",
        party_size="Total squad size needed (including you, 2-10)",
        note="What game/mode are you playing? (e.g. Doors / Lethal Company / Rank)",
    )
    @app_commands.choices(game=lfg_choices)
    async def gaming_lfg_command(
        interaction: discord.Interaction,
        game: app_commands.Choice[str],
        party_size: app_commands.Range[int, 2, 10] = 4,
        note: str = "Let's squad up!",
    ):
        await _handle_lfg(interaction, game, party_size, note)

    @bot.tree.command(name="lfg", description="Find teammates and start an LFG gaming party")
    @app_commands.describe(
        game="Game or platform you want to play",
        party_size="Total squad size needed (including you, 2-10)",
        note="What game/mode are you playing? (e.g. Doors / Lethal Company / Rank)",
    )
    @app_commands.choices(game=lfg_choices)
    async def top_lfg_command(
        interaction: discord.Interaction,
        game: app_commands.Choice[str],
        party_size: app_commands.Range[int, 2, 10] = 4,
        note: str = "Let's squad up!",
    ):
        await _handle_lfg(interaction, game, party_size, note)

    @gaming_group.command(name="set", description="Register or update your gaming handles (Steam, Roblox, Mobile)")
    @app_commands.describe(
        steam_id="Your Steam ID or Friend Code (e.g. 123456789 or custom URL)",
        roblox_name="Your Roblox username or profile link",
        mobile_games="Your favorite mobile games or friend tags",
    )
    async def game_set_command(
        interaction: discord.Interaction,
        steam_id: Optional[str] = "",
        roblox_name: Optional[str] = "",
        mobile_games: Optional[str] = "",
    ):
        existing = get_game_profile(db, interaction.user.id)
        current_steam = steam_id if steam_id else (existing["steam_id"] if existing else "")
        current_roblox = roblox_name if roblox_name else (existing["roblox_name"] if existing else "")
        current_mobile = mobile_games if mobile_games else (existing["mobile_games"] if existing else "")

        if not any((current_steam, current_roblox, current_mobile)):
            await interaction.response.send_message("Please provide at least one handle or game tag.", ephemeral=True)
            return

        save_game_profile(db, interaction.user.id, current_steam, current_roblox, current_mobile)
        await interaction.response.send_message(
            view=xbot_ui.success(
                "🎮 Gaming Profile Saved",
                f"**Steam:** `{current_steam or 'Not set'}`\n"
                f"**Roblox:** `{current_roblox or 'Not set'}`\n"
                f"**Mobile:** `{current_mobile or 'Not set'}`\n\n"
                f"Other members can view this with `/gaming profile user:{interaction.user.mention}`!",
            ),
            ephemeral=True,
        )

    @gaming_group.command(name="profile", description="View a member's interactive gaming profile card")
    @app_commands.describe(user="Member whose gaming profile you want to inspect")
    async def game_profile_command(interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        profile = get_game_profile(db, target.id)
        embed, card_view = build_gamer_card(target, profile, db, interaction.guild)
        await interaction.response.send_message(embed=embed, view=card_view)

    @gaming_group.command(name="leaderboard", description="View the Top 10 Weekly Gamers in Voice Lounges")
    async def gaming_leaderboard_command(interaction: discord.Interaction):
        rows = db.execute(
            """SELECT p.user_id, p.display_name, x.voice_xp, x.weekly_xp
            FROM xp_profiles x
            JOIN players p ON p.user_id=x.user_id
            WHERE x.weekly_xp > 0
            ORDER BY x.voice_xp DESC, x.weekly_xp DESC
            LIMIT 10"""
        ).fetchall()

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for idx, r in enumerate(rows):
            medal = medals[idx] if idx < 3 else f"**#{idx+1}**"
            name = r["display_name"] or f"Player {r['user_id']}"
            lines.append(f"{medal} <@{r['user_id']}> — `{r['voice_xp']:,} Voice XP` (`{r['weekly_xp']:,} Weekly XP`)")

        body = "\n".join(lines) if lines else "No squad gaming activity recorded this week yet."
        body += (
            "\n\n🎁 **Squad Gamer Perk:**\n"
            "Hang out in Lounge VCs for 30+ minutes with your squad to receive **Gamer Supply Drops**!"
        )

        embed = discord.Embed(
            title="🏆 [X BOT WEEKLY GAMER LEADERBOARD]",
            description=body,
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Weekly gaming ranks reset every Monday at 00:00 UTC")
        await interaction.response.send_message(embed=embed)

    @gaming_group.command(name="manage_roles", description="Staff tool: View or modify a member's device & game roles")
    @app_commands.describe(
        user="Target member to manage",
        action="Action to perform",
        role_type="Which device or game role to assign or remove",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="View Roles", value="view"),
            app_commands.Choice(name="Add Role", value="add"),
            app_commands.Choice(name="Remove Role", value="remove"),
        ],
        role_type=[
            app_commands.Choice(name="Device: PC", value="pc"),
            app_commands.Choice(name="Device: Mobile", value="mobile_device"),
            app_commands.Choice(name="Device: Console", value="console"),
            app_commands.Choice(name="Game: Steam", value="steam"),
            app_commands.Choice(name="Game: Valorant", value="valorant"),
            app_commands.Choice(name="Game: Roblox", value="roblox"),
            app_commands.Choice(name="Game: Minecraft", value="minecraft"),
            app_commands.Choice(name="Game: MLBB", value="mlbb"),
            app_commands.Choice(name="Game: Genshin Impact", value="genshin"),
            app_commands.Choice(name="Game: Mobile", value="mobile_game"),
        ],
    )
    async def gaming_manage_roles_command(
        interaction: discord.Interaction,
        user: discord.Member,
        action: app_commands.Choice[str],
        role_type: Optional[app_commands.Choice[str]] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        is_admin = interaction.user.guild_permissions.manage_roles or interaction.user.guild_permissions.administrator
        if is_council_or_admin and not is_admin:
            is_admin = is_council_or_admin(interaction)

        if not is_admin:
            await interaction.response.send_message("❌ You do not have permission to manage member roles.", ephemeral=True)
            return

        if action.value == "view":
            user_roles = []
            for d_key, d_cfg in DEVICE_CONFIG.items():
                r_id_str = setting(db, d_cfg["role_key"])
                role = interaction.guild.get_role(int(r_id_str)) if r_id_str.isdigit() and int(r_id_str) > 0 else None
                if not role:
                    role = discord.utils.find(lambda r: r.name.lower() == d_key.lower() or f"{d_key} player" in r.name.lower() or f"{d_key} gamer" in r.name.lower(), interaction.guild.roles)
                if role and role in user.roles:
                    user_roles.append(f"• **Device:** {d_cfg['emoji']} {role.name}")

            for g_key, g_cfg in GAME_CONFIG.items():
                r_id_str = setting(db, g_cfg["role_key"])
                role = interaction.guild.get_role(int(r_id_str)) if r_id_str.isdigit() and int(r_id_str) > 0 else None
                if not role:
                    role = discord.utils.find(lambda r: g_key.lower() in r.name.lower(), interaction.guild.roles)
                if role and role in user.roles:
                    user_roles.append(f"• **Game:** {g_cfg['emoji']} {role.name}")

            summary = "\n".join(user_roles) if user_roles else "No device or game roles assigned."
            embed = discord.Embed(
                title=f"🎮 Roles for {user.display_name}",
                description=summary,
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not role_type:
            await interaction.response.send_message("Please specify a `role_type` when adding or removing roles.", ephemeral=True)
            return

        target_role = None
        rtype = role_type.value
        if rtype in ("pc", "mobile_device", "console"):
            key = "mobile" if rtype == "mobile_device" else rtype
            r_id_str = setting(db, DEVICE_CONFIG[key]["role_key"])
            if r_id_str.isdigit() and int(r_id_str) > 0:
                target_role = interaction.guild.get_role(int(r_id_str))
            if not target_role:
                target_role = discord.utils.find(lambda r: r.name.lower() == key.lower() or f"{key} player" in r.name.lower() or f"{key} gamer" in r.name.lower(), interaction.guild.roles)
        else:
            key = "mobile" if rtype == "mobile_game" else rtype
            r_id_str = setting(db, GAME_CONFIG[key]["role_key"])
            if r_id_str.isdigit() and int(r_id_str) > 0:
                target_role = interaction.guild.get_role(int(r_id_str))
            if not target_role:
                target_role = discord.utils.find(lambda r: key.lower() in r.name.lower(), interaction.guild.roles)

        if not target_role:
            await interaction.response.send_message(f"❌ Role `{role_type.name}` could not be resolved on this server.", ephemeral=True)
            return

        if action.value == "add":
            try:
                await user.add_roles(target_role, reason=f"Staff {interaction.user} assigned via /gaming manage_roles")
                await interaction.response.send_message(f"✅ Assigned **{target_role.name}** to {user.mention}.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ Bot lacks permission to assign this role. Check role hierarchy.", ephemeral=True)
        elif action.value == "remove":
            try:
                await user.remove_roles(target_role, reason=f"Staff {interaction.user} removed via /gaming manage_roles")
                await interaction.response.send_message(f"🗑️ Removed **{target_role.name}** from {user.mention}.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ Bot lacks permission to remove this role. Check role hierarchy.", ephemeral=True)

    bot.tree.add_command(gaming_group)
