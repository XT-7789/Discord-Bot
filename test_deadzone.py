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
        self.id = 1505437941647015980
        self.name = "Test Server"
        self.roles = {
            1505437941647015986: make_role(1505437941647015986, "Member"),
            1505437186219311236: make_role(1505437186219311236, "Music"),
            1526237128093339848: make_role(1526237128093339848, "Premium Music"),
            1524719900785119354: make_role(1524719900785119354, "Active"),
            1524715220365217842: make_role(1524715220365217842, "Guest"),
            1551839505168859196: make_role(1551839505168859196, "Deadzone"),
        }
        self.system_channel = None
        self.members = []

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
        self.assertEqual(staff_tools.TOOLS["deadzone_send"][0], "Deadzone")
        self.assertIn("deadzone_restore", staff_tools.TOOLS)
        self.assertEqual(staff_tools.TOOLS["deadzone_restore"][0], "Deadzone")
        self.assertIn("deadzone_scan", staff_tools.TOOLS)
        self.assertEqual(staff_tools.TOOLS["deadzone_scan"][0], "Deadzone")
        self.assertIn("level_sync", staff_tools.TOOLS)
        self.assertEqual(staff_tools.TOOLS["level_sync"][0], "Members")

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

    async def test_revival_restores_saved_level_role_when_profile_is_low(self):
        # Setup member with low profile level (Level 1) but who held Level 7 Active role in Discord
        self.db.execute(
            "INSERT INTO xp_profiles(user_id, level, total_xp) VALUES(?,?,?)",
            (66666, 1, 10),
        )
        self.db.commit()

        member_role = self.guild.get_role(1505437941647015986)
        active_role = self.guild.get_role(1524719900785119354)  # Level 7 reward role
        dz_role = self.guild.get_role(1551839505168859196)

        member = make_member(66666, self.guild, roles=[member_role, active_role])
        dummy_bot = SimpleNamespace(get_channel=lambda *a: None)

        # 1. Demote to deadzone
        await deadzone.demote_to_deadzone(dummy_bot, self.db, member)
        self.assertIn(dz_role, member.roles)
        self.assertNotIn(active_role, member.roles)

        # 2. Revive member - should restore Active role and update xp profile to Level 7
        revived = await deadzone.revive_member(dummy_bot, self.db, member, triggered_by="admin_restore")
        self.assertTrue(revived)
        self.assertIn(active_role, member.roles)
        self.assertIn(member_role, member.roles)

        prof = leveling.profile(self.db, 66666)
        self.assertEqual(prof["level"], 7)

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

        # Verify sleeper restored and received Welcome Back Stimulus (100,000 Cash, 150 XC, 100 XP)
        status = deadzone.member_status(self.db, 888)
        self.assertEqual(status["is_in_deadzone"], 0)
        self.assertEqual(status["thaw_count"], 0)
        self.assertIn(member_role, sleeper.roles)

        sleeper_row = self.db.execute("SELECT xc, money FROM players WHERE user_id=?", (888,)).fetchone()
        self.assertEqual(sleeper_row["xc"], 200)  # 50 + 150
        self.assertEqual(sleeper_row["money"], 100000)

        # Verify rescuer got 50 XC reward (100 + 50 = 150)
        rescuer_xc = self.db.execute("SELECT xc FROM players WHERE user_id=?", (999,)).fetchone()["xc"]
        self.assertEqual(rescuer_xc, 150)

    def test_exchange_rates_configuration(self):
        """Test 1 XC = 1,000 Cash configuration."""
        import economy
        self.assertEqual(economy.DEFAULT_SETTINGS["exchange_xc_to_war_percent"], "100000")
        self.assertEqual(economy.DEFAULT_SETTINGS["exchange_war_to_xc_percent"], "1")

    async def test_sync_guild_member_levels(self):
        """Verify sync_guild_member_levels upgrades DB levels of members who hold Discord level roles."""
        active_role = self.guild.get_role(1524719900785119354)  # Level 7 Active
        member = make_member(111222, self.guild, roles=[active_role])
        self.guild.members.append(member)

        # Profile starts at Level 1 with 10 XP
        self.db.execute("INSERT INTO xp_profiles(user_id, level, total_xp) VALUES(?,?,?)", (111222, 1, 10))
        self.db.commit()

        dummy_bot = SimpleNamespace(get_channel=lambda *a: None)
        updated = await leveling.sync_guild_member_levels(dummy_bot, self.db, self.guild)
        self.assertGreaterEqual(updated, 1)

        prof = leveling.profile(self.db, 111222)
        self.assertEqual(prof["level"], 7)
        self.assertGreaterEqual(prof["total_xp"], leveling.xp_for_level(7))

    async def test_deadzone_haunt_mechanics(self):
        """Verify deadzone haunt cooldown, XC reward, and restrictions."""
        # 1. Living member cannot haunt
        living = make_member(333444, self.guild, roles=[])
        self.db.execute("INSERT OR IGNORE INTO deadzone_members(user_id, last_active_at, is_in_deadzone) VALUES(?,0,0)", (333444,))
        self.db.execute("INSERT OR IGNORE INTO players(user_id, nation_name, capital_name, xc) VALUES(?,?,?,?)", (333444, "N", "C", 50))
        self.db.commit()

        status = deadzone.member_status(self.db, 333444)
        self.assertEqual(status["is_in_deadzone"], 0)

        # 2. Deadzone sleeper can haunt and earns XC
        sleeper = make_member(555666, self.guild, roles=[])
        now = int(time.time())
        self.db.execute("INSERT OR IGNORE INTO deadzone_members(user_id, last_active_at, is_in_deadzone, last_haunt_at) VALUES(?,0,1,0)", (555666,))
        self.db.execute("INSERT OR IGNORE INTO players(user_id, nation_name, capital_name, xc) VALUES(?,?,?,?)", (555666, "N", "C", 50))
        self.db.commit()

        # Simulate haunt reward execution
        reward_xc = int(deadzone.setting(self.db, "deadzone_haunt_reward_xc") or 30)
        self.assertEqual(reward_xc, 30)
        self.db.execute("UPDATE deadzone_members SET last_haunt_at=?, last_active_at=? WHERE user_id=?", (now, now, 555666))
        self.db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (reward_xc, 555666))
        self.db.commit()

        new_status = deadzone.member_status(self.db, 555666)
        self.assertEqual(new_status["last_haunt_at"], now)
        player_xc = self.db.execute("SELECT xc FROM players WHERE user_id=?", (555666,)).fetchone()["xc"]
        self.assertEqual(player_xc, 80)  # 50 + 30 = 80

    async def test_resurrection_welcome_party(self):
        """Verify 5-minute welcome party activates on revive and awards 2,000 Cash to active chatters."""
        guild_id = 999888
        self.guild.id = guild_id
        dummy_channel = MagicMock(spec=discord.TextChannel)
        dummy_channel.id = 12345
        dummy_channel.mention = "<#12345>"
        dummy_channel.send = AsyncMock()
        self.guild.system_channel = dummy_channel
        self.guild.text_channels = [dummy_channel]

        dummy_bot = SimpleNamespace(get_channel=lambda *a: dummy_channel)

        revived_member = make_member(444111, self.guild, display_name="RevivedSoldier")
        self.db.execute("INSERT OR IGNORE INTO players(user_id, nation_name, capital_name, money) VALUES(?,?,?,?)", (444111, "R", "RC", 0))
        self.db.execute("INSERT INTO deadzone_members(user_id, is_in_deadzone) VALUES(?, 1)", (444111,))
        self.db.commit()

        await deadzone.revive_member(dummy_bot, self.db, revived_member)
        self.assertIn(guild_id, deadzone.active_parties)
        party = deadzone.active_parties[guild_id]
        self.assertEqual(party["reward_cash"], 2000)
        self.assertEqual(party["revived_user_id"], 444111)

        # Another member chats during party
        chatter = make_member(444222, self.guild, display_name="PartyChatter")
        self.db.execute("INSERT OR IGNORE INTO players(user_id, nation_name, capital_name, money) VALUES(?,?,?,?)", (444222, "P", "PC", 0))
        self.db.commit()

        msg = SimpleNamespace(guild=self.guild, author=chatter, channel=dummy_channel, add_reaction=AsyncMock())
        await deadzone.handle_message(dummy_bot, self.db, msg)

        # Verfiy 2,000 Cash awarded and reaction added
        money = self.db.execute("SELECT money FROM players WHERE user_id=?", (444222,)).fetchone()["money"]
        self.assertEqual(money, 2000)
        msg.add_reaction.assert_called_with("🎉")

        # Second message should not award duplicate
        await deadzone.handle_message(dummy_bot, self.db, msg)
        money_after = self.db.execute("SELECT money FROM players WHERE user_id=?", (444222,)).fetchone()["money"]
        self.assertEqual(money_after, 2000)

    async def test_chat_cash_drop_and_lucky_drop(self):
        """Verify chat activity in leveling.py awards Cash and respects cooldown."""
        member = make_member(777111, self.guild, display_name="Talker")
        dummy_channel = MagicMock(spec=discord.TextChannel)
        dummy_channel.id = 54321
        msg = SimpleNamespace(guild=self.guild, author=member, channel=dummy_channel, add_reaction=AsyncMock(), roles=[])

        dummy_bot = SimpleNamespace(get_channel=lambda *a: None)
        await leveling.handle_message(dummy_bot, self.db, msg)

        player = self.db.execute("SELECT money, xc FROM players WHERE user_id=?", (777111,)).fetchone()
        self.assertIsNotNone(player)
        self.assertGreaterEqual(player["money"], 100)
        self.assertLessEqual(player["money"], 300)

    async def test_sync_reward_roles_with_premium_music(self):
        """Verify Level 5+ leveling sync does NOT grant regular Music if member has Premium Music."""
        premium_music_role = self.guild.get_role(1526237128093339848)
        regular_music_role = self.guild.get_role(1505437186219311236)
        member = make_member(888777, self.guild, roles=[premium_music_role])

        await leveling.sync_reward_roles(self.db, member, level=5)
        self.assertNotIn(regular_music_role, member.roles)
        self.assertIn(premium_music_role, member.roles)

        # Even if regular music was accidentally held, sync_reward_roles strips it
        member.roles.append(regular_music_role)
        await leveling.sync_reward_roles(self.db, member, level=5)
        self.assertNotIn(regular_music_role, member.roles)

    async def test_revival_with_premium_music_does_not_restore_regular_music(self):
        """Verify Deadzone revival preserves Premium Music and skips regular Music."""
        member_role = self.guild.get_role(1505437941647015986)
        premium_music_role = self.guild.get_role(1526237128093339848)
        regular_music_role = self.guild.get_role(1505437186219311236)
        dz_role = self.guild.get_role(1551839505168859196)

        # User had both regular and premium music before demotion
        member = make_member(999666, self.guild, roles=[member_role, premium_music_role, regular_music_role])
        dummy_bot = SimpleNamespace(get_channel=lambda *a: None)

        await deadzone.demote_to_deadzone(dummy_bot, self.db, member)
        self.assertIn(dz_role, member.roles)

        # Re-give premium music directly (as sponsor/admin grant)
        member.roles.append(premium_music_role)

        revived = await deadzone.revive_member(dummy_bot, self.db, member, triggered_by="button")
        self.assertTrue(revived)
        self.assertNotIn(dz_role, member.roles)
        self.assertIn(member_role, member.roles)
        self.assertIn(premium_music_role, member.roles)
        self.assertNotIn(regular_music_role, member.roles)

    def test_deadzone_rescue_reward_defaults(self):
        """Verify Deadzone rescue bounty defaults to $50,000 Cash, 250 XC, and 150 XP."""
        self.assertEqual(deadzone.DEFAULTS["deadzone_rescue_reward_cash"], "50000")
        self.assertEqual(deadzone.DEFAULTS["deadzone_rescue_reward_xc"], "250")
        self.assertEqual(deadzone.DEFAULTS["deadzone_rescue_reward_xp"], "150")

    async def test_rescue_command_auto_detects_thawed_comrade(self):
        """Verify /deadzone rescue without a member auto-detects thawed teammate (5/5)."""
        registered = {}
        mock_tree = MagicMock()
        def add_command(group):
            registered["group"] = group
        mock_tree.add_command = add_command
        mock_bot = MagicMock()
        mock_bot.tree = mock_tree
        mock_bot.get_channel = lambda cid: AsyncMock()

        deadzone.register_commands(mock_bot, self.db, lambda i: False, {})
        group = registered["group"]
        rescue_cmd = next(c for c in group.commands if c.name == "rescue")

        # Demote sleeper and thaw them out
        sleeper = make_member(444333, self.guild, roles=[self.guild.get_role(1505437941647015986)])
        self.guild.members.append(sleeper)
        self.guild.get_member = lambda uid: sleeper if uid == sleeper.id else None
        await deadzone.demote_to_deadzone(mock_bot, self.db, sleeper)
        self.db.execute("UPDATE deadzone_members SET thaw_count=5 WHERE user_id=?", (sleeper.id,))
        self.db.commit()

        # Rescuer runs /deadzone rescue without specifying member
        rescuer = make_member(555666, self.guild)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = rescuer
        interaction.guild = self.guild
        interaction.channel_id = 12345
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        await rescue_cmd.callback(interaction, member=None)

        # Verification: Sleeper is revived and rescuer rewarded
        status = deadzone.member_status(self.db, sleeper.id)
        self.assertEqual(status["is_in_deadzone"], 0)
        rescuer_player = self.db.execute("SELECT money, xc FROM players WHERE user_id=?", (rescuer.id,)).fetchone()
        self.assertGreaterEqual(rescuer_player["money"], 50000)
        self.assertGreaterEqual(rescuer_player["xc"], 250)
        interaction.followup.send.assert_called()

    async def test_revival_includes_welcome_party_callout(self):
        """Verify revival alert includes welcome party info pointing to general channel."""
        member_role = self.guild.get_role(1505437941647015986)
        sleeper = make_member(333222, self.guild, roles=[member_role])
        notif_ch = AsyncMock(spec=discord.TextChannel)
        notif_ch.id = 1526521131048370217
        notif_ch.mention = "<#1526521131048370217>"
        party_ch = AsyncMock(spec=discord.TextChannel)
        party_ch.id = 1524716540988231820
        party_ch.mention = "<#1524716540988231820>"

        dummy_bot = MagicMock()
        dummy_bot.get_channel = lambda cid: party_ch if cid == party_ch.id else (notif_ch if cid == notif_ch.id else None)

        await deadzone.demote_to_deadzone(dummy_bot, self.db, sleeper)
        self.db.execute("UPDATE deadzone_members SET thaw_count=5 WHERE user_id=?", (sleeper.id,))
        self.db.commit()

        revived = await deadzone.revive_member(dummy_bot, self.db, sleeper, triggered_by="button")
        self.assertTrue(revived)
        # Verify notif_ch received the alert embed with the party callout
        notif_ch.send.assert_called()
        call_kwargs = notif_ch.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        embed_desc = call_kwargs["embed"].description
        self.assertIn("Resurrection Welcome Party", embed_desc)
        self.assertIn(party_ch.mention, embed_desc)
        # Verify party_ch received the welcome party announcement
        party_ch.send.assert_called()

    async def test_set_party_channel_and_channels_command(self):
        """Verify /deadzone set_party_channel and /deadzone channels work properly."""
        registered = {}
        mock_tree = MagicMock()
        mock_tree.add_command = lambda g: registered.update({"group": g})
        mock_bot = MagicMock()
        mock_bot.tree = mock_tree

        deadzone.register_commands(mock_bot, self.db, lambda i: True, {})
        group = registered["group"]
        set_cmd = next(c for c in group.commands if c.name == "set_party_channel")
        channels_cmd = next(c for c in group.commands if c.name == "channels")

        target_ch = MagicMock(spec=discord.TextChannel)
        target_ch.id = 999111888
        target_ch.mention = "<#999111888>"

        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()

        # Run set_party_channel
        await set_cmd.callback(interaction, channel=target_ch)
        setting_val = deadzone.setting(self.db, "deadzone_party_channel_id")
        self.assertEqual(setting_val, "999111888")

        # Run channels
        await channels_cmd.callback(interaction)
        interaction.response.send_message.assert_called()
        call_msg = interaction.response.send_message.call_args.args[0]
        self.assertIn("999111888", call_msg)


if __name__ == "__main__":
    unittest.main()
