"""XC-only casino games for X BOT.

These games use fictional server currency (XC) only.  They never connect to
Wabbit or any real-money payment service.
"""
import os
import random
import time

import discord
from discord import app_commands
from discord.ext import tasks
import xbot_ui


CASINO_DEFAULTS = {
    "casino_enabled": "1",
    "casino_min_bet": "10",
    "casino_max_bet": "10000",
    "lottery_ticket_price": "100",
    "lottery_starting_prize": "1000",
    "lottery_prize_ratio": "50",
    "casino_cooldown_seconds": "45",
    "casino_vip_role_id": "1538216081452306442",
    "casino_vip_daily_cost": "100",
    "casino_vip_duration_seconds": "86400",
    "casino_vip_cooldown_percent": "50",
    "server_svip_role_id": "1529040020072169572",
    "server_svip_cooldown_percent": "75",
    "crash_daily_net_win_limit": "2500",
}

# Component interactions do not include the original slash command name.
# Store the name briefly by interaction ID for the same-bet replay buttons.
_replay_game_names: dict[int, str] = {}


def initialise(db) -> None:
    for key, value in CASINO_DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))
    db.execute("""CREATE TABLE IF NOT EXISTS casino_stats (
        user_id INTEGER PRIMARY KEY, games_played INTEGER NOT NULL DEFAULT 0,
        total_wagered INTEGER NOT NULL DEFAULT 0, total_won INTEGER NOT NULL DEFAULT 0,
        biggest_payout INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS lottery_rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT, prize_pool INTEGER NOT NULL,
        opened_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        winner_id INTEGER, drawn_at INTEGER
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS lottery_entries (
        round_id INTEGER NOT NULL, user_id INTEGER NOT NULL, tickets INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(round_id,user_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS casino_game_settings (
        game TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
        min_bet INTEGER NOT NULL DEFAULT 0, max_bet INTEGER NOT NULL DEFAULT 0,
        cooldown_seconds INTEGER NOT NULL DEFAULT -1
    )""")
    for game in ("dice", "coinflip", "blackjack", "slot", "roulette", "scratch", "mines", "crash", "keno", "tower", "highlow", "balloonpop", "lottery", "spin"):
        db.execute("INSERT OR IGNORE INTO casino_game_settings(game) VALUES(?)", (game,))
    db.execute("UPDATE casino_game_settings SET cooldown_seconds=45 WHERE cooldown_seconds<0")
    # Crash is deliberately smaller and slower than the other entertainment
    # games, so it cannot become a fast XC farming command.
    db.execute("UPDATE casino_game_settings SET min_bet=10,max_bet=500,cooldown_seconds=30 WHERE game='crash' AND min_bet=0 AND max_bet=0")
    db.execute("""CREATE TABLE IF NOT EXISTS casino_cooldowns (
        user_id INTEGER NOT NULL, game TEXT NOT NULL, used_at INTEGER NOT NULL,
        PRIMARY KEY(user_id,game)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS casino_vip_members (
        user_id INTEGER PRIMARY KEY, expires_at INTEGER NOT NULL, purchased_at INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS casino_daily_game_net (
        user_id INTEGER NOT NULL, game TEXT NOT NULL, day TEXT NOT NULL,
        net_profit INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id,game,day)
    )""")
    db.commit()


def setting(db, key: str) -> int:
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return int(row["value"]) if row else int(CASINO_DEFAULTS[key])


def casino_day() -> str:
    """Server-local calendar day used by casino daily limits."""
    return time.strftime("%Y-%m-%d", time.localtime())


def crash_daily_net(db, user_id: int) -> int:
    row = db.execute("SELECT net_profit FROM casino_daily_game_net WHERE user_id=? AND game='crash' AND day=?", (user_id, casino_day())).fetchone()
    return int(row["net_profit"]) if row else 0


def consume_item(db, user_id: int, item_name: str) -> int:
    """Consume one named inventory item and return its configured XC face value."""
    item = db.execute("""SELECT i.id,i.price,v.quantity FROM items i JOIN inventories v ON v.item_id=i.id
        WHERE v.user_id=? AND i.name=? COLLATE NOCASE AND v.quantity>0""", (user_id, item_name)).fetchone()
    if item is None:
        return 0
    db.execute("UPDATE inventories SET quantity=quantity-1 WHERE user_id=? AND item_id=?", (user_id, item['id']))
    return max(1, item['price'])


async def game_is_open(interaction, db, game: str) -> bool:
    """Check Dashboard Casino/Game switches for item-backed games too."""
    if not setting(db, "casino_enabled"):
        await interaction.response.send_message(view=xbot_ui.warning("🎰 Casino Closed", "The X BOT Casino is currently closed."), ephemeral=True)
        return False
    game_settings = db.execute("SELECT enabled FROM casino_game_settings WHERE game=?", (game,)).fetchone()
    if game_settings and not game_settings["enabled"]:
        await interaction.response.send_message(view=xbot_ui.warning("🎰 Game Closed", f"/{game} is currently disabled by the Casino Dashboard."), ephemeral=True)
        return False
    return True


async def take_bet(interaction, db, create_player, bet: int):
    game = _replay_game_names.pop(interaction.id, None) or (interaction.command.name if interaction.command else "casino")
    if not await game_is_open(interaction, db, game):
        return None
    game_settings = db.execute("SELECT * FROM casino_game_settings WHERE game=?", (game,)).fetchone()
    minimum = game_settings["min_bet"] if game_settings and game_settings["min_bet"] > 0 else setting(db, "casino_min_bet")
    maximum = game_settings["max_bet"] if game_settings and game_settings["max_bet"] > 0 else setting(db, "casino_max_bet")
    if bet < minimum or bet > maximum:
        await interaction.response.send_message(view=xbot_ui.danger("Invalid Bet", f"Bets must be between **{minimum:,} XC** and **{maximum:,} XC**."), ephemeral=True)
        return None
    if game == "crash":
        limit = setting(db, "crash_daily_net_win_limit")
        net = crash_daily_net(db, interaction.user.id)
        if limit > 0 and net >= limit:
            await interaction.response.send_message(view=xbot_ui.warning("📈 Crash Daily Limit", f"You have reached today's **{limit:,} XC** net-win limit for /crash. Please return tomorrow."), ephemeral=True)
            return None
    player = create_player(interaction.user)
    if player["xc"] < bet:
        await interaction.response.send_message(view=xbot_ui.danger("Not Enough XC", f"You need **{bet:,} XC** to place that bet."), ephemeral=True)
        return None
    now = int(time.time())
    vip = db.execute("SELECT * FROM casino_vip_members WHERE user_id=?", (interaction.user.id,)).fetchone()
    vip_active = bool(vip and vip["expires_at"] > now)
    svip_role_id = setting(db, "server_svip_role_id")
    svip_active = isinstance(interaction.user, discord.Member) and any(role.id == svip_role_id for role in interaction.user.roles)
    if vip and not vip_active:
        db.execute("DELETE FROM casino_vip_members WHERE user_id=?", (interaction.user.id,))
        role = interaction.guild.get_role(setting(db, "casino_vip_role_id")) if interaction.guild else None
        if role and isinstance(interaction.user, discord.Member):
            try:
                await interaction.user.remove_roles(role, reason="X BOT Casino VIP expired")
            except discord.HTTPException:
                pass
    cooldown = game_settings["cooldown_seconds"] if game_settings and game_settings["cooldown_seconds"] >= 0 else setting(db, "casino_cooldown_seconds")
    cooldown_reduction = 0
    if vip_active:
        cooldown_reduction = setting(db, "casino_vip_cooldown_percent")
    if svip_active:
        # SVIP is the top server VIP.  It replaces, never stacks with, the
        # paid Casino VIP reduction.
        cooldown_reduction = max(cooldown_reduction, setting(db, "server_svip_cooldown_percent"))
    if cooldown_reduction:
        cooldown = round(cooldown * max(0, 100 - cooldown_reduction) / 100)
    previous = db.execute("SELECT used_at FROM casino_cooldowns WHERE user_id=? AND game=?", (interaction.user.id, game)).fetchone()
    remaining = cooldown - (now - previous["used_at"]) if previous else 0
    if remaining > 0:
        vip_note = " SVIP cooldown is active." if svip_active else " Casino VIP cooldown is active." if vip_active else ""
        await interaction.response.send_message(view=xbot_ui.warning("⏳ Casino Cooldown", f"/{game} is ready again in **{remaining} seconds**." + vip_note), ephemeral=True)
        return None
    db.execute("UPDATE players SET xc=xc-? WHERE user_id=?", (bet, interaction.user.id))
    db.execute("INSERT INTO casino_cooldowns(user_id,game,used_at) VALUES(?,?,?) ON CONFLICT(user_id,game) DO UPDATE SET used_at=excluded.used_at", (interaction.user.id, game, now))
    return player


