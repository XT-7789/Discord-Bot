"""Single-entry Discord control centre for X BOT staff."""

import sqlite3
import time

import discord
from discord import app_commands

import applications
import tester_feedback
import gaming
import leveling
import war_system
import deadzone


PAGES = {
    'home': 'Control Centre', 'panels': 'Post Panels', 'members': 'Members', 'assets': 'Player Assets',
    'deadzone': 'Deadzone', 'server': 'Server Tools', 'war_tools': 'Armed Forces',
    'applications': 'Applications', 'tester': 'Tester Reports', 'verification': 'Verification',
    'codes': 'Reward Codes', 'economy': 'Economy Status', 'maintenance': 'Maintenance',
}
TOOL_GROUPS = {'members': 'Members', 'assets': 'Assets', 'deadzone': 'Deadzone', 'server': 'Server', 'war_tools': 'War'}

PAGE_METADATA = {
    'home': ('🏠', 'Control Centre', 'Main staff overview & navigation hub'),
    'panels': ('📢', 'Post Panels', 'Deploy Verification, Application, Deadzone, Lounge, Gaming'),
    'server': ('🔔', 'Server Tools', 'Post /say announcements and manage level-up notices'),
    'codes': ('🎟️', 'Reward Codes', 'Create and manage gift redemption codes'),
    'verification': ('🛡️', 'Verification', 'Role mapping and verification status toggle'),
    'members': ('👥', 'Members', 'Give/remove roles, level sync, and inspect members'),
    'assets': ('💰', 'Player Assets', 'Edit money, bank, cash, backpack items and inventory'),
    'war_tools': ('⚔️', 'Armed Forces', 'Inspect military, troops, capital health & resources'),
    'deadzone': ('💀', 'Deadzone', 'Send to Deadzone, revive members, scan inactive (7d)'),
    'applications': ('📝', 'Applications', 'Review pending staff and role applications'),
    'tester': ('🧪', 'Tester Reports', 'Review bug reports and issue tester rewards'),
    'economy': ('📊', 'Economy Status', 'Read-only live economy inflation and financial metrics'),
    'maintenance': ('🛠️', 'Maintenance', 'System diagnostics, database backups, and data repair'),
}


def code_status(row):
    if not row['enabled']:
        return 'Disabled'
    if row['max_uses'] and row['uses'] >= row['max_uses']:
        return 'Fully redeemed'
    return 'Ready to redeem'


class AdminPageSelect(discord.ui.Select):
    def __init__(self, page):
        options = [
            discord.SelectOption(
                emoji=PAGE_METADATA.get(key, ('⚙️', ''))[0],
                label=PAGES[key],
                value=key,
                description=PAGE_METADATA.get(key, ('', '', 'Admin section'))[2][:100],
                default=(key == page),
            )
            for key in PAGES
        ]
        super().__init__(placeholder='📂 Jump directly to any section…', row=0, options=options)

    async def callback(self, interaction):
        await self.view.handle_action(interaction, 'page:' + self.values[0])


def _setting(db, key, default="0"):
    try:
        row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        if hasattr(row, "keys"):
            return str(row["value"])
        return str(row[0])
    except Exception:
        return default


