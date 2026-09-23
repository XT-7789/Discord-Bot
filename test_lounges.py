"""Unit tests for the X BOT Dynamic Lounge Reservation & Auto-Clear System."""

import json
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import lounges


class DummyChannel:
    def __init__(self, channel_id, name="channel"):
        self.id = channel_id
        self.name = name
        self.overwrites = {}
        self.sent_messages = []
        self.purged_count = 0
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

    async def purge(self, limit=100):
        self.purged_count += limit
        return []


class DummyGuild:
    def __init__(self):
        self.id = 1505255035104268480
        self.name = "X Community"
        self.default_role = MagicMock(spec=discord.Role)
        self.default_role.id = 1505255035104268480
        self.channels = {}

        for lid, info in lounges.LOUNGES.items():
            self.channels[info["text_id"]] = DummyChannel(info["text_id"], f"lounge-{lid}-text")
            self.channels[info["vc_id"]] = DummyChannel(info["vc_id"], f"lounge-{lid}-vc")

    def get_channel(self, cid):
        return self.channels.get(cid)

    def get_member(self, uid):
        m = MagicMock(spec=discord.Member)
        m.id = uid
        m.guild = self
        m.display_name = f"User_{uid}"
        m.mention = f"<@{uid}>"
        m.voice = None
        m.move_to = AsyncMock()
        m.send = AsyncMock()
        return m


class LoungesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE economy_settings (key TEXT PRIMARY KEY, value TEXT)")
        lounges.initialise(self.db)
        self.guild = DummyGuild()
        self.bot = MagicMock(spec=discord.Client)
        self.bot.get_channel = self.guild.get_channel

    def tearDown(self):
        self.db.close()

    def test_initialise_and_seed(self):
        all_l = lounges.get_all_lounges(self.db)
        self.assertEqual(len(all_l), 5)
        for l in all_l:
            self.assertEqual(l["status"], "available")
            self.assertEqual(l["host_user_id"], 0)

        self.assertEqual(lounges.setting(self.db, "lounge_enabled"), "1")
        self.assertEqual(lounges.setting(self.db, "lounge_default_duration_mins"), "60")

    def test_queries_by_host_and_channel(self):
        # Reserve lounge 1
        now = int(time.time())
        self.db.execute(
            """UPDATE server_lounges
            SET status='occupied', host_user_id=12345, host_name='HostUser', reserved_at=?, expires_at=?
            WHERE lounge_id=1""",
            (now, now + 3600),
        )
        self.db.commit()

        by_host = lounges.get_active_lounge_by_host(self.db, 12345)
        self.assertIsNotNone(by_host)
        self.assertEqual(by_host["lounge_id"], 1)

        info = lounges.LOUNGES[1]
        by_text = lounges.get_lounge_by_channel(self.db, info["text_id"])
        self.assertEqual(by_text["lounge_id"], 1)

        by_vc = lounges.get_lounge_by_channel(self.db, info["vc_id"])
        self.assertEqual(by_vc["lounge_id"], 1)

    async def test_apply_and_reset_permissions(self):
        host = self.guild.get_member(12345)
        invited_member = self.guild.get_member(67890)

        # Apply private permissions
        await lounges.apply_lounge_permissions(self.guild, 1, host, "private", [67890])

        info = lounges.LOUNGES[1]
        text_ch = self.guild.get_channel(info["text_id"])
        vc_ch = self.guild.get_channel(info["vc_id"])

        # Default role in private should have view_channel=False
        self.assertIn(self.guild.default_role, text_ch.overwrites)
        self.assertFalse(text_ch.overwrites[self.guild.default_role].view_channel)

        # Host should have view_channel=True
        self.assertIn(host, text_ch.overwrites)
        self.assertTrue(text_ch.overwrites[host].view_channel)

        # Reset permissions
        await lounges.reset_lounge_permissions(self.guild, 1)
        self.assertNotIn(host, text_ch.overwrites)
        self.assertTrue(text_ch.overwrites[self.guild.default_role].view_channel)

    async def test_clear_and_reopen_lounge(self):
        now = int(time.time())
        self.db.execute(
            """UPDATE server_lounges
            SET status='occupied', host_user_id=12345, host_name='HostUser', reserved_at=?, expires_at=?
            WHERE lounge_id=1""",
            (now, now + 3600),
        )
        self.db.commit()

        info = lounges.LOUNGES[1]
        vc = self.guild.get_channel(info["vc_id"])
        dummy_member = self.guild.get_member(888)
        vc.members.append(dummy_member)

        # Execute clear and reopen
        success = await lounges.clear_and_reopen_lounge(self.bot, self.db, self.guild, 1, reason="Test Clear")
        self.assertTrue(success)

        # Member should have been disconnected
        dummy_member.move_to.assert_awaited_with(None)

        # Text channel should have been purged
        tc = self.guild.get_channel(info["text_id"])
        self.assertGreater(tc.purged_count, 0)

        # Database state should be available
        row = lounges.get_lounge_row(self.db, 1)
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["host_user_id"], 0)

    def test_lobby_and_host_embeds(self):
        lobby_embed = lounges.build_lobby_embed(self.db)
        self.assertIn("RESERVATION LOBBY", lobby_embed.title)
        self.assertIn("Lounge 1", lobby_embed.description)

        host_embed = lounges.build_host_control_embed({
            "name": "Lounge 1",
            "expires_at": int(time.time()) + 1800,
            "privacy": "private",
            "host_user_id": 12345,
            "invited_user_ids": "[67890]",
        })
        self.assertIn("LOUNGE 1 HOST CONTROL HUB", host_embed.title)
        self.assertIn("Private", host_embed.description)


if __name__ == "__main__":
    unittest.main()