def finish(db, user_id: int, action: str, bet: int, multiplier: float, detail: str):
    """Pay a total multiplier (2.0 means stake + equal profit) and log it."""
    payout = round(bet * multiplier)
    if action == "casino_crash":
        # A player may still play after losses, but a day cannot produce an
        # unlimited profit.  The final winning hand is trimmed to the exact
        # remaining daily net-profit allowance.
        limit = setting(db, "crash_daily_net_win_limit")
        current_net = crash_daily_net(db, user_id)
        if limit > 0 and payout > bet:
            payout = min(payout, bet + max(0, limit - current_net))
    if payout:
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (payout, user_id))
    profit = payout - bet
    if action == "casino_crash":
        db.execute("""INSERT INTO casino_daily_game_net(user_id,game,day,net_profit) VALUES(?,'crash',?,?)
            ON CONFLICT(user_id,game,day) DO UPDATE SET net_profit=net_profit+excluded.net_profit""", (user_id, casino_day(), profit))
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,strftime('%s','now'))", (user_id, action, f"{detail} | bet {bet} XC | payout {payout} XC | profit {profit:+} XC"))
    db.execute("""INSERT INTO casino_stats(user_id,games_played,total_wagered,total_won,biggest_payout,updated_at)
        VALUES(?,1,?,?,?,strftime('%s','now')) ON CONFLICT(user_id) DO UPDATE SET
        games_played=games_played+1,total_wagered=total_wagered+excluded.total_wagered,
        total_won=total_won+excluded.total_won,biggest_payout=MAX(biggest_payout,excluded.biggest_payout),updated_at=excluded.updated_at""",
        (user_id, bet, payout, payout))
    db.commit()
    return payout, profit


CARD_RANKS = [("A", 11), ("2", 2), ("3", 3), ("4", 4), ("5", 5), ("6", 6), ("7", 7), ("8", 8), ("9", 9), ("10", 10), ("J", 10), ("Q", 10), ("K", 10)]
CARD_SUITS = ["♥️", "♦️", "♣️", "♠️"]


def card():
    rank, value = random.choice(CARD_RANKS)
    return rank, value, random.choice(CARD_SUITS)


def hand_value(cards):
    total = sum(card_data[1] for card_data in cards)
    aces = sum(1 for card_data in cards if card_data[0] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def cards_text(cards):
    return " ".join(f"{rank}{suit}" for rank, _, suit in cards)


def active_lottery_round(db):
    row = db.execute("SELECT * FROM lottery_rounds WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        db.execute("INSERT INTO lottery_rounds(prize_pool,opened_at) VALUES(?,strftime('%s','now'))", (setting(db, "lottery_starting_prize"),))
        row = db.execute("SELECT * FROM lottery_rounds WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    return row


def draw_lottery(db):
    """Close the current round and return (winner_id, prize, ticket_count), or no winner."""
    round_row = active_lottery_round(db)
    entries = db.execute("SELECT user_id,tickets FROM lottery_entries WHERE round_id=? AND tickets>0", (round_row['id'],)).fetchall()
    if not entries:
        return None, round_row['prize_pool'], 0
    ticket_bag = [entry['user_id'] for entry in entries for _ in range(entry['tickets'])]
    winner_id = random.choice(ticket_bag)
    prize = round_row['prize_pool']
    db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (prize, winner_id))
    db.execute("UPDATE lottery_rounds SET active=0,winner_id=?,drawn_at=strftime('%s','now') WHERE id=?", (winner_id, round_row['id']))
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,strftime('%s','now'))", (winner_id, "lottery_win", f"Round #{round_row['id']}: +{prize} XC"))
    return winner_id, prize, len(ticket_bag)


_vip_cleanup_loop = None


def start_vip_cleanup_task(bot, db):
    """Remove the temporary Casino VIP role shortly after its paid duration ends."""
    global _vip_cleanup_loop
    if _vip_cleanup_loop is not None and _vip_cleanup_loop.is_running():
        return

    @tasks.loop(minutes=10)
    async def cleanup():
        now = int(time.time())
        expired = db.execute("SELECT user_id FROM casino_vip_members WHERE expires_at<=?", (now,)).fetchall()
        if not expired:
            return
        role_id = setting(db, "casino_vip_role_id")
        for row in expired:
            for guild in bot.guilds:
                member = guild.get_member(row["user_id"])
                role = guild.get_role(role_id)
                if member and role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="X BOT Casino VIP expired")
                    except discord.HTTPException:
                        pass
        db.execute("DELETE FROM casino_vip_members WHERE expires_at<=?", (now,))
        db.commit()

    _vip_cleanup_loop = cleanup
    cleanup.start()


