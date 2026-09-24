"""X BOT Beta 1.4B verification and staff application system."""
import time

import discord
from discord import app_commands

import xbot_ui


DEFAULTS = {
    "verification_enabled": "1", "verification_guest_role_id": "1524715220365217842",
    "verification_unverified_role_id": "1541835319349870712",
    "verification_member_role_id": "1505437941647015986", "verification_log_channel_id": "0",
    "verification_min_account_days": "0", "applications_enabled": "1",
}

# Testers are players who help try new content and report problems. They are
# not staff: this role grants no Dashboard, moderation, economy, or war-admin
# access. The two existing senior roles review tester applications.
TESTER_ROLE_ID = "1534196579794161784"
TESTER_REVIEWER_ROLE_IDS = "1523954152701431848,1531176720097476708"
TESTER_FORM_NAME = "Tester Application"


def _seed_tester_questions(db, form_id):
    tester_questions = [
        ("Why would you like to become an X BOT Tester?", "Tell us what you want to help test.", 1, 1, 10, "long"),
        ("Which device do you mainly use?", "For example: Android, iPhone, Windows, Discord desktop.", 0, 1, 20, "short"),
        ("How often can you test and send feedback?", "For example: daily, weekends, a few times per week.", 0, 1, 30, "short"),
        ("How would you report a bug?", "Explain the steps, expected result, and what happened instead.", 1, 1, 40, "long"),
    ]
    db.executemany(
        """INSERT INTO application_questions(form_id,label,placeholder,paragraph,required,position,question_type)
           VALUES(?,?,?,?,?,?,?)""",
        [(form_id, *question) for question in tester_questions],
    )


def _ensure_single_tester_form(db):
    """Merge old Tester forms without deleting historical submissions."""
    forms = db.execute("SELECT * FROM application_forms ORDER BY id").fetchall()
    tester_forms = []
    for form in forms:
        accepted = set(filter(None, (form["accepted_role_ids"] or form["accepted_role_id"] or "").split(",")))
        if TESTER_ROLE_ID in accepted or "tester" in form["name"].casefold():
            tester_forms.append(form)

    if not tester_forms:
        cursor = db.execute(
            """INSERT INTO application_forms(name,emoji,description,reviewer_role_ids,accepted_role_ids,cooldown_seconds,enabled)
               VALUES(?,?,?,?,?,?,1)""",
            (
                TESTER_FORM_NAME,
                "🧪",
                "Help test upcoming X BOT features, report clear bugs, and give useful feedback. Testers do not receive staff or Dashboard permissions.",
                TESTER_REVIEWER_ROLE_IDS,
                TESTER_ROLE_ID,
                7 * 86400,
            ),
        )
        _seed_tester_questions(db, cursor.lastrowid)
        return

    def submission_count(form):
        return db.execute("SELECT COUNT(*) FROM application_submissions WHERE form_id=?", (form["id"],)).fetchone()[0]

    def question_count(form):
        return db.execute("SELECT COUNT(*) FROM application_questions WHERE form_id=?", (form["id"],)).fetchone()[0]

    # Prefer the form that owns real history as the permanent record. Use the
    # most complete current question set for future applications.
    canonical = max(tester_forms, key=lambda form: (submission_count(form), -int(form["id"])))
    question_source = max(tester_forms, key=lambda form: (question_count(form), int(form["id"])))
    canonical_id = int(canonical["id"])
    source_id = int(question_source["id"])

    for form in tester_forms:
        form_id = int(form["id"])
        if form_id != canonical_id:
            db.execute("UPDATE application_submissions SET form_id=? WHERE form_id=?", (canonical_id, form_id))

    if source_id != canonical_id:
        old_question_ids = [row[0] for row in db.execute("SELECT id FROM application_questions WHERE form_id=?", (canonical_id,))]
        if old_question_ids:
            db.executemany("DELETE FROM application_question_options WHERE question_id=?", [(question_id,) for question_id in old_question_ids])
        db.execute("DELETE FROM application_questions WHERE form_id=?", (canonical_id,))
        db.execute("UPDATE application_questions SET form_id=? WHERE form_id=?", (canonical_id, source_id))

    for form in tester_forms:
        form_id = int(form["id"])
        if form_id == canonical_id:
            continue
        remaining_question_ids = [row[0] for row in db.execute("SELECT id FROM application_questions WHERE form_id=?", (form_id,))]
        if remaining_question_ids:
            db.executemany("DELETE FROM application_question_options WHERE question_id=?", [(question_id,) for question_id in remaining_question_ids])
        db.execute("DELETE FROM application_questions WHERE form_id=?", (form_id,))
        db.execute("DELETE FROM application_forms WHERE id=?", (form_id,))

    db.execute(
        """UPDATE application_forms SET name=?,emoji=?,description=?,reviewer_role_ids=?,accepted_role_ids=?,
           cooldown_seconds=?,enabled=1 WHERE id=?""",
        (
            TESTER_FORM_NAME,
            "🧪",
            "Help test upcoming X BOT features, report clear bugs, and give useful feedback. Testers do not receive staff or Dashboard permissions.",
            TESTER_REVIEWER_ROLE_IDS,
            TESTER_ROLE_ID,
            7 * 86400,
            canonical_id,
        ),
    )
    if question_count({"id": canonical_id}) == 0:
        _seed_tester_questions(db, canonical_id)


