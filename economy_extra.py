"""Beta 0.5 social economy: daily rewards, transfers, and player marketplace."""
import time

import discord
from discord import app_commands

import xbot_ui
import tier5


DEFAULTS = {
    "daily_reward": "50",
    "daily_cooldown": "86400",
    "transfer_min": "1",
    "transfer_max": "1000000",
    "transfer_tax_percent": "0",
    "market_enabled": "1",
    "market_fee_percent": "5",
    "market_min_price": "1",
    "market_max_price": "1000000",
}


def initialise(db):
    columns = {row["name"] for row in db.execute("PRAGMA table_info(players)")}
    if "last_daily" not in columns:
        db.execute("ALTER TABLE players ADD COLUMN last_daily INTEGER NOT NULL DEFAULT 0")
    if "bank_xc" not in columns:
        db.execute("ALTER TABLE players ADD COLUMN bank_xc INTEGER NOT NULL DEFAULT 0")
    db.execute("""CREATE TABLE IF NOT EXISTS market_listings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        price_each INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    )""")
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))
    db.commit()


def setting(db, key):
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return int(row["value"] if row else DEFAULTS[key])


def log(db, user_id, action, detail):
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)", (user_id, action, detail, int(time.time())))


def tier6_setting(db, key, fallback):
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return int(row["value"]) if row else int(fallback)


