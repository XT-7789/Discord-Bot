"""Unit tests for the Gaming Zone suite (Steam, Roblox, Mobile, LFG, Profiles)."""
import asyncio
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import gaming
import economy


def make_role(role_id, name):
    r = MagicMock(spec=discord.Role)
    r.id = int(role_id)
    r.name = name
    return r


class DummyGuild:
    def __init__(self):
        self.id = 1505255035104268480
        self.name = "X Community"
        self.roles = [
            make_role(1552259861654413312, "🎮 Steam"),
            make_role(1552259987667943515, "🟥 Roblox"),
            make_role(1552259988691488818, "📱 Mobile"),
            make_role(1552260001111111111, "🖥️ PC"),
            make_role(1552260002222222222, "📱 Mobile Device"),
            make_role(1552260003333333333, "🎮 Console"),
        ]

    def get_role(self, role_id):
        for r in self.roles:
            if r.id == int(role_id):
                return r
        return None


def make_member(user_id, guild, roles=None, display_name="Gamer"):
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

    m.add_roles = AsyncMock(side_effect=add_roles)
    m.remove_roles = AsyncMock(side_effect=remove_roles)
    return m


class GamingZoneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE economy_settings (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("""CREATE TABLE players (
            user_id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            xc INTEGER NOT NULL DEFAULT 0,
            bank_xc INTEGER NOT NULL DEFAULT 0,
            money INTEGER NOT NULL DEFAULT 0,
            xcrystals INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1,
            xp INTEGER NOT NULL DEFAULT 0
        )""")
        self.db.execute("""CREATE TABLE inventories (
            user_id INTEGER,
            item_id TEXT,
            quantity INTEGER,
            PRIMARY KEY(user_id, item_id)
        )""")
        gaming.initialise(self.db)
        self.guild = DummyGuild()

    def tearDown(self):
        self.db.close()

    def test_initialise_defaults(self):
        self.assertEqual(gaming.setting(self.db, "game_role_steam_id"), "1552259861654413312")
        self.assertEqual(gaming.setting(self.db, "game_role_roblox_id"), "1552259987667943515")
        self.assertEqual(gaming.setting(self.db, "game_role_valorant_id"), "1554759111281745962")
        self.assertEqual(gaming.setting(self.db, "game_role_minecraft_id"), "1554759163442237440")
        self.assertEqual(gaming.setting(self.db, "game_role_mlbb_id"), "1554759232270639216")
        self.assertEqual(gaming.setting(self.db, "game_role_genshin_id"), "1554759280194883615")
        self.assertEqual(gaming.setting(self.db, "game_channel_steam_id"), "1552259761607671908")
        self.assertEqual(gaming.setting(self.db, "game_channel_roblox_id"), "1552237657424265236")
        self.assertEqual(gaming.setting(self.db, "game_channel_mobile_id"), "1552237704878620722")
        self.assertEqual(gaming.setting(self.db, "device_role_pc_id"), "1554759037914976317")
        self.assertEqual(gaming.setting(self.db, "device_role_mobile_id"), "1552259988691488818")
        self.assertEqual(gaming.setting(self.db, "device_role_console_id"), "1554758965454315520")
        self.assertEqual(gaming.setting(self.db, "lfg_team_reward_cash"), "500")

    def test_game_profile_crud(self):
        user_id = 111222333
        profile = gaming.get_game_profile(self.db, user_id)
        self.assertIsNone(profile)

        gaming.save_game_profile(self.db, user_id, "steam_user1", "roblox_builder", "PUBG, Genshin")
        profile = gaming.get_game_profile(self.db, user_id)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["steam_id"], "steam_user1")
        self.assertEqual(profile["roblox_name"], "roblox_builder")
        self.assertEqual(profile["mobile_games"], "PUBG, Genshin")

        # Update profile
        gaming.save_game_profile(self.db, user_id, "steam_pro", "roblox_builder", "Brawl Stars")
        updated = gaming.get_game_profile(self.db, user_id)
        self.assertEqual(updated["steam_id"], "steam_pro")
        self.assertEqual(updated["mobile_games"], "Brawl Stars")

    async def test_game_roles_button_toggle(self):
        member = make_member(12345, self.guild)
        steam_role = self.guild.get_role(1552259861654413312)

        button = gaming.GameRolesButton("steam")

        # Mock interaction
        client = MagicMock()
        client.xbot_db = self.db
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = self.guild
        interaction.user = member
        interaction.client = client
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        # 1. First click: Add role
        self.assertNotIn(steam_role, member.roles)
        await button.callback(interaction)
        self.assertIn(steam_role, member.roles)
        interaction.response.send_message.assert_awaited()
        self.assertIn("Added role", interaction.response.send_message.call_args[0][0])

        # 2. Second click: Remove role
        interaction.response.send_message.reset_mock()
        await button.callback(interaction)
        self.assertNotIn(steam_role, member.roles)
        self.assertIn("Removed role", interaction.response.send_message.call_args[0][0])

    async def test_device_roles_button_toggle(self):
        # Configure PC role
        pc_role = self.guild.get_role(1552260001111111111)
        self.db.execute("INSERT OR REPLACE INTO economy_settings(key, value) VALUES ('device_role_pc_id', '1552260001111111111')")
        self.db.commit()

        member = make_member(54321, self.guild)
        button = gaming.DeviceRolesButton("pc")

        client = MagicMock()
        client.xbot_db = self.db
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = self.guild
        interaction.user = member
        interaction.client = client
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        # 1. Add PC role
        self.assertNotIn(pc_role, member.roles)
        await button.callback(interaction)
        self.assertIn(pc_role, member.roles)
        interaction.response.send_message.assert_awaited()
        self.assertIn("Added device role", interaction.response.send_message.call_args[0][0])

        # 2. Remove PC role
        interaction.response.send_message.reset_mock()
        await button.callback(interaction)
        self.assertNotIn(pc_role, member.roles)
        self.assertIn("Removed device role", interaction.response.send_message.call_args[0][0])

    def test_new_user_onboarding_view(self):
        view = gaming.NewUserOnboardingView()
        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 9)  # 3 device buttons + 6 game buttons (3x3 grid)
        device_btns = [btn for btn in view.children if isinstance(btn, gaming.DeviceRolesButton)]
        game_btns = [btn for btn in view.children if isinstance(btn, gaming.GameRolesButton)]
        self.assertEqual(len(device_btns), 3)
        self.assertEqual(len(game_btns), 6)
        embed = gaming.build_onboarding_embed()
        self.assertIn("COMMUNITY & GAMING ROLES", embed.title)
        self.assertIn("Valorant", embed.description)
        self.assertIn("Minecraft", embed.description)
        self.assertIn("MLBB", embed.description)
        self.assertIn("Genshin", embed.description)

    async def test_lfg_party_flow_and_cash_reward(self):
        host_id = 1001
        joiner_id = 1002

        # Give players initial accounts
        self.db.execute("INSERT INTO players (user_id, display_name, money) VALUES (?, ?, ?)", (host_id, "HostGamer", 100))
        self.db.execute("INSERT INTO players (user_id, display_name, money) VALUES (?, ?, ?)", (joiner_id, "JoinerGamer", 50))
        self.db.commit()

        # Create party of 2
        now = int(time.time())
        cursor = self.db.execute(
            "INSERT INTO lfg_parties (host_id, game, party_size, note, channel_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (host_id, "steam", 2, "Need duo for Lethal Company", 1552259761607671908, now),
        )
        party_id = cursor.lastrowid
        self.db.execute(
            "INSERT INTO lfg_members (party_id, user_id, joined_at) VALUES (?, ?, ?)",
            (party_id, host_id, now),
        )
        self.db.commit()

        view = gaming.LFGPartyView(party_id, host_id, 2, self.db)
        self.assertEqual(view.get_members(), [host_id])

        # Joiner clicks "Join Squad"
        joiner_member = make_member(joiner_id, self.guild)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = joiner_member
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        await view.join_button.callback(interaction)

        # Party should now be full
        party_row = self.db.execute("SELECT * FROM lfg_parties WHERE id=?", (party_id,)).fetchone()
        self.assertEqual(party_row["status"], "full")
        self.assertEqual(view.get_members(), [host_id, joiner_id])

        # Both players should receive +500 Cash
        host_row = self.db.execute("SELECT money FROM players WHERE user_id=?", (host_id,)).fetchone()
        joiner_row = self.db.execute("SELECT money FROM players WHERE user_id=?", (joiner_id,)).fetchone()
        self.assertEqual(host_row["money"], 600)  # 100 + 500
        self.assertEqual(joiner_row["money"], 550)  # 50 + 500

        # Announce message sent
        interaction.followup.send.assert_awaited()
        self.assertIn("The squad is FULL!", interaction.followup.send.call_args[0][0])

    async def test_lfg_party_cancel(self):
        host_id = 2001
        now = int(time.time())
        cursor = self.db.execute(
            "INSERT INTO lfg_parties (host_id, game, party_size, note, channel_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (host_id, "roblox", 4, "Doors run", 123456, now),
        )
        party_id = cursor.lastrowid
        self.db.execute("INSERT INTO lfg_members (party_id, user_id, joined_at) VALUES (?, ?, ?)", (party_id, host_id, now))
        self.db.commit()

        view = gaming.LFGPartyView(party_id, host_id, 4, self.db)

        # Non-host attempt to cancel
        stranger = make_member(9999, self.guild)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = stranger
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        await view.cancel_button.callback(interaction)

        self.assertIn("Only the squad host or an admin can cancel", interaction.response.send_message.call_args[0][0])
        status = self.db.execute("SELECT status FROM lfg_parties WHERE id=?", (party_id,)).fetchone()["status"]
        self.assertEqual(status, "open")

        # Host cancels
        interaction.user = make_member(host_id, self.guild)
        interaction.response.edit_message = AsyncMock()
        await view.cancel_button.callback(interaction)

        status_after = self.db.execute("SELECT status FROM lfg_parties WHERE id=?", (party_id,)).fetchone()["status"]
        self.assertEqual(status_after, "cancelled")

    async def test_gamer_card_view_and_copy(self):
        target = make_member(777, self.guild, display_name="TargetGamer")
        profile = {"user_id": 777, "steam_id": "12345678", "roblox_name": "ProRoblox", "mobile_games": "CODM"}

        view = gaming.GamerCardView(target, profile, self.db)

        # Should have Steam button, Roblox button, Invite button, Edit button, Economy Profile button
        labels = [item.label for item in view.children if isinstance(item, discord.ui.Button)]
        self.assertIn("Copy Steam Code", labels)
        self.assertIn("Copy Roblox User", labels)
        self.assertIn("Invite to Lounge", labels)
        self.assertIn("Edit Handles", labels)
        self.assertIn("Economy Profile", labels)

        # Test Steam copy
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        await view.on_copy_steam(interaction)
        interaction.response.send_message.assert_awaited()
        self.assertIn("12345678", interaction.response.send_message.call_args[0][0])

        # Test Roblox copy
        interaction.response.send_message.reset_mock()
        await view.on_copy_roblox(interaction)
        interaction.response.send_message.assert_awaited()
        self.assertIn("ProRoblox", interaction.response.send_message.call_args[0][0])

        # Test View Economy Profile
        interaction.response.send_message.reset_mock()
        await view.on_view_profile(interaction)
        interaction.response.send_message.assert_awaited()
        self.assertTrue(interaction.response.send_message.call_args[1].get("ephemeral"))

    async def test_quick_game_set_modal(self):
        user_id = 888
        modal = gaming.QuickGameSetModal(self.db, existing=None)
        modal.steam_input._value = "steam_test_99"
        modal.roblox_input._value = "roblox_test_99"
        modal.mobile_input._value = "Apex, MLBB"

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = make_member(user_id, self.guild)
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await modal.on_submit(interaction)
        interaction.response.send_message.assert_awaited()
        self.assertIn("Gaming Handles Updated!", interaction.response.send_message.call_args[0][0])

        saved = gaming.get_game_profile(self.db, user_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["steam_id"], "steam_test_99")
        self.assertEqual(saved["roblox_name"], "roblox_test_99")
        self.assertEqual(saved["mobile_games"], "Apex, MLBB")

    async def test_economy_profile_view_and_gamer_button(self):
        target = make_member(999, self.guild, display_name="TestProfileUser")
        gaming.save_game_profile(self.db, 999, "my_steam_tag", "my_roblox_tag", "Wild Rift")

        view = economy.build_profile_view(target, self.db)
        self.assertIsInstance(view, discord.ui.LayoutView)

        # Check action row buttons
        buttons = [item for item in view.children if isinstance(item, discord.ui.Container)][0].children
        action_rows = [b for b in buttons if isinstance(b, discord.ui.ActionRow)]
        self.assertTrue(len(action_rows) > 0)
        btn_labels = [btn.label for btn in action_rows[0].children if isinstance(btn, discord.ui.Button)]
        self.assertIn("Gamer Card", btn_labels)
        self.assertIn("Edit Handles", btn_labels)

        # Click Gamer Card button
        btn_gamer = [btn for btn in action_rows[0].children if getattr(btn, "label", None) == "Gamer Card"][0]
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = self.guild
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await btn_gamer.callback(interaction)
        interaction.response.send_message.assert_awaited()
        embed = interaction.response.send_message.call_args[1].get("embed")
        self.assertIsNotNone(embed)
        self.assertIn("TestProfileUser's Gamer Card", embed.title)


if __name__ == "__main__":
    unittest.main()