def initialise(db):
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))
    db.execute("""CREATE TABLE IF NOT EXISTS application_forms(
        id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE COLLATE NOCASE,emoji TEXT NOT NULL DEFAULT '📋',
        description TEXT NOT NULL DEFAULT '',reviewer_role_id TEXT NOT NULL DEFAULT '',accepted_role_id TEXT NOT NULL DEFAULT '',
        review_channel_id TEXT NOT NULL DEFAULT '0',result_channel_id TEXT NOT NULL DEFAULT '0',
        cooldown_seconds INTEGER NOT NULL DEFAULT 86400,enabled INTEGER NOT NULL DEFAULT 1
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS application_questions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,form_id INTEGER NOT NULL,label TEXT NOT NULL,placeholder TEXT NOT NULL DEFAULT '',
        paragraph INTEGER NOT NULL DEFAULT 1,required INTEGER NOT NULL DEFAULT 1,position INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS application_submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,form_id INTEGER NOT NULL,user_id INTEGER NOT NULL,user_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',reviewer_id INTEGER,reviewer_name TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,reviewed_at INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS application_answers(
        submission_id INTEGER NOT NULL,question_id INTEGER NOT NULL,question TEXT NOT NULL,answer TEXT NOT NULL,
        PRIMARY KEY(submission_id,question_id)
    )""")
    form_columns = {row[1] for row in db.execute("PRAGMA table_info(application_forms)")}
    for column in ("reviewer_role_ids", "accepted_role_ids"):
        if column not in form_columns:
            db.execute(f"ALTER TABLE application_forms ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    question_columns = {row[1] for row in db.execute("PRAGMA table_info(application_questions)")}
    if "question_type" not in question_columns:
        db.execute("ALTER TABLE application_questions ADD COLUMN question_type TEXT NOT NULL DEFAULT 'text'")
    submission_columns = {row[1] for row in db.execute("PRAGMA table_info(application_submissions)")}
    if "pending_role_ids" not in submission_columns:
        db.execute("ALTER TABLE application_submissions ADD COLUMN pending_role_ids TEXT NOT NULL DEFAULT ''")
    db.execute("""CREATE TABLE IF NOT EXISTS application_question_options(
        id INTEGER PRIMARY KEY AUTOINCREMENT,question_id INTEGER NOT NULL,label TEXT NOT NULL,
        emoji TEXT NOT NULL DEFAULT '🏢',role_id TEXT NOT NULL DEFAULT '',position INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("UPDATE application_forms SET reviewer_role_ids=reviewer_role_id WHERE reviewer_role_ids='' AND reviewer_role_id!=''")
    db.execute("UPDATE application_forms SET accepted_role_ids=accepted_role_id WHERE accepted_role_ids='' AND accepted_role_id!=''")
    _ensure_single_tester_form(db)
    db.commit()


def setting(db, key):
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS[key]


async def handle_member_join(bot, db, member: discord.Member):
    """Give every new human member the configured Unverified role."""
    if member.bot or setting(db, "verification_enabled") != "1":
        return
    role_id = setting(db, "verification_unverified_role_id")
    unverified = member.guild.get_role(int(role_id)) if str(role_id).isdigit() and int(role_id) else None
    if not unverified or unverified in member.roles:
        return
    try:
        await member.add_roles(unverified, reason="X BOT new member verification gate")
    except discord.HTTPException as error:
        print(f"Could not give Unverified role to {member.id}: {error}")


async def complete_verification(bot, db, interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use verification inside the server.", ephemeral=True); return
    if setting(db, "verification_enabled") != "1":
        await interaction.response.send_message(view=xbot_ui.warning("Verification Closed", "Verification is currently disabled."), ephemeral=True); return
    account_days = (discord.utils.utcnow() - interaction.user.created_at).days
    minimum = int(setting(db, "verification_min_account_days"))
    if account_days < minimum:
        await interaction.response.send_message(view=xbot_ui.danger("Verification Failed", f"Your Discord account must be at least **{minimum} days old**."), ephemeral=True); return
    guest = interaction.guild.get_role(int(setting(db, "verification_guest_role_id")))
    if not guest:
        await interaction.response.send_message(view=xbot_ui.danger("Verification Setup Error", "The configured Guest role no longer exists."), ephemeral=True); return
    try:
        if guest not in interaction.user.roles: await interaction.user.add_roles(guest, reason="X BOT verification completed")
        unverified_id = setting(db, "verification_unverified_role_id")
        unverified = interaction.guild.get_role(int(unverified_id)) if str(unverified_id).isdigit() and int(unverified_id) else None
        if unverified and unverified in interaction.user.roles:
            await interaction.user.remove_roles(unverified, reason="X BOT verification completed")
    except discord.HTTPException:
        await interaction.response.send_message(view=xbot_ui.danger("Verification Failed", "Move the X BOT role above both the Unverified and Guest roles, then try again."), ephemeral=True); return
    channel_id = int(setting(db, "verification_log_channel_id") or 0); channel = bot.get_channel(channel_id) if channel_id else None
    if channel:
        try: await channel.send(view=xbot_ui.success("🔓 Guest Verified", f"{interaction.user.mention} completed verification and received {guest.mention}.\nThey will become a Member automatically at **Level 2**."))
        except discord.HTTPException: pass
    await interaction.response.send_message(view=xbot_ui.success("🔓 Verification Complete", f"You now have {guest.mention}. Participate in chat or voice and reach **Level 2** to become a Member."), ephemeral=True)


async def manual_verify_member(bot, db, guild: discord.Guild, member: discord.Member, admin: discord.Member) -> tuple[bool, str]:
    """Admin tool: manually verify a member, grant Guest role, and strip Unverified role."""
    guest_id = setting(db, "verification_guest_role_id")
    guest = guild.get_role(int(guest_id)) if str(guest_id).isdigit() and int(guest_id) else None
    if not guest:
        return False, "The configured Guest role no longer exists in this server."

    unverified_id = setting(db, "verification_unverified_role_id")
    unverified = guild.get_role(int(unverified_id)) if str(unverified_id).isdigit() and int(unverified_id) else None

    try:
        if guest not in member.roles:
            await member.add_roles(guest, reason=f"Manually verified by {admin.display_name}")
        if unverified and unverified in member.roles:
            await member.remove_roles(unverified, reason=f"Manually verified by {admin.display_name}")
    except discord.Forbidden:
        return False, "Bot lacks permission to manage roles. Move the X BOT role above both the Unverified and Guest roles, then try again."
    except discord.HTTPException as e:
        return False, f"Discord error updating roles: {e}"

    channel_id = int(setting(db, "verification_log_channel_id") or 0)
    channel = bot.get_channel(channel_id) if channel_id else None
    if channel:
        try:
            await channel.send(view=xbot_ui.success(
                "🔓 Member Manually Verified",
                f"{member.mention} was manually verified by {admin.mention} and received {guest.mention}."
            ))
        except discord.HTTPException:
            pass

    return True, f"Successfully verified {member.mention}! Granted {guest.mention} and removed Unverified."


class VerificationButton(discord.ui.Button):
    def __init__(self, bot, db):
        super().__init__(label="Verify", emoji="🔒", style=discord.ButtonStyle.primary, custom_id="xbot:verification:complete")
        self.bot = bot; self.db = db

    async def callback(self, interaction):
        await complete_verification(self.bot, self.db, interaction)


class VerificationView(discord.ui.LayoutView):
    def __init__(self, bot, db):
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_color=discord.Color.blue())
        container.add_item(discord.ui.TextDisplay("## 🔐 Server Verification\nTo gain access to this server, click **Verify** below.\n\nX BOT will remove **Unverified** and give you **Guest**. Earn activity XP and reach **Level 2** to become a **Member**."))
        row = discord.ui.ActionRow(); row.add_item(VerificationButton(bot, db)); container.add_item(row)
        self.add_item(container)


def setup_persistent_views(bot, db):
    bot.add_view(VerificationView(bot, db))
    for form in db.execute("SELECT * FROM application_forms WHERE enabled=1").fetchall():
        bot.add_view(ApplicationStartView(bot, db, form))


async def send_submission(bot, db, submission_id):
    row = db.execute("""SELECT s.*,f.name form_name,f.emoji,f.review_channel_id FROM application_submissions s
        JOIN application_forms f ON f.id=s.form_id WHERE s.id=?""", (submission_id,)).fetchone()
    answers = db.execute("SELECT * FROM application_answers WHERE submission_id=? ORDER BY question_id", (submission_id,)).fetchall()
    if not row or row["review_channel_id"] == "0": return
    channel = bot.get_channel(int(row["review_channel_id"]))
    if not channel: return
    body = f"**Applicant:** <@{row['user_id']}> (`{row['user_id']}`)\n**Status:** Pending\n\n" + "\n\n".join(f"**{a['question']}**\n{a['answer']}" for a in answers)
    try:
        await channel.send(view=xbot_ui.panel(f"{row['emoji']} Application #{row['id']} — {row['form_name']}", body, colour=discord.Color.gold(), footer=f"Review with /application_review submission_id:{row['id']}"))
    except discord.HTTPException: pass


class ContinueApplicationView(discord.ui.View):
    def __init__(self, bot, db, form, questions, user, answers, page, pending_role_ids=None):
        super().__init__(timeout=600)
        self.bot = bot; self.db = db; self.form = form; self.questions = questions
        self.applicant = user; self.answers = answers; self.page = page; self.pending_role_ids = list(pending_role_ids or [])

    @discord.ui.button(label="Continue Application", emoji="📝", style=discord.ButtonStyle.primary)
    async def continue_application(self, interaction, _button):
        if interaction.user.id != self.applicant.id:
            await interaction.response.send_message("This application belongs to another user.", ephemeral=True); return
        await interaction.response.send_modal(ApplicationModal(
            self.bot, self.db, self.form, self.questions, self.applicant, self.answers, self.page, self.pending_role_ids
        ))


class ApplicationModal(discord.ui.Modal):
    def __init__(self, bot, db, form, questions, user, previous_answers=None, page=0, pending_role_ids=None):
        super().__init__(title=f"{form['name']} Application"[:45], timeout=600)
        self.bot = bot; self.db = db; self.form = form; self.questions = questions; self.applicant = user
        self.previous_answers = list(previous_answers or []); self.page = page; self.pending_role_ids = list(pending_role_ids or [])
        self.inputs = []
        for question in questions[page * 5:(page + 1) * 5]:
            field = discord.ui.TextInput(label=question["label"][:45], placeholder=question["placeholder"][:100] or None,
                style=discord.TextStyle.paragraph if question["paragraph"] else discord.TextStyle.short,
                required=bool(question["required"]), max_length=1000 if question["paragraph"] else 200)
            self.inputs.append((question, field)); self.add_item(field)

    async def on_submit(self, interaction):
        answers = self.previous_answers + [
            (question["id"], question["label"], field.value.strip()) for question, field in self.inputs
        ]
        next_page = self.page + 1
        if next_page * 5 < len(self.questions):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="📝 Application Part Saved",
                    description=f"Part **{self.page + 1}** is saved. Click **Continue Application** for part **{next_page + 1}**.",
                    colour=discord.Color.blue(),
                ),
                view=ContinueApplicationView(self.bot, self.db, self.form, self.questions, self.applicant, answers, next_page, self.pending_role_ids),
                ephemeral=True,
            )
            return
        now = int(time.time())
        self.db.execute("INSERT INTO application_submissions(form_id,user_id,user_name,created_at,pending_role_ids) VALUES(?,?,?,?,?)",
            (self.form["id"], interaction.user.id, interaction.user.display_name, now, ",".join(self.pending_role_ids)))
        submission_id = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.db.executemany("INSERT INTO application_answers(submission_id,question_id,question,answer) VALUES(?,?,?,?)",
            [(submission_id, question_id, label, answer) for question_id, label, answer in answers])
        self.db.commit()
        await interaction.response.send_message(view=xbot_ui.success("📨 Application Submitted", f"Your **{self.form['name']}** application is **#{submission_id}**. Staff will review it soon."), ephemeral=True)
        await send_submission(self.bot, self.db, submission_id)


class DepartmentSelect(discord.ui.Select):
    def __init__(self, bot, db, form, questions, user, question, options):
        self.bot = bot; self.db = db; self.form = form; self.questions = questions; self.applicant = user
        self.question = question; self.option_rows = {str(row["id"]): row for row in options}
        choices = [discord.SelectOption(label=row["label"][:100], value=str(row["id"]), emoji=row["emoji"] or None) for row in options[:25]]
        super().__init__(placeholder="Choose the department you are applying for", min_values=1, max_values=1, options=choices)

    async def callback(self, interaction):
        if interaction.user.id != self.applicant.id:
            await interaction.response.send_message("This application belongs to another user.", ephemeral=True); return
        option = self.option_rows[self.values[0]]
        answers = [(self.question["id"], self.question["label"], option["label"])]
        pending_roles = [option["role_id"]] if option["role_id"] else []
        text_questions = [question for question in self.questions if question["id"] != self.question["id"]]
        await interaction.response.send_modal(ApplicationModal(self.bot, self.db, self.form, text_questions, self.applicant, answers, 0, pending_roles))


class DepartmentSelectView(discord.ui.View):
    def __init__(self, bot, db, form, questions, user, question, options):
        super().__init__(timeout=600)
        self.add_item(DepartmentSelect(bot, db, form, questions, user, question, options))


class ApplicationChoiceSelect(discord.ui.Select):
    """One-question-at-a-time select used for department and normal choices."""
    def __init__(self, bot, db, form, all_questions, user, choice_questions, index=0, answers=None, pending_role_ids=None):
        self.bot = bot; self.db = db; self.form = form; self.all_questions = all_questions; self.applicant = user
        self.choice_questions = choice_questions; self.index = index
        self.answers = list(answers or []); self.pending_role_ids = list(pending_role_ids or [])
        self.question = choice_questions[index]
        option_rows = db.execute(
            "SELECT * FROM application_question_options WHERE question_id=? ORDER BY position,id",
            (self.question["id"],),
        ).fetchall()
        self.option_rows = {str(row["id"]): row for row in option_rows}
        options = [discord.SelectOption(label=row["label"][:100], value=str(row["id"]), emoji=row["emoji"] or None) for row in option_rows[:25]]
        placeholder = "Choose the department you are applying for" if self.question["question_type"] == "department" else "Choose one answer"
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        if interaction.user.id != self.applicant.id:
            await interaction.response.send_message("This application belongs to another user.", ephemeral=True); return
        option = self.option_rows[self.values[0]]
        answers = self.answers + [(self.question["id"], self.question["label"], option["label"])]
        pending_roles = list(self.pending_role_ids)
        if option["role_id"] and option["role_id"] not in pending_roles:
            pending_roles.append(option["role_id"])
        next_index = self.index + 1
        if next_index < len(self.choice_questions):
            next_question = self.choice_questions[next_index]
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title=f"{self.form['emoji']} {self.form['name']}",
                    description=f"**Question {next_index + 1}/{len(self.choice_questions)}**\n{next_question['label']}",
                    colour=discord.Color.blue(),
                ),
                view=ApplicationChoiceView(self.bot, self.db, self.form, self.all_questions, self.applicant, self.choice_questions, next_index, answers, pending_roles),
            )
            return
        text_questions = [question for question in self.all_questions if question["question_type"] not in {"department", "choice"}]
        if not text_questions:
            await interaction.response.send_message("This application needs at least one text question.", ephemeral=True); return
        await interaction.response.send_modal(ApplicationModal(self.bot, self.db, self.form, text_questions, self.applicant, answers, 0, pending_roles))


class ApplicationChoiceView(discord.ui.View):
    def __init__(self, bot, db, form, all_questions, user, choice_questions, index=0, answers=None, pending_role_ids=None):
        super().__init__(timeout=600)
        self.add_item(ApplicationChoiceSelect(bot, db, form, all_questions, user, choice_questions, index, answers, pending_role_ids))


async def begin_application(bot, db, interaction, selected):
    if setting(db, "applications_enabled") != "1" or not selected or not selected["enabled"]:
        await interaction.response.send_message(view=xbot_ui.warning("Applications Closed", "This application is currently closed."), ephemeral=True); return
    previous = db.execute("SELECT created_at FROM application_submissions WHERE form_id=? AND user_id=? ORDER BY id DESC LIMIT 1", (selected["id"], interaction.user.id)).fetchone()
    remaining = selected["cooldown_seconds"] - (int(time.time()) - previous["created_at"]) if previous else 0
    if remaining > 0:
        await interaction.response.send_message(view=xbot_ui.warning("Application Cooldown", f"Try again in **{remaining // 3600 + 1} hour(s)**."), ephemeral=True); return
    questions = db.execute("SELECT * FROM application_questions WHERE form_id=? ORDER BY position,id LIMIT 10", (selected["id"],)).fetchall()
    if not questions:
        await interaction.response.send_message(view=xbot_ui.danger("Form Not Ready", "This application has no questions yet."), ephemeral=True); return
    choice_questions = [question for question in questions if question["question_type"] in {"department", "choice"}]
    if choice_questions:
        missing = next((question for question in choice_questions if not db.execute(
            "SELECT 1 FROM application_question_options WHERE question_id=? LIMIT 1", (question["id"],)
        ).fetchone()), None)
        if missing:
            await interaction.response.send_message(view=xbot_ui.danger("Form Not Ready", f"The question **{missing['label']}** has no choices yet."), ephemeral=True); return
        first_question = choice_questions[0]
        await interaction.response.send_message(
            embed=discord.Embed(title=f"{selected['emoji']} {selected['name']}", description=f"**Question 1/{len(choice_questions)}**\n{first_question['label']}", colour=discord.Color.blue()),
            view=ApplicationChoiceView(bot, db, selected, questions, interaction.user, choice_questions), ephemeral=True,
        )
        return
    await interaction.response.send_modal(ApplicationModal(bot, db, selected, questions, interaction.user))


class ApplicationStartView(discord.ui.View):
    def __init__(self, bot, db, form):
        super().__init__(timeout=None)
        self.bot = bot; self.db = db; self.form_id = form["id"]
        button = discord.ui.Button(label="Apply Now", emoji="📝", style=discord.ButtonStyle.primary, custom_id=f"xbot:application:{self.form_id}")
        button.callback = self.start
        self.add_item(button)

    async def start(self, interaction):
        selected = self.db.execute("SELECT * FROM application_forms WHERE id=?", (self.form_id,)).fetchone()
        await begin_application(self.bot, self.db, interaction, selected)


def register_commands(bot, db, is_staff=None, staff_kwargs=None):
    kwargs = staff_kwargs or {}

    @bot.tree.command(name="application_review", description="Staff: accept, deny, or hold an application")
    @app_commands.choices(decision=[app_commands.Choice(name="Accept", value="accepted"), app_commands.Choice(name="Deny", value="denied"), app_commands.Choice(name="Hold", value="hold")])
    async def application_review(interaction: discord.Interaction, submission_id: int, decision: app_commands.Choice[str], reason: str = ""):
        row = db.execute("""SELECT s.*,f.name form_name,f.reviewer_role_ids,f.accepted_role_ids,f.result_channel_id
            FROM application_submissions s JOIN application_forms f ON f.id=s.form_id WHERE s.id=?""", (submission_id,)).fetchone()
        if not row:
            await interaction.response.send_message(view=xbot_ui.danger("Not Found", "That application ID does not exist."), ephemeral=True); return
        role_ids = {str(role.id) for role in getattr(interaction.user, "roles", [])}; is_admin = interaction.user.guild_permissions.administrator
        reviewer_roles = set(filter(None, row["reviewer_role_ids"].split(",")))
        if not is_admin and not reviewer_roles.intersection(role_ids):
            await interaction.response.send_message(view=xbot_ui.danger("Access Denied", "You do not have the configured reviewer role."), ephemeral=True); return
        db.execute("UPDATE application_submissions SET status=?,reviewer_id=?,reviewer_name=?,reason=?,reviewed_at=? WHERE id=?",
            (decision.value, interaction.user.id, interaction.user.display_name, reason.strip(), int(time.time()), submission_id)); db.commit()
        applicant = interaction.guild.get_member(row["user_id"]) if interaction.guild else None
        if applicant is None and interaction.guild:
            try: applicant = await interaction.guild.fetch_member(row["user_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException): pass
        if decision.value == "accepted" and applicant:
            accepted_ids = set(filter(None, (row["accepted_role_ids"] + "," + row["pending_role_ids"]).split(",")))
            roles = [interaction.guild.get_role(int(role_id)) for role_id in accepted_ids]
            roles = [role for role in roles if role]
            if roles:
                try: await applicant.add_roles(*roles, reason=f"Accepted application #{submission_id}")
                except discord.HTTPException: pass
        body = f"**Application:** #{submission_id} — {row['form_name']}\n**Applicant:** <@{row['user_id']}>\n**Decision:** {decision.name}\n**Reviewer:** {interaction.user.mention}\n**Reason:** {reason or 'No reason provided'}"
        result_channel = bot.get_channel(int(row["result_channel_id"])) if row["result_channel_id"] != "0" else None
        if result_channel:
            try: await result_channel.send(view=xbot_ui.panel("📋 Application Result", body, colour=discord.Color.green() if decision.value=="accepted" else discord.Color.red() if decision.value=="denied" else discord.Color.gold()))
            except discord.HTTPException: pass
        await interaction.response.send_message(view=xbot_ui.success("Application Reviewed", body), ephemeral=True)

    @bot.tree.command(name="verify", description="Admin: Manually verify a new member and grant Guest role")
    @app_commands.describe(member="The new member to manually verify")
    async def verify_cmd(interaction: discord.Interaction, member: discord.Member):
        if not interaction.guild:
            await interaction.response.send_message("Use this command inside the server.", ephemeral=True); return
        can_verify = is_staff(interaction) if is_staff else getattr(interaction.user.guild_permissions, "administrator", False)
        if not can_verify:
            await interaction.response.send_message("Only Administrators and Staff can manually verify members.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        ok, msg = await manual_verify_member(bot, db, interaction.guild, member, interaction.user)
        if ok:
            await interaction.followup.send(view=xbot_ui.success("🔓 Member Verified", msg), ephemeral=True)
        else:
            await interaction.followup.send(view=xbot_ui.danger("Verification Error", msg), ephemeral=True)
