"""Single-entry Discord control centre for X BOT staff."""

import sqlite3
import time

import discord
from discord import app_commands

import applications


def _setting(db, key, default="0"):
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


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
        super().__init__(placeholder="Choose a pending application…", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_application_id = int(self.values[0])
        await self.view.refresh(interaction)


class RewardCodeSelect(discord.ui.Select):
    def __init__(self, rows, selected_id=None):
        options = [
            discord.SelectOption(
                label=row["code"][:100],
                value=str(row["id"]),
                description=(f"{'Enabled' if row['enabled'] else 'Disabled'} · {row['uses']}/{row['max_uses'] or '∞'} uses")[:100],
                default=int(row["id"]) == int(selected_id or 0),
            )
            for row in rows[:25]
        ]
        super().__init__(placeholder="Choose a reward code…", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_code_id = int(self.values[0])
        await self.view.refresh(interaction)


class ApplicationDecisionModal(discord.ui.Modal):
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
        await interaction.response.defer()
        notice = await self.panel.review_application(interaction, self.decision, str(self.reason).strip())
        replacement = self.panel.clone(notice=notice, selected_application_id=None)
        await interaction.edit_original_response(embed=replacement.build_embed(), view=replacement)


class RewardCodeModal(discord.ui.Modal, title="Create reward code"):
    code = discord.ui.TextInput(label="Code", placeholder="Example: SEASON100", max_length=40)
    xc = discord.ui.TextInput(label="XC", placeholder="0", default="0", max_length=10)
    war_credits = discord.ui.TextInput(label="War Credits", placeholder="0", default="0", max_length=10)
    xcrystals = discord.ui.TextInput(label="XCrystals", placeholder="0", default="0", max_length=10)
    max_uses = discord.ui.TextInput(label="Maximum uses (0 = unlimited)", placeholder="0", default="0", max_length=10)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        clean_code = str(self.code).strip().upper()
        try:
            values = [max(0, int(str(field).strip() or "0")) for field in (self.xc, self.war_credits, self.xcrystals, self.max_uses)]
        except ValueError:
            replacement = self.panel.clone(notice="❌ XC, War Credits, XCrystals and uses must be whole numbers.")
            await interaction.response.edit_message(embed=replacement.build_embed(), view=replacement)
            return
        if not clean_code or not all(character.isalnum() or character in "_-" for character in clean_code):
            replacement = self.panel.clone(notice="❌ Code may only use letters, numbers, `_` and `-`.")
            await interaction.response.edit_message(embed=replacement.build_embed(), view=replacement)
            return
        xc, war_credits, xcrystals, max_uses = values
        if not any((xc, war_credits, xcrystals)):
            replacement = self.panel.clone(notice="❌ Add at least one currency reward.")
            await interaction.response.edit_message(embed=replacement.build_embed(), view=replacement)
            return
        try:
            cursor = self.panel.db.execute(
                """INSERT INTO reward_codes(code,reward_xc,reward_war_credits,reward_xcrystals,max_uses,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (clean_code, xc, war_credits, xcrystals, max_uses, interaction.user.id, int(time.time())),
            )
            self.panel.db.commit()
        except sqlite3.IntegrityError:
            replacement = self.panel.clone(notice=f"❌ `{clean_code}` already exists.")
            await interaction.response.edit_message(embed=replacement.build_embed(), view=replacement)
            return
        replacement = self.panel.clone(notice=f"✅ Created `{clean_code}`.", selected_code_id=cursor.lastrowid)
        await interaction.response.edit_message(embed=replacement.build_embed(), view=replacement)


class AdminPanel(discord.ui.View):
    def __init__(self, bot, db, staff_check, owner_id, *, page="home", selected_application_id=None, selected_code_id=None, notice=""):
        super().__init__(timeout=900)
        self.bot = bot
        self.db = db
        self.staff_check = staff_check
        self.owner_id = owner_id
        self.page = page
        self.selected_application_id = selected_application_id
        self.selected_code_id = selected_code_id
        self.notice = notice
        self._build_controls()

    def clone(self, **changes):
        values = {
            "page": self.page,
            "selected_application_id": self.selected_application_id,
            "selected_code_id": self.selected_code_id,
            "notice": "",
        }
        values.update(changes)
        return AdminPanel(self.bot, self.db, self.staff_check, self.owner_id, **values)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id == self.owner_id and self.staff_check(interaction):
            return True
        await interaction.response.send_message("This Staff panel belongs to another Administrator or Moderator.", ephemeral=True)
        return False

    def _build_controls(self):
        navigation = (
            ("home", "Home", "🏠"),
            ("applications", "Applications", "📋"),
            ("verification", "Verification", "✅"),
            ("codes", "Reward Codes", "🎟️"),
        )
        for page, label, emoji in navigation:
            self.add_item(AdminActionButton(f"page:{page}", label, emoji=emoji, style=discord.ButtonStyle.primary if self.page == page else discord.ButtonStyle.secondary, row=0))

        if self.page == "applications":
            pending = self.pending_applications()
            if pending:
                self.add_item(PendingApplicationSelect(pending, self.selected_application_id))
            self.add_item(AdminActionButton("post_application", "Post Tester Panel Here", emoji="📨", style=discord.ButtonStyle.primary, row=2))
            self.add_item(AdminActionButton("toggle_applications", "Open / Close", emoji="🔁", row=2))
            if self.selected_application():
                self.add_item(AdminActionButton("review:accepted", "Accept", emoji="✅", style=discord.ButtonStyle.success, row=3))
                self.add_item(AdminActionButton("review:hold", "Hold", emoji="⏸️", row=3))
                self.add_item(AdminActionButton("review:denied", "Deny", emoji="❌", style=discord.ButtonStyle.danger, row=3))
        elif self.page == "verification":
            self.add_item(AdminActionButton("post_verification", "Post Verification Here", emoji="✅", style=discord.ButtonStyle.success, row=1))
            self.add_item(AdminActionButton("toggle_verification", "Open / Close", emoji="🔁", row=1))
        elif self.page == "codes":
            codes = self.reward_codes()
            if codes:
                self.add_item(RewardCodeSelect(codes, self.selected_code_id))
            self.add_item(AdminActionButton("create_code", "Create Code", emoji="➕", style=discord.ButtonStyle.success, row=2))
            if self.selected_code():
                self.add_item(AdminActionButton("toggle_code", "Enable / Disable", emoji="🔁", row=2))

    def pending_applications(self):
        return self.db.execute(
            """SELECT s.id,s.user_name,s.status,f.name form_name FROM application_submissions s
               JOIN application_forms f ON f.id=s.form_id WHERE s.status IN ('pending','hold')
               ORDER BY s.created_at LIMIT 25"""
        ).fetchall()

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
        return self.db.execute("SELECT * FROM reward_codes ORDER BY enabled DESC,id DESC LIMIT 25").fetchall()

    def selected_code(self):
        if not self.selected_code_id:
            return None
        return self.db.execute("SELECT * FROM reward_codes WHERE id=?", (self.selected_code_id,)).fetchone()

    def build_embed(self):
        embed = discord.Embed(title="🛡️ X BOT Staff Control Centre", colour=discord.Color.blurple())
        if self.notice:
            embed.add_field(name="Latest action", value=self.notice[:1024], inline=False)
        if self.page == "home":
            pending = self.db.execute("SELECT COUNT(*) FROM application_submissions WHERE status IN ('pending','hold')").fetchone()[0]
            active_codes = self.db.execute("SELECT COUNT(*) FROM reward_codes WHERE enabled=1").fetchone()[0]
            embed.description = "Use the tabs below. Staff actions stay inside this private panel."
            embed.add_field(name="📋 Pending applications", value=str(pending), inline=True)
            embed.add_field(name="🎟️ Active reward codes", value=str(active_codes), inline=True)
            embed.add_field(name="✅ Verification", value="Open" if _setting(self.db, "verification_enabled", "1") == "1" else "Closed", inline=True)
        elif self.page == "applications":
            embed.description = f"Applications are **{'Open' if _setting(self.db, 'applications_enabled', '1') == '1' else 'Closed'}**. Post the Tester form or review a pending submission."
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
        elif self.page == "codes":
            embed.description = "Create currency reward codes or enable/disable an existing code. Players redeem them with `/code_redeem`."
            selected = self.selected_code()
            if selected:
                uses = f"{selected['uses']:,}/{selected['max_uses']:,}" if selected["max_uses"] else f"{selected['uses']:,}/∞"
                embed.add_field(
                    name=f"{selected['code']} · {'Enabled' if selected['enabled'] else 'Disabled'}",
                    value=f"🪙 {selected['reward_xc']:,} XC\n⚔️ {selected['reward_war_credits']:,} War Credits\n💎 {selected['reward_xcrystals']:,} XCrystals\nUses: {uses}",
                    inline=False,
                )
            elif not self.reward_codes():
                embed.add_field(name="Codes", value="No reward codes exist yet.", inline=False)
        embed.set_footer(text="Private panel · only the staff member who opened it can use the buttons")
        return embed

    async def refresh(self, interaction, *, notice=""):
        replacement = self.clone(notice=notice)
        await interaction.response.edit_message(embed=replacement.build_embed(), view=replacement)

    async def handle_action(self, interaction, action):
        if action.startswith("page:"):
            replacement = self.clone(page=action.split(":", 1)[1], selected_application_id=None, selected_code_id=None)
            await interaction.response.edit_message(embed=replacement.build_embed(), view=replacement)
            return
        if action == "toggle_applications":
            value = "0" if _setting(self.db, "applications_enabled", "1") == "1" else "1"
            _set_setting(self.db, "applications_enabled", value)
            await self.refresh(interaction, notice=f"✅ Applications are now {'open' if value == '1' else 'closed'}.")
            return
        if action == "post_application":
            form = self.db.execute("SELECT * FROM application_forms WHERE name=? COLLATE NOCASE", (applications.TESTER_FORM_NAME,)).fetchone()
            if not form:
                await self.refresh(interaction, notice="❌ Tester Application was not found.")
                return
            embed = discord.Embed(title=f"{form['emoji']} {form['name']}", description=form["description"], colour=discord.Color.blue())
            await interaction.channel.send(embed=embed, view=applications.ApplicationStartView(self.bot, self.db, form))
            await self.refresh(interaction, notice=f"✅ Tester Application posted in <#{interaction.channel_id}>.")
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
            await interaction.channel.send(view=applications.VerificationView(self.bot, self.db))
            await self.refresh(interaction, notice=f"✅ Verification panel posted in <#{interaction.channel_id}>.")
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


def register_commands(bot, db, staff_check, command_kwargs):
    @bot.tree.command(name="admin", description="Open the private X BOT Staff Control Centre", **command_kwargs)
    @app_commands.default_permissions(moderate_members=True)
    async def admin(interaction: discord.Interaction):
        if not staff_check(interaction):
            await interaction.response.send_message("Only Administrators and Moderators can open this panel.", ephemeral=True)
            return
        panel = AdminPanel(bot, db, staff_check, interaction.user.id)
        await interaction.response.send_message(embed=panel.build_embed(), view=panel, ephemeral=True)
