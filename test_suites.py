"""Unit tests for X BOT Automated Private Suites System."""

import json
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import deadzone
import discord
import leveling
import suites


class DummyChannel:
    def __init__(self, channel_id, name="channel"):
        self.id = channel_id
        self.name = name
        self.overwrites = {}
        self.sent_messages = []
        self.category = None
        self.members = []

    async def set_permissions(self, target, overwrite=None):
        if overwrite is None:
            self.overwrites.pop(target, None)
        else:
            self.overwrites[target] = overwrite

    async def send(self, *args, **kwargs):
        msg = MagicMock(spec=discord.Message)
        msg.id = 999900001
        self.sent_messages.append((args, kwargs))
        return msg

    async def delete(self, reason=None):
        pass

    @property
    def mention(self):
        return f"<#{self.id}>"


class DummyGuild:
    def __init__(self):
        self.id = 1505255035104268480
        self.name = "X Community"
        self.default_role = MagicMock(spec=discord.Role)
        self.default_role.id = 1505255035104268480
        self.channels = {}
        self.categories = []
        self._members = {}

        # Admin channel
        self.admin_ch = DummyChannel(1552329383681986633, "admin-reviews")
        self.channels[1552329383681986633] = self.admin_ch

    def get_channel(self, cid):
        return self.channels.get(cid)

    def get_member(self, uid):
        if uid not in self._members:
            m = MagicMock(spec=discord.Member)
            m.id = uid
            m.guild = self
            m.display_name = f"User_{uid}"
            m.mention = f"<@{uid}>"
            m.send = AsyncMock()
            m.voice = None
            m.guild_permissions = SimpleNamespace(administrator=True)
            self._members[uid] = m
        return self._members[uid]

    async def create_voice_channel(self, name, category=None, overwrites=None, reason=None):
        cid = 888000 + len(self.channels)
        ch = DummyChannel(cid, name)
        ch.category = category
        ch.overwrites = overwrites or {}
        self.channels[cid] = ch
        return ch

    async def create_text_channel(self, name, category=None, overwrites=None, reason=None):
        cid = 777000 + len(self.channels)
        ch = DummyChannel(cid, name)
        ch.category = category
        ch.overwrites = overwrites or {}
        self.channels[cid] = ch
        return ch


class PrivateSuitesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE economy_settings (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("""CREATE TABLE players (
            user_id INTEGER PRIMARY KEY,
            money INTEGER NOT NULL DEFAULT 0,
            xc INTEGER NOT NULL DEFAULT 0,
            xcrystals INTEGER NOT NULL DEFAULT 0,
            display_name TEXT NOT NULL DEFAULT '',
            nation_name TEXT NOT NULL DEFAULT '',
            capital_name TEXT NOT NULL DEFAULT '',
            last_daily INTEGER NOT NULL DEFAULT 0
        )""")
        self.db.execute("""CREATE TABLE xp_profiles (
            user_id INTEGER PRIMARY KEY,
            total_xp INTEGER NOT NULL DEFAULT 0,
            weekly_xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1,
            message_xp INTEGER NOT NULL DEFAULT 0,
            voice_xp INTEGER NOT NULL DEFAULT 0,
            last_message_xp INTEGER NOT NULL DEFAULT 0,
            last_voice_xp INTEGER NOT NULL DEFAULT 0,
            last_week_key TEXT NOT NULL DEFAULT '',
            current_streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            last_active_day INTEGER NOT NULL DEFAULT 0
        )""")

        deadzone.initialise(self.db)
        suites.initialise(self.db)
        leveling.initialise(self.db)

        self.guild = DummyGuild()
        self.bot = MagicMock(spec=discord.Client)
        self.bot.get_channel = self.guild.get_channel
        suites._db = self.db
        suites._bot = self.bot

    def tearDown(self):
        self.db.close()

    async def test_level_under_10_blocked(self):
        user = self.guild.get_member(1001)
        self.db.execute("INSERT INTO xp_profiles(user_id, level) VALUES(?, 5)", (1001,))
        self.db.commit()

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = user
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await suites.handle_suite_request_start(interaction)
        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args[0][0]
        self.assertIn("Level 10+ Required", msg)

    async def test_level_10_and_20_application_submission(self):
        # 1. Level 10 user submission
        user10 = self.guild.get_member(1010)
        self.db.execute("INSERT INTO xp_profiles(user_id, level) VALUES(?, 12)", (1010,))
        self.db.commit()

        modal10 = suites.PrivateSuiteRequestModal(user_level=12)
        modal10.name_input._value = "Level 10 Squad Den"
        modal10.purpose_input._value = "Gaming squad voice room"

        interaction10 = MagicMock(spec=discord.Interaction)
        interaction10.user = user10
        interaction10.guild = self.guild
        interaction10.response = MagicMock()
        interaction10.response.defer = AsyncMock()
        interaction10.followup = MagicMock()
        interaction10.followup.send = AsyncMock()

        await modal10.on_submit(interaction10)
        interaction10.followup.send.assert_called_once()

        # Verify sent to admin channel with Level 10 badge
        self.assertEqual(len(self.guild.admin_ch.sent_messages), 1)
        args, kwargs = self.guild.admin_ch.sent_messages[0]
        embed = kwargs.get("embed")
        self.assertIn("LEVEL 12 ELITE MEMBER", embed.description)
        self.assertEqual(embed.color.value, 0x3498DB)  # Blue

        # 2. Level 20 user submission
        user20 = self.guild.get_member(1020)
        self.db.execute("INSERT INTO xp_profiles(user_id, level) VALUES(?, 25)", (1020,))
        self.db.commit()

        modal20 = suites.PrivateSuiteRequestModal(user_level=25)
        modal20.name_input._value = "Veteran War Room"
        modal20.purpose_input._value = "Senior guild council meetings"

        interaction20 = MagicMock(spec=discord.Interaction)
        interaction20.user = user20
        interaction20.guild = self.guild
        interaction20.response = MagicMock()
        interaction20.response.defer = AsyncMock()
        interaction20.followup = MagicMock()
        interaction20.followup.send = AsyncMock()

        await modal20.on_submit(interaction20)

        # Verify sent to admin channel with Level 20+ Gold Fast-track badge
        self.assertEqual(len(self.guild.admin_ch.sent_messages), 2)
        args, kwargs = self.guild.admin_ch.sent_messages[1]
        embed20 = kwargs.get("embed")
        self.assertIn("LEVEL 25 SENIOR VETERAN (Lv.20+)", embed20.description)
        self.assertIn("Fast-Track Recommended", embed20.description)
        self.assertEqual(embed20.color.value, 0xF1C40F)  # Gold

    async def test_admin_approve_and_channel_creation(self):
        # Create pending request in DB
        now = int(time.time())
        cursor = self.db.execute(
            """INSERT INTO suite_requests (user_id, user_name, user_level, custom_name, purpose, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (2001, "EliteGamer", 15, "Elite Sanctum", "Private hangout", now),
        )
        self.db.commit()
        req_id = cursor.lastrowid

        admin = self.guild.get_member(9999)
        applicant = self.guild.get_member(2001)

        approve_modal = suites.SuiteApproveModal(req_id)
        approve_modal.duration_input._value = "7d"
        approve_modal.note_input._value = "Have fun in your suite!"

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = admin
        interaction.guild = self.guild
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()
        interaction.message = MagicMock()
        interaction.message.embeds = [discord.Embed(title="Admin Review Card")]
        interaction.message.edit = AsyncMock()

        await approve_modal.on_submit(interaction)
        interaction.followup.send.assert_called_once()
        self.assertIn("Approved!", interaction.followup.send.call_args[0][0])

        # Verify DB updated
        row = self.db.execute("SELECT * FROM suite_requests WHERE id=?", (req_id,)).fetchone()
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["admin_id"], 9999)
        self.assertGreater(row["expires_at"], now)
        self.assertGreater(row["voice_channel_id"], 0)
        self.assertGreater(row["text_channel_id"], 0)

        # Verify channels exist in guild
        vc = self.guild.get_channel(row["voice_channel_id"])
        tc = self.guild.get_channel(row["text_channel_id"])
        self.assertIsNotNone(vc)
        self.assertIsNotNone(tc)
        self.assertIn("Elite Sanctum", vc.name)

        # Applicant notified in DM
        applicant.send.assert_awaited_once()

    async def test_admin_reject_flow(self):
        # Create pending request
        now = int(time.time())
        cursor = self.db.execute(
            """INSERT INTO suite_requests (user_id, user_name, user_level, custom_name, purpose, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (3001, "NoobMaster", 10, "Bad Suite", "Spam", now),
        )
        self.db.commit()
        req_id = cursor.lastrowid

        admin = self.guild.get_member(9999)
        applicant = self.guild.get_member(3001)

        reject_modal = suites.SuiteRejectModal(req_id)
        reject_modal.reason_input._value = "Inappropriate suite purpose."

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = admin
        interaction.guild = self.guild
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()
        interaction.message = MagicMock()
        interaction.message.embeds = [discord.Embed(title="Admin Review Card")]
        interaction.message.edit = AsyncMock()

        await reject_modal.on_submit(interaction)
        interaction.followup.send.assert_called_once()

        # Check DB status
        row = self.db.execute("SELECT * FROM suite_requests WHERE id=?", (req_id,)).fetchone()
        self.assertEqual(row["status"], "rejected")

        # Applicant notified of rejection
        applicant.send.assert_awaited_once()
        dm_embed = applicant.send.call_args[1]["embed"]
        self.assertIn("Declined", dm_embed.title)
        self.assertIn("Inappropriate suite purpose", dm_embed.description)

    async def test_suite_host_invite_and_kick(self):
        # Setup approved suite
        now = int(time.time())
        vc = await self.guild.create_voice_channel("🔊・Suite-VC")
        tc = await self.guild.create_text_channel("💬・Suite-TC")

        cursor = self.db.execute(
            """INSERT INTO suite_requests (user_id, user_name, user_level, custom_name, status, voice_channel_id, text_channel_id, created_at)
            VALUES (?, ?, ?, ?, 'approved', ?, ?, ?)""",
            (4001, "HostUser", 14, "Party Suite", vc.id, tc.id, now),
        )
        self.db.commit()
        suite_id = cursor.lastrowid

        friend = self.guild.get_member(5005)

        # Host invites friend
        select_view = suites.SuiteMemberSelectView(suite_id, mode="invite")
        select_view.select_user._values = [friend]

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = self.guild.get_member(4001)
        interaction.guild = self.guild
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await select_view.on_select(interaction)
        interaction.response.send_message.assert_called_once()
        self.assertIn("added to your suite", interaction.response.send_message.call_args[0][0])

        # Verify DB recorded membership
        member_row = self.db.execute("SELECT * FROM suite_members WHERE suite_id=? AND user_id=?", (suite_id, 5005)).fetchone()
        self.assertIsNotNone(member_row)

        # Host kicks friend
        kick_view = suites.SuiteMemberSelectView(suite_id, mode="kick")
        kick_view.select_user._values = [friend]

        interaction_kick = MagicMock(spec=discord.Interaction)
        interaction_kick.user = self.guild.get_member(4001)
        interaction_kick.guild = self.guild
        interaction_kick.response = MagicMock()
        interaction_kick.response.send_message = AsyncMock()

        await kick_view.on_select(interaction_kick)
        interaction_kick.response.send_message.assert_called_once()
        self.assertIn("removed from your suite", interaction_kick.response.send_message.call_args[0][0])

        member_row_after = self.db.execute("SELECT * FROM suite_members WHERE suite_id=? AND user_id=?", (suite_id, 5005)).fetchone()
        self.assertIsNone(member_row_after)


if __name__ == "__main__":
    unittest.main()
