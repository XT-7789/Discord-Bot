import os
import inspect
import random
import shlex
import time
import sqlite3
import traceback
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv
import economy
import casino
import xbot_ui
import war_system
import war_tier
import economy_extra
import advanced_systems
import leveling
import applications
import staff_panel
import tester_feedback
import tier4
import tier5
import tier6
import tier7
import tier8
import system_ui
import casual_games
import deadzone

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# ---------- Balance ----------

ATTACK_COOLDOWN = 60
COLLECT_COOLDOWN = 60
MINE_COOLDOWN = 60

LAND_INCOME_PER_LAND = 100
MINE_BUILD_COST = 500
RESOURCE_PER_MINE = 5

CAPITAL_DAMAGE = 25
CAPITAL_REWARD_PERCENT = 0.25

UNIT_COSTS = {
    "land": 50,
    "air": 150,
    "navy": 250
}

RESOURCE_VALUES = {
    "iron": 10,
    "gold": 35,
    "oil": 20
}

# ---------- Database ----------

db = sqlite3.connect("xwar.db", timeout=30)
db.row_factory = sqlite3.Row
db.execute("PRAGMA busy_timeout = 30000")
db.execute("PRAGMA journal_mode = WAL")
db.execute("PRAGMA synchronous = NORMAL")

db.execute("""
CREATE TABLE IF NOT EXISTS players (
    user_id INTEGER PRIMARY KEY,
    nation_name TEXT NOT NULL,
    money INTEGER NOT NULL DEFAULT 1000,
    land INTEGER NOT NULL DEFAULT 1,
    land_army INTEGER NOT NULL DEFAULT 10,
    air_army INTEGER NOT NULL DEFAULT 0,
    navy INTEGER NOT NULL DEFAULT 0,
    last_attack INTEGER NOT NULL DEFAULT 0,
    last_collect INTEGER NOT NULL DEFAULT 0,
    capital_name TEXT NOT NULL DEFAULT 'National Capital',
    capital_health INTEGER NOT NULL DEFAULT 100,
    iron_mines INTEGER NOT NULL DEFAULT 0,
    gold_mines INTEGER NOT NULL DEFAULT 0,
    oil_mines INTEGER NOT NULL DEFAULT 0,
    iron INTEGER NOT NULL DEFAULT 0,
    gold INTEGER NOT NULL DEFAULT 0,
    oil INTEGER NOT NULL DEFAULT 0,
    last_mine INTEGER NOT NULL DEFAULT 0
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS alliances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    tag TEXT NOT NULL UNIQUE COLLATE NOCASE,
    leader_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS alliance_members (
    user_id INTEGER PRIMARY KEY,
    alliance_id INTEGER NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS wars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attacker_alliance_id INTEGER NOT NULL,
    defender_alliance_id INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    winner_alliance_id INTEGER
)
""")

db.commit()

economy.initialise(db)
casino.initialise(db)
war_system.initialise(db)
war_tier.initialise(db)
economy_extra.initialise(db)
advanced_systems.initialise(db)
leveling.initialise(db)
applications.initialise(db)
tester_feedback.initialise(db)
tier4.initialise(db)
tier5.initialise(db)
tier6.initialise(db)
tier7.initialise(db)
tier8.initialise(db)
casual_games.initialise(db)
deadzone.initialise(db)

# Add missing columns safely for old databases.
columns = {
    column["name"]
    for column in db.execute("PRAGMA table_info(players)").fetchall()
}

upgrades = {
    "display_name": "TEXT NOT NULL DEFAULT ''",
    "last_attack": "INTEGER NOT NULL DEFAULT 0",
    "last_collect": "INTEGER NOT NULL DEFAULT 0",
    "capital_name": "TEXT NOT NULL DEFAULT 'National Capital'",
    "capital_health": "INTEGER NOT NULL DEFAULT 100",
    "iron_mines": "INTEGER NOT NULL DEFAULT 0",
    "gold_mines": "INTEGER NOT NULL DEFAULT 0",
    "oil_mines": "INTEGER NOT NULL DEFAULT 0",
    "iron": "INTEGER NOT NULL DEFAULT 0",
    "gold": "INTEGER NOT NULL DEFAULT 0",
    "oil": "INTEGER NOT NULL DEFAULT 0",
    "last_mine": "INTEGER NOT NULL DEFAULT 0",
    "nation_created_at": "INTEGER NOT NULL DEFAULT 0",
}

for column_name, column_type in upgrades.items():
    if column_name not in columns:
        db.execute(
            f"ALTER TABLE players ADD COLUMN {column_name} {column_type}"
        )

db.commit()


# ---------- Helpers ----------

def get_player(user_id: int):
    return db.execute(
        "SELECT * FROM players WHERE user_id = ?",
        (user_id,)
    ).fetchone()


def create_player(user: discord.abc.User):
    player = get_player(user.id)

    if player is None:
        db.execute(
            """
            INSERT INTO players (user_id, nation_name, capital_name)
            VALUES (?, ?, ?)
            """,
            (
                user.id,
                f"{user.display_name}'s Nation",
                f"{user.display_name} Capital"
            )
        )
        db.execute(
            "UPDATE players SET xc = ? WHERE user_id = ?",
            (economy.setting(db, "starting_xc"), user.id)
        )
        db.execute("UPDATE players SET nation_created_at=? WHERE user_id=?", (int(time.time()), user.id))
        infantry = db.execute("SELECT id FROM war_unit_types WHERE code='infantry'").fetchone()
        if infantry:
            db.execute("INSERT OR IGNORE INTO player_war_units(user_id,unit_type_id,quantity) VALUES(?,?,10)", (user.id, infantry["id"]))
        db.commit()
        player = get_player(user.id)

    display_name = getattr(user, "display_name", str(user))
    if player["display_name"] != display_name:
        db.execute("UPDATE players SET display_name=? WHERE user_id=?", (display_name, user.id))
        db.commit()
        player = get_player(user.id)
    return player


def power(player):
    return war_system.total_power(db, player["user_id"])


def calculate_losses(player, percentage):
    return war_system.calculate_losses(db, player["user_id"], percentage)


def save_losses(user_id, losses):
    war_system.save_losses(db, user_id, losses)


def losses_text(losses):
    return war_system.losses_text(db, losses)


def get_alliance_for_user(user_id: int):
    return db.execute(
        """
        SELECT alliances.*
        FROM alliances
        INNER JOIN alliance_members
            ON alliances.id = alliance_members.alliance_id
        WHERE alliance_members.user_id = ?
        """,
        (user_id,)
    ).fetchone()


def get_alliance_by_name(name: str):
    return db.execute(
        "SELECT * FROM alliances WHERE name = ?",
        (name,)
    ).fetchone()

