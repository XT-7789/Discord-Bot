"""Single-entry Discord control centre for X BOT staff."""

import sqlite3
import time

import discord
from discord import app_commands

import applications
import tester_feedback


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
                description=(f"{'Enabled' if row['enabled'] else 'Disabled'} · {row['uses']}/{row['max_uses'] or '∞'} uses")[:100],
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


class AdminPanel(discord.ui.LayoutView):
    def __init__(self, bot, db, staff_check, owner_id, *, page="home", selected_form_id=None, selected_application_id=None, selected_code_id=None, selected_feedback_id=None, notice="", history=(), offsets=None):
        super().__init__(timeout=900)
        self.bot = bot
        self.db = db
        self.staff_check = staff_check
        self.owner_id = owner_id
        self.page = page
        self.selected_form_id = selected_form_id
        self.selected_application_id = selected_application_id
        self.selected_code_id = selected_code_id
        self.selected_feedback_id = selected_feedback_id
        self.notice = notice
        self.history = history
        self.offsets = dict(offsets or {})
        self._controls = []
        self._build_controls()
        content = self.build_embed()
        parts = [discord.ui.TextDisplay(f"-# ✦ X SYSTEM · ADMIN\n# {self.page.replace('_', ' ').upper()}\n{content.description or ''}")]
        for field in content.fields:
            parts.extend((discord.ui.Separator(), discord.ui.TextDisplay(f"### {field.name}\n{field.value}")))
        rows = {}
        for control in self._controls:
            row = control.row or 0
            control.row = None
            rows.setdefault(row, []).append(control)
        for row in sorted(rows):
            controls = rows[row]
            width = 3 if row == 0 else 5
            for start in range(0, len(controls), width):
                parts.append(discord.ui.ActionRow(*controls[start:start + width]))
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
            "selected_form_id": self.selected_form_id,
            "selected_application_id": self.selected_application_id,
            "selected_code_id": self.selected_code_id,
            "selected_feedback_id": self.selected_feedback_id,
            "notice": "",
            "history": self.history,
            "offsets": self.offsets,
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
        if len(rows) > 25:
            for direction, target, label in ((-1, offset - 25, f"‹ {kind.title()}"), (1, offset + 25, f"{kind.title()} ›")):
                button = AdminActionButton(f"list:{kind}:{direction}", label, row=3)
                button.disabled = target < 0 or target >= len(rows)
                self.add_item(button)
        return rows[offset:offset + 25]

    def _build_controls(self):
        navigation = (
            ("home", "Home", "🏠"),
            ("applications", "Applications", "📋"),
            ("tester", "Tester Reports", "🐛"),
            ("verification", "Verification", "✅"),
            ("codes", "Reward Codes", "🎟️"),
            ("economy", "Economy", "💰"),
        )
        for page, label, emoji in navigation:
            self.add_item(AdminActionButton(f"page:{page}", label, emoji=emoji, style=discord.ButtonStyle.primary if self.page == page else discord.ButtonStyle.secondary, row=0))

        if self.page == "applications":
            forms = self.application_forms()
            if forms:
                if self.selected_form_id is None:
                    self.selected_form_id = forms[0]['id']
                self.add_item(ApplicationFormSelect(self._page_rows(forms, "forms"), self.selected_form_id))
            pending = self.pending_applications()
            if pending:
                self.add_item(PendingApplicationSelect(self._page_rows(pending, "applications"), self.selected_application_id))
            self.add_item(AdminActionButton("post_application", "Post Selected Form Here", emoji="📨", style=discord.ButtonStyle.primary, row=3))
            self.add_item(AdminActionButton("toggle_applications", "Open / Close", emoji="🔁", row=3))
            if self.selected_application():
                self.add_item(AdminActionButton("review:accepted", "Accept", emoji="✅", style=discord.ButtonStyle.success, row=4))
                self.add_item(AdminActionButton("review:hold", "Hold", emoji="⏸️", row=4))
                self.add_item(AdminActionButton("review:denied", "Deny", emoji="❌", style=discord.ButtonStyle.danger, row=4))
        elif self.page == "verification":
            self.add_item(AdminActionButton("post_verification", "Post Verification Here", emoji="✅", style=discord.ButtonStyle.success, row=1))
            self.add_item(AdminActionButton("toggle_verification", "Open / Close", emoji="🔁", row=1))
        elif self.page == "tester":
            reports = self.pending_feedback()
            if reports:
                self.add_item(TesterFeedbackSelect(self._page_rows(reports, "reports"), self.selected_feedback_id))
            self.add_item(AdminActionButton("post_tester_feedback", "Post Tester Panel Here", emoji="📨", style=discord.ButtonStyle.primary, row=2))
            if self.selected_feedback():
                self.add_item(AdminActionButton("accept_feedback", "Accept + Reward", emoji="🎁", style=discord.ButtonStyle.success, row=2))
                self.add_item(AdminActionButton("reject_feedback", "Reject", emoji="❌", style=discord.ButtonStyle.danger, row=2))
        elif self.page == "codes":
            codes = self.reward_codes()
            if codes:
                self.add_item(RewardCodeSelect(self._page_rows(codes, "codes"), self.selected_code_id))
            self.add_item(AdminActionButton("create_code", "Create Code", emoji="➕", style=discord.ButtonStyle.success, row=2))
            if self.selected_code():
                self.add_item(AdminActionButton("toggle_code", "Enable / Disable", emoji="🔁", row=2))
        elif self.page == "home":
            import staff_tools
            self.add_item(staff_tools.ToolSelect(self))
            self.add_item(AdminActionButton("system_status", "System Status", emoji="📡", style=discord.ButtonStyle.primary, row=1))
            self.add_item(AdminActionButton("backup_now", "Backup Now", emoji="💾", style=discord.ButtonStyle.success, row=1))
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
        return self.db.execute("SELECT * FROM reward_codes ORDER BY enabled DESC,id DESC").fetchall()

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
            embed.description = "Choose a management tool below — no command typing. Select targets, fill in details, then confirm.\nMember tools · Assets · Announcements · Alliance War\nEconomy rules remain editable in Dashboard only."
            embed.add_field(name="📋 Pending applications", value=str(pending), inline=True)
            embed.add_field(name="🎟️ Active reward codes", value=str(active_codes), inline=True)
            embed.add_field(name="✅ Verification", value="Open" if _setting(self.db, "verification_enabled", "1") == "1" else "Closed", inline=True)
            reports = self.db.execute("SELECT COUNT(*) FROM tester_feedback WHERE status='pending'").fetchone()[0]
            embed.add_field(name="🐛 Tester reports", value=str(reports), inline=True)
        elif self.page == "applications":
            embed.description = f"Applications are **{'Open' if _setting(self.db, 'applications_enabled', '1') == '1' else 'Closed'}**. Choose any open form to post, or review a pending submission."
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
            embed.description = "Post a simple bug/suggestion panel for Testers. Review each report and optionally reward useful testing."
            report = self.selected_feedback()
            if report:
                embed.add_field(name=f"#{report['id']} · {report['kind'].title()} · {report['user_name']}",
                                value=f"**{report['title']}**\n{report['details']}"[:1024], inline=False)
            elif not self.pending_feedback():
                embed.add_field(name="Queue", value="No pending Tester reports.", inline=False)
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
        if not await self.interaction_check(interaction):
            return
        replacement = self.clone(notice=notice)
        await self._edit(interaction, replacement)

    async def handle_action(self, interaction, action):
        if not await self.interaction_check(interaction):
            return
        if action.startswith("list:"):
            _, kind, direction = action.split(":")
            offsets = dict(self.offsets)
            offsets[kind] = max(0, offsets.get(kind, 0) + int(direction) * 25)
            await self._edit(interaction, self.clone(offsets=offsets))
            return
        if action in {"system_status", "tier5_repair", "tier6_repair", "backup_now", "post_application", "post_verification", "post_tester_feedback"}:
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
            replacement = self.clone(page=self.history[-1] if self.history else "home", history=self.history[:-1])
            await interaction.response.edit_message(embed=None, view=replacement)
            return
        if action.startswith("page:"):
            page = action.split(":", 1)[1]
            replacement = self.clone(page=page, history=(*self.history, self.page)[-20:] if page != self.page else self.history)
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
            embed = discord.Embed(title=f"{form['emoji']} {form['name']}", description=form["description"], colour=discord.Color.blue())
            await interaction.channel.send(embed=embed, view=applications.ApplicationStartView(self.bot, self.db, form))
            await self.refresh(interaction, notice=f"✅ {form['name']} posted in <#{interaction.channel_id}>.")
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
        if action == "post_tester_feedback":
            embed = discord.Embed(title="🧪 X BOT Tester Feedback", description="Testers: report a bug or suggest an improvement. Staff review every report in `/admin`.", colour=discord.Color.teal())
            await interaction.channel.send(embed=embed, view=tester_feedback.TesterFeedbackView(self.db))
            await self.refresh(interaction, notice=f"✅ Tester feedback panel posted in <#{interaction.channel_id}>.")
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