def _set_setting(db, key, value):
    db.execute(
        "INSERT INTO economy_settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    db.commit()


class AdminActionButton(discord.ui.Button):
    def __init__(self, action, label, *, emoji=None, style=discord.ButtonStyle.secondary, row=None):
        super().__init__(label=label, emoji=emoji, style=style, row=row)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.view.handle_action(interaction, self.action)


class PendingApplicationSelect(discord.ui.Select):
    def __init__(self, rows, selected_id=None):
        options = [
            discord.SelectOption(
                label=f"#{row['id']} · {row['user_name']}"[:100],
                value=str(row["id"]),
                description=f"{row['form_name']} · {row['status'].title()}"[:100],
                default=int(row["id"]) == int(selected_id or 0),
            )
            for row in rows[:25]
        ]
        super().__init__(placeholder="Choose a pending application…", options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        if not await self.view.interaction_check(interaction):
            return
        self.view.selected_application_id = int(self.values[0])
        await self.view.refresh(interaction)


class ApplicationFormSelect(discord.ui.Select):
    def __init__(self, rows, selected_id=None):
        options = [
            discord.SelectOption(
                label=f"{row['emoji']} {row['name']}"[:100],
                value=str(row["id"]),
                description=(row["description"] or "Open application form")[:100],
                default=int(row["id"]) == int(selected_id or rows[0]["id"]),
            )
            for row in rows[:25]
        ]
        super().__init__(placeholder="Choose an application form to post…", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not await self.view.interaction_check(interaction):
            return
        self.view.selected_form_id = int(self.values[0])
        await self.view.refresh(interaction)


class RewardCodeSelect(discord.ui.Select):
    def __init__(self, rows, selected_id=None):
        options = [
            discord.SelectOption(
                label=row["code"][:100],
                value=str(row["id"]),
                description=(f"{code_status(row)} · {row['reward_xc']} XC / {row['reward_war_credits']} WC / {row['reward_xcrystals']} XCrystals")[:100],
                default=int(row["id"]) == int(selected_id or 0),
            )
            for row in rows[:25]
        ]
        super().__init__(placeholder="Choose a reward code…", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not await self.view.interaction_check(interaction):
            return
        self.view.selected_code_id = int(self.values[0])
        await self.view.refresh(interaction)


class TesterFeedbackSelect(discord.ui.Select):
    def __init__(self, rows, selected_id=None):
        options = [discord.SelectOption(label=f"#{row['id']} · {row['user_name']} · {row['title']}"[:100],
                                        value=str(row['id']), description=f"{row['kind'].title()} · {row['status'].title()}"[:100],
                                        default=int(row['id']) == int(selected_id or 0)) for row in rows[:25]]
        super().__init__(placeholder="Choose a Tester report…", options=options, row=1)

    async def callback(self, interaction):
        if not await self.view.interaction_check(interaction):
            return
        self.view.selected_feedback_id = int(self.values[0])
        await self.view.refresh(interaction)


class AdminUserSelect(discord.ui.UserSelect):
    def __init__(self, placeholder="Select a player…", default_user_id=None, row=None):
        kwargs = {"placeholder": placeholder, "min_values": 1, "max_values": 1}
        if default_user_id and int(default_user_id) > 0:
            kwargs["default_values"] = [discord.Object(id=int(default_user_id))]
        if row is not None:
            kwargs["row"] = row
        super().__init__(**kwargs)

    async def callback(self, interaction: discord.Interaction):
        if not await self.view.interaction_check(interaction):
            return
        self.view.target_user_id = self.values[0].id
        await self.view.refresh(interaction)


class AdminChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, placeholder="Select a text channel…", default_channel_id=None, row=None):
        kwargs = {
            "placeholder": placeholder,
            "channel_types": [discord.ChannelType.text],
            "min_values": 1,
            "max_values": 1,
        }
        if default_channel_id and str(default_channel_id).isdigit() and int(default_channel_id) > 0:
            kwargs["default_values"] = [discord.Object(id=int(default_channel_id))]
        if row is not None:
            kwargs["row"] = row
        super().__init__(**kwargs)

    async def callback(self, interaction: discord.Interaction):
        if not await self.view.interaction_check(interaction):
            return
        self.view.target_channel_id = self.values[0].id
        await self.view.refresh(interaction)


class StaffModal(discord.ui.Modal):
    async def on_error(self, interaction, error):
        self.panel.db.rollback()
        await self.panel.on_error(interaction, error, self)


class FeedbackRewardModal(StaffModal, title="Accept Tester report and reward"):
    xc = discord.ui.TextInput(label="XC reward", default="0", max_length=10)
    war_credits = discord.ui.TextInput(label="War Credits reward", default="0", max_length=10)
    note = discord.ui.TextInput(label="Staff note", placeholder="Optional thank-you note", required=False, max_length=300)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction):
        if not await self.panel.interaction_check(interaction):
            return
        try:
            xc, credits = max(0, int(str(self.xc))), max(0, int(str(self.war_credits)))
        except ValueError:
            await interaction.response.send_message("XC and War Credits must be whole numbers.", ephemeral=True)
            return
        await interaction.response.defer()
        notice = self.panel.review_feedback(interaction, "accepted", str(self.note).strip(), xc, credits)
        replacement = self.panel.clone(notice=notice, selected_feedback_id=None)
        await interaction.edit_original_response(embed=None, view=replacement)


class ApplicationDecisionModal(StaffModal):
    reason = discord.ui.TextInput(
        label="Reason / staff note",
        placeholder="Optional reason shown in the result",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, panel, decision):
        title = {"accepted": "Accept application", "hold": "Hold application", "denied": "Deny application"}[decision]
        super().__init__(title=title)
        self.panel = panel
        self.decision = decision

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        await interaction.response.defer()
        notice = await self.panel.review_application(interaction, self.decision, str(self.reason).strip())
        replacement = self.panel.clone(notice=notice, selected_application_id=None)
        await interaction.edit_original_response(embed=None, view=replacement)


class RewardCodeModal(StaffModal, title="Create reward code"):
    code = discord.ui.TextInput(label="Code", placeholder="Example: SEASON100", max_length=40)
    xc = discord.ui.TextInput(label="XC", placeholder="0", default="0", max_length=10)
    war_credits = discord.ui.TextInput(label="War Credits", placeholder="0", default="0", max_length=10)
    xcrystals = discord.ui.TextInput(label="XCrystals", placeholder="0", default="0", max_length=10)
    max_uses = discord.ui.TextInput(label="Maximum uses (0 = unlimited)", placeholder="0", default="0", max_length=10)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        clean_code = str(self.code).strip().upper()
        try:
            values = [max(0, int(str(field).strip() or "0")) for field in (self.xc, self.war_credits, self.xcrystals, self.max_uses)]
        except ValueError:
            replacement = self.panel.clone(notice="❌ XC, War Credits, XCrystals and uses must be whole numbers.")
            await interaction.response.edit_message(embed=None, view=replacement)
            return
        if not clean_code or not all(character.isalnum() or character in "_-" for character in clean_code):
            replacement = self.panel.clone(notice="❌ Code may only use letters, numbers, `_` and `-`.")
            await interaction.response.edit_message(embed=None, view=replacement)
            return
        xc, war_credits, xcrystals, max_uses = values
        if not any((xc, war_credits, xcrystals)):
            replacement = self.panel.clone(notice="❌ Add at least one currency reward.")
            await interaction.response.edit_message(embed=None, view=replacement)
            return
        try:
            cursor = self.panel.db.execute(
                """INSERT INTO reward_codes(code,reward_xc,reward_war_credits,reward_xcrystals,max_uses,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (clean_code, xc, war_credits, xcrystals, max_uses, interaction.user.id, int(time.time())),
            )
            self.panel.db.commit()
        except sqlite3.IntegrityError:
            self.panel.db.rollback()
            replacement = self.panel.clone(notice=f"❌ `{clean_code}` already exists.")
            await interaction.response.edit_message(embed=None, view=replacement)
            return
        replacement = self.panel.clone(notice=f"✅ Created `{clean_code}`.", selected_code_id=cursor.lastrowid)
        await interaction.response.edit_message(embed=None, view=replacement)


class AssetEditMoneyModal(StaffModal, title="Edit Player Currency"):
    currency = discord.ui.TextInput(
        label="Currency (money, xc, bank_xc, xcrystals)",
        default="money",
        placeholder="money (Cash), xc, bank_xc, or xcrystals",
        max_length=20,
    )
    amount = discord.ui.TextInput(
        label="Amount (+ to add, - to deduct)",
        placeholder="Example: +5000 or -200",
        max_length=20,
    )
    reason = discord.ui.TextInput(
        label="Reason / Audit Note",
        placeholder="Staff adjustment",
        required=False,
        max_length=200,
    )

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        raw_curr = str(self.currency).strip().lower()
        curr_map = {
            "money": "money",
            "cash": "money",
            "wc": "money",
            "xc": "xc",
            "wallet": "xc",
            "bank": "bank_xc",
            "bank_xc": "bank_xc",
            "xcrystals": "xcrystals",
            "crystals": "xcrystals",
        }
        if raw_curr not in curr_map:
            await interaction.response.send_message("❌ Invalid currency. Use `money` (Cash), `xc`, `bank_xc`, or `xcrystals`.", ephemeral=True)
            return
        col = curr_map[raw_curr]
        raw_amt = str(self.amount).strip().replace(",", "")
        try:
            val = int(raw_amt)
        except ValueError:
            await interaction.response.send_message("❌ Amount must be an integer (e.g. +500 or -200).", ephemeral=True)
            return

        target_uid = self.panel.target_user_id or self.panel.owner_id
        self.panel.db.execute("INSERT OR IGNORE INTO players (user_id, nation_name, display_name, money, xc) VALUES (?, 'New Nation', ?, 1000, 100)", (target_uid, f"User {target_uid}"))
        self.panel.db.execute(f"UPDATE players SET {col} = MAX(0, {col} + ?) WHERE user_id = ?", (val, target_uid))
        now = int(time.time())
        note = str(self.reason).strip() or "Staff adjustment"
        self.panel.db.execute(
            "INSERT INTO economy_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (target_uid, "staff_economy_adjust", f"{col} adjusted by {val:+,} ({note})", now),
        )
        self.panel.db.commit()
        await interaction.response.defer()
        notice = f"✅ Adjusted `{col}` for <@{target_uid}> by **{val:+,}**. Reason: {note}"
        await self.panel.refresh(interaction, notice=notice)


class AssetSpawnItemModal(StaffModal, title="Spawn Item to Player"):
    item_id = discord.ui.TextInput(
        label="Item ID",
        placeholder="e.g. iron_sword, first_aid_kit",
        max_length=50,
    )
    quantity = discord.ui.TextInput(
        label="Quantity",
        default="1",
        max_length=10,
    )
    reason = discord.ui.TextInput(
        label="Reason / Audit Note",
        placeholder="Staff grant",
        required=False,
        max_length=200,
    )

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        clean_item = str(self.item_id).strip()
        if not clean_item:
            await interaction.response.send_message("❌ Item ID is required.", ephemeral=True)
            return
        try:
            qty = max(1, int(str(self.quantity).strip()))
        except ValueError:
            await interaction.response.send_message("❌ Quantity must be a positive whole number.", ephemeral=True)
            return

        target_uid = self.panel.target_user_id or self.panel.owner_id
        self.panel.db.execute(
            "INSERT INTO inventories (user_id, item_id, quantity) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, item_id) DO UPDATE SET quantity = quantity + excluded.quantity",
            (target_uid, clean_item, qty),
        )
        now = int(time.time())
        note = str(self.reason).strip() or "Staff spawn"
        self.panel.db.execute(
            "INSERT INTO economy_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (target_uid, "staff_spawn_item", f"Spawned {qty}x {clean_item} ({note})", now),
        )
        self.panel.db.commit()
        await interaction.response.defer()
        await self.panel.refresh(interaction, notice=f"✅ Spawned **{qty}× `{clean_item}`** for <@{target_uid}>.")


class AssetRemoveItemModal(StaffModal, title="Remove Item from Player"):
    item_id = discord.ui.TextInput(
        label="Item ID",
        placeholder="e.g. iron_sword",
        max_length=50,
    )
    quantity = discord.ui.TextInput(
        label="Quantity to remove",
        default="1",
        max_length=10,
    )

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        clean_item = str(self.item_id).strip()
        try:
            qty = max(1, int(str(self.quantity).strip()))
        except ValueError:
            await interaction.response.send_message("❌ Quantity must be a positive whole number.", ephemeral=True)
            return

        target_uid = self.panel.target_user_id or self.panel.owner_id
        row = self.panel.db.execute("SELECT quantity FROM inventories WHERE user_id=? AND item_id=?", (target_uid, clean_item)).fetchone()
        if not row:
            await interaction.response.send_message(f"❌ Player does not have `{clean_item}` in their backpack.", ephemeral=True)
            return

        current_qty = row["quantity"]
        new_qty = current_qty - qty
        if new_qty <= 0:
            self.panel.db.execute("DELETE FROM inventories WHERE user_id=? AND item_id=?", (target_uid, clean_item))
        else:
            self.panel.db.execute("UPDATE inventories SET quantity=? WHERE user_id=? AND item_id=?", (new_qty, target_uid, clean_item))
        now = int(time.time())
        self.panel.db.execute(
            "INSERT INTO economy_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (target_uid, "staff_remove_item", f"Removed {qty}x {clean_item} (was {current_qty})", now),
        )
        self.panel.db.commit()
        await interaction.response.defer()
        await self.panel.refresh(interaction, notice=f"✅ Removed **{qty}× `{clean_item}`** from <@{target_uid}> (Remaining: `{max(0, new_qty)}`).")


class WarEditTroopsModal(StaffModal, title="Edit Armed Forces Troops"):
    branch = discord.ui.TextInput(
        label="Branch (land, air, navy)",
        default="land",
        max_length=10,
    )
    amount = discord.ui.TextInput(
        label="Adjustment (+/- amount or set count)",
        placeholder="e.g. +50 or -10 or 100",
        max_length=15,
    )

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        b = str(self.branch).strip().lower()
        branch_map = {"land": "land_army", "air": "air_army", "navy": "navy"}
        if b not in branch_map:
            await interaction.response.send_message("❌ Invalid branch. Choose `land`, `air`, or `navy`.", ephemeral=True)
            return
        col = branch_map[b]
        raw = str(self.amount).strip()
        try:
            val = int(raw)
        except ValueError:
            await interaction.response.send_message("❌ Amount must be an integer.", ephemeral=True)
            return

        target_uid = self.panel.target_user_id or self.panel.owner_id
        self.panel.db.execute("INSERT OR IGNORE INTO players (user_id, nation_name, display_name, money, xc) VALUES (?, 'New Nation', ?, 1000, 100)", (target_uid, f"User {target_uid}"))
        if raw.startswith(("+", "-")):
            self.panel.db.execute(f"UPDATE players SET {col} = MAX(0, {col} + ?) WHERE user_id = ?", (val, target_uid))
        else:
            self.panel.db.execute(f"UPDATE players SET {col} = MAX(0, ?) WHERE user_id = ?", (val, target_uid))
        now = int(time.time())
        self.panel.db.execute(
            "INSERT INTO economy_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (target_uid, "staff_forces_adjust", f"{col} set/adjusted by {raw}", now),
        )
        self.panel.db.commit()
        await interaction.response.defer()
        await self.panel.refresh(interaction, notice=f"✅ Updated **{b.title()}** for <@{target_uid}> with `{raw}` units.")


class WarEditCapitalModal(StaffModal, title="Set Capital Health & Name"):
    health = discord.ui.TextInput(
        label="Capital Health HP (0 to 100)",
        default="100",
        max_length=3,
    )
    capital_name = discord.ui.TextInput(
        label="Capital Name",
        placeholder="Leave blank to keep current name",
        required=False,
        max_length=50,
    )

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        try:
            hp = max(0, min(100, int(str(self.health).strip())))
        except ValueError:
            await interaction.response.send_message("❌ Capital health must be 0 to 100.", ephemeral=True)
            return
        target_uid = self.panel.target_user_id or self.panel.owner_id
        cname = str(self.capital_name).strip()
        if cname:
            self.panel.db.execute("UPDATE players SET capital_health=?, capital_name=? WHERE user_id=?", (hp, cname, target_uid))
        else:
            self.panel.db.execute("UPDATE players SET capital_health=? WHERE user_id=?", (hp, target_uid))
        self.panel.db.commit()
        await interaction.response.defer()
        await self.panel.refresh(interaction, notice=f"✅ Set Capital Health for <@{target_uid}> to **{hp}%**.")


class WarEditResourcesModal(StaffModal, title="Adjust Strategic Resources"):
    iron = discord.ui.TextInput(label="Iron (+/- or amount)", default="+0", max_length=15)
    gold = discord.ui.TextInput(label="Gold (+/- or amount)", default="+0", max_length=15)
    oil = discord.ui.TextInput(label="Oil (+/- or amount)", default="+0", max_length=15)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        target_uid = self.panel.target_user_id or self.panel.owner_id
        for field, col in ((self.iron, "iron"), (self.gold, "gold"), (self.oil, "oil")):
            raw = str(field).strip()
            try:
                v = int(raw)
                if raw.startswith(("+", "-")):
                    self.panel.db.execute(f"UPDATE players SET {col} = MAX(0, {col} + ?) WHERE user_id = ?", (v, target_uid))
                elif v != 0 or raw == "0":
                    self.panel.db.execute(f"UPDATE players SET {col} = MAX(0, ?) WHERE user_id = ?", (v, target_uid))
            except ValueError:
                pass
        self.panel.db.commit()
        await interaction.response.defer()
        await self.panel.refresh(interaction, notice=f"✅ Updated strategic resources for <@{target_uid}>.")


class ServerLevelTemplateModal(StaffModal, title="Level-Up Announcement Template"):
    message = discord.ui.TextInput(
        label="Message template",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        placeholder="Use {mention}, {user}, {level}, {reward}",
    )

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.message.default = leveling.setting(panel.db, "xp_announcement_template")

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        clean_msg = str(self.message).strip()
        _set_setting(self.panel.db, "xp_announcement_template", clean_msg)
        await interaction.response.defer()
        await self.panel.refresh(interaction, notice="✅ Level-up announcement template updated.")


class ServerSayModal(StaffModal, title="Post Announcement as X BOT"):
    message = discord.ui.TextInput(
        label="Announcement Message",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        placeholder="Type the announcement to post...",
    )

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        text = str(self.message).strip()
        if not text:
            await interaction.response.send_message("❌ Message cannot be empty.", ephemeral=True)
            return

        target_cid = self.panel.target_channel_id or interaction.channel_id
        target_ch = self.panel.bot.get_channel(int(target_cid)) or interaction.channel
        if not target_ch:
            await interaction.response.send_message("❌ Target channel not found.", ephemeral=True)
            return

        try:
            await target_ch.send(text)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to post message: {e}", ephemeral=True)
            return

        await interaction.response.defer()
        await self.panel.refresh(interaction, notice=f"✅ Announcement posted in <#{target_cid}>!")


class AdminPanel(discord.ui.LayoutView):
    def __init__(self, bot, db, staff_check, owner_id, *, page="home", target_user_id=None, target_channel_id=None, selected_form_id=None, selected_application_id=None, selected_code_id=None, selected_feedback_id=None, notice="", history=(), offsets=None, pending_action=None, code_filter='all'):
        super().__init__(timeout=900)
        self.bot = bot
        self.db = db
        self.staff_check = staff_check
        self.owner_id = owner_id
        self.page = page
        self.target_user_id = int(target_user_id) if target_user_id else owner_id
        self.target_channel_id = int(target_channel_id) if target_channel_id else 0
        self.selected_form_id = selected_form_id
        self.selected_application_id = selected_application_id
        self.selected_code_id = selected_code_id
        self.selected_feedback_id = selected_feedback_id
        self.notice = notice
        self.history = history
        self.offsets = dict(offsets or {})
        self.pending_action = pending_action
        self.maintenance_used = False
        self.code_filter = code_filter if code_filter in {'all','enabled','disabled'} else 'all'
        self.list_pages = {}
        self._controls = []
        self._build_controls()
        content = self.build_embed()
        if self.page != 'home':
            import staff_sections
            parts = staff_sections.build(self, content)
            super().add_item(discord.ui.Container(*parts, accent_colour=0x36CFC9))
            super().add_item(discord.ui.ActionRow(
                AdminActionButton('back','‹ Back'),AdminActionButton('page:home','⌂ Home'),
                AdminActionButton('refresh','Refresh'),AdminActionButton('close','× Close')))
            return
        parts = [discord.ui.TextDisplay(f"-# ✦ X SYSTEM · ADMIN / {PAGES.get(self.page,self.page)}\n# {PAGES.get(self.page,self.page).upper()}\n{content.description or ''}")]
        for field in content.fields:
            parts.extend((discord.ui.Separator(), discord.ui.TextDisplay(f"### {field.name}\n{field.value}")))
        rows = {}
        for control in self._controls:
            row = control.row if control.row is not None else 0
            control.row = None
            rows.setdefault(row, []).append(control)
        if self.page == "home":
            if 99 in rows:
                parts.extend((discord.ui.Separator(), discord.ui.ActionRow(*rows.pop(99))))
            sections = (
                (0, '📢 Channel Panels & Server Setup', 'Deploy public panels to channels, announcements & reward codes.'),
                (1, '👥 Player & Moderation Manager', 'Manage member roles, player economy assets, armed forces & Deadzone.'),
                (2, '📬 Submissions & Reviews', 'Review pending staff applications and beta tester bug reports.'),
                (3, '🛠️ System Diagnostics & Health', 'Live economy status, system health checks & backups.'),
            )
            for row, title, hint in sections:
                if row in rows:
                    parts.extend((discord.ui.Separator(), discord.ui.TextDisplay(f'### {title}\n{hint}'), discord.ui.ActionRow(*rows.pop(row))))
        super().add_item(discord.ui.Container(*parts, accent_colour=0x36CFC9))
        super().add_item(discord.ui.ActionRow(
            AdminActionButton("back", "‹ Back"), AdminActionButton("page:home", "⌂ Home"),
            AdminActionButton("refresh", "Refresh"), AdminActionButton("close", "× Close")))

    def add_item(self, item):
        # Existing controls keep their logical rows; LayoutView owns every item.
        self._controls.append(item)
        return self

    def clone(self, **changes):
        values = {
            "page": self.page,
            "target_user_id": self.target_user_id,
            "target_channel_id": self.target_channel_id,
            "selected_form_id": self.selected_form_id,
            "selected_application_id": self.selected_application_id,
            "selected_code_id": self.selected_code_id,
            "selected_feedback_id": self.selected_feedback_id,
            "notice": "",
            "history": self.history,
            "offsets": self.offsets,
            "pending_action": self.pending_action,
            "code_filter": self.code_filter,
        }
        values.update(changes)
        return AdminPanel(self.bot, self.db, self.staff_check, self.owner_id, **values)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id == self.owner_id and self.staff_check(interaction):
            return True
        sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await sender("This panel requires its original user and current staff permission. Reopen /admin if your access has changed.", ephemeral=True)
        return False

    async def on_error(self, interaction, error, item):
        import logging
        logging.getLogger(__name__).exception("Admin panel action failed", exc_info=error)
        sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await sender("Action could not finish. Refresh this panel to check its current status before retrying.", ephemeral=True)

    async def _edit(self, interaction, replacement):
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=None, view=replacement)
        else:
            await interaction.response.edit_message(embed=None, view=replacement)

    def _page_rows(self, rows, kind):
        offset = min(max(0, self.offsets.get(kind, 0)), max(0, (len(rows) - 1) // 25 * 25))
        self.offsets[kind] = offset
        self.list_pages[kind] = f"Page {offset // 25 + 1} / {max(1,(len(rows)+24)//25)} · {len(rows)} records"
        if len(rows) > 25:
            for direction, target, label in ((-1, offset - 25, f"‹ {kind.title()}"), (1, offset + 25, f"{kind.title()} ›")):
                button = AdminActionButton(f"list:{kind}:{direction}", label, row=3)
                button.disabled = target < 0 or target >= len(rows)
                self.add_item(button)
        return rows[offset:offset + 25]

    def _build_controls(self):
        if self.page != 'home':
            self.add_item(AdminPageSelect(self.page))
        if self.page in TOOL_GROUPS:
            import staff_tools
            for name,(group,label) in staff_tools.TOOLS.items():
                if group == TOOL_GROUPS[self.page]:
                    control=staff_tools.ToolButton(self,name);control.row=1;self.add_item(control)

        if self.page == "assets":
            self.add_item(AdminUserSelect(placeholder="Select player to manage economy & inventory…", default_user_id=self.target_user_id, row=2))
            self.add_item(AdminActionButton("asset_edit_money", "Edit Money", emoji="💵", style=discord.ButtonStyle.primary, row=3))
            self.add_item(AdminActionButton("asset_spawn_item", "Spawn Item", emoji="📦", style=discord.ButtonStyle.success, row=3))
            self.add_item(AdminActionButton("asset_remove_item", "Remove Item", emoji="🗑️", style=discord.ButtonStyle.danger, row=3))
            self.add_item(AdminActionButton("refresh", "Refresh Inventory", emoji="🔄", style=discord.ButtonStyle.secondary, row=3))
        elif self.page == "war_tools":
            self.add_item(AdminUserSelect(placeholder="Select player to manage armed forces…", default_user_id=self.target_user_id, row=2))
            self.add_item(AdminActionButton("war_edit_troops", "Edit Troops", emoji="🪖", style=discord.ButtonStyle.primary, row=3))
            self.add_item(AdminActionButton("war_edit_capital", "Set Capital HP", emoji="🏰", style=discord.ButtonStyle.secondary, row=3))
            self.add_item(AdminActionButton("war_edit_resources", "Edit Resources", emoji="⛏️", style=discord.ButtonStyle.secondary, row=3))
            self.add_item(AdminActionButton("war_toggle_status", "War Status", emoji="⚔️", style=discord.ButtonStyle.danger, row=3))
        elif self.page == "panels":
            forms = self.application_forms()
            if forms:
                if self.selected_form_id is None:
                    self.selected_form_id = forms[0]['id']
                self.add_item(ApplicationFormSelect(self._page_rows(forms, "forms"), self.selected_form_id))
            self.add_item(AdminChannelSelect(placeholder="Target channel to deploy panel…", default_channel_id=self.target_channel_id, row=2))
            self.add_item(AdminActionButton("post_verification", "Post Verification", emoji="✅", style=discord.ButtonStyle.success, row=3))
            self.add_item(AdminActionButton("server_post_gaming_roles", "Post Roles Panel", emoji="🎮", style=discord.ButtonStyle.success, row=3))
            self.add_item(AdminActionButton("post_application", "Post Application Form", emoji="📨", style=discord.ButtonStyle.primary, row=3))
            self.add_item(AdminActionButton("server_post_deadzone", "Post Deadzone", emoji="⚰️", style=discord.ButtonStyle.secondary, row=4))
            self.add_item(AdminActionButton("server_post_lounge_lobby", "Post Lounge Lobby", emoji="🛋️", style=discord.ButtonStyle.primary, row=4))
            self.add_item(AdminActionButton("post_tester_feedback", "Post Tester Panel", emoji="🧪", style=discord.ButtonStyle.secondary, row=4))
        elif self.page == "server":
            self.add_item(AdminChannelSelect(placeholder="Choose destination channel to deploy panels…", default_channel_id=self.target_channel_id, row=2))
            self.add_item(AdminActionButton("server_post_gaming_roles", "Post Gaming Roles", emoji="🎮", style=discord.ButtonStyle.success, row=3))
            self.add_item(AdminActionButton("post_verification", "Post Verification", emoji="✅", style=discord.ButtonStyle.success, row=3))
            self.add_item(AdminActionButton("post_tester_feedback", "Post Feedback", emoji="🧪", style=discord.ButtonStyle.primary, row=3))
            self.add_item(AdminActionButton("server_post_deadzone", "Post Deadzone", emoji="⚰️", style=discord.ButtonStyle.secondary, row=3))
            self.add_item(AdminActionButton("server_post_lounge_lobby", "Post Lounge Lobby", emoji="🛋️", style=discord.ButtonStyle.primary, row=3))
            self.add_item(AdminActionButton("server_say", "Post /say", emoji="📢", style=discord.ButtonStyle.primary, row=4))
            self.add_item(AdminActionButton("server_toggle_level", "Toggle Notices", emoji="🔔", style=discord.ButtonStyle.secondary, row=4))
            self.add_item(AdminActionButton("server_set_level_channel", "Set Level Channel", emoji="📌", style=discord.ButtonStyle.secondary, row=4))
            self.add_item(AdminActionButton("server_edit_template", "Edit Level Msg", emoji="✏️", style=discord.ButtonStyle.secondary, row=4))
            self.add_item(AdminActionButton("server_lottery_draw", "Draw Lottery", emoji="🎲", style=discord.ButtonStyle.secondary, row=4))
        elif self.page == "applications":
            forms = self.application_forms()
            if forms:
                if self.selected_form_id is None:
                    self.selected_form_id = forms[0]['id']
                self.add_item(ApplicationFormSelect(self._page_rows(forms, "forms"), self.selected_form_id))
            pending = self.pending_applications()
            if pending:
                self.add_item(PendingApplicationSelect(self._page_rows(pending, "applications"), self.selected_application_id))
            self.add_item(AdminChannelSelect(placeholder="Target channel to deploy form / verification…", default_channel_id=self.target_channel_id, row=3))
            self.add_item(AdminActionButton("post_application", "Post Selected Form", emoji="📨", style=discord.ButtonStyle.primary, row=4))
            self.add_item(AdminActionButton("post_verification", "Post Verification", emoji="✅", style=discord.ButtonStyle.success, row=4))
            self.add_item(AdminActionButton("toggle_applications", "Close Applications" if _setting(self.db,'applications_enabled','1')=='1' else "Open Applications", emoji="🔁", row=4))
            if self.selected_application():
                self.add_item(AdminActionButton("review:accepted", "Accept", emoji="✅", style=discord.ButtonStyle.success, row=4))
                self.add_item(AdminActionButton("review:hold", "Hold", emoji="⏸️", row=4))
                self.add_item(AdminActionButton("review:denied", "Deny", emoji="❌", style=discord.ButtonStyle.danger, row=4))
        elif self.page == "verification":
            self.add_item(AdminChannelSelect(placeholder="Target channel to deploy verification…", default_channel_id=self.target_channel_id, row=1))
            self.add_item(AdminActionButton("post_verification", "Post Verification", emoji="✅", style=discord.ButtonStyle.success, row=2))
            self.add_item(AdminActionButton("toggle_verification", "Close Verification" if _setting(self.db,'verification_enabled','1')=='1' else "Open Verification", emoji="🔁", row=2))
            self.add_item(AdminUserSelect(placeholder="Select member to manually verify…", default_user_id=self.target_user_id, row=3))
            self.add_item(AdminActionButton("manual_verify_member", "Manually Verify Member", emoji="🔓", style=discord.ButtonStyle.primary, row=4))
        elif self.page == "tester":
            reports = self.pending_feedback()
            if reports:
                self.add_item(TesterFeedbackSelect(self._page_rows(reports, "reports"), self.selected_feedback_id))
            self.add_item(AdminChannelSelect(placeholder="Target channel to deploy feedback panel…", default_channel_id=self.target_channel_id, row=2))
            self.add_item(AdminActionButton("post_tester_feedback", "Post Tester Panel", emoji="📨", style=discord.ButtonStyle.primary, row=3))
            if self.selected_feedback():
                self.add_item(AdminActionButton("accept_feedback", "Accept + Reward", emoji="🎁", style=discord.ButtonStyle.success, row=3))
                self.add_item(AdminActionButton("reject_feedback", "Reject", emoji="❌", style=discord.ButtonStyle.danger, row=3))
        elif self.page == "codes":
            codes = self.reward_codes()
            for value,label in (('all','All codes'),('enabled','Enabled'),('disabled','Disabled')):
                self.add_item(AdminActionButton('filter_codes:'+value,label,style=discord.ButtonStyle.primary if value==self.code_filter else discord.ButtonStyle.secondary,row=3))
            if self.selected_code_id is None and codes:
                self.selected_code_id = codes[0]['id']
            if codes:
                self.add_item(RewardCodeSelect(self._page_rows(codes, "codes"), self.selected_code_id))
            self.add_item(AdminActionButton("create_code", "Create Code", emoji="➕", style=discord.ButtonStyle.success, row=2))
            if self.selected_code():
                self.add_item(AdminActionButton("toggle_code", "Disable Code" if self.selected_code()['enabled'] else "Enable Code", emoji="🔁", row=2))
        elif self.page == "home":
            quick_select = AdminPageSelect(self.page)
            quick_select.row = 99
            self.add_item(quick_select)

            pending = len(self.pending_applications())
            reports = len(self.pending_feedback())

            # Row 0: 📢 Deploy Panels & Server Tools
            for page in ('panels', 'server', 'codes', 'verification'):
                emoji, label = PAGE_METADATA[page][0], PAGES[page]
                style = discord.ButtonStyle.primary if page == 'panels' else discord.ButtonStyle.secondary
                self.add_item(AdminActionButton('page:' + page, label, emoji=emoji, style=style, row=0))

            # Row 1: 👥 Player & Moderation Manager
            for page in ('members', 'assets', 'war_tools', 'deadzone'):
                emoji, label = PAGE_METADATA[page][0], PAGES[page]
                self.add_item(AdminActionButton('page:' + page, label, emoji=emoji, style=discord.ButtonStyle.secondary, row=1))

            # Row 2: 📬 Submissions & Reviews
            for page in ('applications', 'tester'):
                count = {'applications': pending, 'tester': reports}.get(page, 0)
                label = PAGES[page] + (f' · {count}' if count is not None else '')
                emoji = PAGE_METADATA[page][0]
                style = discord.ButtonStyle.primary if count else discord.ButtonStyle.secondary
                self.add_item(AdminActionButton('page:' + page, label, emoji=emoji, style=style, row=2))

            # Row 3: 🛠️ Diagnostics & Maintenance
            for page in ('economy', 'maintenance'):
                emoji, label = PAGE_METADATA[page][0], PAGES[page]
                self.add_item(AdminActionButton('page:' + page, label, emoji=emoji, style=discord.ButtonStyle.secondary, row=3))
        elif self.page == "maintenance":
            if self.pending_action:
                self.add_item(AdminActionButton('confirm_maintenance:'+self.pending_action,'Confirm action',style=discord.ButtonStyle.danger if 'repair' in self.pending_action else discord.ButtonStyle.primary,row=1))
                self.add_item(AdminActionButton('cancel_maintenance','Cancel',row=1))
                return
            self.add_item(AdminActionButton("system_status", "System Status", emoji="📡", style=discord.ButtonStyle.primary, row=1))
            self.add_item(AdminActionButton("backup_now", "Create Backup", emoji="💾", style=discord.ButtonStyle.secondary, row=1))
            self.add_item(AdminActionButton("tier5_repair", "Repair Missions", emoji="🛠️", style=discord.ButtonStyle.secondary, row=1))
            self.add_item(AdminActionButton("tier6_repair", "Repair Economy", emoji="💰", style=discord.ButtonStyle.secondary, row=1))

    def pending_applications(self):
        return self.db.execute(
            """SELECT s.id,s.user_name,s.status,f.name form_name FROM application_submissions s
               JOIN application_forms f ON f.id=s.form_id WHERE s.status IN ('pending','hold')
               ORDER BY s.created_at,s.id"""
        ).fetchall()

    def application_forms(self):
        return self.db.execute("SELECT * FROM application_forms WHERE enabled=1 ORDER BY name,id").fetchall()

    def selected_form(self):
        forms = self.application_forms()
        if not forms:
            return None
        selected_id = self.selected_form_id or forms[0]["id"]
        return self.db.execute("SELECT * FROM application_forms WHERE id=? AND enabled=1", (selected_id,)).fetchone() or forms[0]

    def selected_application(self):
        if not self.selected_application_id:
            return None
        return self.db.execute(
            """SELECT s.*,f.name form_name,f.reviewer_role_ids,f.accepted_role_ids,f.result_channel_id
               FROM application_submissions s JOIN application_forms f ON f.id=s.form_id
               WHERE s.id=? AND s.status IN ('pending','hold')""",
            (self.selected_application_id,),
        ).fetchone()

    def reward_codes(self):
        condition = {'all':'1=1','enabled':'enabled=1','disabled':'enabled=0'}[self.code_filter]
        return self.db.execute(f"SELECT * FROM reward_codes WHERE {condition} ORDER BY enabled DESC,id DESC").fetchall()

    def selected_code(self):
        if not self.selected_code_id:
            return None
        return self.db.execute("SELECT * FROM reward_codes WHERE id=?", (self.selected_code_id,)).fetchone()

    def pending_feedback(self):
        return self.db.execute("SELECT * FROM tester_feedback WHERE status='pending' ORDER BY created_at,id").fetchall()

    def selected_feedback(self):
        if not self.selected_feedback_id:
            return None
        return self.db.execute("SELECT * FROM tester_feedback WHERE id=? AND status='pending'", (self.selected_feedback_id,)).fetchone()

    def build_embed(self):
        embed = discord.Embed(title="🛡️ X BOT Staff Control Centre", colour=discord.Color.blurple())
        if self.notice:
            embed.add_field(name="Latest action", value=self.notice[:1024], inline=False)
        if self.page == "economy":
            import economy_settings_admin
            embed.description = "Read only · Edit values in Dashboard. Refresh to read committed settings."
            for title, body in economy_settings_admin.status_sections(self.db):
                embed.add_field(name=title, value=body, inline=False)
        elif self.page == "home":
            pending = self.db.execute("SELECT COUNT(*) FROM application_submissions WHERE status IN ('pending','hold')").fetchone()[0]
            active_codes = self.db.execute("SELECT COUNT(*) FROM reward_codes WHERE enabled=1").fetchone()[0]
            reports = self.db.execute("SELECT COUNT(*) FROM tester_feedback WHERE status='pending'").fetchone()[0]
            verification = "Open" if _setting(self.db, "verification_enabled", "1") == "1" else "Closed"
            embed.description = (
                f"🟢 **System Operational** · 🛡️ Verification **{verification}**\n"
                f"📬 **{pending}** Applications · 🐞 **{reports}** Reports · 🎟️ **{active_codes}** Active Codes\n\n"
                f"-# 💡 Select any category below, or use the quick dropdown to jump directly to a tool."
            )
        elif self.page == "maintenance":
            embed.description = "Check system health or create a backup.\nRepair buttons change stored data — use only when needed."
        elif self.page == "assets":
            target_uid = self.target_user_id or self.owner_id
            player = self.db.execute("SELECT * FROM players WHERE user_id=?", (target_uid,)).fetchone()
            if not player:
                self.db.execute("INSERT OR IGNORE INTO players (user_id, nation_name, display_name, money, xc) VALUES (?, 'New Nation', ?, 1000, 100)", (target_uid, f"User {target_uid}"))
                self.panel.db.commit() if hasattr(self, 'panel') else self.db.commit()
                player = self.db.execute("SELECT * FROM players WHERE user_id=?", (target_uid,)).fetchone()
            embed.title = "💰 Player Economy & Asset Manager"
            embed.description = f"Managing economy assets for <@{target_uid}> (`{target_uid}`). Choose a player below, then click any action button."
            embed.add_field(
                name="💵 Currency Balances",
                value=(
                    f"🪙 **Wallet XC:** {player['xc']:,}\n"
                    f"🏦 **Bank XC:** {player['bank_xc']:,}\n"
                    f"💵 **Cash:** {player['money']:,}\n"
                    f"💎 **XCrystals:** {player['xcrystals']:,}"
                ),
                inline=False,
            )
            inv_rows = self.db.execute(
                "SELECT i.item_id, i.quantity, COALESCE(it.name, i.item_id) as item_name FROM inventories i LEFT JOIN items it ON i.item_id = it.id WHERE i.user_id=? ORDER BY i.quantity DESC",
                (target_uid,)
            ).fetchall()
            if inv_rows:
                inv_text = "\n".join(f"• **{r['item_name']}** (`{r['item_id']}`): {r['quantity']:,}" for r in inv_rows[:15])
                if len(inv_rows) > 15:
                    inv_text += f"\n*...and {len(inv_rows) - 15} more items*"
            else:
                inv_text = "*Backpack is currently empty.*"
            embed.add_field(name=f"🎒 Backpack Inventory ({len(inv_rows)} items)", value=inv_text[:1024], inline=False)
        elif self.page == "war_tools":
            target_uid = self.target_user_id or self.owner_id
            player = self.db.execute("SELECT * FROM players WHERE user_id=?", (target_uid,)).fetchone()
            if not player:
                self.db.execute("INSERT OR IGNORE INTO players (user_id, nation_name, display_name, money, xc) VALUES (?, 'New Nation', ?, 1000, 100)", (target_uid, f"User {target_uid}"))
                self.panel.db.commit() if hasattr(self, 'panel') else self.db.commit()
                player = self.db.execute("SELECT * FROM players WHERE user_id=?", (target_uid,)).fetchone()
            total_pwr = war_system.total_power(self.db, target_uid) if hasattr(war_system, "total_power") else 0
            alliance = self.db.execute(
                "SELECT a.name FROM alliances a JOIN alliance_members m ON a.id=m.alliance_id WHERE m.user_id=?",
                (target_uid,)
            ).fetchone()
            alliance_name = alliance['name'] if alliance else "*No Alliance*"
            embed.title = "⚔️ Armed Forces & Military Manager"
            embed.description = f"Managing armed forces for <@{target_uid}> (`{target_uid}`). Choose a player below, then click any action button."
            embed.add_field(
                name="🛡️ Nation & Power",
                value=(
                    f"🏛️ **Nation:** {player['nation_name'] or 'Unnamed Nation'}\n"
                    f"🏰 **Capital:** {player['capital_name']} ({player['capital_health']}% HP)\n"
                    f"⚡ **Total Military Power:** {total_pwr:,}\n"
                    f"🚩 **Alliance:** {alliance_name}"
                ),
                inline=False,
            )
            embed.add_field(
                name="🪖 Armed Forces Roster",
                value=(
                    f"🪖 **Land Army:** {player['land_army']:,} units\n"
                    f"✈️ **Air Force:** {player['air_army']:,} squadrons\n"
                    f"⚓ **Navy:** {player['navy']:,} warships"
                ),
                inline=True,
            )
            embed.add_field(
                name="⛏️ Strategic Stockpile",
                value=(
                    f"⚙️ **Iron:** {player['iron']:,}\n"
                    f"🪙 **Gold:** {player['gold']:,}\n"
                    f"🛢️ **Oil:** {player['oil']:,}"
                ),
                inline=True,
            )
        elif self.page == "panels":
            target_ch_text = f"<#{self.target_channel_id}>" if self.target_channel_id else "*Current Channel*"
            selected_form = self.selected_form()
            form_name = selected_form['name'] if selected_form else "Default Form"
            embed.title = "📢 Post & Deploy Interactive Panels"
            embed.description = (
                f"Deploy interactive server panels into any channel with 1-click.\n"
                f"📍 **Target Destination:** {target_ch_text}\n"
                f"📝 **Selected Application Form:** `{form_name}`\n\n"
                f"Choose the target channel from the picker, then click any button to deploy:\n"
                f"• ✅ **Verification:** Onboarding verification gate & member role assignment\n"
                f"• 📨 **Application Form:** Interactive submission form button\n"
                f"• ⚰️ **Deadzone:** Live Crypt status board & member rescue breakout\n"
                f"• 🎮 **Gaming Roles:** Steam, Roblox & Mobile role toggles\n"
                f"• 🛋️ **Lounge Lobby:** 5 dynamic voice lounges reservation hub\n"
                f"• 🧪 **Tester Feedback:** Beta testing & bug report panel"
            )
        elif self.page == "server":
            enabled = leveling.setting(self.db, "xp_announcement_enabled") == "1"
            channel_id = leveling.setting(self.db, "xp_announcement_channel_id")
            channel_text = f"<#{channel_id}>" if channel_id.isdigit() and channel_id != "0" else "*Not configured*"
            template = leveling.setting(self.db, "xp_announcement_template")

            steam_role = gaming.setting(self.db, "game_role_steam_id")
            roblox_role = gaming.setting(self.db, "game_role_roblox_id")
            mobile_role = gaming.setting(self.db, "game_role_mobile_id")
            steam_ch = gaming.setting(self.db, "game_channel_steam_id")
            roblox_ch = gaming.setting(self.db, "game_channel_roblox_id")
            mobile_ch = gaming.setting(self.db, "game_channel_mobile_id")

            embed.title = "⚙️ Server Settings & Gaming Zone"
            embed.description = "Configure level-up broadcast notices, post announcements, or deploy the Gaming Zone roles panel."
            embed.add_field(
                name="📢 Level-Up Chat Notices",
                value=(
                    f"• **Status:** {'🟢 Enabled' if enabled else '🔴 Disabled'}\n"
                    f"• **Channel:** {channel_text}\n"
                    f"• **Template:** `{template[:300]}`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🎮 Gaming Zone Setup",
                value=(
                    f"• 🎮 **Steam:** Role <@&{steam_role}> · Channel <#{steam_ch}>\n"
                    f"• 🟥 **Roblox:** Role <@&{roblox_role}> · Channel <#{roblox_ch}>\n"
                    f"• 📱 **Mobile:** Role <@&{mobile_role}> · Channel <#{mobile_ch}>"
                ),
                inline=False,
            )
            embed.add_field(
                name="📢 Deploy Public Interactive Panels",
                value=(
                    "Select a target channel from the picker, then click any button to deploy:\n"
                    "• 🎮 **Gaming Roles:** Steam, Roblox & Mobile role picker\n"
                    "• ✅ **Verification:** Server onboarding gate\n"
                    "• 🧪 **Tester Feedback:** Bug report & feedback panel\n"
                    "• ⚰️ **Deadzone:** Resurrection breakout status & rescue board\n"
                    "• 📢 **Post Announcement:** Post custom message as X BOT via `/say`"
                ),
                inline=False,
            )
        elif self.page in TOOL_GROUPS:
            embed.description = {
                'members':'Inspect a player or manage roles and activity level.',
                'assets':'Select a player, review the amount, then confirm. No assets change on opening a tool.',
                'deadzone':'Manage inactive members, send to crypt, or run inactivity scan.',
                'server':'Prepare an announcement or review a lottery draw.',
                'war_tools':'Review alliance targets before starting or ending a war.',
            }[self.page] + '\n**1 Choose tool → 2 Fill details → 3 Review & confirm**'
            if self.page == "deadzone":
                try:
                    sleepers = self.db.execute("SELECT COUNT(*) FROM deadzone_members WHERE is_in_deadzone=1").fetchone()[0]
                    thawed = self.db.execute("SELECT COUNT(*) FROM deadzone_members WHERE is_in_deadzone=1 AND thaw_count>=5").fetchone()[0]
                    threshold_days = deadzone.setting(self.db, "deadzone_days") or "7"
                except Exception:
                    sleepers, thawed, threshold_days = 0, 0, "7"
                embed.add_field(name="Crypt Population", value=f"💀 **Active Sleepers:** `{sleepers}`\n🧊 **Thawed Comrades:** `{thawed}`\n⏰ **Auto Inactivity Threshold:** `{threshold_days} days`", inline=False)
        elif self.page == "applications":
            embed.description = f"**{'Open' if _setting(self.db, 'applications_enabled', '1') == '1' else 'Closed'}** · **{len(self.pending_applications())}** awaiting review"
            form = self.selected_form()
            if form:
                embed.add_field(name="Form selected to post", value=f"{form['emoji']} **{form['name']}**\n{form['description'] or 'No description.'}"[:1024], inline=False)
            selected = self.selected_application()
            if selected:
                answers = self.db.execute("SELECT question,answer FROM application_answers WHERE submission_id=? ORDER BY question_id", (selected["id"],)).fetchall()
                body = "\n\n".join(f"**{row['question']}**\n{row['answer']}" for row in answers) or "No saved answers."
                embed.add_field(name=f"#{selected['id']} · {selected['user_name']} · {selected['form_name']}", value=body[:1024], inline=False)
            elif not self.pending_applications():
                embed.add_field(name="Queue", value="No pending applications.", inline=False)
        elif self.page == "verification":
            embed.description = f"Verification is **{'Open' if _setting(self.db, 'verification_enabled', '1') == '1' else 'Closed'}**. Post the permanent Verify button in the current channel."
            embed.add_field(name="Unverified role", value=f"<@&{_setting(self.db, 'verification_unverified_role_id')}>", inline=True)
            embed.add_field(name="Guest role", value=f"<@&{_setting(self.db, 'verification_guest_role_id')}>", inline=True)
            embed.add_field(name="Member role", value=f"<@&{_setting(self.db, 'verification_member_role_id')}>", inline=True)
        elif self.page == "tester":
            embed.description = f"**{len(self.pending_feedback())}** pending reports · Review before accepting or rejecting."
            report = self.selected_feedback()
            if report:
                embed.add_field(name=f"#{report['id']} · {report['kind'].title()} · {report['user_name']}",
                                value=f"**{report['title']}**\n{report['details']}"[:1024], inline=False)
            elif not self.pending_feedback():
                embed.add_field(name="Queue", value="No pending Tester reports.", inline=False)
        elif self.page == "codes":
            counts=self.db.execute('SELECT COUNT(*) total,COALESCE(SUM(enabled=1),0) enabled FROM reward_codes').fetchone()
            embed.description = f"**{counts['enabled']} enabled** · **{counts['total']-counts['enabled']} disabled**\nPlayers redeem with `/code_redeem`."
            selected = self.selected_code()
            if selected:
                uses = f"{selected['uses']:,} / {selected['max_uses']:,}" if selected["max_uses"] else f"{selected['uses']:,} used · Unlimited total uses"
                item=self.db.execute('SELECT name FROM items WHERE id=?',(selected['item_id'],)).fetchone() if selected['item_id'] else None
                item_text=f"\n📦 {selected['item_quantity']}× {item['name'] if item else 'Unavailable item'}" if selected['item_id'] else ''
                embed.add_field(
                    name=f"{selected['code']} · {code_status(selected)}",
                    value=f"**Reward per redemption**\n🪙 {selected['reward_xc']:,} XC · ⚔️ {selected['reward_war_credits']:,} WC\n💎 {selected['reward_xcrystals']:,} XCrystals{item_text}\n**Usage** · {uses}",
                    inline=False,
                )
            elif not self.reward_codes():
                embed.add_field(name="Codes", value="No reward codes exist yet.", inline=False)
        embed.set_footer(text="Private panel · only the staff member who opened it can use the buttons")
        return embed

    async def refresh(self, interaction, *, notice=""):
        if not await self.interaction_check(interaction):
            return
        replacement = self.clone(notice=notice)
        await self._edit(interaction, replacement)

    def _resolve_target_channel(self, interaction):
        target_cid = self.target_channel_id or getattr(interaction, "channel_id", None)
        get_ch = getattr(self.bot, "get_channel", None)
        target_ch = None
        if get_ch and target_cid:
            try:
                target_ch = get_ch(int(target_cid))
            except Exception:
                target_ch = None
        return target_cid, target_ch or getattr(interaction, "channel", None)

    async def handle_action(self, interaction, action):
        if not await self.interaction_check(interaction):
            return
        if action.startswith('filter_codes:'):
            value=action.split(':',1)[1]
            if value not in {'all','enabled','disabled'}:return
            await self._edit(interaction,self.clone(code_filter=value,selected_code_id=None,offsets={**self.offsets,'codes':0}))
            return
        if action in {'backup_now','tier5_repair','tier6_repair'}:
            await self._edit(interaction,self.clone(page='maintenance',pending_action=action))
            return
        if action == 'cancel_maintenance':
            await self._edit(interaction,self.clone(pending_action=None))
            return
        if action.startswith('confirm_maintenance:'):
            requested=action.split(':',1)[1]
            if requested != self.pending_action or self.maintenance_used or requested not in {'backup_now','tier5_repair','tier6_repair'}:
                await interaction.response.send_message('Open a fresh maintenance confirmation.',ephemeral=True)
                return
            self.maintenance_used=True
            self.pending_action=None
            action=requested
        if action.startswith("list:"):
            _, kind, direction = action.split(":")
            offsets = dict(self.offsets)
            offsets[kind] = max(0, offsets.get(kind, 0) + int(direction) * 25)
            await self._edit(interaction, self.clone(offsets=offsets))
            return
        if action in {
            "system_status", "tier5_repair", "tier6_repair", "backup_now",
            "post_application", "post_verification", "post_tester_feedback",
            "server_post_gaming_roles", "server_post_onboarding_roles", "server_post_deadzone", "server_post_lounge_lobby",
        }:
            await interaction.response.defer()
        if action == "close":
            closed = discord.ui.LayoutView()
            closed.add_item(discord.ui.TextDisplay("Admin panel closed. Use /admin to reopen."))
            await interaction.response.edit_message(embed=None, view=closed)
            self.stop()
            return
        if action == "refresh":
            await self.refresh(interaction)
            return
        if action == "back":
            replacement = self.clone(page=self.history[-1] if self.history else "home", history=self.history[:-1],pending_action=None)
            await interaction.response.edit_message(embed=None, view=replacement)
            return
        if action.startswith("page:"):
            page = action.split(":", 1)[1]
            if page not in PAGES:
                await interaction.response.send_message('This admin section is unavailable.',ephemeral=True);return
            replacement = self.clone(page=page, history=(*self.history, self.page)[-20:] if page != self.page else self.history,pending_action=None)
            await interaction.response.edit_message(embed=None, view=replacement)
            return
        if action == "system_status":
            health = getattr(self.bot, "xbot_tier4_health", None)
            tier5_health = getattr(self.bot, "xbot_tier5_health", None)
            tier6_health = getattr(self.bot, "xbot_tier6_health", None)
            reports = [health() if health else "Tier 4 status is unavailable."]
            reports.append(tier5_health() if tier5_health else "Tier 5 status is unavailable.")
            reports.append(tier6_health() if tier6_health else "Tier 6 status is unavailable.")
            await self.refresh(interaction, notice=" | ".join(reports))
            return
        if action == "tier5_repair":
            repair = getattr(self.bot, "xbot_tier5_repair", None)
            await self.refresh(interaction, notice=repair() if repair else "❌ Tier 5 repair service is unavailable.")
            return
        if action == "tier6_repair":
            repair = getattr(self.bot, "xbot_tier6_repair", None)
            await self.refresh(interaction, notice=repair() if repair else "❌ Tier 6 repair service is unavailable.")
            return
        if action == "backup_now":
            backup = getattr(self.bot, "xbot_tier4_backup", None)
            if not backup:
                await self.refresh(interaction, notice="❌ Backup service is unavailable.")
                return
            target = backup()
            await self.refresh(interaction, notice=f"✅ Database backup created: {target.name}")
            return
        if action == "toggle_applications":
            value = "0" if _setting(self.db, "applications_enabled", "1") == "1" else "1"
            _set_setting(self.db, "applications_enabled", value)
            await self.refresh(interaction, notice=f"✅ Applications are now {'open' if value == '1' else 'closed'}.")
            return
        if action == "post_application":
            form = self.selected_form()
            if not form:
                await self.refresh(interaction, notice="❌ No open Application Form was found.")
                return
            target_cid, target_ch = self._resolve_target_channel(interaction)
            if not target_ch:
                await self.refresh(interaction, notice="❌ Target channel was not found.")
                return
            embed = discord.Embed(title=f"{form['emoji']} {form['name']}", description=form["description"], colour=discord.Color.blue())
            await target_ch.send(embed=embed, view=applications.ApplicationStartView(self.bot, self.db, form))
            await self.refresh(interaction, notice=f"✅ {form['name']} posted in <#{target_cid}>.")
            return
        if action.startswith("review:"):
            if not self.selected_application():
                await self.refresh(interaction, notice="❌ Choose a pending application first.")
                return
            await interaction.response.send_modal(ApplicationDecisionModal(self, action.split(":", 1)[1]))
            return
        if action == "toggle_verification":
            value = "0" if _setting(self.db, "verification_enabled", "1") == "1" else "1"
            _set_setting(self.db, "verification_enabled", value)
            await self.refresh(interaction, notice=f"✅ Verification is now {'open' if value == '1' else 'closed'}.")
            return
        if action == "post_verification":
            target_cid, target_ch = self._resolve_target_channel(interaction)
            if not target_ch:
                await self.refresh(interaction, notice="❌ Target channel was not found.")
                return
            await target_ch.send(view=applications.VerificationView(self.bot, self.db))
            await self.refresh(interaction, notice=f"✅ Verification panel posted in <#{target_cid}>.")
            return
        if action == "manual_verify_member":
            if not interaction.guild:
                await self.refresh(interaction, notice="❌ Use verification inside a server.")
                return
            target_id = self.target_user_id or self.owner_id
            target_member = interaction.guild.get_member(target_id)
            if not target_member:
                try: target_member = await interaction.guild.fetch_member(target_id)
                except Exception: target_member = None
            if not target_member:
                await self.refresh(interaction, notice=f"❌ Could not find member <@{target_id}> in this server.")
                return
            ok, msg = await applications.manual_verify_member(self.bot, self.db, interaction.guild, target_member, interaction.user)
            await self.refresh(interaction, notice=("✅ " if ok else "❌ ") + msg)
            return
        if action == "post_tester_feedback":
            target_cid, target_ch = self._resolve_target_channel(interaction)
            if not target_ch:
                await self.refresh(interaction, notice="❌ Target channel was not found.")
                return
            embed = discord.Embed(title="🧪 X BOT Tester Feedback", description="Testers: report a bug or suggest an improvement. Staff review every report in `/admin`.", colour=discord.Color.teal())
            await target_ch.send(embed=embed, view=tester_feedback.TesterFeedbackView(self.db))
            await self.refresh(interaction, notice=f"✅ Tester feedback panel posted in <#{target_cid}>.")
            return
        if action == "accept_feedback":
            if not self.selected_feedback():
                await self.refresh(interaction, notice="❌ Choose a Tester report first.")
                return
            await interaction.response.send_modal(FeedbackRewardModal(self))
            return
        if action == "reject_feedback":
            notice = self.review_feedback(interaction, "rejected", "", 0, 0)
            await self.refresh(interaction, notice=notice)
            return
        if action == "create_code":
            await interaction.response.send_modal(RewardCodeModal(self))
            return
        if action == "toggle_code":
            selected = self.selected_code()
            if not selected:
                await self.refresh(interaction, notice="❌ Choose a reward code first.")
                return
            enabled = 0 if selected["enabled"] else 1
            self.db.execute("UPDATE reward_codes SET enabled=? WHERE id=?", (enabled, selected["id"]))
            self.db.commit()
            await self.refresh(interaction, notice=f"✅ `{selected['code']}` is now {'enabled' if enabled else 'disabled'}.")
            return
        if action == "asset_edit_money":
            await interaction.response.send_modal(AssetEditMoneyModal(self))
            return
        if action == "asset_spawn_item":
            await interaction.response.send_modal(AssetSpawnItemModal(self))
            return
        if action == "asset_remove_item":
            await interaction.response.send_modal(AssetRemoveItemModal(self))
            return
        if action == "war_edit_troops":
            await interaction.response.send_modal(WarEditTroopsModal(self))
            return
        if action == "war_edit_capital":
            await interaction.response.send_modal(WarEditCapitalModal(self))
            return
        if action == "war_edit_resources":
            await interaction.response.send_modal(WarEditResourcesModal(self))
            return
        if action == "war_toggle_status":
            war = self.db.execute("SELECT * FROM wars WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
            if war:
                await self.refresh(interaction, notice=f"⚔️ Active War #{war['id']} ongoing. End war via `/war_end`.")
            else:
                await self.refresh(interaction, notice="🕊️ No active war currently ongoing. Start war via `/war_start`.")
            return
        if action == "server_toggle_level":
            current = leveling.setting(self.db, "xp_announcement_enabled") == "1"
            new_val = "0" if current else "1"
            _set_setting(self.db, "xp_announcement_enabled", new_val)
            await self.refresh(interaction, notice=f"✅ Level-up notices are now {'enabled' if new_val == '1' else 'disabled'}.")
            return
        if action == "server_set_level_channel":
            target_cid = self.target_channel_id or interaction.channel_id
            _set_setting(self.db, "xp_announcement_channel_id", str(target_cid))
            await self.refresh(interaction, notice=f"✅ Level-up announcement channel set to <#{target_cid}>.")
            return
        if action == "server_edit_template":
            await interaction.response.send_modal(ServerLevelTemplateModal(self))
            return
        if action == "server_post_gaming_roles":
            target_cid, target_ch = self._resolve_target_channel(interaction)
            if not target_ch:
                await self.refresh(interaction, notice="❌ Target channel was not found.")
                return
            embed = gaming.build_gaming_roles_embed()
            await target_ch.send(embed=embed, view=gaming.GameRolesView())
            await self.refresh(interaction, notice=f"✅ Roles panel posted in <#{target_cid}>!")
            return
        if action == "server_post_onboarding_roles":
            target_cid, target_ch = self._resolve_target_channel(interaction)
            if not target_ch:
                await self.refresh(interaction, notice="❌ Target channel was not found.")
                return
            embed = gaming.build_onboarding_embed()
            await target_ch.send(embed=embed, view=gaming.NewUserOnboardingView())
            await self.refresh(interaction, notice=f"✅ New User Onboarding panel posted in <#{target_cid}>!")
            return
        if action == "server_post_deadzone":
            target_cid, target_ch = self._resolve_target_channel(interaction)
            if not target_ch:
                await self.refresh(interaction, notice="❌ Target channel was not found.")
                return
            embed = deadzone.build_deadzone_board_embed(self.db)
            msg = await target_ch.send(embed=embed, view=deadzone.DeadzoneReviveView())
            self.db.execute("INSERT INTO economy_settings(key,value) VALUES('deadzone_crypt_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(target_cid),))
            self.db.execute("INSERT INTO economy_settings(key,value) VALUES('deadzone_crypt_message_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(msg.id),))
            self.db.commit()
            await self.refresh(interaction, notice=f"✅ Deadzone Revival board posted in <#{target_cid}>!")
            return
        if action == "server_post_lounge_lobby":
            target_cid, target_ch = self._resolve_target_channel(interaction)
            if not target_ch:
                await self.refresh(interaction, notice="❌ Target channel was not found.")
                return
            import lounges
            embed = lounges.build_lobby_embed(self.db)
            view = lounges.LoungeLobbyView()
            msg = await target_ch.send(embed=embed, view=view)
            lounges.set_setting(self.db, "lounge_lobby_channel_id", str(target_cid))
            lounges.set_setting(self.db, "lounge_lobby_message_id", str(msg.id))
            await self.refresh(interaction, notice=f"✅ Lounge Lobby panel posted in <#{target_cid}>!")
            return
        if action == "server_say":
            await interaction.response.send_modal(ServerSayModal(self))
            return
        if action == "server_lottery_draw":
            draw_func = getattr(self.bot, "xbot_lottery_draw", None)
            if draw_func:
                res = draw_func()
                await self.refresh(interaction, notice=f"✅ Lottery drawn: {res}")
            else:
                await self.refresh(interaction, notice="✅ Lottery draw requested.")
            return

    async def review_application(self, interaction, decision, reason):
        row = self.selected_application()
        if not row:
            return "❌ That application is no longer pending."
        self.db.execute(
            "UPDATE application_submissions SET status=?,reviewer_id=?,reviewer_name=?,reason=?,reviewed_at=? WHERE id=?",
            (decision, interaction.user.id, interaction.user.display_name, reason, int(time.time()), row["id"]),
        )
        self.db.commit()
        applicant = interaction.guild.get_member(row["user_id"]) if interaction.guild else None
        if applicant is None and interaction.guild:
            try:
                applicant = await interaction.guild.fetch_member(row["user_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                applicant = None
        if decision == "accepted" and applicant:
            accepted_ids = set(filter(None, (row["accepted_role_ids"] + "," + row["pending_role_ids"]).split(",")))
            roles = [interaction.guild.get_role(int(role_id)) for role_id in accepted_ids if role_id.isdigit()]
            roles = [role for role in roles if role]
            if roles:
                try:
                    await applicant.add_roles(*roles, reason=f"Accepted application #{row['id']}")
                except discord.HTTPException:
                    return f"⚠️ Application #{row['id']} accepted, but X BOT could not give its configured role."
        decision_name = {"accepted": "Accepted", "hold": "On hold", "denied": "Denied"}[decision]
        result_body = f"**Application:** #{row['id']} — {row['form_name']}\n**Applicant:** <@{row['user_id']}>\n**Decision:** {decision_name}\n**Reviewer:** {interaction.user.mention}\n**Reason:** {reason or 'No reason provided'}"
        result_channel = self.bot.get_channel(int(row["result_channel_id"])) if row["result_channel_id"] != "0" else None
        if result_channel:
            colour = discord.Color.green() if decision == "accepted" else discord.Color.red() if decision == "denied" else discord.Color.gold()
            try:
                await result_channel.send(embed=discord.Embed(title="📋 Application Result", description=result_body, colour=colour))
            except discord.HTTPException:
                pass
        return f"✅ Application #{row['id']} is now **{decision_name}**."

    def review_feedback(self, interaction, decision, note, xc, credits):
        row = self.selected_feedback()
        if not row:
            return "❌ That Tester report is no longer pending."
        self.db.execute("""UPDATE tester_feedback SET status=?,reviewer_id=?,reviewer_name=?,review_note=?,reward_xc=?,reward_war_credits=?,reviewed_at=? WHERE id=?""",
                        (decision, interaction.user.id, interaction.user.display_name, note, xc, credits, int(time.time()), row['id']))
        if decision == "accepted" and (xc or credits):
            self.db.execute("UPDATE players SET xc=xc+?,money=money+? WHERE user_id=?", (xc, credits, row['user_id']))
            self.db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
                            (row['user_id'], 'tester_feedback_reward', f"Tester report #{row['id']}", int(time.time())))
        self.db.commit()
        return f"✅ Tester report #{row['id']} {decision}." + (f" Rewarded {xc:,} XC and {credits:,} War Credits." if xc or credits else "")


def register_commands(bot, db, staff_check, command_kwargs):
    @bot.tree.command(name="admin", description="Open the private X BOT Staff Control Centre", **command_kwargs)
    @app_commands.default_permissions(moderate_members=True)
    async def admin(interaction: discord.Interaction):
        if not staff_check(interaction):
            await interaction.response.send_message("Only Administrators and Moderators can open this panel.", ephemeral=True)
            return
        panel = AdminPanel(bot, db, staff_check, interaction.user.id)
        await interaction.response.send_message(view=panel, ephemeral=True)