def expire_market_listings(db, now=None):
    """Close old listings once and return every unsold item to its seller."""
    now = int(now or time.time())
    cutoff = now - max(1, tier6_setting(db, "tier6_market_expiry_days", 7)) * 86400
    rows = db.execute(
        "SELECT * FROM market_listings WHERE active=1 AND quantity>0 AND created_at<=?",
        (cutoff,),
    ).fetchall()
    for row in rows:
        changed = db.execute(
            "UPDATE market_listings SET active=0 WHERE id=? AND active=1",
            (row["id"],),
        )
        if not changed.rowcount:
            continue
        db.execute(
            """INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
               ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
            (row["seller_id"], row["item_id"], row["quantity"]),
        )
        log(db, row["seller_id"], "market_expired", f"listing {row['id']}: returned {row['quantity']} item(s)")
    if rows:
        db.commit()
    return len(rows)


def register_commands(bot, db, create_player, find_item):
    def panel_command(interaction: discord.Interaction, name: str):
        """Find a command regardless of whether Discord registered it globally or to this server."""
        command = bot.tree.get_command(name)
        if command is None and interaction.guild:
            command = bot.tree.get_command(name, guild=interaction.guild)
        return command

    def economy_home_view(owner_id: int, notice: str | None = None):
        builder = getattr(bot, "xbot_tier6_economy_builder", None)
        if builder is not None:
            return builder(owner_id, notice or "")
        return EconomyCentreView(owner_id, notice)

    class EconomyAmountModal(discord.ui.Modal):
        def __init__(self, action: str, source_message: discord.Message):
            super().__init__(title=f"{action.title()} XC")
            self.action = action
            self.source_message = source_message
            self.amount = discord.ui.TextInput(
                label="Amount of XC",
                placeholder="For example: 500",
                min_length=1,
                max_length=12,
            )
            self.add_item(self.amount)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                amount = int(str(self.amount.value).strip().replace(",", ""))
            except ValueError:
                await interaction.response.send_message("❌ Please enter a whole number.", ephemeral=True)
                return
            if amount <= 0:
                await interaction.response.send_message("❌ Amount must be at least 1 XC.", ephemeral=True)
                return
            player = create_player_from_id(interaction.user.id)
            if self.action == "deposit":
                if amount > int(player["xc"]):
                    await interaction.response.send_message("❌ You do not have that much XC in your Wallet.", ephemeral=True)
                    return
                db.execute("UPDATE players SET xc=xc-?,bank_xc=bank_xc+? WHERE user_id=?", (amount, amount, interaction.user.id))
                detail = f"✅ Deposited **{amount:,} XC** into your Bank."
            else:
                if amount > int(player["bank_xc"]):
                    await interaction.response.send_message("❌ You do not have that much XC in your Bank.", ephemeral=True)
                    return
                db.execute("UPDATE players SET bank_xc=bank_xc-?,xc=xc+? WHERE user_id=?", (amount, amount, interaction.user.id))
                detail = f"✅ Withdrew **{amount:,} XC** to your Wallet."
            log(db, interaction.user.id, self.action, f"{amount} XC")
            db.commit()
            # Modal submits are a separate Discord interaction. Update the
            # original Economy Centre message instead of posting a result.
            await interaction.response.defer()
            await self.source_message.edit(view=EconomyBankView(interaction.user.id))

    class EconomyPanelButton(discord.ui.Button):
        def __init__(self, owner_id: int, action: str, label: str, emoji: str, style=discord.ButtonStyle.secondary):
            super().__init__(label=label, emoji=emoji, style=style)
            self.owner_id, self.action = owner_id, action

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open `/lobby` for your own Economy Centre.", ephemeral=True)
                return
            if self.action in {"deposit", "withdraw"}:
                await interaction.response.send_modal(EconomyAmountModal(self.action, interaction.message))
                return
            if self.action == "refresh":
                # Refresh in place: the player stays in the same Economy
                # Centre instead of receiving another Discord message.
                await interaction.response.edit_message(view=economy_home_view(self.owner_id))
                return
            if self.action == "lobby":
                await interaction.response.edit_message(view=XBotLobbyView(self.owner_id))
                return
            if self.action == "more":
                await interaction.response.edit_message(view=EconomyMoreView(self.owner_id))
                return
            if self.action == "bank":
                await interaction.response.edit_message(view=EconomyBankView(self.owner_id))
                return
            if self.action == "balance":
                await interaction.response.edit_message(view=EconomyBalanceView(self.owner_id))
                return
            if self.action == "daily":
                player = create_player_from_id(self.owner_id)
                remaining = setting(db, "daily_cooldown") - (int(time.time()) - int(player["last_daily"]))
                if remaining > 0:
                    hours, remainder = divmod(remaining, 3600)
                    await interaction.response.edit_message(view=economy_home_view(
                        self.owner_id,
                        notice=f"⏳ Daily reward is ready in **{hours}h {remainder // 60}m**.",
                    ))
                    return
                reward = setting(db, "daily_reward")
                db.execute("UPDATE players SET xc=xc+?,last_daily=? WHERE user_id=?", (reward, int(time.time()), self.owner_id))
                log(db, self.owner_id, "daily", f"+{reward} XC")
                db.commit()
                await interaction.response.edit_message(view=economy_home_view(
                    self.owner_id, notice=f"✅ Daily reward collected: **+{reward:,} XC**."
                ))
                return
            # Other player systems register a view factory on the bot.  Opening
            # them this way replaces the Economy Centre instead of posting a
            # second Discord message in the channel.
            builders = getattr(bot, "xbot_player_panel_builders", {})
            builder = builders.get(self.action)
            if builder is not None:
                await interaction.response.edit_message(view=builder(self.owner_id))
                return
            command = panel_command(interaction, self.action)
            if command is None:
                await interaction.response.send_message("❌ This economy action is not available right now.", ephemeral=True)
                return
            await command.callback(interaction)

    class EconomyBackButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Economy Centre", emoji="💰", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open `/lobby` for your own Economy Centre.", ephemeral=True)
                return
            await interaction.response.edit_message(view=economy_home_view(self.owner_id))

    class EconomyBankView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            player = create_player_from_id(owner_id)
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay(
                f"## 🏦 X BOT Bank\n"
                f"🪙 Wallet: **{player['xc']:,} XC**\n"
                f"🏦 Bank: **{player['bank_xc']:,} XC**\n"
                f"💰 Total: **{player['xc'] + player['bank_xc']:,} XC**\n"
                f"-# Deposit keeps XC safe in your Bank. Withdraw moves it back to your Wallet."
            ))
            container.add_item(discord.ui.ActionRow(
                EconomyPanelButton(owner_id, "deposit", "Deposit", "📥", discord.ButtonStyle.success),
                EconomyPanelButton(owner_id, "withdraw", "Withdraw", "📤", discord.ButtonStyle.primary),
                EconomyBackButton(owner_id),
            ))
            self.add_item(container)

    class EconomyBalanceView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            player = create_player_from_id(owner_id)
            item_count = db.execute("SELECT COALESCE(SUM(quantity),0) total FROM inventories WHERE user_id=?", (owner_id,)).fetchone()["total"]
            container = discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay(
                f"## 💳 X BOT Balance\n"
                f"🪙 Wallet: **{player['xc']:,} XC** · 🏦 Bank: **{player['bank_xc']:,} XC**\n"
                f"⚔️ War Credits: **{player['money']:,}** · 💎 XCrystals: **{player['xcrystals']:,}**\n"
                f"🎒 Backpack: **{item_count:,} item(s)** · 🏳️ Nation: **{player['nation_name']}**"
            ))
            container.add_item(discord.ui.ActionRow(EconomyBackButton(owner_id)))
            self.add_item(container)

    class XBotLobbyButton(discord.ui.Button):
        def __init__(self, owner_id: int, destination: str, label: str, emoji: str, style=discord.ButtonStyle.secondary):
            super().__init__(label=label, emoji=emoji, style=style)
            self.owner_id = owner_id
            self.destination = destination

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open your own X BOT panel with `/lobby`.", ephemeral=True)
                return
            if self.destination == "economy":
                await interaction.response.edit_message(view=economy_home_view(self.owner_id))
                return
            builders = getattr(bot, "xbot_player_panel_builders", {})
            builder = builders.get(self.destination)
            if builder is None:
                await interaction.response.send_message("This panel is loading. Please try again in a moment.", ephemeral=True)
                return
            await interaction.response.defer()
            await interaction.edit_original_response(view=builder(self.owner_id))

    class XBotLobbyView(discord.ui.LayoutView):
        """The player home screen: one starting command, then button navigation."""
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            player = create_player_from_id(owner_id)
            progress = tier5.lobby_snapshot(db, owner_id)
            xp_text = "MAX" if progress["next_threshold"] is None else f"{progress['xp']}/{progress['next_threshold']} XP"
            container = discord.ui.Container(accent_color=discord.Color.blurple())
            container.add_item(discord.ui.TextDisplay(
                f"## ✨ X BOT Lobby\n"
                f"🏳️ **{player['nation_name']}** · 🪙 **{player['xc']:,} XC** · ⚔️ **{player['money']:,} War Credits**\n"
                f"⭐ Nation Level **{progress['level']} — {progress['rank']}** · **{xp_text}**\n"
                f"🧭 Next: **{progress['label']}**\n"
                f"Choose a system below. Every page stays in this same panel."
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(
                XBotLobbyButton(owner_id, "economy", "Economy", "💰", discord.ButtonStyle.success),
                XBotLobbyButton(owner_id, "war", "War", "⚔️", discord.ButtonStyle.danger),
                XBotLobbyButton(owner_id, "casino", "Casino", "🎰", discord.ButtonStyle.primary),
                XBotLobbyButton(owner_id, "craft", "Crafting", "🧪", discord.ButtonStyle.secondary),
            ))
            container.add_item(discord.ui.ActionRow(
                XBotLobbyButton(owner_id, "city", "City", "🏙️", discord.ButtonStyle.success),
                XBotLobbyButton(owner_id, "army", "Army", "🪖", discord.ButtonStyle.danger),
                XBotLobbyButton(owner_id, "mining", "Mining", "⛏️", discord.ButtonStyle.secondary),
                XBotLobbyButton(owner_id, "shop", "Shop", "🏪", discord.ButtonStyle.primary),
                XBotLobbyButton(owner_id, "market", "Market", "🏷️", discord.ButtonStyle.secondary),
            ))
            container.add_item(discord.ui.ActionRow(
                XBotLobbyButton(owner_id, "missions", "Missions", "🎯", discord.ButtonStyle.success),
                XBotLobbyButton(owner_id, progress["destination"], "Continue", "▶️", discord.ButtonStyle.primary),
            ))
            container.add_item(discord.ui.TextDisplay(
                "-# Missions guide your next action. Fast commands remain available for experienced players."
            ))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("Open your own X BOT panel with `/lobby`.", ephemeral=True)
            return False

    def create_player_from_id(user_id: int):
        """Read the already-created player for a panel refresh without inventing a Discord user object."""
        return db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()

    class EconomyCentreView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, notice: str | None = None):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            expire_market_listings(db)
            player = create_player_from_id(owner_id)
            inventory = db.execute("SELECT COALESCE(SUM(quantity),0) total FROM inventories WHERE user_id=?", (owner_id,)).fetchone()["total"]
            listings = db.execute("SELECT COUNT(*) total FROM market_listings WHERE seller_id=? AND active=1", (owner_id,)).fetchone()["total"]
            daily_remaining = max(0, setting(db, "daily_cooldown") - (int(time.time()) - int(player["last_daily"])))
            daily_text = "Ready now" if daily_remaining == 0 else f"Ready <t:{int(time.time()) + daily_remaining}:R>"
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay(
                f"## 💰 X BOT Economy Centre\n"
                f"🪙 Wallet **{player['xc']:,} XC** · 🏦 Bank **{player['bank_xc']:,} XC**\n"
                f"⚔️ **{player['money']:,} War Credits** · 💎 **{player['xcrystals']:,} XCrystals**\n"
                f"🎒 {inventory:,} inventory item(s) · 🏷️ {listings} market listing(s)\n"
                f"🎁 Daily reward: **{daily_text}**"
            ))
            container.add_item(discord.ui.Separator())
            if notice:
                container.add_item(discord.ui.TextDisplay(f"-# {notice}"))
            container.add_item(discord.ui.ActionRow(
                EconomyPanelButton(owner_id, "balance", "Balance", "💳", discord.ButtonStyle.primary),
                EconomyPanelButton(owner_id, "daily", "Daily", "🎁", discord.ButtonStyle.success),
                EconomyPanelButton(owner_id, "shop", "Shop", "🏪", discord.ButtonStyle.primary),
                EconomyPanelButton(owner_id, "mining", "Mining", "⛏️", discord.ButtonStyle.secondary),
                EconomyPanelButton(owner_id, "more", "More", "➕", discord.ButtonStyle.secondary),
            ))
            container.add_item(discord.ui.TextDisplay(
                "-# Start here for your wallet, shop and mining. Tap **More** for Backpack, Market, Exchange, Bank and Casino."
            ))
            self.add_item(container)

    class EconomyMoreView(discord.ui.LayoutView):
        """Second Economy page: keeps the first page small enough for phones."""
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            container = discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay(
                "## ➕ Economy Tools\n"
                "Open your items, player market, exchange, bank or casino."
            ))
            container.add_item(discord.ui.ActionRow(
                EconomyPanelButton(owner_id, "inventory", "Backpack", "🎒", discord.ButtonStyle.secondary),
                EconomyPanelButton(owner_id, "market", "Market", "🏷️", discord.ButtonStyle.primary),
                EconomyPanelButton(owner_id, "exchange", "Exchange", "🔄", discord.ButtonStyle.secondary),
                EconomyPanelButton(owner_id, "bank", "Bank", "🏦", discord.ButtonStyle.success),
                EconomyPanelButton(owner_id, "craft", "Crafting", "🧪", discord.ButtonStyle.primary),
            ))
            container.add_item(discord.ui.ActionRow(
                EconomyPanelButton(owner_id, "casino", "Casino", "🎰", discord.ButtonStyle.danger),
                EconomyBackButton(owner_id),
                EconomyPanelButton(owner_id, "lobby", "Lobby", "✨"),
            ))
            self.add_item(container)

    async def tradeable_backpack_autocomplete(interaction: discord.Interaction, current: str):
        rows = db.execute("""SELECT i.name,i.emoji,inv.quantity FROM inventories inv
            JOIN items i ON i.id=inv.item_id
            WHERE inv.user_id=? AND inv.quantity>0 AND i.tradeable=1 AND i.name LIKE ?
            ORDER BY i.name LIMIT 25""", (interaction.user.id, f"%{current.strip()}%")).fetchall()
        return [app_commands.Choice(name=f"{row['emoji']} {row['name']} ×{row['quantity']}"[:100], value=row["name"]) for row in rows]

    @bot.tree.command(name="lobby", description="Open your X BOT player lobby")
    async def lobby(interaction: discord.Interaction):
        await interaction.response.defer()
        create_player(interaction.user)
        await interaction.edit_original_response(view=XBotLobbyView(interaction.user.id))

    @bot.tree.command(name="economy", description="Open your X BOT Economy Centre")
    async def economy_centre(interaction: discord.Interaction):
        """Fast shortcut to the Economy page; /lobby remains the main home."""
        await interaction.response.defer()
        create_player(interaction.user)
        await interaction.edit_original_response(view=economy_home_view(interaction.user.id))

    @bot.tree.command(name="bank", description="View your XC wallet and bank balance")
    async def bank(interaction: discord.Interaction):
        player = create_player(interaction.user)
        await interaction.response.send_message(view=xbot_ui.panel("🏦 X BOT Bank", f"🪙 Wallet: **{player['xc']:,} XC**\n🏦 Bank: **{player['bank_xc']:,} XC**\n💰 Net XC: **{player['xc'] + player['bank_xc']:,} XC**", colour=discord.Color.gold(), footer="Use /deposit and /withdraw to move XC."))

    @bot.tree.command(name="deposit", description="Move XC from your wallet into your bank")
    async def deposit(interaction: discord.Interaction, amount: int):
        player = create_player(interaction.user)
        if amount <= 0 or player["xc"] < amount:
            await interaction.response.send_message(view=xbot_ui.danger("Deposit Rejected", "Enter a positive amount available in your wallet."), ephemeral=True); return
        db.execute("UPDATE players SET xc=xc-?,bank_xc=bank_xc+? WHERE user_id=?", (amount, amount, interaction.user.id))
        log(db, interaction.user.id, "deposit", f"{amount} XC"); db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🏦 Deposit Complete", f"Deposited **{amount:,} XC**.\nBank balance: **{player['bank_xc'] + amount:,} XC**"), ephemeral=True)

    @bot.tree.command(name="withdraw", description="Move XC from your bank into your wallet")
    async def withdraw(interaction: discord.Interaction, amount: int):
        player = create_player(interaction.user)
        if amount <= 0 or player["bank_xc"] < amount:
            await interaction.response.send_message(view=xbot_ui.danger("Withdrawal Rejected", "Enter a positive amount available in your bank."), ephemeral=True); return
        db.execute("UPDATE players SET bank_xc=bank_xc-?,xc=xc+? WHERE user_id=?", (amount, amount, interaction.user.id))
        log(db, interaction.user.id, "withdraw", f"{amount} XC"); db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🏦 Withdrawal Complete", f"Withdrew **{amount:,} XC**.\nWallet balance: **{player['xc'] + amount:,} XC**"), ephemeral=True)

    @bot.tree.command(name="daily", description="Collect your daily XC reward")
    async def daily(interaction: discord.Interaction):
        player = create_player(interaction.user)
        remaining = setting(db, "daily_cooldown") - (int(time.time()) - player["last_daily"])
        if remaining > 0:
            hours, remainder = divmod(remaining, 3600); minutes = remainder // 60
            await interaction.response.send_message(view=xbot_ui.warning("🎁 Daily Reward", f"Your next reward is ready in **{hours}h {minutes}m**."), ephemeral=True)
            return
        reward = setting(db, "daily_reward")
        db.execute("UPDATE players SET xc=xc+?,last_daily=? WHERE user_id=?", (reward, int(time.time()), interaction.user.id))
        log(db, interaction.user.id, "daily", f"+{reward} XC")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🎁 Daily Reward", f"You collected **{reward:,} XC**."))

    @bot.tree.command(name="pay", description="Send XC to another player")
    @app_commands.describe(player="Player receiving XC", amount="XC to send")
    async def pay(interaction: discord.Interaction, player: discord.Member, amount: int):
        sender = create_player(interaction.user); create_player(player)
        minimum, maximum = setting(db, "transfer_min"), setting(db, "transfer_max")
        if player.bot or player.id == interaction.user.id:
            await interaction.response.send_message(view=xbot_ui.danger("Transfer Rejected", "Choose another human player."), ephemeral=True); return
        if amount < minimum or amount > maximum:
            await interaction.response.send_message(view=xbot_ui.danger("Invalid Amount", f"Transfers must be **{minimum:,}–{maximum:,} XC**."), ephemeral=True); return
        tax = amount * setting(db, "transfer_tax_percent") // 100
        received = amount - tax
        if sender["xc"] < amount:
            await interaction.response.send_message(view=xbot_ui.danger("Not Enough XC", f"You need **{amount:,} XC**."), ephemeral=True); return
        db.execute("UPDATE players SET xc=xc-? WHERE user_id=?", (amount, interaction.user.id))
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (received, player.id))
        log(db, interaction.user.id, "pay", f"{amount} XC to {player.id}; tax {tax}")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("💸 Transfer Complete", f"Sent **{amount:,} XC** to {player.mention}.\nThey received **{received:,} XC**."))

    class MarketBuyModal(discord.ui.Modal):
        def __init__(self, listing_id, item_name):
            super().__init__(title=f"Buy {item_name}"[:45]); self.listing_id = listing_id
            self.amount = discord.ui.TextInput(label="Quantity", default="1", min_length=1, max_length=7)
            self.add_item(self.amount)

        async def on_submit(self, interaction):
            try:
                quantity = int(self.amount.value)
                if quantity <= 0: raise ValueError
            except ValueError:
                await interaction.response.send_message(view=xbot_ui.danger("Invalid Quantity", "Enter a whole number greater than 0."), ephemeral=True); return
            if not setting(db, "market_enabled"):
                await interaction.response.send_message(view=xbot_ui.warning("🛒 Market Closed", "The player market is currently closed."), ephemeral=True); return
            expire_market_listings(db)
            listing = db.execute("""SELECT l.*,i.name,i.emoji FROM market_listings l JOIN items i ON i.id=l.item_id
                WHERE l.id=? AND l.active=1""", (self.listing_id,)).fetchone()
            if listing is None or listing["quantity"] < quantity:
                await interaction.response.send_message(view=xbot_ui.danger("Listing Unavailable", "That quantity is no longer available."), ephemeral=True); return
            if listing["seller_id"] == interaction.user.id:
                await interaction.response.send_message(view=xbot_ui.danger("Purchase Rejected", "You cannot buy your own listing."), ephemeral=True); return
            buyer = create_player(interaction.user); total = listing["price_each"] * quantity
            if buyer["xc"] < total:
                await interaction.response.send_message(view=xbot_ui.danger("Not Enough XC", f"You need **{total:,} XC**."), ephemeral=True); return
            fee = total * setting(db, "market_fee_percent") // 100; seller_payment = total - fee
            db.execute("UPDATE players SET xc=xc-? WHERE user_id=?", (total, interaction.user.id))
            db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (seller_payment, listing["seller_id"]))
            db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
                ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""", (interaction.user.id, listing["item_id"], quantity))
            db.execute("UPDATE market_listings SET quantity=quantity-?,active=CASE WHEN quantity-?<=0 THEN 0 ELSE 1 END WHERE id=?", (quantity, quantity, listing["id"]))
            log(db, interaction.user.id, "market_buy", f"listing {listing['id']}: {quantity}x {listing['name']} for {total} XC")
            log(db, listing["seller_id"], "market_sale", f"listing {listing['id']}: sold {quantity}x {listing['name']} for {seller_payment} XC")
            db.execute(
                """INSERT INTO tier6_market_trades
                   (listing_id,buyer_id,seller_id,item_id,quantity,price_each,fee,total,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (listing["id"], interaction.user.id, listing["seller_id"], listing["item_id"], quantity, listing["price_each"], fee, total, int(time.time())),
            )
            db.commit()
            await interaction.response.send_message(view=xbot_ui.success("🛒 Market Purchase", f"Bought **{quantity}x {listing['emoji']} {listing['name']}** for **{total:,} XC**."), ephemeral=True)

    class MarketBuyButton(discord.ui.Button):
        def __init__(self, listing):
            super().__init__(label=f"Buy ({listing['price_each']:,} XC each)", emoji="🛒", style=discord.ButtonStyle.success)
            self.listing_id, self.item_name = listing["id"], listing["name"]
        async def callback(self, interaction):
            await interaction.response.send_modal(MarketBuyModal(self.listing_id, self.item_name))

    class MyListingCancelButton(discord.ui.Button):
        def __init__(self, listing):
            super().__init__(label="Cancel & Return", emoji="↩️", style=discord.ButtonStyle.danger)
            self.listing_id, self.item_name = listing["id"], listing["name"]

        async def callback(self, interaction):
            row = db.execute("SELECT * FROM market_listings WHERE id=? AND seller_id=? AND active=1", (self.listing_id, interaction.user.id)).fetchone()
            if row is None:
                await interaction.response.send_message(view=xbot_ui.warning("Listing Unavailable", "This listing is already sold or cancelled."), ephemeral=True)
                return
            db.execute("UPDATE market_listings SET active=0 WHERE id=?", (self.listing_id,))
            db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
                ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""", (interaction.user.id, row["item_id"], row["quantity"]))
            log(db, interaction.user.id, "market_cancel", f"listing {self.listing_id}: returned {row['quantity']}x {self.item_name}")
            db.commit()
            await interaction.response.send_message(view=xbot_ui.warning("↩️ Listing Cancelled", f"Returned **{row['quantity']}x {self.item_name}** to your Backpack."), ephemeral=True)

    class MyListingsView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            expire_market_listings(db)
            rows = db.execute("""SELECT l.*,i.name,i.emoji FROM market_listings l JOIN items i ON i.id=l.item_id
                WHERE l.seller_id=? AND l.active=1 AND l.quantity>0 ORDER BY l.id DESC LIMIT 10""", (owner_id,)).fetchall()
            container = discord.ui.Container(accent_color=discord.Color.orange())
            container.add_item(discord.ui.TextDisplay("## 🏷️ My Market Listings\nCancel a listing to return its unsold items to your Backpack."))
            for row in rows:
                container.add_item(discord.ui.Separator())
                text = f"### #{row['id']} · {row['emoji']} {row['name']}\n📦 **{row['quantity']} remaining** · 💰 **{row['price_each']:,} XC each**"
                container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=MyListingCancelButton(row)))
            if not rows:
                container.add_item(discord.ui.TextDisplay("You have no active listings. Use `/market_sell` to list a tradeable Backpack item."))
            container.add_item(discord.ui.ActionRow(MarketLobbyButton(owner_id)))
            container.add_item(discord.ui.TextDisplay("-# Your latest 10 active listings."))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("This market list belongs to another player.", ephemeral=True)
            return False

    class MarketLobbyButton(discord.ui.Button):
        def __init__(self, owner_id: int):
            super().__init__(label="Economy Centre", emoji="⬅️", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("This market panel belongs to another player.", ephemeral=True)
                return
            await interaction.response.edit_message(view=economy_home_view(self.owner_id))

    class MarketView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            expire_market_listings(db)
            rows = db.execute("""SELECT l.*,i.name,i.emoji,i.description,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(l.seller_id AS TEXT)) seller_name
                FROM market_listings l JOIN items i ON i.id=l.item_id LEFT JOIN players p ON p.user_id=l.seller_id
                WHERE l.active=1 AND l.quantity>0 ORDER BY l.id DESC LIMIT 10""").fetchall()
            container = discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay("## 🛒 X BOT Player Market\nBuy tradeable items listed by other players."))
            for row in rows:
                container.add_item(discord.ui.Separator())
                text = f"### {row['emoji']} {row['name']}\n📦 **{row['quantity']} available** · 👤 {row['seller_name']}\n💰 **{row['price_each']:,} XC each**"
                container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=MarketBuyButton(row)))
            if not rows:
                container.add_item(discord.ui.TextDisplay("No active listings. Open your Backpack from the Economy Centre to create one."))
            container.add_item(discord.ui.ActionRow(MarketLobbyButton(owner_id)))
            container.add_item(discord.ui.TextDisplay("-# Latest 10 listings · Market purchases use XC only."))
            self.add_item(container)

    # EconomyCentre is registered after Economy, Casino and Market.  Keep all
    # player navigation in a single message by exporting the local view factory.
    bot.xbot_player_panel_builders = getattr(bot, "xbot_player_panel_builders", {})
    bot.xbot_player_panel_builders["economy"] = lambda owner_id: EconomyCentreView(owner_id)
    bot.xbot_player_panel_builders["market"] = lambda owner_id: MarketView(owner_id)
    bot.xbot_player_lobby_builder = lambda owner_id: XBotLobbyView(owner_id)

    @bot.tree.command(name="market", description="Open the X BOT player marketplace")
    async def market(interaction: discord.Interaction):
        if not setting(db, "market_enabled"):
            await interaction.response.send_message(view=xbot_ui.warning("🛒 Market Closed", "The player market is currently closed."), ephemeral=True); return
        await interaction.response.defer()
        create_player(interaction.user)
        await interaction.edit_original_response(view=MarketView(interaction.user.id))

    @bot.tree.command(name="market_mine", description="View and cancel your own active market listings")
    async def market_mine(interaction: discord.Interaction):
        create_player(interaction.user)
        await interaction.response.send_message(view=MyListingsView(interaction.user.id), ephemeral=True)

    @bot.tree.command(name="market_sell", description="List a tradeable item on the player market")
    @app_commands.autocomplete(item=tradeable_backpack_autocomplete)
    async def market_sell(interaction: discord.Interaction, item: str, quantity: int, price_each: int):
        create_player(interaction.user)
        expire_market_listings(db)
        active_listings = int(db.execute(
            "SELECT COUNT(*) FROM market_listings WHERE seller_id=? AND active=1",
            (interaction.user.id,),
        ).fetchone()[0])
        listing_limit = max(1, tier6_setting(db, "tier6_market_max_listings", 20))
        if active_listings >= listing_limit:
            await interaction.response.send_message(
                view=xbot_ui.warning("Listing Limit Reached", f"You may have up to **{listing_limit} active listings**. Cancel or sell one first."),
                ephemeral=True,
            )
            return
        found = find_item(db, item, interaction.user.id)
        if found is None or found["quantity"] < quantity or quantity <= 0:
            await interaction.response.send_message(view=xbot_ui.danger("Listing Rejected", "You do not own that quantity."), ephemeral=True); return
        if not found["tradeable"]:
            await interaction.response.send_message(view=xbot_ui.danger("Listing Rejected", "This item is not tradeable."), ephemeral=True); return
        if not setting(db, "market_enabled") or not setting(db, "market_min_price") <= price_each <= setting(db, "market_max_price"):
            await interaction.response.send_message(view=xbot_ui.danger("Invalid Price", f"Price must be **{setting(db,'market_min_price'):,}–{setting(db,'market_max_price'):,} XC**."), ephemeral=True); return
        db.execute("UPDATE inventories SET quantity=quantity-? WHERE user_id=? AND item_id=?", (quantity, interaction.user.id, found["id"]))
        cursor = db.execute("INSERT INTO market_listings(seller_id,item_id,quantity,price_each,created_at) VALUES(?,?,?,?,?)", (interaction.user.id, found["id"], quantity, price_each, int(time.time())))
        log(db, interaction.user.id, "market_sell", f"listing {cursor.lastrowid}: {quantity}x {found['name']} at {price_each}")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🏷️ Market Listing Created", f"Listing **#{cursor.lastrowid}**: {quantity}x **{found['emoji']} {found['name']}** at **{price_each:,} XC each**."))

    @bot.tree.command(name="market_cancel", description="Cancel your market listing")
    async def market_cancel(interaction: discord.Interaction, listing_id: int):
        row = db.execute("SELECT * FROM market_listings WHERE id=? AND seller_id=? AND active=1", (listing_id, interaction.user.id)).fetchone()
        if row is None:
            await interaction.response.send_message(view=xbot_ui.danger("Listing Not Found", "That is not one of your active listings."), ephemeral=True); return
        db.execute("UPDATE market_listings SET active=0 WHERE id=?", (listing_id,))
        db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
            ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""", (interaction.user.id, row["item_id"], row["quantity"]))
        log(db, interaction.user.id, "market_cancel", f"listing {listing_id}")
        db.commit()
        await interaction.response.send_message(view=xbot_ui.warning("↩️ Listing Cancelled", f"Listing **#{listing_id}** was cancelled and its items returned."), ephemeral=True)
