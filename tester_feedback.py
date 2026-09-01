"""Tester-only bug and suggestion reports, reviewed from the Staff Centre."""

import time

import discord

TESTER_ROLE_ID = 1534196579794161784


def initialise(db):
    db.execute("""CREATE TABLE IF NOT EXISTS tester_feedback(
        id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,user_name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('bug','suggestion')),title TEXT NOT NULL,details TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',reviewer_id INTEGER,reviewer_name TEXT,
        review_note TEXT NOT NULL DEFAULT '',reward_xc INTEGER NOT NULL DEFAULT 0,
        reward_war_credits INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,reviewed_at INTEGER
    )""")
    db.commit()


def is_tester(member):
    return any(role.id == TESTER_ROLE_ID for role in getattr(member, "roles", []))


class FeedbackModal(discord.ui.Modal):
    title_input = discord.ui.TextInput(label="Short title", placeholder="Example: Map button does not return", max_length=100)
    details = discord.ui.TextInput(label="Details", placeholder="What happened? Include steps, expected result, and screenshots/links if useful.", style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self, db, kind):
        super().__init__(title="Report a bug" if kind == "bug" else "Suggest an idea")
        self.db, self.kind = db, kind

    async def on_submit(self, interaction):
        self.db.execute("""INSERT INTO tester_feedback(user_id,user_name,kind,title,details,created_at)
            VALUES(?,?,?,?,?,?)""", (interaction.user.id, interaction.user.display_name, self.kind,
                                        str(self.title_input).strip(), str(self.details).strip(), int(time.time())))
        self.db.commit()
        await interaction.response.send_message("✅ Thank you. Your Tester report was sent to the Staff Centre.", ephemeral=True)


class FeedbackButton(discord.ui.Button):
    def __init__(self, db, kind):
        super().__init__(label="Report Bug" if kind == "bug" else "Suggest Idea", emoji="🐛" if kind == "bug" else "💡",
                         style=discord.ButtonStyle.danger if kind == "bug" else discord.ButtonStyle.primary,
                         custom_id=f"xbot:tester_feedback:{kind}")
        self.db, self.kind = db, kind

    async def callback(self, interaction):
        if not is_tester(interaction.user):
            await interaction.response.send_message("This panel is for approved Testers only.", ephemeral=True)
            return
        await interaction.response.send_modal(FeedbackModal(self.db, self.kind))


class TesterFeedbackView(discord.ui.View):
    def __init__(self, db):
        super().__init__(timeout=None)
        self.add_item(FeedbackButton(db, "bug"))
        self.add_item(FeedbackButton(db, "suggestion"))


def setup_persistent_views(bot, db):
    bot.add_view(TesterFeedbackView(db))