def get_active_war():
    return db.execute(
        "SELECT * FROM wars WHERE active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()


def alliance_power(alliance_id: int):
    return war_system.alliance_total_power(db, alliance_id)


def is_server_admin(interaction: discord.Interaction):
    permissions = getattr(interaction.user, "guild_permissions", None)
    return interaction.guild is not None and permissions.administrator


STAFF_ROLE_IDS = {1523954152701431848, 1531176720097476708}  # X Council, Administration
# Discord allows 100 global slash commands. X BOT already uses that full
# global catalogue, so Tier 1.6 staff tools are registered directly to this
# server instead of pushing the global catalogue past Discord's hard limit.
_staff_guild_id = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
STAFF_COMMAND_KWARGS = {"guild": discord.Object(id=_staff_guild_id)} if _staff_guild_id else {}

# Public player catalogue.  All other player actions stay available through
# the Lobby panels, without filling Discord's slash-command picker.
PUBLIC_PLAYER_COMMANDS = {
    # Main panels: short, memorable direct access for ordinary players.
    "menu", "overview", "profile", "economy", "shop", "backpack", "market", "mining", "stock",
    "warfront", "city", "army", "recruit", "diplomacy", "casino", "craft", "research",
    # Fast actions that are still useful without opening a panel first.
    "collect", "mine", "sell_item", "map_detail", "map", "claim_land",
    "declare_war", "attack", "balance", "code_redeem", "daily", "deadzone",
    "level", "rank",
    # Direct slash commands restored for convenient fast access without menu-clicking fatigue.
    "exchange", "pay", "work", "bank", "deposit", "withdraw",
    "coinflip", "blackjack", "slot", "dice", "roulette", "scratch",
}

# These commands are deliberately retained for Administration / Moderators.
# Their own permission checks and Dashboard Command Access rules still apply.
STAFF_SLASH_COMMANDS = {
    "admin", "say",
    # Kept temporarily until the second Staff Centre migration. These tools
    # must not disappear before their button-driven replacements are ready.
    "inrole", "role", "spawn", "remove_item", "economy_adjust",
    "inventory_check", "lottery_draw", "setlevel", "server_settings",
    "war_start", "war_end", "forces_check",
    "deadzone_send", "deadzone_restore", "deadzone_scan", "deadzone_post",
    "level",
}


def is_council_or_admin(interaction: discord.Interaction) -> bool:
    """Allow senior staff and Discord Moderators to use safe role tools."""
    if interaction.guild is None:
        return False
    permissions = getattr(interaction.user, "guild_permissions", None)
    if permissions and permissions.administrator:
        return True
    if permissions and permissions.moderate_members:
        return True
    return bool({role.id for role in getattr(interaction.user, "roles", [])} & STAFF_ROLE_IDS)


def _server_settings_embed() -> discord.Embed:
    """A compact, single-page view of the server chat settings."""
    enabled = leveling.setting(db, "xp_announcement_enabled") == "1"
    channel_id = leveling.setting(db, "xp_announcement_channel_id")
    channel_text = f"<#{channel_id}>" if channel_id.isdigit() and channel_id != "0" else "Not selected"
    template = leveling.setting(db, "xp_announcement_template")
    embed = discord.Embed(title="⚙️ X BOT Server Settings", colour=discord.Color.blurple())
    embed.description = "Manage level-up chat notices here. Use `/say` when you want X BOT to post a normal announcement."
    embed.add_field(name="Level-up notices", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=True)
    embed.add_field(name="Announcement channel", value=channel_text, inline=True)
    embed.add_field(name="Current message", value=f"```{template[:900]}```", inline=False)
    embed.set_footer(text="Only Administrators and Moderators can use these controls.")
    return embed


class AnnouncementChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent):
        super().__init__(placeholder="Choose the level-up announcement channel…", channel_types=[discord.ChannelType.text])
        self.parent_panel = parent

    async def callback(self, interaction: discord.Interaction):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("Only Administrators and Moderators can change settings.", ephemeral=True)
            return
        channel = self.values[0]
        db.execute("INSERT INTO economy_settings(key,value) VALUES('xp_announcement_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(channel.id),))
        db.commit()
        await interaction.response.edit_message(embed=_server_settings_embed(), view=self.parent_panel)


class AnnouncementTemplateModal(discord.ui.Modal, title="Level-up announcement message"):
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
        placeholder="Use {mention}, {user}, {level}, and {reward}",
    )

    def __init__(self):
        super().__init__()
        self.message.default = leveling.setting(db, "xp_announcement_template")

    async def on_submit(self, interaction: discord.Interaction):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("Only Administrators and Moderators can change settings.", ephemeral=True)
            return
        db.execute("INSERT INTO economy_settings(key,value) VALUES('xp_announcement_template',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(self.message),))
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("📣 Announcement Saved", "The level-up message was updated."), ephemeral=True)