def register_commands(bot, db, create_player) -> None:
    # bot.py loads .env before calling this function.  Keep this lookup here,
    # not at module import time, so the configured server ID is always used.
    casino_guild_id = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
    casino_guild_kwargs = {"guild": discord.Object(id=casino_guild_id)} if casino_guild_id else {"guild": discord.Object(id=0)}

    class CasinoReplayButton(discord.ui.Button):
        def __init__(self, owner_id: int, game: str, command, args):
            super().__init__(label="🔄 Next Round", style=discord.ButtonStyle.success)
            self.owner_id, self.game, self.command, self.args = owner_id, game, command, args

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("This Casino result belongs to the player who started it.", ephemeral=True)
                return
            # A component interaction has no slash-command name.  Preserve it
            # so Dashboard game rules and cooldowns apply to the correct game.
            _replay_game_names[interaction.id] = self.game
            try:
                await self.command.callback(interaction, *self.args)
            finally:
                _replay_game_names.pop(interaction.id,None)

    async def send_result(interaction, view):
        if interaction.message is not None:
            await interaction.response.edit_message(view=view)
        else:
            await interaction.response.send_message(view=view)

    class ChangeBetButton(discord.ui.Button):
        def __init__(self,owner_id,game):
            super().__init__(label='Change Bet',style=discord.ButtonStyle.secondary)
            self.owner_id,self.game=owner_id,game
        async def callback(self,interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message('Open /casino for your own game.',ephemeral=True)
                return
            await interaction.response.send_modal(CasinoGameModal(self.game))

    def casino_result(title: str, body: str, *, won: bool, owner_id: int, game: str, command, args, stake: int, payout: int):
        """A coloured end card with a same-bet Next Round button."""
        view = discord.ui.LayoutView(timeout=300)
        container = discord.ui.Container(accent_color=discord.Color.green() if won else discord.Color.red())
        wallet=db.execute('SELECT xc FROM players WHERE user_id=?',(owner_id,)).fetchone()[0]
        summary=f"Stake: **{stake:,} XC** · Total returned: **{payout:,} XC**\nNet result: **{payout-stake:+,} XC** · Wallet: **{wallet:,} XC**"
        if game in {'spin','balloonpop'} and args[-1]==0:
            summary+='\nItem-funded round: stake is the item value; the consumed item is not returned.'
        container.add_item(discord.ui.TextDisplay(f"## {title}\n{body}\n\n{summary}"))
        container.add_item(discord.ui.Separator())
        replay=CasinoReplayButton(owner_id, game, command, args)
        replay.label=f'Play Again · {stake:,} XC'[:80]
        if game in {'spin','balloonpop'} and args[-1]==0:
            replay.label='Play Again · Use Item'
        container.add_item(discord.ui.ActionRow(replay,ChangeBetButton(owner_id,game),CasinoHubBackButton(owner_id),CasinoLobbyButton(owner_id)))
        container.add_item(discord.ui.TextDisplay('-# Each click starts one paid round, subject to cooldown. No automatic bets. XC is a fictional game currency.'))
        view.add_item(container)
        return view

    def casino_command(interaction: discord.Interaction, name: str):
        command = bot.tree.get_command(name)
        if command is None and interaction.guild:
            command = bot.tree.get_command(name, guild=interaction.guild)
        return command

    class CasinoGameModal(discord.ui.Modal):
        """One guided input panel for every Casino game, so players do not need command syntax."""
        def __init__(self, game: str):
            self.game = game
            super().__init__(title=f"Play {game.replace('_', ' ').title()}"[:45])
            label = "Tickets" if game == "lottery" else "Bet (XC)"
            placeholder = "1" if game == "lottery" else "Enter your XC bet"
            rules=db.execute('SELECT * FROM casino_game_settings WHERE game=?',(game,)).fetchone()
            minimum=rules['min_bet'] if rules and rules['min_bet']>0 else setting(db,'casino_min_bet')
            maximum=rules['max_bet'] if rules and rules['max_bet']>0 else setting(db,'casino_max_bet')
            if game!='lottery':
                label=f'Bet XC ({minimum:,}–{maximum:,})'[:45]
            self.primary = discord.ui.TextInput(label=label, placeholder=placeholder, default='1' if game=='lottery' else str(minimum), max_length=12)
            self.add_item(self.primary)
            details = {
                "dice": ("Guess (1–6)", "For example: 4", "1"),
                "coinflip": ("Choice", "heads or tails", "heads"),
                "roulette": ("Choice", "red, black, or green", "red"),
                "mines": ("Safe tiles (1–5)", "For example: 2", "2"),
                "crash": ("Cash-out multiplier", "From 1.1 to 5.0", "1.5"),
                "keno": ("Five numbers", "For example: 1 3 5 7 9", "1 3 5 7 9"),
                "tower": ("Floors (1–6)", "For example: 3", "3"),
                "highlow": ("Choice", "higher or lower", "higher"),
                "balloonpop": ("Balloon colour", "red, blue, green, or gold", "red"),
            }
            self.detail = None
            if game in details:
                label, placeholder, default = details[game]
                self.detail = discord.ui.TextInput(label=label, placeholder=placeholder, default=default, max_length=80)
                self.add_item(self.detail)

        async def on_submit(self, interaction: discord.Interaction):
            raw_primary = str(self.primary.value).strip().replace(",", "")
            try:
                primary = int(raw_primary)
            except ValueError:
                await interaction.response.send_message("❌ Enter a whole number for your bet or tickets.", ephemeral=True)
                return
            if primary < 0 or (self.game != "lottery" and primary <= 0):
                await interaction.response.send_message("❌ Enter a valid positive amount.", ephemeral=True)
                return
            detail = str(self.detail.value).strip().casefold() if self.detail else ""
            try:
                if self.game == "dice":
                    args = (primary, int(detail))
                elif self.game in {"coinflip", "roulette", "highlow", "balloonpop"}:
                    allowed = {
                        "coinflip": {"heads", "tails"},
                        "roulette": {"red", "black", "green"},
                        "highlow": {"higher", "lower"},
                        "balloonpop": {"red", "blue", "green", "gold"},
                    }[self.game]
                    if detail not in allowed:
                        raise ValueError(f"Choose one of: {', '.join(sorted(allowed))}.")
                    choice = app_commands.Choice(name=detail.title(), value=detail)
                    args = (choice, primary) if self.game == "balloonpop" else (primary, choice)
                elif self.game == "mines":
                    args = (primary, int(detail))
                elif self.game == "crash":
                    args = (primary, float(detail))
                elif self.game == "keno":
                    args = (primary, detail)
                elif self.game == "tower":
                    args = (primary, int(detail))
                elif self.game == "lottery":
                    args = (primary,)
                else:
                    args = (primary,)
            except ValueError as error:
                await interaction.response.send_message(view=xbot_ui.danger("Invalid Game Choice", str(error)), ephemeral=True)
                return
            command = casino_command(interaction, self.game)
            if command is None:
                await interaction.response.send_message("❌ This game is not available right now.", ephemeral=True)
                return
            _replay_game_names[interaction.id]=self.game
            try:
                await command.callback(interaction, *args)
            finally:
                _replay_game_names.pop(interaction.id,None)

    class CasinoGameSelect(discord.ui.Select):
        def __init__(self, owner_id: int):
            options = [
                discord.SelectOption(label="Blackjack", value="blackjack", emoji="🃏", description="Interactive cards against the dealer"),
                discord.SelectOption(label="Coinflip", value="coinflip", emoji="🪙", description="Pick heads or tails"),
                discord.SelectOption(label="Dice", value="dice", emoji="🎲", description="Guess a number from 1 to 6"),
                discord.SelectOption(label="Slot", value="slot", emoji="🎰", description="Spin for matching symbols"),
                discord.SelectOption(label="Roulette", value="roulette", emoji="🎡", description="Red, black, or green"),
                discord.SelectOption(label="Scratch", value="scratch", emoji="🎟️", description="Scratch a fictional ticket"),
                discord.SelectOption(label="Mines", value="mines", emoji="💣", description="Choose how much risk to take"),
                discord.SelectOption(label="Crash", value="crash", emoji="📈", description="Cash out before the crash"),
                discord.SelectOption(label="Keno", value="keno", emoji="🔢", description="Pick five numbers"),
                discord.SelectOption(label="Tower", value="tower", emoji="🗼", description="Climb risky floors"),
                discord.SelectOption(label="High / Low", value="highlow", emoji="🃏", description="Guess the next card"),
                discord.SelectOption(label="Balloon Pop", value="balloonpop", emoji="🎈", description="Pop a coloured balloon"),
                discord.SelectOption(label="Lottery", value="lottery", emoji="🏆", description="Buy tickets for the prize pool"),
                discord.SelectOption(label="Prize Wheel", value="spin", emoji="🎡", description="Spin the prize wheel"),
            ]
            super().__init__(placeholder="Choose a Casino game…", min_values=1, max_values=1, options=options)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open `/casino` for your own Casino panel.", ephemeral=True)
                return
            await interaction.response.send_modal(CasinoGameModal(self.values[0]))

    class CasinoHubButton(discord.ui.Button):
        def __init__(self, owner_id: int, action: str, label: str, emoji: str, style=discord.ButtonStyle.secondary):
            super().__init__(label=label, emoji=emoji, style=style)
            self.owner_id, self.action = owner_id, action

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open `/casino` for your own Casino panel.", ephemeral=True)
                return
            if self.action == "casino_stats":
                row = db.execute("SELECT * FROM casino_stats WHERE user_id=?", (self.owner_id,)).fetchone()
                if row is None:
                    body = "You have not played an X BOT Casino game yet."
                else:
                    profit = int(row['total_won']) - int(row['total_wagered'])
                    body = (f"Games played: **{row['games_played']:,}**\n"
                            f"Total wagered: **{row['total_wagered']:,} XC**\n"
                            f"Total payouts: **{row['total_won']:,} XC**\n"
                            f"Net result: **{profit:+,} XC**\n"
                            f"Biggest payout: **{row['biggest_payout']:,} XC**")
                await interaction.response.edit_message(view=CasinoDetailView(self.owner_id, "📊 Casino Record", body))
                return
            if self.action == "casino_leaderboard":
                rows = db.execute("SELECT * FROM casino_stats ORDER BY total_won-total_wagered DESC, biggest_payout DESC LIMIT 10").fetchall()
                body = "\n".join(f"**{number}.** <@{row['user_id']}> — **{int(row['total_won']) - int(row['total_wagered']):+,} XC**" for number, row in enumerate(rows, 1)) or "No Casino games have been played yet."
                await interaction.response.edit_message(view=CasinoDetailView(self.owner_id, "🏆 Casino Leaderboard", body))
                return
            command = casino_command(interaction, self.action)
            if command is None:
                await interaction.response.send_message("❌ This Casino action is not available right now.", ephemeral=True)
                return
            await command.callback(interaction)

    class CasinoHubBackButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Back to Casino", emoji="⬅️", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message('Open /casino for your own panel.',ephemeral=True)
                return
            await interaction.response.edit_message(view=CasinoHubView(self.owner_id))

    class CasinoLobbyButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Lobby", emoji="✨", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("This Casino panel belongs to another player.", ephemeral=True)
                return
            builder = getattr(bot, "xbot_player_lobby_builder", None)
            if builder is None:
                await interaction.response.send_message("Lobby is loading. Please try again shortly.", ephemeral=True)
                return
            await interaction.response.edit_message(view=builder(self.owner_id))

    class CasinoDetailView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, title: str, body: str):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay(f"## {title}\n{body}"))
            container.add_item(discord.ui.ActionRow(CasinoHubBackButton(owner_id), CasinoLobbyButton(owner_id)))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This Casino panel belongs to another player.", ephemeral=True)
            return False

    class CasinoHubView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            player = create_player_from_id(owner_id)
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay(
                f"## 🎰 X BOT Casino\n"
                f"🪙 Wallet: **{player['xc']:,} XC**\n"
                f"💰 Bet range: **{setting(db, 'casino_min_bet'):,}–{setting(db, 'casino_max_bet'):,} XC**\n"
                f"⏳ Standard cooldown: **{setting(db, 'casino_cooldown_seconds')} seconds**\n"
                f"Choose a game → enter your bet → submit one round.\n"
                f"Game-specific limits appear in the bet form.\n"
                f"-# XC is fictional game currency. You can lose your stake; Casino is optional, not a guaranteed way to earn."
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(CasinoGameSelect(owner_id)))
            container.add_item(discord.ui.ActionRow(
                CasinoHubButton(owner_id, "casino_stats", "My Stats", "📊", discord.ButtonStyle.primary),
                CasinoHubButton(owner_id, "casino_leaderboard", "Leaderboard", "🏆", discord.ButtonStyle.secondary),
                CasinoHubButton(owner_id, "casino_vip", "Casino VIP", "💎", discord.ButtonStyle.success),
                CasinoLobbyButton(owner_id),
            ))
            self.add_item(container)

    def create_player_from_id(user_id: int):
        return db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()

    @bot.tree.command(name="casino", description="View X BOT Casino games and bet limits")
    async def casino(interaction: discord.Interaction):
        await interaction.response.defer()
        create_player(interaction.user)
        await interaction.edit_original_response(view=CasinoHubView(interaction.user.id))

    # The app is at Discord's 100 global command limit, so this server-only
    # feature deliberately lives in the configured Discord server.
    @bot.tree.command(name="casino_vip", description="Buy or view the 24-hour Casino VIP cooldown benefit", **casino_guild_kwargs)
    async def casino_vip(interaction: discord.Interaction):
        player = create_player(interaction.user); now = int(time.time())
        svip_role_id = setting(db, "server_svip_role_id")
        if isinstance(interaction.user, discord.Member) and any(role.id == svip_role_id for role in interaction.user.roles):
            await interaction.response.send_message(view=xbot_ui.success("🪙 Server VIP Active", f"You already have **Server Very Important Person (SVIP)**.\nYour Casino cooldown is reduced by **{setting(db, 'server_svip_cooldown_percent')}%** with no daily XC cost."), ephemeral=True)
            return
        active = db.execute("SELECT * FROM casino_vip_members WHERE user_id=?", (interaction.user.id,)).fetchone()
        if active and active["expires_at"] > now:
            remaining = max(1, (active["expires_at"] - now + 3599) // 3600)
            await interaction.response.send_message(view=xbot_ui.panel("💎 Casino VIP Active", f"You have **{remaining} hour(s)** remaining.\nCasino cooldown reduction: **{setting(db, 'casino_vip_cooldown_percent')}%**.", colour=discord.Color.teal()), ephemeral=True)
            return
        cost = setting(db, "casino_vip_daily_cost")
        if player["xc"] < cost:
            await interaction.response.send_message(view=xbot_ui.danger("Not Enough XC", f"Casino VIP costs **{cost:,} XC** for 24 hours."), ephemeral=True); return
        db.execute("UPDATE players SET xc=xc-? WHERE user_id=?", (cost, interaction.user.id))
        expires_at = now + setting(db, "casino_vip_duration_seconds")
        db.execute("INSERT INTO casino_vip_members(user_id,expires_at,purchased_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at,purchased_at=excluded.purchased_at", (interaction.user.id, expires_at, now))
        role = interaction.guild.get_role(setting(db, "casino_vip_role_id")) if interaction.guild else None
        if role and isinstance(interaction.user, discord.Member):
            try:
                await interaction.user.add_roles(role, reason="Purchased X BOT Casino VIP")
            except discord.HTTPException:
                pass
        db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)", (interaction.user.id, "casino_vip_purchase", f"Casino VIP for 24 hours: -{cost} XC", now)); db.commit()
        await interaction.response.send_message(view=xbot_ui.success("💎 Casino VIP Activated", f"Paid **{cost:,} XC**.\nYour Casino cooldown is reduced by **{setting(db, 'casino_vip_cooldown_percent')}%** for 24 hours."), ephemeral=True)

    @bot.tree.command(name="casino_stats", description="View your X BOT Casino record")
    async def casino_stats(interaction: discord.Interaction):
        row = db.execute("SELECT * FROM casino_stats WHERE user_id=?", (interaction.user.id,)).fetchone()
        if row is None:
            await interaction.response.send_message("🎰 You have not played an X BOT Casino game yet.")
            return
        profit = row['total_won'] - row['total_wagered']
        await interaction.response.send_message(view=xbot_ui.panel("🎰 Casino Record",
            f"🎰 **{interaction.user.display_name}'s Casino Record**\n"
            f"Games played: **{row['games_played']}**\n"
            f"Total wagered: **{row['total_wagered']} XC**\n"
            f"Total payouts: **{row['total_won']} XC**\n"
            f"Net result: **{profit:+} XC**\n"
            f"Biggest payout: **{row['biggest_payout']} XC**", colour=discord.Color.gold()))

    @bot.tree.command(name="casino_leaderboard", description="View X BOT Casino's biggest winners")
    async def casino_leaderboard(interaction: discord.Interaction):
        rows = db.execute("SELECT * FROM casino_stats ORDER BY total_won-total_wagered DESC, biggest_payout DESC LIMIT 10").fetchall()
        if not rows:
            await interaction.response.send_message("🎰 No Casino games have been played yet.")
            return
        lines = ["🏆 **X BOT Casino Leaderboard**"]
        for number, row in enumerate(rows, 1):
            lines.append(f"**{number}.** <@{row['user_id']}> — **{row['total_won'] - row['total_wagered']:+} XC**")
        await interaction.response.send_message(view=xbot_ui.panel("🏆 Casino Leaderboard", "\n".join(lines[1:]), colour=discord.Color.gold()))
    @bot.tree.command(name="dice", description="Casino: guess a dice number")
    @app_commands.describe(bet="XC to bet", guess="Choose 1 to 6")
    async def dice(interaction: discord.Interaction, bet: int, guess: app_commands.Range[int, 1, 6]):
        if await take_bet(interaction, db, create_player, bet) is None: return
        # A small positive edge keeps this a fun server game instead of a grind.
        rolled = random.randint(1, 6); mult = 7.0 if rolled == guess else 0
        payout, _ = finish(db, interaction.user.id, "casino_dice", bet, mult, f"guessed {guess}, rolled {rolled}")
        await send_result(interaction, casino_result("🎲 Dice Result", f"Rolled **{rolled}** · " + (f"Won **{payout:,} XC**!" if payout else f"Lost **{bet:,} XC**."), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="dice", command=dice, args=(bet, guess)))

    @bot.tree.command(name="coinflip", description="Casino: bet on heads or tails")
    @app_commands.choices(choice=[app_commands.Choice(name="Heads", value="heads"), app_commands.Choice(name="Tails", value="tails")])
    async def coinflip(interaction: discord.Interaction, bet: int, choice: app_commands.Choice[str]):
        if await take_bet(interaction, db, create_player, bet) is None: return
        result = random.choice(["heads", "tails"]); mult = 2.1 if result == choice.value else 0
        payout, _ = finish(db, interaction.user.id, "casino_coinflip", bet, mult, f"picked {choice.value}, result {result}")
        await send_result(interaction, casino_result("🪙 Coinflip Result", f"**{result.title()}!** " + (f"Won **{payout:,} XC**." if payout else f"Lost **{bet:,} XC**."), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="coinflip", command=coinflip, args=(bet, choice)))

    class BlackjackButton(discord.ui.Button):
        def __init__(self, label, style, action, disabled=False):
            super().__init__(label=label, style=style, disabled=disabled)
            self.action = action

        async def callback(self, interaction: discord.Interaction):
            await self.view.play_action(interaction, self.action)

    class BlackjackView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, bet: int, player_cards, dealer_cards):
            super().__init__(timeout=180)
            self.owner_id, self.bet = owner_id, bet
            self.player_cards, self.dealer_cards = player_cards, dealer_cards
            self.finished = False
            self.result = None
            self.payout = 0
            self.reveal = False
            self.rebuild()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This Blackjack hand belongs to the player who started it.", ephemeral=True)
            return False

        def rebuild(self):
            self.clear_items()
            player_total = hand_value(self.player_cards)
            dealer_display = cards_text(self.dealer_cards) if self.reveal else f"{cards_text(self.dealer_cards[:1])} ❓"
            dealer_total = hand_value(self.dealer_cards) if self.reveal else "?"
            result_lower = (self.result or "").lower()
            colour = discord.Color.red() if self.finished and ("lost" in result_lower or "bust" in result_lower or "dealer wins" in result_lower or "quit" in result_lower) else discord.Color.green() if self.finished and ("win" in result_lower or "blackjack" in result_lower) else discord.Color.blurple()
            container = discord.ui.Container(accent_color=colour)
            header = f"## 🃏 X BOT Blackjack\n{self.result + chr(10) if self.result else ''}"
            container.add_item(discord.ui.TextDisplay(header))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(
                f"### You (Player)                         Dealer\n"
                f"**Cards:** {cards_text(self.player_cards)}    **Cards:** {dealer_display}\n"
                f"**Total:** {player_total}                           **Total:** {dealer_total}\n"
                f"**Bet:** {self.bet} XC"
            ))
            container.add_item(discord.ui.Separator())
            if self.finished:
                wallet=db.execute('SELECT xc FROM players WHERE user_id=?',(self.owner_id,)).fetchone()[0]
                container.add_item(discord.ui.TextDisplay(f'Total returned: **{self.payout:,} XC** · Net result: **{self.payout-self.bet:+,} XC**\nWallet: **{wallet:,} XC**'))
                container.add_item(discord.ui.ActionRow(
                    BlackjackButton(f"Play Again · {self.bet:,} XC", discord.ButtonStyle.success, "again"),
                    ChangeBetButton(self.owner_id,'blackjack'),CasinoHubBackButton(self.owner_id),CasinoLobbyButton(self.owner_id),
                ))
            else:
                container.add_item(discord.ui.ActionRow(
                    BlackjackButton("Hit", discord.ButtonStyle.primary, "hit"),
                    BlackjackButton("Stand", discord.ButtonStyle.primary, "stand"),
                    BlackjackButton("Quit", discord.ButtonStyle.danger, "quit"),
                    BlackjackButton("Double Down", discord.ButtonStyle.secondary, "double", len(self.player_cards) != 2),
                    BlackjackButton("Surrender", discord.ButtonStyle.secondary, "surrender", len(self.player_cards) != 2),
                ))
            self.add_item(container)

        async def resolve(self, interaction, result, multiplier):
            while hand_value(self.dealer_cards) < 17:
                self.dealer_cards.append(card())
            payout, _ = finish(db, self.owner_id, "casino_blackjack", self.bet, multiplier, f"player {hand_value(self.player_cards)}, dealer {hand_value(self.dealer_cards)}, {result}")
            self.payout = payout
            self.finished = True
            self.reveal = True
            self.result = f"**{result}** " + (f"Payout: **{payout} XC**." if payout else f"You lost **{self.bet} XC**.")
            self.rebuild()
            await interaction.response.edit_message(view=self)

        async def play_action(self, interaction, action):
            if self.finished:
                if action == "again":
                    _replay_game_names[interaction.id] = "blackjack"
                    await blackjack.callback(interaction, self.bet)
                    return
                await interaction.response.send_message("This Blackjack hand is already finished.", ephemeral=True)
                return
            if action == "hit":
                self.player_cards.append(card())
                if hand_value(self.player_cards) > 21:
                    await self.resolve(interaction, "Bust!", 0)
                elif hand_value(self.player_cards) == 21:
                    while hand_value(self.dealer_cards) < 17:
                        self.dealer_cards.append(card())
                    if hand_value(self.dealer_cards) == 21:
                        await self.resolve(interaction, "21! Automatic stand — Push!", 1)
                    else:
                        await self.resolve(interaction, "21! Automatic stand — you win!", 2)
                else:
                    self.rebuild()
                    await interaction.response.edit_message(view=self)
                return
            if action == "surrender":
                self.finished = True
                self.reveal = True
                payout, _ = finish(db, self.owner_id, "casino_blackjack", self.bet, 0.5, "surrendered")
                self.payout = payout
                self.result = f"**Surrendered.** Half your bet was returned: **{payout} XC**."
                self.rebuild()
                await interaction.response.edit_message(view=self)
                return
            if action == "quit":
                self.finished = True
                self.reveal = True
                finish(db, self.owner_id, "casino_blackjack", self.bet, 0, "quit")
                self.result = f"**Quit.** You lost **{self.bet} XC**."
                self.rebuild()
                await interaction.response.edit_message(view=self)
                return
            if action == "double":
                if len(self.player_cards) != 2:
                    await interaction.response.send_message("❌ Double Down is only available on your first two cards.", ephemeral=True)
                    return
                player = create_player(interaction.user)
                if player['xc'] < self.bet:
                    await interaction.response.send_message(f"❌ You need another **{self.bet} XC** to Double Down.", ephemeral=True)
                    return
                db.execute("UPDATE players SET xc=xc-? WHERE user_id=?", (self.bet, self.owner_id))
                self.bet *= 2
                self.player_cards.append(card())
                player_total, dealer_total = hand_value(self.player_cards), hand_value(self.dealer_cards)
                if player_total > 21:
                    await self.resolve(interaction, "Bust after Double Down!", 0)
                else:
                    while dealer_total < 17:
                        self.dealer_cards.append(card()); dealer_total = hand_value(self.dealer_cards)
                    if dealer_total > 21 or player_total > dealer_total: await self.resolve(interaction, "Double Down Win!", 2)
                    elif player_total == dealer_total: await self.resolve(interaction, "Push!", 1)
                    else: await self.resolve(interaction, "Dealer wins.", 0)
                return
            player_total = hand_value(self.player_cards)
            while hand_value(self.dealer_cards) < 17:
                self.dealer_cards.append(card())
            dealer_total = hand_value(self.dealer_cards)
            if dealer_total > 21 or player_total > dealer_total: await self.resolve(interaction, "You win!", 2)
            elif player_total == dealer_total: await self.resolve(interaction, "Push!", 1)
            else: await self.resolve(interaction, "Dealer wins.", 0)

    @bot.tree.command(name="blackjack", description="Casino: play interactive Blackjack")
    async def blackjack(interaction: discord.Interaction, bet: int):
        if await take_bet(interaction, db, create_player, bet) is None: return
        player_cards, dealer_cards = [card(), card()], [card(), card()]
        view = BlackjackView(interaction.user.id, bet, player_cards, dealer_cards)
        if hand_value(player_cards) == 21:
            while hand_value(dealer_cards) < 17:
                dealer_cards.append(card())
            multiplier = 1 if hand_value(dealer_cards) == 21 else 2.5
            payout, _ = finish(db, interaction.user.id, "casino_blackjack", bet, multiplier, f"natural blackjack, dealer {hand_value(dealer_cards)}")
            result = "Blackjack Push! Stake returned." if multiplier == 1 else "Blackjack!"
            view.payout = payout
            view.finished = True; view.reveal = True; view.result = f"**{result}** Payout: **{payout} XC**."; view.rebuild()
            await send_result(interaction,view)
            return
        await send_result(interaction,view)

    @bot.tree.command(name="slot", description="Casino: spin the slot machine")
    async def slot(interaction: discord.Interaction, bet: int):
        if await take_bet(interaction, db, create_player, bet) is None: return
        symbols = ["🍒", "🍋", "🔔", "💎", "7️⃣"]
        roll = [random.choice(symbols) for _ in range(3)]
        mult = 12 if len(set(roll)) == 1 and roll[0] == "7️⃣" else 6 if len(set(roll)) == 1 else 2 if len(set(roll)) == 2 else 0
        payout, _ = finish(db, interaction.user.id, "casino_slot", bet, mult, " ".join(roll))
        await send_result(interaction, casino_result("🎰 Slot Result", f"## {' | '.join(roll)}\n" + (f"Won **{payout:,} XC**!" if payout else f"No match — lost **{bet:,} XC**."), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="slot", command=slot, args=(bet,)))

    @bot.tree.command(name="roulette", description="Casino: bet red, black, or green")
    @app_commands.choices(choice=[app_commands.Choice(name="Red", value="red"), app_commands.Choice(name="Black", value="black"), app_commands.Choice(name="Green", value="green")])
    async def roulette(interaction: discord.Interaction, bet: int, choice: app_commands.Choice[str]):
        if await take_bet(interaction, db, create_player, bet) is None: return
        result = random.choices(["red", "black", "green"], weights=[48, 48, 4])[0]
        mult = 14 if result == "green" and choice.value == result else 2 if result == choice.value else 0
        payout, _ = finish(db, interaction.user.id, "casino_roulette", bet, mult, f"picked {choice.value}, wheel {result}")
        await send_result(interaction, casino_result("🎡 Roulette Result", f"The wheel landed on **{result.title()}**. " + (f"Won **{payout:,} XC**!" if payout else f"Lost **{bet:,} XC**."), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="roulette", command=roulette, args=(bet, choice)))

    @bot.tree.command(name="scratch", description="Casino: scratch a virtual ticket")
    async def scratch(interaction: discord.Interaction, bet: int):
        if await take_bet(interaction, db, create_player, bet) is None: return
        roll = random.randint(1, 100)
        # More frequent small prizes make Scratch welcoming for new players.
        mult = 12 if roll == 100 else 4 if roll >= 90 else 2 if roll >= 55 else 0
        payout, _ = finish(db, interaction.user.id, "casino_scratch", bet, mult, f"roll {roll}")
        await send_result(interaction, casino_result("🎫 Scratch Result", f"Score: **{roll}/100** · " + (f"Won **{payout:,} XC**!" if payout else f"No prize — lost **{bet:,} XC**."), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="scratch", command=scratch, args=(bet,)))

    @bot.tree.command(name="mines", description="Casino: choose how many mine tiles to risk")
    @app_commands.describe(safe_tiles="1–5 safe tiles to reveal")
    async def mines(interaction: discord.Interaction, bet: int, safe_tiles: app_commands.Range[int, 1, 5]):
        if await take_bet(interaction, db, create_player, bet) is None: return
        success = random.random() < (0.8 ** safe_tiles); mult = 1 + safe_tiles * 0.35 if success else 0
        payout, _ = finish(db, interaction.user.id, "casino_mines", bet, mult, f"revealed {safe_tiles} safe tiles")
        await send_result(interaction, casino_result("💣 Mines Result", f"Tiles risked: **{safe_tiles}** · " + (f"Safe path! Won **{payout:,} XC**." if payout else f"Boom! Lost **{bet:,} XC**."), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="mines", command=mines, args=(bet, safe_tiles)))

    @bot.tree.command(name="crash", description="Casino: cash out before the crash")
    @app_commands.describe(cashout="Multiplier to cash out at, from 1.10 to 5.00")
    async def crash(interaction: discord.Interaction, bet: int, cashout: app_commands.Range[float, 1.1, 5.0]):
        if await take_bet(interaction, db, create_player, bet) is None: return
        # Friendly low multipliers are common, while large multipliers are
        # intentionally rare.  This keeps Crash entertaining without making
        # it an XC printing machine.
        roll = random.random()
        if roll < .12: crash_at = 1.00
        elif roll < .30: crash_at = random.uniform(1.01, 1.40)
        elif roll < .60: crash_at = random.uniform(1.41, 2.25)
        elif roll < .85: crash_at = random.uniform(2.26, 3.50)
        elif roll < .95: crash_at = random.uniform(3.51, 5.00)
        elif roll < .99: crash_at = random.uniform(5.01, 8.00)
        else: crash_at = random.uniform(8.01, 10.00)
        crash_at = round(crash_at, 2); mult = cashout if crash_at >= cashout else 0
        payout, profit = finish(db, interaction.user.id, "casino_crash", bet, mult, f"cashout {cashout:.2f}x, crashed {crash_at:.2f}x")
        limit = setting(db, "crash_daily_net_win_limit")
        net = crash_daily_net(db, interaction.user.id)
        cap_note = f"\nToday's Crash net result: **{net:+,} / {limit:,} XC**." if limit else ""
        await send_result(interaction, casino_result("📈 Crash Result", f"Crashed at **{crash_at:.2f}x** · " + (f"Cashed out for **{payout:,} XC**!" if payout else f"Lost **{bet:,} XC**.") + cap_note, won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="crash", command=crash, args=(bet, cashout)))

    @bot.tree.command(name="keno", description="Casino: pick five numbers from 1 to 10")
    @app_commands.describe(picks="Five different numbers, for example: 1,2,3,4,5")
    async def keno(interaction: discord.Interaction, bet: int, picks: str):
        try:
            chosen = {int(part.strip()) for part in picks.split(",")}
            if len(chosen) != 5 or not all(1 <= number <= 10 for number in chosen): raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Enter exactly five different numbers from 1–10, like `1,2,3,4,5`.", ephemeral=True); return
        if await take_bet(interaction, db, create_player, bet) is None: return
        drawn = set(random.sample(range(1, 11), 5)); matches = len(chosen & drawn)
        mult = {5: 12, 4: 3, 3: 1.5}.get(matches, 0)
        payout, _ = finish(db, interaction.user.id, "casino_keno", bet, mult, f"picked {sorted(chosen)}, drawn {sorted(drawn)}, matches {matches}")
        await send_result(interaction, casino_result("🔢 Keno Result", f"Drawn: **{', '.join(map(str, sorted(drawn)))}**\nMatches: **{matches}**\n" + (f"Payout: **{payout:,} XC**." if payout else f"Lost **{bet:,} XC**."), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="keno", command=keno, args=(bet, picks)))

    @bot.tree.command(name="tower", description="Casino: climb a risky tower")
    @app_commands.describe(floors="How many floors to attempt, from 1 to 6")
    async def tower(interaction: discord.Interaction, bet: int, floors: app_commands.Range[int, 1, 6]):
        if await take_bet(interaction, db, create_player, bet) is None: return
        cleared = 0
        for _ in range(floors):
            if random.random() > 0.76: break
            cleared += 1
        mult = round(1.35 ** floors, 2) if cleared == floors else 0
        payout, _ = finish(db, interaction.user.id, "casino_tower", bet, mult, f"cleared {cleared}/{floors}")
        await send_result(interaction, casino_result("🗼 Tower Result", f"Cleared **{cleared}/{floors}** floors. " + (f"Payout: **{payout:,} XC**." if payout else f"The tower fell — lost **{bet:,} XC**."), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="tower", command=tower, args=(bet, floors)))

    @bot.tree.command(name="highlow", description="Casino: guess whether the next card is higher or lower")
    @app_commands.choices(choice=[app_commands.Choice(name="Higher", value="higher"), app_commands.Choice(name="Lower", value="lower")])
    async def highlow(interaction: discord.Interaction, bet: int, choice: app_commands.Choice[str]):
        if await take_bet(interaction, db, create_player, bet) is None: return
        first, second = random.randint(1, 13), random.randint(1, 13)
        if first == second: mult, result = 1, "Tie — stake returned"
        elif (second > first and choice.value == "higher") or (second < first and choice.value == "lower"): mult, result = 2, "Correct"
        else: mult, result = 0, "Wrong"
        payout, _ = finish(db, interaction.user.id, "casino_highlow", bet, mult, f"{first} then {second}, chose {choice.value}")
        await send_result(interaction, casino_result("🃏 High-Low Result", f"First: **{first}** → Next: **{second}** · **{result}**\n" + (f"Payout: **{payout:,} XC**." if payout else f"Lost **{bet:,} XC**."), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="highlow", command=highlow, args=(bet, choice)))

    @bot.tree.command(name="balloonpop", description="Casino: pop a coloured balloon")
    @app_commands.choices(color=[app_commands.Choice(name="Red", value="red"), app_commands.Choice(name="Blue", value="blue"), app_commands.Choice(name="Gold", value="gold")])
    async def balloonpop(interaction: discord.Interaction, color: app_commands.Choice[str], bet: int = 0):
        used_item = False
        if bet == 0:
            if not await game_is_open(interaction, db, "balloonpop"):
                return
            create_player(interaction.user)
            bet = consume_item(db, interaction.user.id, "Balloon")
            if not bet:
                await interaction.response.send_message("❌ Enter an XC bet, or buy a **Balloon** item from `/shop`.", ephemeral=True)
                return
            used_item = True
        elif await take_bet(interaction, db, create_player, bet) is None: return
        result = random.choices(["red", "blue", "gold"], weights=[44, 44, 12])[0]
        mult = 5 if result == "gold" and color.value == result else 2 if result == color.value else 0
        payout, _ = finish(db, interaction.user.id, "casino_balloonpop", bet, mult, f"picked {color.value}, popped {result}, item={used_item}")
        await send_result(interaction, casino_result("🎈 Balloon Pop", f"The **{result}** balloon popped. " + (f"Won **{payout:,} XC**!" if payout else ("Your Balloon was used." if used_item else f"Lost **{bet:,} XC**.")), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="balloonpop", command=balloonpop, args=(color, 0 if used_item else bet)))

    @bot.tree.command(name="lottery", description="Casino: buy tickets for the X BOT Lottery prize pool")
    @app_commands.describe(tickets="How many tickets to buy")
    async def lottery(interaction: discord.Interaction, tickets: app_commands.Range[int, 1, 100] = 1):
        if not await game_is_open(interaction, db, "lottery"):
            return
        create_player(interaction.user)
        owned = db.execute("""SELECT COALESCE(v.quantity,0) quantity,i.id FROM items i LEFT JOIN inventories v
            ON v.item_id=i.id AND v.user_id=? WHERE i.name='Lottery Ticket' COLLATE NOCASE""", (interaction.user.id,)).fetchone()
        item_tickets = min(tickets, owned['quantity']) if owned else 0
        paid_tickets = tickets - item_tickets
        price = setting(db, "lottery_ticket_price"); bet = paid_tickets * price
        if bet and await take_bet(interaction, db, create_player, bet) is None: return
        if item_tickets:
            db.execute("UPDATE inventories SET quantity=quantity-? WHERE user_id=? AND item_id=?", (item_tickets, interaction.user.id, owned['id']))
        round_row = active_lottery_round(db)
        face_value = tickets * price
        added_prize = round(face_value * setting(db, "lottery_prize_ratio") / 100)
        db.execute("INSERT INTO lottery_entries(round_id,user_id,tickets) VALUES(?,?,?) ON CONFLICT(round_id,user_id) DO UPDATE SET tickets=tickets+excluded.tickets", (round_row['id'], interaction.user.id, tickets))
        db.execute("UPDATE lottery_rounds SET prize_pool=prize_pool+? WHERE id=?", (added_prize, round_row['id']))
        db.execute("INSERT INTO casino_stats(user_id,games_played,total_wagered,total_won,biggest_payout,updated_at) VALUES(?,1,?,?,0,strftime('%s','now')) ON CONFLICT(user_id) DO UPDATE SET games_played=games_played+1,total_wagered=total_wagered+excluded.total_wagered,updated_at=excluded.updated_at", (interaction.user.id, bet, 0))
        db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,strftime('%s','now'))", (interaction.user.id, "lottery_ticket", f"Round #{round_row['id']}: {tickets} tickets"))
        db.commit()
        updated = db.execute("SELECT prize_pool FROM lottery_rounds WHERE id=?", (round_row['id'],)).fetchone()
        payment = f"{bet} XC" if not item_tickets else f"{item_tickets} inventory ticket(s)" + (f" + {bet} XC" if bet else "")
        await interaction.response.send_message(view=xbot_ui.success("🎟️ Lottery Entry", f"Entered **{tickets} ticket(s)** using **{payment}**.\nRound **#{round_row['id']}** prize pool: **{updated['prize_pool']:,} XC**."))

    @bot.tree.command(name="lottery_draw", description="Admin: draw the current X BOT Lottery round")
    async def lottery_draw(interaction: discord.Interaction):
        if not getattr(interaction.user, "guild_permissions", None) or not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only server administrators can draw the Lottery.", ephemeral=True)
            return
        winner_id, prize, ticket_count = draw_lottery(db)
        if winner_id is None:
            await interaction.response.send_message(f"🎟️ There are no tickets in the current Lottery round. Prize pool remains **{prize} XC**.", ephemeral=True)
            return
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🎉 Lottery Round Complete", f"Winner: <@{winner_id}>\nPrize: **{prize:,} XC**\nTickets entered: **{ticket_count}**"))

    @bot.tree.command(name="spin", description="Casino: spin the X BOT prize wheel")
    async def spin(interaction: discord.Interaction, bet: int = 0):
        used_item = False
        if bet == 0:
            if not await game_is_open(interaction, db, "spin"):
                return
            create_player(interaction.user)
            bet = consume_item(db, interaction.user.id, "Spin Token")
            if not bet:
                await interaction.response.send_message("❌ Enter an XC bet, or buy a **Spin Token** from `/shop`.", ephemeral=True)
                return
            used_item = True
        elif await take_bet(interaction, db, create_player, bet) is None: return
        prize = random.choices([(0, "Miss"), (1.5, "Small prize"), (2, "Double"), (5, "Jackpot")], weights=[45, 30, 20, 5])[0]
        payout, _ = finish(db, interaction.user.id, "casino_spin", bet, prize[0], f"{prize[1]}, item={used_item}")
        await send_result(interaction, casino_result("🎡 Prize Wheel", f"Result: **{prize[1]}** · " + (f"Won **{payout:,} XC**!" if payout else ("Your Spin Token was used." if used_item else f"Lost **{bet:,} XC**.")), won=bool(payout), stake=bet, payout=payout, owner_id=interaction.user.id, game="spin", command=spin, args=(0 if used_item else bet,)))

    # Used by Economy Centre to open Casino in the same message.
    bot.xbot_player_panel_builders = getattr(bot, "xbot_player_panel_builders", {})
    bot.xbot_player_panel_builders["casino"] = lambda owner_id: CasinoHubView(owner_id)
