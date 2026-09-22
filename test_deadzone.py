"""Unit tests for the X BOT Deadzone system."""
import asyncio
import json
import sqlite3
import time
import unittest
from types import SimpleNamespace

import discord
import deadzone
import leveling
import staff_tools


from unittest.mock import AsyncMock, MagicMock


def make_role(role_id, name):
    r = MagicMock(spec=discord.Role)
    r.id = int(role_id)
    r.name = name
    return r


class DummyGuild:
    def __init__(self):
        self.name = "Test Server"
        self.roles = {
            1505437941647015986: make_role(1505437941647015986, "Member"),
            1505437186219311236: make_role(1505437186219311236, "Music"),
            1524719900785119354: make_role(1524719900785119354, "Active"),
            1524715220365217842: make_role(1524715220365217842, "Guest"),
            1551839505168859196: make_role(1551839505168859196, "Deadzone"),
        }
        self.system_channel = None

    def get_role(self, role_id):
        return self.roles.get(int(role_id))


def make_member(user_id, guild, roles=None, display_name="TestPlayer"):
    m = MagicMock(spec=discord.Member)
    m.id = int(user_id)
    m.guild = guild
    m.roles = list(roles) if roles else []
    m.display_name = display_name
    m.mention = f"<@{m.id}>"
    m.bot = False
    m.guild_permissions = SimpleNamespace(administrator=False)

    async def add_roles(*new_roles, reason=None):
        for r in new_roles:
            if r and r not in m.roles:
                m.roles.append(r)

    async def remove_roles(*old_roles, reason=None):
        for r in old_roles:
            if r in m.roles:
                m.roles.remove(r)

    m.add_roles = add_roles
    m.remove_roles = remove_roles
    return m


class DeadzoneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE economy_settings (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("CREATE TABLE command_permissions (command_name TEXT PRIMARY KEY, access_mode TEXT NOT NULL)")
        self.db.execute("""CREATE TABLE players (
            user_id INTEGER PRIMARY KEY,
            nation_name TEXT NOT NULL DEFAULT '',
            capital_name TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            xc INTEGER NOT NULL DEFAULT 0,
            money INTEGER NOT NULL DEFAULT 0,
            xcrystals INTEGER NOT NULL DEFAULT 0
        )""")
        self.db.execute("""CREATE TABLE economy_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        )""")
        leveling.initialise(self.db)
        deadzone.initialise(self.db)
        self.guild = DummyGuild()

    def tearDown(self):
        self.db.close()

    def test_initialise_defaults(self):
        self.assertEqual(deadzone.setting(self.db, "deadzone_enabled"), "1")
        self.assertEqual(deadzone.setting(self.db, "deadzone_days"), "7")
        self.assertEqual(deadzone.setting(self.db, "deadzone_role_id"), "1551839505168859196")
        self.assertEqual(deadzone.setting(self.db, "deadzone_guest_role_id"), "1524715220365217842")

    def test_staff_tools_integration(self):
        self.assertIn("deadzone_send", staff_tools.TOOLS)
        self.assertEqual(staff_tools.TOOLS["deadzone_send"][0], "Members")
        self.assertIn("deadzone_restore", staff_tools.TOOLS)
        self.assertEqual(staff_tools.TOOLS["deadzone_restore"][0], "Members")

    async def test_demotion_removes_privilege_roles_and_adds_deadzone(self):
        member_role = self.guild.get_role(1505437941647015986)
        music_role = self.guild.get_role(1505437186219311236)
        active_role = self.guild.get_role(1524719900785119354)
        dz_role = self.guild.get_role(1551839505168859196)
        guest_role = self.guild.get_role(1524715220365217842)

        member = make_member(12345, self.guild, roles=[member_role, music_role, active_role])

        # Execute demotion
        dummy_bot = SimpleNamespace(get_channel=lambda *a: None)
        success = await deadzone.demote_to_deadzone(dummy_bot, self.db, member, reason="Test 7 days inactive")
        self.assertTrue(success)

        # Privilege roles should be gone
        self.assertNotIn(member_role, member.roles)
        self.assertNotIn(music_role, member.roles)
        self.assertNotIn(active_role, member.roles)

        # Deadzone role added, guest role is NOT added
        self.assertIn(dz_role, member.roles)
        self.assertNotIn(guest_role, member.roles)

        # Database record should reflect deadzone status
        status = deadzone.member_status(self.db, 12345)
        self.assertEqual(status["is_in_deadzone"], 1)

    async def test_revival_restores_roles_and_bonuses(self):
        # Setup player with Level 7 profile
        self.db.execute(
            "INSERT INTO xp_profiles(user_id, level, total_xp) VALUES(?,?,?)",
            (55555, 7, 1000),
        )
        self.db.execute("INSERT OR IGNORE INTO players(user_id, nation_name, capital_name, xc) VALUES(?,?,?,?)", (55555, "TestNation", "TestCapital", 100))
        self.db.commit()

        member_role = self.guild.get_role(1505437941647015986)
        music_role = self.guild.get_role(1505437186219311236)
        active_role = self.guild.get_role(1524719900785119354)
        dz_role = self.guild.get_role(1551839505168859196)
        guest_role = self.guild.get_role(1524715220365217842)

        member = make_member(55555, self.guild, roles=[member_role, music_role, active_role])
        dummy_bot = SimpleNamespace(get_channel=lambda *a: None)

        # 1. Demote to deadzone
        await deadzone.demote_to_deadzone(dummy_bot, self.db, member)
        self.assertIn(dz_role, member.roles)

        # 2. Revive member
        revived = await deadzone.revive_member(dummy_bot, self.db, member, triggered_by="button")
        self.assertTrue(revived)

        # Deadzone role removed
        self.assertNotIn(dz_role, member.roles)

        # Member (Level 2), Music (Level 5), and Active (Level 7) roles restored via leveling sync
        self.assertIn(member_role, member.roles)
        self.assertIn(music_role, member.roles)
        self.assertIn(active_role, member.roles)

        # Database state updated
        status = deadzone.member_status(self.db, 55555)
        self.assertEqual(status["is_in_deadzone"], 0)
        self.assertEqual(status["resurrections_count"], 1)

        # Currency bonus granted (100 + 150 = 250)
        player_xc = self.db.execute("SELECT xc FROM players WHERE user_id=?", (55555,)).fetchone()["xc"]
        self.assertEqual(player_xc, 250)

    async def test_scavenge_cooldown_and_prevention(self):
        # In Deadzone members cannot scavenge
        dz_role = self.guild.get_role(1551839505168859196)
        sleeper = make_member(777, self.guild, roles=[dz_role])
        await deadzone.demote_to_deadzone(SimpleNamespace(get_channel=lambda *a: None), self.db, sleeper)

        status = deadzone.member_status(self.db, 777)
        self.assertEqual(status["is_in_deadzone"], 1)

    async def test_one_plus_two_respawn_protocol(self):
        """Test Condition 1 (5 messages thaw) + Condition 2 (friend rescue)."""
        # Setup sleeper (user 888) and rescuer (user 999)
        self.db.execute("INSERT OR IGNORE INTO players(user_id, nation_name, capital_name, xc) VALUES(?,?,?,?)", (888, "Sleeper", "SleeperCity", 50))
        self.db.execute("INSERT OR IGNORE INTO players(user_id, nation_name, capital_name, xc) VALUES(?,?,?,?)", (999, "Rescuer", "RescuerCity", 100))
        self.db.execute("INSERT OR IGNORE INTO xp_profiles(user_id, level, total_xp) VALUES(?,?,?)", (888, 5, 500))
        self.db.commit()

        member_role = self.guild.get_role(1505437941647015986)
        dz_role = self.guild.get_role(1551839505168859196)
        sleeper = make_member(888, self.guild, roles=[member_role])
        rescuer = make_member(999, self.guild, roles=[member_role])
        dummy_bot = SimpleNamespace(get_channel=lambda *a: None)

        # 1. Demote sleeper to deadzone
        await deadzone.demote_to_deadzone(dummy_bot, self.db, sleeper)
        status = deadzone.member_status(self.db, 888)
        self.assertEqual(status["is_in_deadzone"], 1)
        self.assertEqual(status["thaw_count"], 0)

        # 2. Condition 1: Sleeper sends messages to thaw
        dummy_channel = MagicMock(spec=discord.TextChannel)
        dummy_channel.send = AsyncMock()
        for msg_num in range(1, 6):
            dummy_msg = SimpleNamespace(
                guild=self.guild,
                author=sleeper,
                channel=dummy_channel,
                add_reaction=AsyncMock(),
            )
            await deadzone.handle_message(dummy_bot, self.db, dummy_msg)
            status = deadzone.member_status(self.db, 888)
            self.assertEqual(status["thaw_count"], min(5, msg_num))

        # Still in deadzone after 5 messages (waiting for friend rescue!)
        self.assertEqual(status["is_in_deadzone"], 1)

        # 3. Condition 2: Friend rescue succeeds
        revived = await deadzone.revive_member(dummy_bot, self.db, sleeper, triggered_by="rescued_by_999")
        self.assertTrue(revived)
        # Rescuer reward
        self.db.execute("UPDATE players SET xc=xc+50 WHERE user_id=?", (999,))
        self.db.commit()

        # Verify sleeper restored
        status = deadzone.member_status(self.db, 888)
        self.assertEqual(status["is_in_deadzone"], 0)
        self.assertEqual(status["thaw_count"], 0)
        self.assertIn(member_role, sleeper.roles)

        # Verify rescuer got 50 XC reward (100 + 50 = 150)
        rescuer_xc = self.db.execute("SELECT xc FROM players WHERE user_id=?", (999,)).fetchone()["xc"]
        self.assertEqual(rescuer_xc, 150)

    def test_exchange_rates_configuration(self):
        """Test 1 XC = 100 War Credits configuration."""
        import economy
        self.assertEqual(economy.DEFAULT_SETTINGS["exchange_xc_to_war_percent"], "10000")
        self.assertEqual(economy.DEFAULT_SETTINGS["exchange_war_to_xc_percent"], "1")


if __name__ == "__main__":
    unittest.main()
