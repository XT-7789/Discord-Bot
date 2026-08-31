"""Beta 1.3 configurable systems and Command Access 2.0."""
import asyncio
import time

import discord
from discord import app_commands

import xbot_ui


DEFAULTS = {
    "auction_enabled": "0",
    "bills_enabled": "1",
    "income_enabled": "1",
    "recipes_enabled": "1",
    "role_shop_enabled": "1",
    "fortify_base_cost": "250",
    "fortify_power_percent": "5",
    "fortify_max_level": "10",
}


def initialise(db):
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))
    db.execute("""CREATE TABLE IF NOT EXISTS recipes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        emoji TEXT NOT NULL DEFAULT '🧪', description TEXT NOT NULL DEFAULT '',
        output_item_id INTEGER NOT NULL, output_quantity INTEGER NOT NULL DEFAULT 1,
        xc_cost INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS recipe_ingredients(
        recipe_id INTEGER NOT NULL, item_id INTEGER NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(recipe_id,item_id)
    )""")
    # Starter recipes make mined materials useful immediately.  They are only
    # inserted when missing, so Dashboard changes made by administrators stay
    # untouched on later restarts.
    starter_recipes = (
        ("Field Repair Kit", "🧰", "Craft a Capital Repair Kit from basic mine materials.",
         "Capital Repair Kit", 1, 30, (("Stone", 8), ("Copper", 3), ("Iron", 4))),
        ("War Supply Crate", "📦", "Convert mine materials into one War Supply Crate.",
         "War Supply Crate", 1, 150, (("Stone", 12), ("Coal", 6), ("Iron", 6), ("Copper", 4))),
        ("XC Voucher", "🎟️", "Craft one voucher that can be redeemed for XC.",
         "XC Voucher", 1, 10, (("Stone", 10), ("Coal", 5), ("Copper", 3))),
    )
    for recipe_name, emoji, description, output_name, output_quantity, xc_cost, ingredients in starter_recipes:
        output = db.execute("SELECT id FROM items WHERE name=? COLLATE NOCASE", (output_name,)).fetchone()
        if output is None:
            continue
        db.execute("""INSERT OR IGNORE INTO recipes
            (name,emoji,description,output_item_id,output_quantity,xc_cost,enabled)
            VALUES(?,?,?,?,?,?,1)""",
            (recipe_name, emoji, description, output["id"], output_quantity, xc_cost))
        recipe = db.execute("SELECT id FROM recipes WHERE name=? COLLATE NOCASE", (recipe_name,)).fetchone()
        if recipe is None:
            continue
        for item_name, quantity in ingredients:
            ingredient = db.execute("SELECT id FROM items WHERE name=? COLLATE NOCASE", (item_name,)).fetchone()
            if ingredient is not None:
                db.execute("""INSERT OR IGNORE INTO recipe_ingredients(recipe_id,item_id,quantity)
                    VALUES(?,?,?)""", (recipe["id"], ingredient["id"], quantity))
    db.execute("""CREATE TABLE IF NOT EXISTS bills(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        emoji TEXT NOT NULL DEFAULT '🧾', amount INTEGER NOT NULL DEFAULT 100,
        interval_seconds INTEGER NOT NULL DEFAULT 86400, enabled INTEGER NOT NULL DEFAULT 1
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS player_bill_state(
        user_id INTEGER NOT NULL, bill_id INTEGER NOT NULL, last_paid INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id,bill_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS income_sources(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        emoji TEXT NOT NULL DEFAULT '💵', amount INTEGER NOT NULL DEFAULT 100,
        interval_seconds INTEGER NOT NULL DEFAULT 86400, enabled INTEGER NOT NULL DEFAULT 1
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS player_income_state(
        user_id INTEGER NOT NULL, income_id INTEGER NOT NULL, last_collected INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id,income_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS role_shop(
        id INTEGER PRIMARY KEY AUTOINCREMENT, role_id TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT '🎭', description TEXT NOT NULL DEFAULT '',
        price INTEGER NOT NULL DEFAULT 1000, currency TEXT NOT NULL DEFAULT 'xc',
        stock INTEGER NOT NULL DEFAULT -1, enabled INTEGER NOT NULL DEFAULT 1
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS command_permissions(
        command_name TEXT PRIMARY KEY, access_mode TEXT NOT NULL DEFAULT 'everyone',
        allowed_role_ids TEXT NOT NULL DEFAULT '', allowed_user_ids TEXT NOT NULL DEFAULT ''
    )""")
    command_columns = {row["name"] for row in db.execute("PRAGMA table_info(command_permissions)")}
    for name, definition in {
        "enabled": "INTEGER NOT NULL DEFAULT 1", "blocked_role_ids": "TEXT NOT NULL DEFAULT ''",
        "allowed_channel_ids": "TEXT NOT NULL DEFAULT ''", "blocked_channel_ids": "TEXT NOT NULL DEFAULT ''",
        "cooldown_seconds": "INTEGER NOT NULL DEFAULT 0", "cooldown_scope": "TEXT NOT NULL DEFAULT 'user'",
        "cooldown_bypass_role_ids": "TEXT NOT NULL DEFAULT ''", "required_permissions": "TEXT NOT NULL DEFAULT ''",
        "log_usage": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in command_columns:
            db.execute(f"ALTER TABLE command_permissions ADD COLUMN {name} {definition}")
    db.execute("""CREATE TABLE IF NOT EXISTS command_usage_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,command_name TEXT NOT NULL,user_id INTEGER NOT NULL,
        guild_id INTEGER NOT NULL DEFAULT 0,channel_id INTEGER NOT NULL DEFAULT 0,
        allowed INTEGER NOT NULL,reason TEXT NOT NULL,created_at INTEGER NOT NULL,
        cooldown_key TEXT NOT NULL DEFAULT ''
    )""")
    if "cooldown_key" not in {row["name"] for row in db.execute("PRAGMA table_info(command_usage_logs)")}:
        db.execute("ALTER TABLE command_usage_logs ADD COLUMN cooldown_key TEXT NOT NULL DEFAULT ''")
    # Tier 1.6: one-use reward codes.  These are intentionally part of the
    # main X BOT database, so a reward cannot be redeemed twice after a restart.
    db.execute("""CREATE TABLE IF NOT EXISTS reward_codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        reward_xc INTEGER NOT NULL DEFAULT 0,
        reward_war_credits INTEGER NOT NULL DEFAULT 0,
        reward_xcrystals INTEGER NOT NULL DEFAULT 0,
        item_id INTEGER,
        item_quantity INTEGER NOT NULL DEFAULT 0,
        max_uses INTEGER NOT NULL DEFAULT 0,
        uses INTEGER NOT NULL DEFAULT 0,
        expires_at INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS reward_code_redemptions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        redeemed_at INTEGER NOT NULL,
        UNIQUE(code_id,user_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS log_settings(
        log_type TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,channel_id TEXT NOT NULL DEFAULT '0',
        log_success INTEGER NOT NULL DEFAULT 1,log_failure INTEGER NOT NULL DEFAULT 1,
        mention_role_id TEXT NOT NULL DEFAULT ''
    )""")
    for log_type in ("command", "economy", "job", "shop", "market", "war", "dashboard", "error"):
        db.execute("INSERT OR IGNORE INTO log_settings(log_type) VALUES(?)", (log_type,))
    war_columns = {row["name"] for row in db.execute("PRAGMA table_info(player_war_settings)")}
    if "fortification_level" not in war_columns:
        db.execute("ALTER TABLE player_war_settings ADD COLUMN fortification_level INTEGER NOT NULL DEFAULT 0")
    if "morale" not in war_columns:
        db.execute("ALTER TABLE player_war_settings ADD COLUMN morale INTEGER NOT NULL DEFAULT 100")
    db.commit()


def setting(db, key):
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return int(row["value"] if row else DEFAULTS[key])


def seed_command_permissions(db, commands):
    db.executemany(
        "INSERT OR IGNORE INTO command_permissions(command_name) VALUES(?)",
        [(command.name,) for command in commands],
    )
    # Safe Beta 1.1 defaults: Council may grant items; destructive economy tools stay admin-only.
    marker = db.execute("SELECT value FROM economy_settings WHERE key='beta_1_1_admin_permissions_seeded'").fetchone()
    if marker is None:
        db.execute("UPDATE command_permissions SET access_mode='roles',allowed_role_ids='1523954152701431848' WHERE command_name='spawn'")
        db.execute("UPDATE command_permissions SET access_mode='admin' WHERE command_name IN ('remove_item','economy_adjust')")
        db.execute("INSERT INTO economy_settings(key,value) VALUES('beta_1_1_admin_permissions_seeded','1')")
    marker_12 = db.execute("SELECT value FROM economy_settings WHERE key='beta_1_2_admin_permissions_seeded'").fetchone()
    if marker_12 is None:
        db.execute("UPDATE command_permissions SET access_mode='admin' WHERE command_name='job_log_channel'")
        db.execute("INSERT INTO economy_settings(key,value) VALUES('beta_1_2_admin_permissions_seeded','1')")
    marker_16 = db.execute("SELECT value FROM economy_settings WHERE key='beta_1_6_staff_permissions_seeded'").fetchone()
    if marker_16 is None:
        staff_roles = '1523954152701431848,1531176720097476708'  # X Council + Administration
        db.execute("""UPDATE command_permissions SET access_mode='roles',allowed_role_ids=?
            WHERE command_name IN ('inrole','role','code_create','code_disable','code_list')""", (staff_roles,))
        db.execute("INSERT INTO economy_settings(key,value) VALUES('beta_1_6_staff_permissions_seeded','1')")
    db.commit()


async def command_access_check(interaction, db):
    command = getattr(interaction, "command", None)
    if command is None:
        return True
    row = db.execute("SELECT * FROM command_permissions WHERE command_name=?", (command.name,)).fetchone()
    if row is None:
        return True
    role_ids = {str(role.id) for role in getattr(interaction.user, "roles", [])}
    user_id = str(interaction.user.id); channel_id = str(interaction.channel_id or 0); guild_id = interaction.guild_id or 0
    permissions = getattr(interaction.user, "guild_permissions", None)
    is_admin = bool(permissions and permissions.administrator)
    # Moderators should be able to use the safe staff role tools without
    # needing to be made Council or Administration. Economy-changing and
    # destructive commands deliberately remain restricted to their Dashboard
    # permissions / senior staff roles.
    is_moderator = bool(permissions and getattr(permissions, "moderate_members", False))
    moderator_tool = command.name in {"inrole", "role"}
    bypass = bool(role_ids & _csv(row["cooldown_bypass_role_ids"])) or is_admin
    denial = None
    if not row["enabled"]:
        denial = "This command is currently disabled."
    elif not is_admin and role_ids & _csv(row["blocked_role_ids"]):
        denial = "One of your roles is blocked from this command."
    elif not is_admin and _csv(row["allowed_channel_ids"]) and channel_id not in _csv(row["allowed_channel_ids"]):
        denial = "This command cannot be used in this channel."
    elif not is_admin and channel_id in _csv(row["blocked_channel_ids"]):
        denial = "This command is blocked in this channel."
    elif not is_admin and row["required_permissions"]:
        missing = [name for name in _csv(row["required_permissions"]) if not getattr(permissions, name, False)]
        if missing: denial = "Missing Discord permission: " + ", ".join(missing)
    allowed = False
    if denial is None:
        if is_admin or row["access_mode"] == "everyone": allowed = True
        elif row["access_mode"] == "users": allowed = user_id in _csv(row["allowed_user_ids"])
        elif row["access_mode"] == "roles":
            allowed = moderator_tool and is_moderator or bool(role_ids & _csv(row["allowed_role_ids"]))
        elif row["access_mode"] == "admin": allowed = False
        if not allowed: denial = "You do not have permission to use this command."
    if allowed and row["cooldown_seconds"] > 0 and not bypass:
        scope = row["cooldown_scope"] if row["cooldown_scope"] in {"user", "guild", "global"} else "user"
        cooldown_key = f"user:{interaction.user.id}" if scope == "user" else f"guild:{guild_id}" if scope == "guild" else "global"
        previous = db.execute("""SELECT created_at FROM command_usage_logs WHERE command_name=? AND allowed=1
            AND cooldown_key=? ORDER BY id DESC LIMIT 1""", (command.name, cooldown_key)).fetchone()
        remaining = row["cooldown_seconds"] - (int(time.time()) - previous["created_at"]) if previous else 0
        if remaining > 0: allowed = False; denial = f"Command cooldown: try again in {remaining} seconds."
    scope = row["cooldown_scope"] if row["cooldown_scope"] in {"user", "guild", "global"} else "user"
    cooldown_key = f"user:{interaction.user.id}" if scope == "user" else f"guild:{guild_id}" if scope == "guild" else "global"
    db.execute("INSERT INTO command_usage_logs(command_name,user_id,guild_id,channel_id,allowed,reason,created_at,cooldown_key) VALUES(?,?,?,?,?,?,?,?)",
        (command.name, interaction.user.id, guild_id, interaction.channel_id or 0, int(allowed), "allowed" if allowed else denial, int(time.time()), cooldown_key))
    db.commit()
    if row["log_usage"]:
        settings = db.execute("SELECT * FROM log_settings WHERE log_type='command'").fetchone()
        should_send = settings and settings["enabled"] and settings["channel_id"] != "0" and ((allowed and settings["log_success"]) or (not allowed and settings["log_failure"]))
        if should_send:
            channel = interaction.client.get_channel(int(settings["channel_id"]))
            if channel:
                mention = f"<@&{settings['mention_role_id']}> " if settings["mention_role_id"] else ""
                async def send_usage_log():
                    try:
                        await channel.send(f"{mention}🔐 **/{command.name}** · <@{interaction.user.id}> · {'Allowed' if allowed else 'Denied'} · <#{interaction.channel_id}>\n`{'allowed' if allowed else denial}`")
                    except discord.HTTPException:
                        pass

                # Discord requires an application command acknowledgement in
                # about three seconds. Logging must never delay the command.
                asyncio.create_task(send_usage_log())
    if not allowed:
        message = xbot_ui.danger("🔒 Command Restricted", denial)
        if interaction.response.is_done():
            await interaction.followup.send(view=message, ephemeral=True)
        else:
            await interaction.response.send_message(view=message, ephemeral=True)
    return allowed


def _csv(value):
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _inventory_quantity(db, user_id, item_id):
    row = db.execute("SELECT quantity FROM inventories WHERE user_id=? AND item_id=?", (user_id, item_id)).fetchone()
    return row["quantity"] if row else 0


def register_commands(bot, db, create_player):
    async def recipe_autocomplete(interaction: discord.Interaction, current: str):
        rows = db.execute("SELECT name,emoji FROM recipes WHERE enabled=1 AND name LIKE ? ORDER BY name LIMIT 25", (f"%{current.strip()}%",)).fetchall()
        return [app_commands.Choice(name=f"{row['emoji']} {row['name']}"[:100], value=row["name"]) for row in rows]

    async def bill_autocomplete(interaction: discord.Interaction, current: str):
        rows = db.execute("SELECT name,emoji FROM bills WHERE enabled=1 AND name LIKE ? ORDER BY name LIMIT 25", (f"%{current.strip()}%",)).fetchall()
        return [app_commands.Choice(name=f"{row['emoji']} {row['name']}"[:100], value=row["name"]) for row in rows]

    async def role_offer_autocomplete(interaction: discord.Interaction, current: str):
        rows = db.execute("SELECT name,emoji,price,currency FROM role_shop WHERE enabled=1 AND stock<>0 AND name LIKE ? ORDER BY price,name LIMIT 25", (f"%{current.strip()}%",)).fetchall()
        return [app_commands.Choice(name=f"{row['emoji']} {row['name']} · {row['price']:,} {'XC' if row['currency']=='xc' else 'XCrystals'}"[:100], value=row["name"]) for row in rows]

    def recipe_rows():
        return db.execute("""SELECT r.*,o.name output_name,o.emoji output_emoji
            FROM recipes r JOIN items o ON o.id=r.output_item_id
            WHERE r.enabled=1 ORDER BY r.name LIMIT 25""").fetchall()

    def recipe_ingredients(recipe_id):
        return db.execute("""SELECT g.*,i.name,i.emoji FROM recipe_ingredients g
            JOIN items i ON i.id=g.item_id WHERE g.recipe_id=? ORDER BY i.name""", (recipe_id,)).fetchall()

    def max_craftable(user_id, recipe, ingredients):
        limits = []
        for ingredient in ingredients:
            limits.append(_inventory_quantity(db, user_id, ingredient["item_id"]) // ingredient["quantity"])
        player = db.execute("SELECT xc FROM players WHERE user_id=?", (user_id,)).fetchone()
        if recipe["xc_cost"] > 0:
            limits.append((player["xc"] if player else 0) // recipe["xc_cost"])
        return min(limits) if limits else 100

    def perform_craft(user_id, recipe, amount):
        ingredients = recipe_ingredients(recipe["id"])
        player = db.execute("SELECT xc FROM players WHERE user_id=?", (user_id,)).fetchone()
        total_cost = recipe["xc_cost"] * amount
        missing = [
            f"{item['emoji']} {item['name']} ×{item['quantity'] * amount}"
            for item in ingredients
            if _inventory_quantity(db, user_id, item["item_id"]) < item["quantity"] * amount
        ]
        if missing:
            return False, "Missing materials: " + ", ".join(missing)
        if player is None or player["xc"] < total_cost:
            return False, f"You need **{total_cost:,} XC**."
        for ingredient in ingredients:
            db.execute("UPDATE inventories SET quantity=quantity-? WHERE user_id=? AND item_id=?",
                (ingredient["quantity"] * amount, user_id, ingredient["item_id"]))
        db.execute("DELETE FROM inventories WHERE user_id=? AND quantity<=0", (user_id,))
        db.execute("UPDATE players SET xc=xc-? WHERE user_id=?", (total_cost, user_id))
        db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
            ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
            (user_id, recipe["output_item_id"], recipe["output_quantity"] * amount))
        db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
            (user_id, "craft", f"{amount}x {recipe['name']}", int(time.time())))
        db.commit()
        return True, f"Created **{recipe['output_quantity'] * amount}× {recipe['output_emoji']} {recipe['output_name']}**."

    class CraftNavigationButton(discord.ui.Button):
        def __init__(self, owner_id, destination, label, emoji):
            super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id; self.destination = destination

        async def callback(self, interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open your own panel with `/craft`.", ephemeral=True); return
            if self.destination == "lobby":
                builder = getattr(bot, "xbot_player_lobby_builder", None)
            else:
                builder = getattr(bot, "xbot_player_panel_builders", {}).get(self.destination)
            if builder is None:
                await interaction.response.send_message("This panel is loading. Try again in a moment.", ephemeral=True); return
            await interaction.response.edit_message(view=builder(self.owner_id))

    class RecipeSelect(discord.ui.Select):
        def __init__(self, owner_id, selected_id=None):
            rows = recipe_rows()
            options = [discord.SelectOption(
                label=row["name"][:100], value=str(row["id"]), emoji=row["emoji"],
                description=f"Makes {row['output_quantity']}× {row['output_name']}"[:100],
                default=row["id"] == selected_id,
            ) for row in rows]
            super().__init__(placeholder="Choose a recipe…", min_values=1, max_values=1, options=options)
            self.owner_id = owner_id

        async def callback(self, interaction):
            await interaction.response.edit_message(view=CraftingCentreView(self.owner_id, int(self.values[0])))

    class CraftAmountButton(discord.ui.Button):
        def __init__(self, owner_id, recipe_id, amount, label, disabled=False):
            super().__init__(label=label, emoji="🧪", style=discord.ButtonStyle.success, disabled=disabled)
            self.owner_id = owner_id; self.recipe_id = recipe_id; self.amount = amount

        async def callback(self, interaction):
            recipe = db.execute("""SELECT r.*,o.name output_name,o.emoji output_emoji FROM recipes r
                JOIN items o ON o.id=r.output_item_id WHERE r.id=? AND r.enabled=1""", (self.recipe_id,)).fetchone()
            if recipe is None:
                await interaction.response.edit_message(view=CraftingCentreView(self.owner_id, notice="❌ This recipe is no longer available.")); return
            amount = max_craftable(self.owner_id, recipe, recipe_ingredients(recipe["id"])) if self.amount == 0 else self.amount
            await interaction.response.edit_message(view=CraftConfirmView(self.owner_id, recipe["id"], max(1, min(amount, 100))))

    class CraftConfirmButton(discord.ui.Button):
        def __init__(self, owner_id, recipe_id, amount):
            super().__init__(label="Confirm Craft", emoji="✅", style=discord.ButtonStyle.success)
            self.owner_id = owner_id; self.recipe_id = recipe_id; self.amount = amount

        async def callback(self, interaction):
            recipe = db.execute("""SELECT r.*,o.name output_name,o.emoji output_emoji FROM recipes r
                JOIN items o ON o.id=r.output_item_id WHERE r.id=? AND r.enabled=1""", (self.recipe_id,)).fetchone()
            if recipe is None:
                await interaction.response.edit_message(view=CraftingCentreView(self.owner_id, notice="❌ This recipe is no longer available.")); return
            ok, message = perform_craft(self.owner_id, recipe, self.amount)
            await interaction.response.edit_message(view=CraftingCentreView(self.owner_id, recipe["id"], ("✅ " if ok else "❌ ") + message))

    class CraftBackButton(discord.ui.Button):
        def __init__(self, owner_id, recipe_id):
            super().__init__(label="Back to Recipes", emoji="⬅️", style=discord.ButtonStyle.secondary)
            self.owner_id = owner_id; self.recipe_id = recipe_id
        async def callback(self, interaction):
            await interaction.response.edit_message(view=CraftingCentreView(self.owner_id, self.recipe_id))

    class CraftConfirmView(discord.ui.LayoutView):
        def __init__(self, owner_id, recipe_id, amount):
            super().__init__(timeout=300); self.owner_id = owner_id
            recipe = db.execute("""SELECT r.*,o.name output_name,o.emoji output_emoji FROM recipes r
                JOIN items o ON o.id=r.output_item_id WHERE r.id=?""", (recipe_id,)).fetchone()
            ingredients = recipe_ingredients(recipe_id)
            material_lines = "\n".join(
                f"{i['emoji']} {i['name']}: **{i['quantity'] * amount} needed** · {_inventory_quantity(db, owner_id, i['item_id'])} owned"
                for i in ingredients) or "No materials required."
            container = discord.ui.Container(accent_color=discord.Color.orange())
            container.add_item(discord.ui.TextDisplay(
                f"## 🧪 Confirm Craft\n{recipe['emoji']} **{recipe['name']} ×{amount}**\n"
                f"Creates: **{recipe['output_quantity'] * amount}× {recipe['output_emoji']} {recipe['output_name']}**\n\n"
                f"### Materials\n{material_lines}\n🪙 XC cost: **{recipe['xc_cost'] * amount:,} XC**\n\n-# Nothing is spent until you press Confirm Craft."
            ))
            container.add_item(discord.ui.ActionRow(CraftConfirmButton(owner_id, recipe_id, amount), CraftBackButton(owner_id, recipe_id)))
            self.add_item(container)

    class CraftingCentreView(discord.ui.LayoutView):
        def __init__(self, owner_id, selected_id=None, notice=None):
            super().__init__(timeout=300); self.owner_id = owner_id
            rows = recipe_rows()
            if selected_id is None and rows: selected_id = rows[0]["id"]
            recipe = next((row for row in rows if row["id"] == selected_id), rows[0] if rows else None)
            container = discord.ui.Container(accent_color=discord.Color.teal())
            if recipe is None:
                container.add_item(discord.ui.TextDisplay("## 🧪 X BOT Crafting Centre\nNo recipes are enabled right now."))
            else:
                ingredients = recipe_ingredients(recipe["id"])
                maximum = min(100, max_craftable(owner_id, recipe, ingredients))
                material_lines = "\n".join(
                    f"{i['emoji']} **{i['name']}** · need {i['quantity']} · own {_inventory_quantity(db, owner_id, i['item_id'])}"
                    for i in ingredients) or "No materials required."
                container.add_item(discord.ui.TextDisplay(
                    f"## 🧪 X BOT Crafting Centre\n"
                    f"{recipe['emoji']} **{recipe['name']}** → {recipe['output_emoji']} **{recipe['output_name']} ×{recipe['output_quantity']}**\n"
                    f"{recipe['description'] or 'Craft this item from your collected materials.'}\n\n"
                    f"### Recipe\n{material_lines}\n🪙 Cost: **{recipe['xc_cost']:,} XC each** · Craftable now: **{maximum}**"
                ))
                container.add_item(discord.ui.ActionRow(RecipeSelect(owner_id, recipe["id"])))
                container.add_item(discord.ui.ActionRow(
                    CraftAmountButton(owner_id, recipe["id"], 1, "Craft 1", maximum < 1),
                    CraftAmountButton(owner_id, recipe["id"], 5, "Craft 5", maximum < 5),
                    CraftAmountButton(owner_id, recipe["id"], 0, "Craft Max", maximum < 1),
                ))
            if notice: container.add_item(discord.ui.TextDisplay(f"-# {notice}"))
            container.add_item(discord.ui.ActionRow(
                CraftNavigationButton(owner_id, "economy", "Economy", "💰"),
                CraftNavigationButton(owner_id, "lobby", "Lobby", "✨"),
            ))
            self.add_item(container)

        async def interaction_check(self, interaction):
            if interaction.user.id == self.owner_id: return True
            await interaction.response.send_message("Open your own Crafting Centre with `/craft`.", ephemeral=True); return False

    @bot.tree.command(name="recipes", description="View craftable X BOT recipes")
    async def recipes(interaction: discord.Interaction):
        if not setting(db, "recipes_enabled"):
            await interaction.response.send_message(view=xbot_ui.warning("🧪 Recipes Closed", "Crafting is currently disabled."), ephemeral=True); return
        rows = db.execute("""SELECT r.*,o.name output_name,o.emoji output_emoji,
            GROUP_CONCAT(i.emoji||' '||i.name||' ×'||g.quantity, ', ') ingredients
            FROM recipes r JOIN items o ON o.id=r.output_item_id
            LEFT JOIN recipe_ingredients g ON g.recipe_id=r.id LEFT JOIN items i ON i.id=g.item_id
            WHERE r.enabled=1 GROUP BY r.id ORDER BY r.name""").fetchall()
        body = "\n\n".join(f"{r['emoji']} **{r['name']}** → {r['output_emoji']} {r['output_name']} ×{r['output_quantity']}\n📦 {r['ingredients'] or 'No materials'} · 🪙 {r['xc_cost']:,} XC" for r in rows) or "No recipes are enabled."
        await interaction.response.send_message(view=xbot_ui.panel("🧪 X BOT Recipes", body, colour=discord.Color.teal(), footer="Craft with /craft recipe:<name> amount:<number>"))

    @bot.tree.command(name="craft", description="Open the interactive X BOT Crafting Centre")
    async def craft(interaction: discord.Interaction):
        if not setting(db, "recipes_enabled"):
            await interaction.response.send_message(view=xbot_ui.warning("🧪 Recipes Closed", "Crafting is currently disabled."), ephemeral=True); return
        create_player(interaction.user)
        await interaction.response.send_message(view=CraftingCentreView(interaction.user.id))

    bot.xbot_player_panel_builders = getattr(bot, "xbot_player_panel_builders", {})
    bot.xbot_player_panel_builders["craft"] = lambda owner_id: CraftingCentreView(owner_id)

    @bot.tree.command(name="bills", description="View your recurring X BOT bills")
    async def bills(interaction: discord.Interaction):
        if not setting(db, "bills_enabled"):
            await interaction.response.send_message(view=xbot_ui.warning("🧾 Bills Disabled", "The bill system is currently disabled."), ephemeral=True); return
        create_player(interaction.user); now = int(time.time())
        rows = db.execute("""SELECT b.*,s.last_paid FROM bills b LEFT JOIN player_bill_state s
            ON s.bill_id=b.id AND s.user_id=? WHERE b.enabled=1 ORDER BY b.name""", (interaction.user.id,)).fetchall()
        lines = []
        for row in rows:
            due_at = (row["last_paid"] or 0) + row["interval_seconds"]
            state = "**DUE NOW**" if not row["last_paid"] or now >= due_at else f"due <t:{due_at}:R>"
            lines.append(f"{row['emoji']} **{row['name']}** · {row['amount']:,} XC · {state}")
        await interaction.response.send_message(view=xbot_ui.panel("🧾 Your Bills", "\n".join(lines) or "No active bills.", colour=discord.Color.orange(), footer="Pay with /bill_pay bill:<name>"))

    @bot.tree.command(name="bill_pay", description="Pay one due X BOT bill")
    @app_commands.autocomplete(bill=bill_autocomplete)
    async def bill_pay(interaction: discord.Interaction, bill: str):
        if not setting(db, "bills_enabled"):
            await interaction.response.send_message(view=xbot_ui.warning("Bills Disabled", "The bill system is disabled."), ephemeral=True); return
        player = create_player(interaction.user); now = int(time.time())
        row = db.execute("SELECT * FROM bills WHERE name=? COLLATE NOCASE AND enabled=1", (bill.strip(),)).fetchone()
        if row is None:
            await interaction.response.send_message(view=xbot_ui.danger("Bill Not Found", "Use `/bills` to view active bills."), ephemeral=True); return
        state = db.execute("SELECT last_paid FROM player_bill_state WHERE user_id=? AND bill_id=?", (interaction.user.id,row["id"])).fetchone()
        if state and now < state["last_paid"] + row["interval_seconds"]:
            await interaction.response.send_message(view=xbot_ui.warning("Already Paid", f"This bill is next due <t:{state['last_paid']+row['interval_seconds']}:R>."), ephemeral=True); return
        if player["xc"] < row["amount"]:
            await interaction.response.send_message(view=xbot_ui.danger("Not Enough XC", f"This bill costs **{row['amount']:,} XC**."), ephemeral=True); return
        db.execute("UPDATE players SET xc=xc-? WHERE user_id=?", (row["amount"],interaction.user.id))
        db.execute("""INSERT INTO player_bill_state(user_id,bill_id,last_paid) VALUES(?,?,?)
            ON CONFLICT(user_id,bill_id) DO UPDATE SET last_paid=excluded.last_paid""", (interaction.user.id,row["id"],now))
        db.commit(); await interaction.response.send_message(view=xbot_ui.success("🧾 Bill Paid", f"Paid **{row['amount']:,} XC** for **{row['name']}**."))

    @bot.tree.command(name="income", description="Collect all available configured income")
    async def income(interaction: discord.Interaction):
        if not setting(db, "income_enabled"):
            await interaction.response.send_message(view=xbot_ui.warning("Income Disabled", "The income system is disabled."), ephemeral=True); return
        create_player(interaction.user); now = int(time.time()); collected = []
        rows = db.execute("""SELECT i.*,s.last_collected FROM income_sources i LEFT JOIN player_income_state s
            ON s.income_id=i.id AND s.user_id=? WHERE i.enabled=1 ORDER BY i.name""", (interaction.user.id,)).fetchall()
        for row in rows:
            if not row["last_collected"] or now >= row["last_collected"] + row["interval_seconds"]:
                collected.append(row)
                db.execute("""INSERT INTO player_income_state(user_id,income_id,last_collected) VALUES(?,?,?)
                    ON CONFLICT(user_id,income_id) DO UPDATE SET last_collected=excluded.last_collected""", (interaction.user.id,row["id"],now))
        total = sum(row["amount"] for row in collected)
        if not total:
            await interaction.response.send_message(view=xbot_ui.warning("💵 Income", "No income is ready to collect."), ephemeral=True); return
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (total,interaction.user.id)); db.commit()
        detail = "\n".join(f"{r['emoji']} {r['name']}: **{r['amount']:,} XC**" for r in collected)
        await interaction.response.send_message(view=xbot_ui.success("💵 Income Collected", f"{detail}\n\nTotal: **{total:,} XC**"))

    async def purchase_role(interaction: discord.Interaction, offer):
        """Single purchase path for both the slash command and shop buttons."""
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(view=xbot_ui.danger("Server Only", "Role purchases only work inside the Discord server."), ephemeral=True); return
        if not setting(db, "role_shop_enabled"):
            await interaction.response.send_message(view=xbot_ui.warning("Role Shop Closed", "The Role Shop is currently closed."), ephemeral=True); return
        if offer is None or not offer["enabled"] or offer["stock"] == 0:
            await interaction.response.send_message(view=xbot_ui.danger("Role Unavailable", "This role is no longer for sale."), ephemeral=True); return
        discord_role = interaction.guild.get_role(int(offer["role_id"]))
        if discord_role is None:
            await interaction.response.send_message(view=xbot_ui.danger("Role Unavailable", "The Dashboard role setting no longer matches a server role."), ephemeral=True); return
        if discord_role in interaction.user.roles:
            await interaction.response.send_message(view=xbot_ui.warning("Already Owned", "You already have this role."), ephemeral=True); return
        player = create_player(interaction.user); currency = offer["currency"] if offer["currency"] in {"xc", "xcrystals"} else "xc"
        currency_label = "XC" if currency == "xc" else "XCrystals"
        if player[currency] < offer["price"]:
            await interaction.response.send_message(view=xbot_ui.danger("Not Enough Currency", f"You need **{offer['price']:,} {currency_label}**."), ephemeral=True); return
        try:
            await interaction.user.add_roles(discord_role, reason="Purchased from X BOT Role Shop")
        except discord.Forbidden:
            await interaction.response.send_message(view=xbot_ui.danger("Role Delivery Failed", "Move the X BOT role above the role being sold and enable Manage Roles."), ephemeral=True); return
        db.execute(f"UPDATE players SET {currency}={currency}-? WHERE user_id=?", (offer["price"],interaction.user.id))
        if offer["stock"] > 0:
            db.execute("UPDATE role_shop SET stock=stock-1 WHERE id=?", (offer["id"],))
        db.commit()
        await interaction.response.send_message(view=xbot_ui.success("🎭 Role Purchased", f"You received **{discord_role.name}** for **{offer['price']:,} {currency_label}**."), ephemeral=True)

    class RoleShopBuyButton(discord.ui.Button):
        def __init__(self, offer):
            label = f"Buy ({offer['price']:,} {'XC' if offer['currency']=='xc' else 'XCrystals'})"
            super().__init__(label=label[:80], emoji="🛍️", style=discord.ButtonStyle.success)
            self.offer_id = offer["id"]

        async def callback(self, interaction: discord.Interaction):
            offer = db.execute("SELECT * FROM role_shop WHERE id=?", (self.offer_id,)).fetchone()
            await purchase_role(interaction, offer)

    class RoleShopView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=300)
            rows = db.execute("SELECT * FROM role_shop WHERE enabled=1 AND stock<>0 ORDER BY price,name LIMIT 10").fetchall()
            container = discord.ui.Container(accent_color=discord.Color.purple())
            container.add_item(discord.ui.TextDisplay("## 🎭 X BOT Role Shop\nClick **Buy** to purchase a role using your X BOT balance."))
            for row in rows:
                container.add_item(discord.ui.Separator())
                currency = "XC" if row["currency"] == "xc" else "XCrystals"
                stock = "Unlimited" if row["stock"] < 0 else str(row["stock"])
                text = f"### {row['emoji']} {row['name']}\n💰 **{row['price']:,} {currency}** · 📦 **{stock}**\n{row['description'] or 'No description provided.'}"
                container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=RoleShopBuyButton(row)))
            if not rows:
                container.add_item(discord.ui.TextDisplay("No roles are currently for sale."))
            container.add_item(discord.ui.TextDisplay("-# Showing up to 10 roles · `/role_buy` remains available as a backup."))
            self.add_item(container)

    @bot.tree.command(name="role_shop", description="View Discord roles sold by X BOT")
    async def role_shop(interaction: discord.Interaction):
        if not setting(db, "role_shop_enabled"):
            await interaction.response.send_message(view=xbot_ui.warning("🎭 Role Shop Closed", "The Role Shop is currently closed."), ephemeral=True); return
        await interaction.response.send_message(view=RoleShopView())

    @bot.tree.command(name="role_buy", description="Buy a Discord role from X BOT")
    @app_commands.autocomplete(role=role_offer_autocomplete)
    async def role_buy(interaction: discord.Interaction, role: str):
        offer = db.execute("SELECT * FROM role_shop WHERE name=? COLLATE NOCASE AND enabled=1 AND stock<>0", (role.strip(),)).fetchone()
        if offer is None:
            await interaction.response.send_message(view=xbot_ui.danger("Role Unavailable", "Use `/role_shop` to view available roles."), ephemeral=True); return
        await purchase_role(interaction, offer)
