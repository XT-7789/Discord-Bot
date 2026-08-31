import os
import time
import sqlite3
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()

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

db = sqlite3.connect("xwar.db")
db.row_factory = sqlite3.Row

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

# Add missing columns safely for old databases.
columns = {
    column["name"]
    for column in db.execute("PRAGMA table_info(players)").fetchall()
}

upgrades = {
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
    "last_mine": "INTEGER NOT NULL DEFAULT 0"
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
        db.commit()
        player = get_player(user.id)

    return player


def power(player):
    return (
        player["land_army"]
        + player["air_army"] * 3
        + player["navy"] * 5
    )


def calculate_losses(player, percentage):
    losses = {}

    for column in ["land_army", "air_army", "navy"]:
        amount = player[column]
        losses[column] = max(1, int(amount * percentage)) if amount > 0 else 0

    return losses


def save_losses(user_id, losses):
    db.execute(
        """
        UPDATE players
        SET land_army = land_army - ?,
            air_army = air_army - ?,
            navy = navy - ?
        WHERE user_id = ?
        """,
        (
            losses["land_army"],
            losses["air_army"],
            losses["navy"],
            user_id
        )
    )


def losses_text(losses):
    return (
        f"Land -{losses['land_army']} | "
        f"Air -{losses['air_army']} | "
        f"Navy -{losses['navy']}"
    )


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
    result = db.execute(
        """
        SELECT COALESCE(
            SUM(
                players.land_army
                + players.air_army * 3
                + players.navy * 5
            ),
            0
        ) AS total_power
        FROM players
        INNER JOIN alliance_members
            ON players.user_id = alliance_members.user_id
        WHERE alliance_members.alliance_id = ?
        """,
        (alliance_id,)
    ).fetchone()

    return result["total_power"]


def is_server_admin(interaction: discord.Interaction):
    permissions = getattr(interaction.user, "guild_permissions", None)
    return interaction.guild is not None and permissions.administrator

# ---------- Bot ----------

class XWarBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()


bot = XWarBot()


@bot.event
async def on_ready():
    print(f"Bot is online: {bot.user}")


@bot.tree.command(name="ping", description="Check whether X War Bot is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 X War Bot is online!")


@bot.tree.command(name="army", description="View a Nation's army")
@app_commands.describe(player="Leave empty to view your own Nation")
async def army(
    interaction: discord.Interaction,
    player: Optional[discord.Member] = None
):
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

    await interaction.response.send_message(
        f"⚔️ **{data['nation_name']}**\n"
        f"🤝 Alliance: **{alliance_text}**\n"
        f"🗺️ Land: **{data['land']}**\n"
        f"💰 War Credits: **${data['money']}**\n"
        f"🪖 Land Army: **{data['land_army']}**\n"
        f"✈️ Air Army: **{data['air_army']}**\n"
        f"🚢 Navy: **{data['navy']}**\n"
        f"💥 Total Power: **{power(data)}**\n"
        f"🏛️ Capital: **{capital_status}**"
    )


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

    await interaction.response.send_message(
        f"🏳️ Your Nation is now called **{name}**."
    )


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

    await interaction.response.send_message(
        f"🏛️ **{data['capital_name']}**\n"
        f"❤️ Capital Health: **{data['capital_health']} / 100**\n"
        f"Status: {status}"
    )


# ---------- Tier 3 Economy ----------

@bot.tree.command(name="collect", description="Collect income from your Land")
async def collect(interaction: discord.Interaction):
    data = create_player(interaction.user)

    now = int(time.time())
    seconds_left = COLLECT_COOLDOWN - (now - data["last_collect"])

    if seconds_left > 0:
        await interaction.response.send_message(
            f"⏳ Please wait **{seconds_left} seconds** before collecting again.",
            ephemeral=True
        )
        return

    income = data["land"] * LAND_INCOME_PER_LAND

    db.execute(
        """
        UPDATE players
        SET money = money + ?, last_collect = ?
        WHERE user_id = ?
        """,
        (income, now, interaction.user.id)
    )
    db.commit()

    updated = get_player(interaction.user.id)

    await interaction.response.send_message(
        f"💰 **Land Income Collected!**\n"
        f"🗺️ Land: {data['land']}\n"
        f"💵 Income: ${income}\n"
        f"💰 New Balance: ${updated['money']}"
    )


@bot.tree.command(name="shop", description="View Army and Resource Market prices")
async def shop(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🏪 **X War Shop**\n\n"
        "**Army Shop**\n"
        "🪖 Land Army — **$50** each\n"
        "✈️ Air Army — **$150** each\n"
        "🚢 Navy — **$250** each\n\n"
        "**Resource Market**\n"
        "⛓️ Iron — sell for **$10** each\n"
        "🥇 Gold — sell for **$35** each\n"
        "🛢️ Oil — sell for **$20** each\n\n"
        "Use `/recruit` or `/sell`."
    )


@bot.tree.command(name="recruit", description="Recruit military units")
@app_commands.describe(unit="Choose a unit type", amount="How many units to recruit")
@app_commands.choices(unit=[
    app_commands.Choice(name="Land Army — $50 each", value="land"),
    app_commands.Choice(name="Air Army — $150 each", value="air"),
    app_commands.Choice(name="Navy — $250 each", value="navy")
])
async def recruit(
    interaction: discord.Interaction,
    unit: app_commands.Choice[str],
    amount: int
):
    if amount <= 0:
        await interaction.response.send_message(
            "❌ Amount must be greater than 0.",
            ephemeral=True
        )
        return

    data = create_player(interaction.user)
    cost = UNIT_COSTS[unit.value] * amount

    if data["money"] < cost:
        await interaction.response.send_message(
            f"❌ Not enough War Credits. You need ${cost}, "
            f"but you only have ${data['money']}.",
            ephemeral=True
        )
        return

    column = {
        "land": "land_army",
        "air": "air_army",
        "navy": "navy"
    }[unit.value]

    db.execute(
        f"""
        UPDATE players
        SET money = money - ?, {column} = {column} + ?
        WHERE user_id = ?
        """,
        (cost, amount, interaction.user.id)
    )
    db.commit()

    updated = get_player(interaction.user.id)

    await interaction.response.send_message(
        f"✅ **Recruitment successful!**\n"
        f"Recruited: {amount} {unit.name}\n"
        f"Cost: ${cost}\n"
        f"New Balance: ${updated['money']}"
    )


@bot.tree.command(name="mine_build", description="Build a resource mine")
@app_commands.describe(resource="Choose the mine type to build")
@app_commands.choices(resource=[
    app_commands.Choice(name="Iron Mine — $500", value="iron"),
    app_commands.Choice(name="Gold Mine — $500", value="gold"),
    app_commands.Choice(name="Oil Mine — $500", value="oil")
])
async def mine_build(
    interaction: discord.Interaction,
    resource: app_commands.Choice[str]
):
    data = create_player(interaction.user)

    if data["money"] < MINE_BUILD_COST:
        await interaction.response.send_message(
            f"❌ You need ${MINE_BUILD_COST} to build a mine.",
            ephemeral=True
        )
        return

    mine_column = f"{resource.value}_mines"

    db.execute(
        f"""
        UPDATE players
        SET money = money - ?, {mine_column} = {mine_column} + 1
        WHERE user_id = ?
        """,
        (MINE_BUILD_COST, interaction.user.id)
    )
    db.commit()

    await interaction.response.send_message(
        f"⛏️ You built one **{resource.name}**."
    )


@bot.tree.command(name="mine", description="Collect resources from all your mines")
async def mine(interaction: discord.Interaction):
    data = create_player(interaction.user)

    total_mines = (
        data["iron_mines"]
        + data["gold_mines"]
        + data["oil_mines"]
    )

    if total_mines == 0:
        await interaction.response.send_message(
            "❌ You have no mines. Use `/mine_build` first.",
            ephemeral=True
        )
        return

    now = int(time.time())
    seconds_left = MINE_COOLDOWN - (now - data["last_mine"])

    if seconds_left > 0:
        await interaction.response.send_message(
            f"⏳ Please wait **{seconds_left} seconds** before mining again.",
            ephemeral=True
        )
        return

    iron_income = data["iron_mines"] * RESOURCE_PER_MINE
    gold_income = data["gold_mines"] * RESOURCE_PER_MINE
    oil_income = data["oil_mines"] * RESOURCE_PER_MINE

    db.execute(
        """
        UPDATE players
        SET iron = iron + ?,
            gold = gold + ?,
            oil = oil + ?,
            last_mine = ?
        WHERE user_id = ?
        """,
        (
            iron_income,
            gold_income,
            oil_income,
            now,
            interaction.user.id
        )
    )
    db.commit()

    await interaction.response.send_message(
        "⛏️ **Mining Complete!**\n"
        f"⛓️ Iron: +{iron_income}\n"
        f"🥇 Gold: +{gold_income}\n"
        f"🛢️ Oil: +{oil_income}"
    )


@bot.tree.command(name="resources", description="View your mines and resources")
async def resources(interaction: discord.Interaction):
    data = create_player(interaction.user)

    await interaction.response.send_message(
        "⛏️ **Your Mines**\n"
        f"⛓️ Iron Mines: **{data['iron_mines']}**\n"
        f"🥇 Gold Mines: **{data['gold_mines']}**\n"
        f"🛢️ Oil Mines: **{data['oil_mines']}**\n\n"
        "**Your Resources**\n"
        f"⛓️ Iron: **{data['iron']}**\n"
        f"🥇 Gold: **{data['gold']}**\n"
        f"🛢️ Oil: **{data['oil']}**"
    )


@bot.tree.command(name="sell", description="Sell resources for War Credits")
@app_commands.describe(resource="Choose a resource", amount="How much to sell")
@app_commands.choices(resource=[
    app_commands.Choice(name="Iron — $10 each", value="iron"),
    app_commands.Choice(name="Gold — $35 each", value="gold"),
    app_commands.Choice(name="Oil — $20 each", value="oil")
])
async def sell(
    interaction: discord.Interaction,
    resource: app_commands.Choice[str],
    amount: int
):
    if amount <= 0:
        await interaction.response.send_message(
            "❌ Amount must be greater than 0.",
            ephemeral=True
        )
        return

    data = create_player(interaction.user)
    resource_name = resource.value

    if data[resource_name] < amount:
        await interaction.response.send_message(
            f"❌ You only have {data[resource_name]} {resource.name}.",
            ephemeral=True
        )
        return

    reward = amount * RESOURCE_VALUES[resource_name]

    db.execute(
        f"""
        UPDATE players
        SET {resource_name} = {resource_name} - ?,
            money = money + ?
        WHERE user_id = ?
        """,
        (amount, reward, interaction.user.id)
    )
    db.commit()

    await interaction.response.send_message(
        f"✅ Sold {amount} {resource.name} for **${reward}** War Credits."
    )


# ---------- Tier 4 Alliance ----------

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

    await interaction.response.send_message(
        f"🤝 Alliance created: **[{tag}] {name}**"
    )


@bot.tree.command(name="alliance_join", description="Join an Alliance")
@app_commands.describe(name="The exact Alliance name")
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

    await interaction.response.send_message(
        f"🤝 You joined **[{alliance['tag']}] {alliance['name']}**!"
    )


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

    await interaction.response.send_message(
        f"👋 You left **[{alliance['tag']}] {alliance['name']}**.{message}"
    )


@bot.tree.command(name="alliance_info", description="View Alliance information")
@app_commands.describe(name="Leave empty to view your own Alliance")
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

    await interaction.response.send_message(
        f"🤝 **[{alliance['tag']}] {alliance['name']}**\n"
        f"👑 Leader: <@{alliance['leader_id']}>\n"
        f"👥 Members ({len(members)}): {member_mentions}"
    )

# ---------- Alliance War Events ----------

@bot.tree.command(name="war_start", description="Admin: start an Alliance War")
@app_commands.describe(
    attacker_alliance="Attacking Alliance name",
    defender_alliance="Defending Alliance name"
)
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

    await interaction.response.send_message(
        "⚔️ **ALLIANCE WAR DECLARED!**\n"
        f"**[{attacker['tag']}] {attacker['name']}**\n"
        "vs\n"
        f"**[{defender['tag']}] {defender['name']}**"
    )


@bot.tree.command(name="war_status", description="View the current Alliance War")
async def war_status(interaction: discord.Interaction):
    war = get_active_war()

    if war is None:
        await interaction.response.send_message(
            "🕊️ There is no active Alliance War."
        )
        return

    attacker = db.execute(
        "SELECT * FROM alliances WHERE id = ?",
        (war["attacker_alliance_id"],)
    ).fetchone()

    defender = db.execute(
        "SELECT * FROM alliances WHERE id = ?",
        (war["defender_alliance_id"],)
    ).fetchone()

    await interaction.response.send_message(
        "⚔️ **ACTIVE ALLIANCE WAR**\n"
        f"**[{attacker['tag']}] {attacker['name']}**\n"
        f"Power: **{alliance_power(attacker['id'])}**\n\n"
        "vs\n\n"
        f"**[{defender['tag']}] {defender['name']}**\n"
        f"Power: **{alliance_power(defender['id'])}**"
    )


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

    await interaction.response.send_message(
        f"🕊️ **Alliance War Ended!**\n{winner_text}"
    )

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

    if power(attacker) <= 0:
        await interaction.response.send_message(
            "❌ You have no army and cannot attack.",
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

    now = int(time.time())
    seconds_left = ATTACK_COOLDOWN - (now - attacker["last_attack"])

    if seconds_left > 0:
        await interaction.response.send_message(
            f"⏳ Please wait **{seconds_left} seconds** before attacking again.",
            ephemeral=True
        )
        return

    attacker_power = power(attacker)
    defender_power = power(defender)

    if attacker_power > defender_power:
        winner_losses = calculate_losses(attacker, 0.10)
        loser_losses = calculate_losses(defender, 0.30)

        save_losses(interaction.user.id, winner_losses)
        save_losses(target.id, loser_losses)

        if defender["land"] > 0:
            db.execute(
                "UPDATE players SET land = land + 1 WHERE user_id = ?",
                (interaction.user.id,)
            )
            db.execute(
                "UPDATE players SET land = land - 1 WHERE user_id = ?",
                (target.id,)
            )
            reward = "🗺️ You captured **1 Land**!"

        else:
            new_health = max(
                defender["capital_health"] - CAPITAL_DAMAGE,
                0
            )

            db.execute(
                "UPDATE players SET capital_health = ? WHERE user_id = ?",
                (new_health, target.id)
            )

            if new_health == 0:
                credits = int(defender["money"] * CAPITAL_REWARD_PERCENT)

                db.execute(
                    "UPDATE players SET money = money + ? WHERE user_id = ?",
                    (credits, interaction.user.id)
                )
                db.execute(
                    "UPDATE players SET money = money - ? WHERE user_id = ?",
                    (credits, target.id)
                )

                reward = (
                    f"🏛️ **{defender['capital_name']} has fallen!**\n"
                    f"💰 You captured ${credits} War Credits."
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
        winner_losses = calculate_losses(defender, 0.10)
        loser_losses = calculate_losses(attacker, 0.30)

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

    db.execute(
        "UPDATE players SET last_attack = ? WHERE user_id = ?",
        (now, interaction.user.id)
    )
    db.commit()

    await interaction.response.send_message(result)


if not TOKEN:
    raise ValueError("DISCORD_TOKEN was not found. Check your .env file.")

bot.run(TOKEN)