class ServerSettingsView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.add_item(AnnouncementChannelSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id and is_council_or_admin(interaction):
            return True
        await interaction.response.send_message("This settings panel belongs to another staff member.", ephemeral=True)
        return False

    @discord.ui.button(label="Edit level-up message", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit_message(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(AnnouncementTemplateModal())

    @discord.ui.button(label="Enable / disable notices", emoji="🔔", style=discord.ButtonStyle.secondary)
    async def toggle_notices(self, interaction: discord.Interaction, _button: discord.ui.Button):
        current = leveling.setting(db, "xp_announcement_enabled") == "1"
        db.execute("INSERT INTO economy_settings(key,value) VALUES('xp_announcement_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("0" if current else "1",))
        db.commit()
        await interaction.response.edit_message(embed=_server_settings_embed(), view=self)

# ---------- Bot ----------

class XCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        command_name = interaction.command.name if interaction.command else ""
        if command_name in {"army_recruit", "armed_forces"} and not interaction.response.is_done():
            try:
                await interaction.response.defer(thinking=True)
            except discord.NotFound:
                return False
        return await advanced_systems.command_access_check(interaction, db)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        print(f"Slash command error in /{interaction.command.name if interaction.command else 'unknown'}: {original}")
        traceback.print_exception(type(original), original, original.__traceback__)
        message = xbot_ui.danger(
            "⚠️ Command Error",
            "Open /menu to refresh your panel. If this involved a payment or reward, check your balance and items before trying again. The issue has been logged for the administrator.",
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(view=message, ephemeral=True)
            else:
                await interaction.response.send_message(view=message, ephemeral=True)
        except discord.HTTPException:
            pass


class XBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = XCommandTree(self)

    async def setup_hook(self):
        applications.setup_persistent_views(self, db)
        tester_feedback.setup_persistent_views(self, db)
        self.add_view(deadzone.DeadzoneReviveView())
        published = PUBLIC_PLAYER_COMMANDS | STAFF_SLASH_COMMANDS

        def hide_panel_commands(command_guild, allowed_names):
            hidden = []
            for command in list(self.tree.get_commands(guild=command_guild)):
                if command.name not in allowed_names:
                    removed = self.tree.remove_command(command.name, guild=command_guild)
                    if removed is not None:
                        hidden.append(removed)
            return hidden

        def restore_panel_commands(command_guild, hidden):
            for command in hidden:
                self.tree.add_command(command, guild=command_guild)

        def report_sync(command_guild, hidden):
            scope_name = "global" if command_guild is None else f"guild {command_guild.id}"
            visible_names = sorted(command.name for command in self.tree.get_commands(guild=command_guild))
            print(f"Published {len(visible_names)} X BOT commands to {scope_name}: {', '.join(visible_names)}")
            print(f"Kept {len(hidden)} panel-only commands hidden from {scope_name}.")

        if _staff_guild_id:
            staff_guild = discord.Object(id=_staff_guild_id)

            # X BOT is a single-server game. Keep the public entry commands in
            # the configured guild catalogue so Discord refreshes them at once,
            # and clear the global catalogue to prevent every command appearing
            # twice (one global copy plus one guild copy).
            global_commands = list(self.tree.get_commands())
            for command in global_commands:
                if command.name in published:
                    self.tree.add_command(command, guild=staff_guild, override=True)

            removed_global = []
            for command in list(self.tree.get_commands()):
                removed = self.tree.remove_command(command.name)
                if removed is not None:
                    removed_global.append(removed)
            await self.tree.sync()
            print("Cleared global X BOT commands; this server uses the immediate guild catalogue.")

            guild_hidden = hide_panel_commands(staff_guild, published)
            await self.tree.sync(guild=staff_guild)
            report_sync(staff_guild, guild_hidden)
            restore_panel_commands(staff_guild, guild_hidden)
            restore_panel_commands(None, removed_global)
        else:
            # Fallback for installations that have not configured a server ID.
            global_hidden = hide_panel_commands(None, PUBLIC_PLAYER_COMMANDS)
            await self.tree.sync()
            report_sync(None, global_hidden)
            restore_panel_commands(None, global_hidden)
        casino.start_vip_cleanup_task(self, db)
        leveling.start_voice_task(self, db)
        tier4.start_backup_task(self, db)
        deadzone.start_deadzone_task(self, db)


bot = XBot()
bot.db = db


@tasks.loop(minutes=10)
async def season_settlement_loop():
    """Close due Seasons even when nobody presses a command."""
    for summary in war_tier.settle_expired_seasons(db):
        print(f"Season settlement: {summary}")


@season_settlement_loop.before_loop
async def before_season_settlement_loop():
    await bot.wait_until_ready()


class XBPrefixResponse:
    """Small Interaction response adapter used by XB-prefixed text commands."""
    def __init__(self, message: discord.Message):
        self.message = message
        self.sent_message = None
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, content=None, **kwargs):
        kwargs.pop("ephemeral", None)
        self.sent_message = await self.message.channel.send(content=content, **kwargs)
        self._done = True
        return self.sent_message

    async def defer(self, **kwargs):
        self._done = True
        await self.message.channel.typing()


class XBPrefixFollowup:
    def __init__(self, message: discord.Message):
        self.message = message

    async def send(self, content=None, **kwargs):
        kwargs.pop("ephemeral", None)
        return await self.message.channel.send(content=content, **kwargs)


class XBPrefixInteraction:
    def __init__(self, message: discord.Message, command):
        self.user = message.author
        self.guild = message.guild
        self.guild_id = message.guild.id if message.guild else None
        self.channel = message.channel
        self.channel_id = message.channel.id
        self.client = bot
        self.command = command
        self.message = message
        self.response = XBPrefixResponse(message)
        self.followup = XBPrefixFollowup(message)

    async def edit_original_response(self, **kwargs):
        if self.response.sent_message:
            return await self.response.sent_message.edit(**kwargs)
        return await self.channel.send(**kwargs)


def xb_prefix_help():
    return xbot_ui.panel(
        "⌨️ X BOT Prefix Commands",
        "The main player experience now uses the guided panels.\n\n"
        "`/menu` or `XBmenu` · all systems\n"
        "`/economy` or `XBeconomy` · Economy Centre\n"
        "`/warfront` or `XBwarfront` · War Centre\n"
        "`/casino` or `XBcasino` · Casino\n"
        "`/craft` or `XBcraft` · Crafting Centre\n\n"
        "Use `XBcommands` to view the small supported command list.",
        colour=discord.Color.blurple(),
    )


def _mention_id(value: str):
    digits = "".join(character for character in value if character.isdigit())
    return int(digits) if digits else None


async def _convert_prefix_argument(message, parameter, value):
    option_type = parameter.type
    if parameter.choices:
        choice = next((choice for choice in parameter.choices
            if str(choice.value).lower() == value.lower() or choice.name.lower() == value.lower()), None)
        if choice is None:
            raise ValueError(f"{parameter.name} choices: " + ", ".join(choice.name for choice in parameter.choices))
        return choice
    if option_type is discord.AppCommandOptionType.integer:
        return int(value)
    if option_type is discord.AppCommandOptionType.number:
        return float(value)
    if option_type is discord.AppCommandOptionType.boolean:
        lowered = value.lower()
        if lowered not in {"true", "false", "yes", "no", "1", "0", "on", "off"}:
            raise ValueError(f"{parameter.name} must be true or false")
        return lowered in {"true", "yes", "1", "on"}
    resource_id = _mention_id(value)
    if option_type is discord.AppCommandOptionType.user:
        if message.guild is None or resource_id is None:
            raise ValueError(f"{parameter.name} must be a server @user")
        member = message.guild.get_member(resource_id)
        if member is None:
            member = await message.guild.fetch_member(resource_id)
        return member
    if option_type is discord.AppCommandOptionType.role:
        role = message.guild.get_role(resource_id) if message.guild and resource_id else None
        if role is None:
            raise ValueError(f"{parameter.name} must be a server @role")
        return role
    if option_type is discord.AppCommandOptionType.mentionable:
        resource = None
        if message.guild and resource_id:
            resource = message.guild.get_member(resource_id) or message.guild.get_role(resource_id)
        if resource is None:
            raise ValueError(f"{parameter.name} must be a server @user or @role")
        return resource
    if option_type is discord.AppCommandOptionType.channel:
        resource = message.guild.get_channel(resource_id) if message.guild and resource_id else None
        if resource is None:
            raise ValueError(f"{parameter.name} must be a server channel mention")
        return resource
    return value


async def run_xb_prefix(message: discord.Message):
    raw = message.content.strip()
    if len(raw) < 3 or raw[:2].lower() != "xb":
        return False
    body = raw[2:].lstrip()
    if not body:
        await message.channel.send(view=xb_prefix_help())
        return True
    try:
        tokens = shlex.split(body)
    except ValueError as error:
        await message.channel.send(view=xbot_ui.danger("⌨️ Prefix Syntax Error", str(error)))
        return True
    command_name = tokens.pop(0).lower().lstrip("/")
    if command_name in {"help", "prefix"}:
        await message.channel.send(view=xb_prefix_help())
        return True
    if command_name in {"commands", "commandlist"}:
        permissions = getattr(message.author, "guild_permissions", None)
        role_ids = {role.id for role in getattr(message.author, "roles", [])}
        can_manage = bool(
            message.guild
            and ((permissions and (permissions.administrator or permissions.moderate_members))
                 or role_ids & STAFF_ROLE_IDS)
        )
        names = sorted(PUBLIC_PLAYER_COMMANDS | (STAFF_SLASH_COMMANDS if can_manage else set()))
        await message.channel.send(view=xbot_ui.panel("⌨️ XB Command Names", " · ".join(f"`XB{name}`" for name in names), colour=discord.Color.blurple(), footer="Commands with settings are easier to use as Slash Commands."))
        return True
    if command_name not in PUBLIC_PLAYER_COMMANDS | STAFF_SLASH_COMMANDS:
        await message.channel.send(view=xbot_ui.danger(
            "Command Moved to a Panel",
            f"`XB{command_name}` is no longer a public shortcut. Open `XBmenu`, `XBeconomy`, `XBwarfront`, `XBcasino`, or `XBcraft` instead.",
        ))
        return True
    command = bot.tree.get_command(command_name)
    # Tier 1.6 staff tools are server commands (Discord's global command list
    # is full), but they should still keep the promised XB prefix alternative.
    if not isinstance(command, app_commands.Command) and message.guild:
        command = bot.tree.get_command(command_name, guild=message.guild)
    if not isinstance(command, app_commands.Command):
        await message.channel.send(view=xbot_ui.danger("Unknown XB Command", f"`XB{command_name}` was not found. Use `XBcommands`."))
        return True
    interaction = XBPrefixInteraction(message, command)
    if not await advanced_systems.command_access_check(interaction, db):
        return True
    callback_signature = inspect.signature(command.callback)
    callback_parameters = list(callback_signature.parameters.values())[1:]
    app_parameters = {parameter.name: parameter for parameter in command.parameters}
    arguments = []
    try:
        for callback_parameter in callback_parameters:
            app_parameter = app_parameters.get(callback_parameter.name)
            if tokens:
                arguments.append(await _convert_prefix_argument(message, app_parameter, tokens.pop(0)))
            elif callback_parameter.default is not inspect.Parameter.empty:
                arguments.append(callback_parameter.default)
            else:
                raise ValueError(f"Missing `{callback_parameter.name}`. Try `/{command_name}` for the guided form.")
        if tokens:
            raise ValueError("Too many arguments. Put text containing spaces inside quotes.")
        await command.callback(interaction, *arguments)
    except (ValueError, TypeError, discord.HTTPException) as error:
        if not interaction.response.is_done():
            await interaction.response.send_message(view=xbot_ui.danger("⌨️ XB Command Error", f"{error}\nTry `/{command_name}` for Discord's guided options."))
    except Exception as error:
        print(f"XB prefix error in {command_name}: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)
        if not interaction.response.is_done():
            await interaction.response.send_message(view=xbot_ui.danger("⚠️ Command Error", "X BOT could not complete this XB command. The error was written to the Bot terminal."))
    return True


@bot.event
async def on_ready():
    print(f"Bot is online: {bot.user}")
    for summary in war_tier.settle_expired_seasons(db):
        print(f"Season settlement: {summary}")
    if not season_settlement_loop.is_running():
        season_settlement_loop.start()


@bot.event
async def on_member_join(member: discord.Member):
    await applications.handle_member_join(bot, db, member)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await leveling.handle_message(bot, db, message)
    await deadzone.handle_message(bot, db, message)
    await run_xb_prefix(message)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    if after.channel is not None:
        deadzone.touch_activity(db, member.id)


@bot.tree.command(name="ping", description="Check whether X BOT is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(view=xbot_ui.success("🏓 X BOT Online", "X BOT V2 · New War Tier is running."))


@bot.tree.command(name="server_settings", description="Admin: manage X BOT announcement settings", **STAFF_COMMAND_KWARGS)
async def server_settings(interaction: discord.Interaction):
    if not is_council_or_admin(interaction):
        await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators and Moderators can manage server settings."), ephemeral=True)
        return
    await interaction.response.send_message(embed=_server_settings_embed(), view=ServerSettingsView(interaction.user.id), ephemeral=True)


@bot.tree.command(name="say", description="Admin: make X BOT post an announcement", **STAFF_COMMAND_KWARGS)
@app_commands.describe(message="Text that X BOT should post", channel="Optional destination; defaults to this channel")
async def say(interaction: discord.Interaction, message: str, channel: Optional[discord.TextChannel] = None):
    if not is_council_or_admin(interaction):
        await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only Administrators and Moderators can post as X BOT."), ephemeral=True)
        return
    text = message.strip()
    if not text:
        await interaction.response.send_message(view=xbot_ui.danger("Message Required", "Write the message that X BOT should post."), ephemeral=True)
        return
    destination = channel or interaction.channel
    if not isinstance(destination, discord.TextChannel):
        await interaction.response.send_message(view=xbot_ui.danger("Channel Required", "Choose a normal text channel for this announcement."), ephemeral=True)
        return
    try:
        await destination.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        await interaction.response.send_message(view=xbot_ui.danger("Cannot Post", f"X BOT cannot send messages in {destination.mention}."), ephemeral=True)
        return
    economy.log(db, interaction.user.id, "staff_say", f"Posted in #{destination.name}: {text[:120]}")
    db.commit()
    await interaction.response.send_message(view=xbot_ui.success("📣 Announcement Posted", f"X BOT posted your message in {destination.mention}."), ephemeral=True)


@bot.tree.command(name="inrole", description="Show the members who have a Discord role (Council/Admin)", **STAFF_COMMAND_KWARGS)
@app_commands.describe(role="The Discord role to inspect")
async def inrole(interaction: discord.Interaction, role: discord.Role):
    if not is_council_or_admin(interaction):
        await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only X Council, Administration, and Discord Moderators can use this command."), ephemeral=True)
        return
    members = sorted(role.members, key=lambda member: member.display_name.casefold())
    shown = members[:50]
    lines = [f"• {member.mention} — {member.display_name}" for member in shown]
    if len(members) > len(shown):
        lines.append(f"\n…and **{len(members) - len(shown)}** more member(s). Use the Dashboard or run this in a smaller role.")
    body = f"**Role:** {role.mention}\n**Members:** {len(members)}\n\n" + ("\n".join(lines) if lines else "No server members currently have this role.")
    await interaction.response.send_message(view=xbot_ui.panel("👥 Role Members", body, colour=discord.Color.blurple()), ephemeral=True)


@bot.tree.command(name="role", description="Give or remove a Discord role from a member (Council/Admin)", **STAFF_COMMAND_KWARGS)
@app_commands.describe(member="Member to change", role="Role to add, or remove if they already have it")
async def role_toggle(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not is_council_or_admin(interaction):
        await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only X Council, Administration, and Discord Moderators can use this command."), ephemeral=True)
        return
    me = interaction.guild.me if interaction.guild else None
    if role.is_default() or role.managed:
        await interaction.response.send_message(view=xbot_ui.danger("Role Cannot Be Changed", "The @everyone role and integration-managed roles cannot be changed."), ephemeral=True)
        return
    if me is None or me.top_role <= role:
        await interaction.response.send_message(view=xbot_ui.danger("Role Hierarchy Required", "Move the **X BOT** role above this role in Server Settings → Roles, then try again."), ephemeral=True)
        return
    try:
        reason = f"X BOT role toggle by {interaction.user} ({interaction.user.id})"
        if role in member.roles:
            await member.remove_roles(role, reason=reason)
            action = "removed"
            heading = "➖ Role Removed"
        else:
            await member.add_roles(role, reason=reason)
            action = "given"
            heading = "➕ Role Given"
    except discord.Forbidden:
        await interaction.response.send_message(view=xbot_ui.danger("Discord Permission Error", "X BOT cannot manage that role. Check role hierarchy and its **Manage Roles** permission."), ephemeral=True)
        return
    economy.log(db, interaction.user.id, f"staff_role_{action}", f"{action.title()} {role.name} ({role.id}) {'from' if action == 'removed' else 'to'} {member.display_name} ({member.id})")
    db.commit()
    await interaction.response.send_message(view=xbot_ui.success(heading, f"{role.mention} was {action} {'from' if action == 'removed' else 'to'} {member.mention}."))


def _reward_code_item(code: str):
    return db.execute("SELECT * FROM items WHERE name=? COLLATE NOCASE", (code.strip(),)).fetchone()


async def reward_item_autocomplete(interaction: discord.Interaction, current: str):
    rows = db.execute("SELECT name,emoji FROM items WHERE enabled=1 AND name LIKE ? ORDER BY name LIMIT 25", (f"%{current.strip()}%",)).fetchall()
    return [app_commands.Choice(name=f"{row['emoji']} {row['name']}"[:100], value=row["name"]) for row in rows]


@bot.tree.command(name="code_create", description="Create an X BOT reward code (Council/Admin)", **STAFF_COMMAND_KWARGS)
@app_commands.describe(code="Unique code, for example SUMMER100", xc="XC reward", war_credits="War Credits reward", xcrystals="XCrystals reward", max_uses="0 means unlimited", item="Optional exact item name", item_quantity="Quantity of the optional item")
@app_commands.autocomplete(item=reward_item_autocomplete)
async def code_create(interaction: discord.Interaction, code: str, xc: app_commands.Range[int, 0, 100000000] = 0, war_credits: app_commands.Range[int, 0, 100000000] = 0, xcrystals: app_commands.Range[int, 0, 100000000] = 0, max_uses: app_commands.Range[int, 0, 1000000] = 0, item: str = "", item_quantity: app_commands.Range[int, 0, 1000000] = 0):
    if not is_council_or_admin(interaction):
        await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only X Council and Administrators can create reward codes."), ephemeral=True)
        return
    clean_code = code.strip().upper()
    if not clean_code or len(clean_code) > 40 or not all(character.isalnum() or character in "_-" for character in clean_code):
        await interaction.response.send_message(view=xbot_ui.danger("Invalid Code", "Use 1–40 letters, numbers, `_` or `-` only."), ephemeral=True); return
    item_row = _reward_code_item(item) if item.strip() else None
    if item.strip() and item_row is None:
        await interaction.response.send_message(view=xbot_ui.danger("Item Not Found", "Use the exact Item Library name, or create the code from Dashboard."), ephemeral=True); return
    if not any((xc, war_credits, xcrystals, item_quantity)):
        await interaction.response.send_message(view=xbot_ui.danger("No Reward", "Add at least one currency reward or an item quantity."), ephemeral=True); return
    try:
        db.execute("""INSERT INTO reward_codes(code,reward_xc,reward_war_credits,reward_xcrystals,item_id,item_quantity,max_uses,created_by,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (clean_code, xc, war_credits, xcrystals, item_row["id"] if item_row else None, item_quantity if item_row else 0, max_uses, interaction.user.id, int(time.time())))
        db.commit()
    except sqlite3.IntegrityError:
        await interaction.response.send_message(view=xbot_ui.danger("Code Already Exists", f"`{clean_code}` already exists. Disable it or use a different code."), ephemeral=True); return
    economy.log(db, interaction.user.id, "reward_code_created", f"Created {clean_code}")
    db.commit()
    reward = f"🪙 {xc:,} XC · ⚔️ {war_credits:,} War Credits · 💎 {xcrystals:,} XCrystals"
    if item_row and item_quantity: reward += f" · {item_row['emoji']} {item_row['name']} ×{item_quantity}"
    await interaction.response.send_message(view=xbot_ui.success("🎟 Reward Code Created", f"`{clean_code}`\n{reward}\nUses: **{'Unlimited' if max_uses == 0 else max_uses}**"), ephemeral=True)


@bot.tree.command(name="code_disable", description="Enable or disable an X BOT reward code (Council/Admin)", **STAFF_COMMAND_KWARGS)
async def code_disable(interaction: discord.Interaction, code: str, enabled: bool = False):
    if not is_council_or_admin(interaction):
        await interaction.response.send_message(view=xbot_ui.danger("🔒 Staff Command", "Only X Council and Administrators can manage reward codes."), ephemeral=True); return
    row = db.execute("SELECT * FROM reward_codes WHERE code=? COLLATE NOCASE", (code.strip(),)).fetchone()
    if row is None:
        await interaction.response.send_message(view=xbot_ui.danger("Code Not Found", "No reward code has that name."), ephemeral=True); return
    db.execute("UPDATE reward_codes SET enabled=? WHERE id=?", (int(enabled), row["id"]))
    economy.log(db, interaction.user.id, "reward_code_status", f"{'Enabled' if enabled else 'Disabled'} {row['code']}")
    db.commit()
    await interaction.response.send_message(view=xbot_ui.success("🎟 Reward Code Updated", f"`{row['code']}` is now **{'enabled' if enabled else 'disabled'}**."), ephemeral=True)


@bot.tree.command(name="code_redeem", description="Redeem an X BOT reward code", **STAFF_COMMAND_KWARGS)
async def code_redeem(interaction: discord.Interaction, code: str):
    create_player(interaction.user)
    now = int(time.time()); clean_code = code.strip().upper()
    try:
        db.execute("BEGIN IMMEDIATE")
        reward = db.execute("SELECT * FROM reward_codes WHERE code=? COLLATE NOCASE", (clean_code,)).fetchone()
        if reward is None or not reward["enabled"]:
            db.rollback(); await interaction.response.send_message(view=xbot_ui.danger("Code Unavailable", "That reward code does not exist or is disabled."), ephemeral=True); return
        if reward["expires_at"] and reward["expires_at"] < now:
            db.rollback(); await interaction.response.send_message(view=xbot_ui.danger("Code Expired", "That reward code has expired."), ephemeral=True); return
        if reward["max_uses"] and reward["uses"] >= reward["max_uses"]:
            db.rollback(); await interaction.response.send_message(view=xbot_ui.danger("Code Fully Used", "That reward code has reached its usage limit."), ephemeral=True); return
        used = db.execute("INSERT OR IGNORE INTO reward_code_redemptions(code_id,user_id,redeemed_at) VALUES(?,?,?)", (reward["id"], interaction.user.id, now))
        if used.rowcount != 1:
            db.rollback(); await interaction.response.send_message(view=xbot_ui.warning("Already Redeemed", "You have already used this reward code."), ephemeral=True); return
        db.execute("UPDATE reward_codes SET uses=uses+1 WHERE id=?", (reward["id"],))
        db.execute("UPDATE players SET xc=xc+?,money=money+?,xcrystals=xcrystals+? WHERE user_id=?", (reward["reward_xc"], reward["reward_war_credits"], reward["reward_xcrystals"], interaction.user.id))
        item = None
        if reward["item_id"] and reward["item_quantity"] > 0:
            item = db.execute("SELECT name,emoji FROM items WHERE id=?", (reward["item_id"],)).fetchone()
            if item:
                db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
                    ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""", (interaction.user.id, reward["item_id"], reward["item_quantity"]))
        db.commit()
    except sqlite3.Error:
        db.rollback(); raise
    economy.log(db, interaction.user.id, "reward_code_redeemed", f"Redeemed {reward['code']}")
    db.commit()
    result = f"🪙 **{reward['reward_xc']:,} XC**\n⚔️ **{reward['reward_war_credits']:,} War Credits**\n💎 **{reward['reward_xcrystals']:,} XCrystals**"
    if item:
        result += f"\n{item['emoji']} **{item['name']} ×{reward['item_quantity']}**"
    await interaction.response.send_message(view=xbot_ui.success("🎁 Reward Redeemed", f"Code: `{reward['code']}`\n\n{result}"), ephemeral=True)


async def show_service_branch(interaction: discord.Interaction, player: Optional[discord.Member], branches: set[str], title: str, icon: str, colour: discord.Color):
    if player is None:
        selected_player = interaction.user
        data = create_player(selected_player)
    else:
        selected_player = player
        data = get_player(selected_player.id)

        if data is None:
            await interaction.response.send_message(
                f"❌ {selected_player.display_name} has not created a Nation yet.",
                ephemeral=True
            )
            return

    alliance = get_alliance_for_user(selected_player.id)
    alliance_text = (
        f"[{alliance['tag']}] {alliance['name']}"
        if alliance else "None"
    )

    capital_status = (
        "Active" if data["capital_health"] > 0 else "Conquered"
    )

    unit_rows = [unit for unit in war_system.units_for_player(db, selected_player.id, enabled_only=True)
                 if unit["branch"] in branches and int(unit["quantity"]) > 0]
    branch_power = sum(unit["quantity"] * unit["power"] for unit in unit_rows)
    branch = next(iter(branches)) if len(branches) == 1 else "land"
    service_title = war_tier.service_names(db, selected_player.id).get(branch, title)
    view = discord.ui.LayoutView(timeout=180)
    container = discord.ui.Container(accent_color=colour)
    container.add_item(discord.ui.TextDisplay(
        f"## {icon} {data['nation_name']} — {service_title}\n"
        f"🤝 **Alliance:** {alliance_text}\n"
        f"🗺️ **Land:** {data['land']:,}\n"
        f"⚔️ **War Credits:** {data['money']:,}\n"
        f"💥 **{service_title} Power:** {branch_power:,}\n"
        f"🌐 **Total Armed Forces Power:** {power(data):,}"
    ))
    container.add_item(discord.ui.Separator())
    if unit_rows:
        for unit in unit_rows:
            unit_power = int(unit["quantity"]) * int(unit["power"])
            container.add_item(discord.ui.TextDisplay(
                f"### {unit['emoji']} {unit['name']}\n"
                f"📦 **Units:** {int(unit['quantity']):,}  ·  "
                f"💥 **Power:** {unit_power:,}"
            ))
    else:
        container.add_item(discord.ui.TextDisplay(
            f"### No {service_title} units yet\nUse `/army_recruit` to recruit your first unit."
        ))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(
        f"-# Capital: {data['capital_name']} · {capital_status} · Recruit with /army_recruit"
    ))
    view.add_item(container)
    await interaction.response.send_message(view=view)


# /armed_forces is the unified player-facing view for all three services.
# The older separate /army, /navy and /airforce commands are retired.


@bot.tree.command(name="nation", description="Set your Nation name")
@app_commands.describe(name="Your Nation name")
async def nation(interaction: discord.Interaction, name: str):
    name = name.strip()

    if len(name) < 3 or len(name) > 30:
        await interaction.response.send_message(
            "❌ Nation name must be between 3 and 30 characters.",
            ephemeral=True
        )
        return

    create_player(interaction.user)

    db.execute(
        "UPDATE players SET nation_name = ? WHERE user_id = ?",
        (name, interaction.user.id)
    )
    db.commit()

    await interaction.response.send_message(view=xbot_ui.success("🏳️ Nation Updated", f"Your Nation is now called **{name}**."))


@bot.tree.command(name="capital", description="View or rename your Capital")
@app_commands.describe(name="Optional: enter a new Capital name")
async def capital(interaction: discord.Interaction, name: Optional[str] = None):
    data = create_player(interaction.user)

    if name is not None:
        name = name.strip()

        if len(name) < 3 or len(name) > 30:
            await interaction.response.send_message(
                "❌ Capital name must be between 3 and 30 characters.",
                ephemeral=True
            )
            return

        db.execute(
            "UPDATE players SET capital_name = ? WHERE user_id = ?",
            (name, interaction.user.id)
        )
        db.commit()
        data = get_player(interaction.user.id)

    status = "🏛️ Active" if data["capital_health"] > 0 else "💀 Conquered"

    await interaction.response.send_message(view=xbot_ui.panel("🏛️ Capital Command", f"## {data['capital_name']}\n❤️ **Capital Health:** {data['capital_health']} / 100\n**Status:** {status}", colour=discord.Color.gold()))


# ---------- X BOT V2 Player Economy & War ----------
# The old fixed mine commands are intentionally retired. A later beta will add
# configurable Wabbit-style pickaxes and weighted material drops.
economy.register_commands(bot, db, create_player)
casino.register_commands(bot, db, create_player)
economy_extra.register_commands(bot, db, create_player, economy.find_item)
war_tier.register_commands(bot, db, create_player, get_active_war, get_alliance_for_user)
tier4.register_commands(bot, db, create_player)
tier5.register_commands(bot, db, create_player)
tier6.register_commands(bot, db, create_player)
advanced_systems.register_commands(bot, db, create_player)
leveling.register_commands(bot, db)
applications.register_commands(bot, db)
staff_panel.register_commands(bot, db, is_council_or_admin, STAFF_COMMAND_KWARGS)
deadzone.register_commands(bot, db, is_council_or_admin, STAFF_COMMAND_KWARGS)

# X Community has retired the old company/job economy.  Keep the historical
# database tables for old logs, but do not publish these commands any more.
for _retired_command in (
    "job_list", "job_apply",
    "keno", "tower", "highlow", "balloonpop",
    "armed_forces", "army_recruit",
    "prepare", "rally", "supply_buy", "navy", "airforce",
    # Replaced by the single interactive /server_settings panel.
    "setannouncement", "announcementshow", "setannouncementchat",
):
    bot.tree.remove_command(_retired_command)

# Mark all registered commands as wrapped for consistency and testing
for _cmd in bot.tree.get_commands():
    if hasattr(_cmd, "callback") and not hasattr(_cmd.callback, "system_ui_wrapped"):
        setattr(_cmd.callback, "system_ui_wrapped", True)


async def open_quick_player_panel(interaction: discord.Interaction, builder):
    """Open a main player page safely on slower phone-hosted instances."""
    await interaction.response.defer()
    create_player(interaction.user)
    await interaction.edit_original_response(view=builder(interaction.user.id))


@bot.tree.command(name="army", description="Open your Land Army, Air Force and Navy")
async def army_shortcut(interaction: discord.Interaction):
    builder = getattr(bot, "xbot_armed_forces_builder", None)
    if builder is None:
        await interaction.response.send_message("The Armed Forces panel is loading. Please try again.", ephemeral=True)
        return
    await open_quick_player_panel(interaction, builder)


@bot.tree.command(name="recruit", description="Open the Army Recruit Centre")
async def recruit_shortcut(interaction: discord.Interaction):
    builder = getattr(bot, "xbot_army_recruit_builder", None)
    if builder is None:
        await interaction.response.send_message("The Recruit panel is loading. Please try again.", ephemeral=True)
        return
    await open_quick_player_panel(interaction, builder)


@bot.tree.command(name="backpack", description="Open your item Backpack")
async def backpack_shortcut(interaction: discord.Interaction):
    builder = getattr(bot, "xbot_player_panel_builders", {}).get("inventory")
    if builder is None:
        await interaction.response.send_message("The Backpack panel is loading. Please try again.", ephemeral=True)
        return
    await open_quick_player_panel(interaction, builder)


@bot.tree.command(name="diplomacy", description="Open Nation relations, Alliances, trade and diplomatic inbox")
async def diplomacy_shortcut(interaction: discord.Interaction):
    builder = getattr(bot, "xbot_player_panel_builders", {}).get("diplomacy")
    if builder is None:
        await interaction.response.send_message("The Diplomacy panel is loading. Please try again.", ephemeral=True)
        return
    await open_quick_player_panel(interaction, builder)


# ---------- Tier 4 Alliance ----------

async def alliance_name_autocomplete(interaction: discord.Interaction, current: str):
    """Discord picker for Alliance names; players no longer need exact spelling."""
    rows = db.execute("""SELECT name,tag FROM alliances
        WHERE name LIKE ? COLLATE NOCASE OR tag LIKE ? COLLATE NOCASE
        ORDER BY name LIMIT 25""", (f"%{current}%", f"%{current}%")).fetchall()
    return [app_commands.Choice(name=f"[{row['tag']}] {row['name']}"[:100], value=row["name"]) for row in rows]

@bot.tree.command(name="alliance_create", description="Create an Alliance")
@app_commands.describe(name="Alliance name", tag="Short tag, for example XW")
async def alliance_create(
    interaction: discord.Interaction,
    name: str,
    tag: str
):
    create_player(interaction.user)
    name = name.strip()
    tag = tag.strip().upper()

    if len(name) < 3 or len(name) > 30:
        await interaction.response.send_message(
            "❌ Alliance name must be between 3 and 30 characters.",
            ephemeral=True
        )
        return

    if not tag.isalnum() or len(tag) < 2 or len(tag) > 5:
        await interaction.response.send_message(
            "❌ Alliance tag must be 2–5 letters or numbers.",
            ephemeral=True
        )
        return

    if get_alliance_for_user(interaction.user.id):
        await interaction.response.send_message(
            "❌ Leave your current Alliance first.",
            ephemeral=True
        )
        return

    try:
        cursor = db.execute(
            """
            INSERT INTO alliances (name, tag, leader_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (name, tag, interaction.user.id, int(time.time()))
        )

        db.execute(
            """
            INSERT INTO alliance_members (user_id, alliance_id)
            VALUES (?, ?)
            """,
            (interaction.user.id, cursor.lastrowid)
        )
        db.commit()

    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            "❌ That Alliance name or tag is already taken.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(view=xbot_ui.success("🤝 Alliance Created", f"**[{tag}] {name}** is ready for members."))


@bot.tree.command(name="alliance_join", description="Join an Alliance")
@app_commands.describe(name="The exact Alliance name")
@app_commands.autocomplete(name=alliance_name_autocomplete)
async def alliance_join(interaction: discord.Interaction, name: str):
    create_player(interaction.user)

    if get_alliance_for_user(interaction.user.id):
        await interaction.response.send_message(
            "❌ Leave your current Alliance first.",
            ephemeral=True
        )
        return

    alliance = get_alliance_by_name(name.strip())

    if alliance is None:
        await interaction.response.send_message(
            "❌ Alliance not found.",
            ephemeral=True
        )
        return

    db.execute(
        "INSERT INTO alliance_members (user_id, alliance_id) VALUES (?, ?)",
        (interaction.user.id, alliance["id"])
    )
    db.commit()

    await interaction.response.send_message(view=xbot_ui.success("🤝 Alliance Joined", f"You joined **[{alliance['tag']}] {alliance['name']}**!"))


@bot.tree.command(name="alliance_leave", description="Leave your current Alliance")
async def alliance_leave(interaction: discord.Interaction):
    alliance = get_alliance_for_user(interaction.user.id)

    if alliance is None:
        await interaction.response.send_message(
            "❌ You are not in an Alliance.",
            ephemeral=True
        )
        return

    members = db.execute(
        "SELECT user_id FROM alliance_members WHERE alliance_id = ?",
        (alliance["id"],)
    ).fetchall()

    message = ""

    if alliance["leader_id"] == interaction.user.id:
        other_members = [
            member["user_id"]
            for member in members
            if member["user_id"] != interaction.user.id
        ]

        if other_members:
            new_leader = other_members[0]
            db.execute(
                "UPDATE alliances SET leader_id = ? WHERE id = ?",
                (new_leader, alliance["id"])
            )
            message = f" New leader: <@{new_leader}>."
        else:
            db.execute(
                "DELETE FROM alliances WHERE id = ?",
                (alliance["id"],)
            )
            message = " The Alliance was disbanded."

    db.execute(
        "DELETE FROM alliance_members WHERE user_id = ?",
        (interaction.user.id,)
    )
    db.commit()

    await interaction.response.send_message(view=xbot_ui.warning("👋 Alliance Left", f"You left **[{alliance['tag']}] {alliance['name']}**.{message}"))


@bot.tree.command(name="alliance_info", description="View Alliance information")
@app_commands.describe(name="Leave empty to view your own Alliance")
@app_commands.autocomplete(name=alliance_name_autocomplete)
async def alliance_info(
    interaction: discord.Interaction,
    name: Optional[str] = None
):
    alliance = (
        get_alliance_for_user(interaction.user.id)
        if name is None
        else get_alliance_by_name(name.strip())
    )

    if alliance is None:
        await interaction.response.send_message(
            "❌ Alliance not found.",
            ephemeral=True
        )
        return

    members = db.execute(
        "SELECT user_id FROM alliance_members WHERE alliance_id = ?",
        (alliance["id"],)
    ).fetchall()

    member_mentions = ", ".join(
        f"<@{member['user_id']}>"
        for member in members
    )

    await interaction.response.send_message(view=xbot_ui.panel(f"🤝 [{alliance['tag']}] {alliance['name']}", f"👑 **Leader:** <@{alliance['leader_id']}>\n👥 **Members ({len(members)}):** {member_mentions}", colour=discord.Color.teal()))

# ---------- Alliance War Events ----------

@bot.tree.command(name="war_start", description="Admin: start an Alliance War")
@app_commands.describe(
    attacker_alliance="Attacking Alliance name",
    defender_alliance="Defending Alliance name"
)
@app_commands.autocomplete(attacker_alliance=alliance_name_autocomplete, defender_alliance=alliance_name_autocomplete)
async def war_start(
    interaction: discord.Interaction,
    attacker_alliance: str,
    defender_alliance: str
):
    if not is_server_admin(interaction):
        await interaction.response.send_message(
            "❌ Only server administrators can start a war.",
            ephemeral=True
        )
        return

    if get_active_war():
        await interaction.response.send_message(
            "❌ A war is already active. End it before starting another war.",
            ephemeral=True
        )
        return

    attacker = get_alliance_by_name(attacker_alliance.strip())
    defender = get_alliance_by_name(defender_alliance.strip())

    if attacker is None or defender is None:
        await interaction.response.send_message(
            "❌ One or both Alliance names were not found.",
            ephemeral=True
        )
        return

    if attacker["id"] == defender["id"]:
        await interaction.response.send_message(
            "❌ An Alliance cannot declare war on itself.",
            ephemeral=True
        )
        return

    db.execute(
        """
        INSERT INTO wars (
            attacker_alliance_id,
            defender_alliance_id,
            started_at
        )
        VALUES (?, ?, ?)
        """,
        (attacker["id"], defender["id"], int(time.time()))
    )
    db.commit()

    await interaction.response.send_message(view=xbot_ui.danger("⚔️ Alliance War Declared", f"## [{attacker['tag']}] {attacker['name']}\n### VS\n## [{defender['tag']}] {defender['name']}"))


@bot.tree.command(name="war_status", description="View the current Alliance War")
async def war_status(interaction: discord.Interaction):
    war = get_active_war()

    if war is None:
        await interaction.response.send_message(view=xbot_ui.success("🕊️ World at Peace", "There is no active Alliance War."))
        return

    attacker = db.execute(
        "SELECT * FROM alliances WHERE id = ?",
        (war["attacker_alliance_id"],)
    ).fetchone()

    defender = db.execute(
        "SELECT * FROM alliances WHERE id = ?",
        (war["defender_alliance_id"],)
    ).fetchone()

    await interaction.response.send_message(view=xbot_ui.panel("⚔️ Active Alliance War", f"## [{attacker['tag']}] {attacker['name']}\n💥 Power: **{alliance_power(attacker['id']):,}**\n\n### VS\n\n## [{defender['tag']}] {defender['name']}\n💥 Power: **{alliance_power(defender['id']):,}**", colour=discord.Color.dark_red()))


@bot.tree.command(name="war_end", description="Admin: end the current Alliance War")
@app_commands.describe(winner_alliance="Optional: winning Alliance name")
async def war_end(
    interaction: discord.Interaction,
    winner_alliance: Optional[str] = None
):
    if not is_server_admin(interaction):
        await interaction.response.send_message(
            "❌ Only server administrators can end a war.",
            ephemeral=True
        )
        return

    war = get_active_war()

    if war is None:
        await interaction.response.send_message(
            "❌ There is no active Alliance War.",
            ephemeral=True
        )
        return

    winner_id = None
    winner_text = "No winner was selected."

    if winner_alliance:
        winner = get_alliance_by_name(winner_alliance.strip())

        if winner is None:
            await interaction.response.send_message(
                "❌ Alliance not found.",
                ephemeral=True
            )
            return

        allowed_ids = {
            war["attacker_alliance_id"],
            war["defender_alliance_id"]
        }

        if winner["id"] not in allowed_ids:
            await interaction.response.send_message(
                "❌ The winner must be one of the two Alliances in this war.",
                ephemeral=True
            )
            return

        winner_id = winner["id"]
        winner_text = f"🏆 Winner: **[{winner['tag']}] {winner['name']}**"

    db.execute(
        """
        UPDATE wars
        SET active = 0, winner_alliance_id = ?
        WHERE id = ?
        """,
        (winner_id, war["id"])
    )
    db.commit()

    await interaction.response.send_message(view=xbot_ui.warning("🕊️ Alliance War Ended", winner_text))

# ---------- War ----------

@bot.tree.command(name="attack", description="Attack another Nation")
@app_commands.describe(target="Choose a player to attack")
async def attack(interaction: discord.Interaction, target: discord.Member):
    if target.bot:
        await interaction.response.send_message(
            "❌ You cannot attack a bot.",
            ephemeral=True
        )
        return

    if target.id == interaction.user.id:
        await interaction.response.send_message(
            "❌ You cannot attack yourself.",
            ephemeral=True
        )
        return

    attacker = create_player(interaction.user)
    defender = get_player(target.id)

    if defender is None:
        await interaction.response.send_message(
            f"❌ {target.display_name} has not created a Nation yet.",
            ephemeral=True
        )
        return

    attacker_alliance = get_alliance_for_user(interaction.user.id)
    defender_alliance = get_alliance_for_user(target.id)

    if (
        attacker_alliance
        and defender_alliance
        and attacker_alliance["id"] == defender_alliance["id"]
    ):
        await interaction.response.send_message(
            "❌ You cannot attack a member of your own Alliance.",
            ephemeral=True
        )
        return

    if attacker["capital_health"] <= 0:
        await interaction.response.send_message(
            "❌ Your Capital has been conquered. You cannot declare war.",
            ephemeral=True
        )
        return

    if defender["capital_health"] <= 0:
        await interaction.response.send_message(
            "❌ This Nation's Capital has already been conquered.",
            ephemeral=True
        )
        return

    if war_system.branch_power(db, interaction.user.id, "land") <= 0:
        await interaction.response.send_message(
            "❌ You need Land Army units before you can launch an attack.",
            ephemeral=True
        )
        return

    active_war = get_active_war()

    if active_war:
        war_alliance_ids = {
            active_war["attacker_alliance_id"],
            active_war["defender_alliance_id"]
        }

        if (
            attacker_alliance is None
            or defender_alliance is None
            or attacker_alliance["id"] not in war_alliance_ids
            or defender_alliance["id"] not in war_alliance_ids
            or attacker_alliance["id"] == defender_alliance["id"]
        ):
            await interaction.response.send_message(
                "❌ During an Alliance War, only the two warring Alliances "
                "can attack each other.",
                ephemeral=True
            )
            return
    else:
        # Season 1 uses formal, border-based Nation wars.  This prevents a
        # player from attacking a random Nation on the other side of the map;
        # `/declare_war` first verifies that the two Nations share a Land border.
        nation_war = war_tier.active_nation_war(db, interaction.user.id, target.id)
        db.commit()  # persist automatic expiry of an old declaration
        if nation_war is None:
            await interaction.response.send_message(view=xbot_ui.warning(
                "⚔️ Declaration Required",
                "You must first use `/declare_war` against a bordering Nation. "
                "After the declaration, both sides can use `/attack` until peace is made or the war expires."
            ), ephemeral=True)
            return

    now = int(time.time())
    season = war_tier.active_season(db)
    if season:
        protection = war_tier.setting(db, "war_new_nation_protection_seconds")
        created_at = int(defender["nation_created_at"] or 0)
        protected_until = created_at + protection
        if created_at and now < protected_until:
            await interaction.response.send_message(
                f"🛡️ **{target.display_name}** has New Nation Protection until <t:{protected_until}:R>.",
                ephemeral=True,
            )
            return
        pair_last = db.execute("""SELECT created_at FROM battle_history
            WHERE attacker_id=? AND defender_id=? ORDER BY created_at DESC LIMIT 1""",
            (interaction.user.id, target.id)).fetchone()
        pair_cooldown = war_tier.setting(db, "war_pair_attack_cooldown")
        pair_left = pair_cooldown - (now - int(pair_last["created_at"])) if pair_last else 0
        if pair_left > 0:
            available_at = now + pair_left
            await interaction.response.send_message(
                f"⏳ You already attacked this Nation recently. You may attack it again <t:{available_at}:R>.",
                ephemeral=True,
            )
            return
    seconds_left = war_tier.setting(db, "attack_cooldown") - (now - attacker["last_attack"])

    if seconds_left > 0:
        await interaction.response.send_message(
            f"⏳ Please wait **{seconds_left} seconds** before attacking again.",
            ephemeral=True
        )
        return

    # Three-front resolution can include many database reads. Acknowledge the
    # Discord interaction first so slower computers do not hit error 10062.
    await interaction.response.defer(thinking=True)

    variance = max(0, min(50, war_tier.setting(db, "war_battle_variance_percent")))

    def resolve_front(service):
        attacker_base = war_tier.effective_service_power(db, interaction.user.id, service, attacking=True)
        defender_base = war_tier.effective_service_power(db, target.id, service, attacking=False)
        if attacker_base <= 0 and defender_base <= 0:
            return {"winner": None, "attacker": 0, "defender": 0, "attacker_roll": 0, "defender_roll": 0}
        attacker_roll = random.randint(100 - variance, 100 + variance)
        defender_roll = random.randint(100 - variance, 100 + variance)
        attacker_front = attacker_base * attacker_roll // 100
        defender_front = defender_base * defender_roll // 100
        return {
            "winner": "attacker" if attacker_front > defender_front else "defender",
            "attacker": attacker_front, "defender": defender_front,
            "attacker_roll": attacker_roll, "defender_roll": defender_roll,
        }

    air_front = resolve_front("air")
    navy_front = resolve_front("navy")
    land_front = resolve_front("land")
    air_bonus = war_tier.setting(db, "war_air_superiority_bonus")
    navy_penalty = war_tier.setting(db, "war_navy_blockade_penalty")
    attacker_land_modifier = 100
    defender_land_modifier = 100
    air_effect = "No air forces engaged — no air superiority."
    if air_front["winner"] == "attacker":
        attacker_land_modifier += air_bonus
        air_effect = f"{interaction.user.display_name} gained air superiority: +{air_bonus}% Land power."
    elif air_front["winner"] == "defender" and (air_front["attacker"] or air_front["defender"]):
        defender_land_modifier += air_bonus
        air_effect = f"{target.display_name} gained air superiority: +{air_bonus}% Land defence."
    navy_effect = "No naval engagement — no blockade effect."
    navy_supply_target = None
    if navy_front["winner"] == "attacker":
        defender_land_modifier = max(0, defender_land_modifier - navy_penalty)
        navy_supply_target = target.id
        navy_effect = f"{interaction.user.display_name} won naval control: enemy Land power -{navy_penalty}% and Supply will be damaged."
    elif navy_front["winner"] == "defender" and (navy_front["attacker"] or navy_front["defender"]):
        attacker_land_modifier = max(0, attacker_land_modifier - navy_penalty)
        navy_supply_target = interaction.user.id
        navy_effect = f"{target.display_name} won naval control: enemy Land power -{navy_penalty}% and Supply will be damaged."

    attacker_roll = land_front["attacker_roll"]
    defender_roll = land_front["defender_roll"]
    attacker_power = land_front["attacker"] * attacker_land_modifier // 100
    defender_power = land_front["defender"] * defender_land_modifier // 100
    front_report = (
        "## 🌐 Three-Front Battle\n"
        f"✈️ **Air Front:** {air_front['attacker']:,} vs {air_front['defender']:,}\n> {air_effect}\n"
        f"⚓ **Naval Front:** {navy_front['attacker']:,} vs {navy_front['defender']:,}\n> {navy_effect}\n"
        f"🪖 **Land Front:** {land_front['attacker']:,} vs {land_front['defender']:,}\n"
        f"> Final Land Power: **{attacker_power:,} vs {defender_power:,}**"
    )
    winner_loss_rate = war_tier.setting(db, "war_winner_loss_percent") / 100
    loser_loss_rate = war_tier.setting(db, "war_loser_loss_percent") / 100
    land_captured = capital_damage_done = credits_captured = 0
    capital_captured = False

    if attacker_power > defender_power:
        winner_id, battle_outcome = interaction.user.id, "Attacker Land victory"
        winner_losses = calculate_losses(attacker, winner_loss_rate)
        loser_losses = calculate_losses(defender, loser_loss_rate)
        attacker_losses, defender_losses = winner_losses, loser_losses

        save_losses(interaction.user.id, winner_losses)
        save_losses(target.id, loser_losses)

        if defender["land"] > 0:
            # Keep the real strategic map truthful: a captured Land is a
            # specific province/state, not only a number in the player table.
            war_tier.sync_map_ownership(db, war_tier.world_city_tiles(war_tier._province_features()))
            captured_territory = war_tier.transfer_one_land(db, interaction.user.id, target.id)
            db.execute(
                "UPDATE players SET land = land + 1 WHERE user_id = ?",
                (interaction.user.id,)
            )
            db.execute(
                "UPDATE players SET land = land - 1 WHERE user_id = ?",
                (target.id,)
            )
            land_captured = 1
            reward = (f"🗺️ You captured **{captured_territory['territory_name']}**!"
                      if captured_territory else "🗺️ You captured **1 Land**!")

        else:
            new_health = max(
                defender["capital_health"] - war_tier.setting(db, "capital_damage"),
                0
            )

            db.execute(
                "UPDATE players SET capital_health = ? WHERE user_id = ?",
                (new_health, target.id)
            )
            capital_damage_done = defender["capital_health"] - new_health

            if new_health == 0:
                capital_captured = True
                credits = int(defender["money"] * war_tier.setting(db, "capital_reward_percent") / 100)

                db.execute(
                    "UPDATE players SET money = money + ? WHERE user_id = ?",
                    (credits, interaction.user.id)
                )
                db.execute(
                    "UPDATE players SET money = money - ? WHERE user_id = ?",
                    (credits, target.id)
                )
                credits_captured = credits

                reward = (
                    f"🏛️ **{defender['capital_name']} has fallen!**\n"
                    f"💰 You captured **{credits:,} War Credits**."
                )
            else:
                reward = (
                    f"🏛️ Capital Siege!\n"
                    f"Capital Health: **{new_health} / 100**"
                )

        result = (
            f"🏆 **{interaction.user.display_name} wins the war!**\n"
            f"Attacker Power: {attacker_power}\n"
            f"Defender Power: {defender_power}\n"
            f"{reward}\n\n"
            f"Your losses: {losses_text(winner_losses)}\n"
            f"{target.display_name}'s losses: {losses_text(loser_losses)}"
        )

    else:
        winner_id, battle_outcome = target.id, "Defender Land victory"
        winner_losses = calculate_losses(defender, winner_loss_rate)
        loser_losses = calculate_losses(attacker, loser_loss_rate)
        attacker_losses, defender_losses = loser_losses, winner_losses

        save_losses(target.id, winner_losses)
        save_losses(interaction.user.id, loser_losses)

        result = (
            f"🛡️ **{target.display_name} defended successfully!**\n"
            f"Attacker Power: {attacker_power}\n"
            f"Defender Power: {defender_power}\n"
            f"Ties are won by the defender.\n\n"
            f"{target.display_name}'s losses: {losses_text(winner_losses)}\n"
            f"Your losses: {losses_text(loser_losses)}"
        )

    if navy_supply_target is not None:
        navy_supply_damage = war_tier.setting(db, "war_navy_supply_damage")
        db.execute("UPDATE player_war_settings SET supply=MAX(0,supply-?) WHERE user_id=?", (navy_supply_damage, navy_supply_target))
        result += f"\n⚓ Naval blockade damaged the losing side's Supply by **{navy_supply_damage}**."

    result = front_report + "\n\n---\n\n" + result
    for participant in (interaction.user.id, target.id):
        db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (participant,))
        war_tier.consume_battle_resources(db, participant)
    loser_id = target.id if winner_id == interaction.user.id else interaction.user.id
    morale_gain = war_tier.setting(db, "war_winner_morale_gain")
    morale_loss = war_tier.setting(db, "war_loser_morale_loss")
    db.execute("UPDATE player_war_settings SET morale=MIN(100,morale+?) WHERE user_id=?", (morale_gain, winner_id))
    db.execute("UPDATE player_war_settings SET morale=MAX(0,morale-?) WHERE user_id=?", (morale_loss, loser_id))
    result += f"\n\n🔥 Winner morale **+{morale_gain}** · Loser morale **-{morale_loss}**"
    result += f"\n🎲 Battle variance: attacker **{attacker_roll}%** · defender **{defender_roll}%**"
    front_label = lambda front: "Attacker" if front["winner"] == "attacker" else "Defender" if front["winner"] == "defender" else "No battle"
    battle_outcome += f" · Air: {front_label(air_front)} · Navy: {front_label(navy_front)}"

    db.execute(
        "UPDATE players SET last_attack = ? WHERE user_id = ?",
        (now, interaction.user.id)
    )
    war_tier.log_battle(
        db, interaction.user.id, target.id, attacker_power, defender_power, winner_id, battle_outcome,
        war_id=active_war["id"] if active_war else None,
        attacker_losses=attacker_losses, defender_losses=defender_losses,
        land_captured=land_captured, capital_damage=capital_damage_done,
        credits_captured=credits_captured,
    )
    war_tier.record_season_battle(
        db, interaction.user.id, target.id, winner_id,
        land_captured=land_captured,
        capital_damage=capital_damage_done,
        credits_captured=credits_captured, capital_captured=capital_captured,
    )
    db.commit()

    await interaction.followup.send(view=xbot_ui.panel("⚔️ Battle Report", result, colour=discord.Color.dark_red()))


# Tier 7 replaces the legacy /warfront and /attack registrations above after every
# dependency and panel builder exists.  It keeps the same two command names,
# so Discord's command catalogue does not grow.
tier7.register_commands(bot, db, create_player, get_active_war, get_alliance_for_user)
tier8.register_commands(bot, db, create_player)
casual_games.register(bot, db, create_player)
system_ui.register(bot, db, create_player)


# Seed server-only Tier 1.6 commands before applying their Council/Admin
# defaults.  Guild commands do not appear in the global get_commands() list.
db.executemany("INSERT OR IGNORE INTO command_permissions(command_name) VALUES(?)",
    [(name,) for name in ("inrole", "role", "code_create", "code_disable", "code_redeem")])
db.commit()
advanced_systems.seed_command_permissions(db, bot.tree.get_commands())
if _staff_guild_id:
    # War/map commands are registered directly to X Community because the
    # global Discord command catalogue is full. Include them in Dashboard
    # Command Access as well, otherwise members can see a command that the
    # Dashboard cannot configure or diagnose.
    advanced_systems.seed_command_permissions(
        db,
        bot.tree.get_commands(guild=discord.Object(id=_staff_guild_id)),
    )


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN was not found. Check your .env file.")
    bot.run(TOKEN)
