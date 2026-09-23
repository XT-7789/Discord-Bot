"""Tests for unified Admin Panel v2: interactive economy, armed forces, server settings, and deployment."""
import asyncio
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
import gaming
import leveling
import staff_panel
import war_system
import war_tier


class StaffPanelV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import bot
        self.bot_module = bot
        self.db = bot.db

        self.owner_id = 99000111
        self.target_user_id = 99000222
        self.channel_id = 99000333

        # Clean up any leftover test data
        self.db.execute("DELETE FROM players WHERE user_id IN (?, ?)", (self.owner_id, self.target_user_id))
        self.db.execute("DELETE FROM inventories WHERE user_id IN (?, ?)", (self.owner_id, self.target_user_id))
        
        # Insert test target player
        self.db.execute(
            """INSERT INTO players(user_id, money, xc, bank_xc, xcrystals, nation_name, capital_health, capital_name, iron, gold, oil, land_army, air_army, navy)
               VALUES(?, 500, 1000, 2000, 50, 'Republic of Test', 100, 'TestCity', 100, 200, 300, 10, 20, 30)""",
            (self.target_user_id,)
        )
        self.db.commit()

        self.mock_bot = SimpleNamespace(
            tree=SimpleNamespace(get_command=Mock(return_value=None)),
            get_channel=Mock()
        )

    async def asyncTearDown(self):
        self.db.execute("DELETE FROM players WHERE user_id IN (?, ?)", (self.owner_id, self.target_user_id))
        self.db.execute("DELETE FROM inventories WHERE user_id IN (?, ?)", (self.owner_id, self.target_user_id))
        self.db.commit()
        import sys
        sys.modules.pop("bot", None)

    def make_interaction(self, user_id=None, is_staff=True):
        uid = user_id or self.owner_id
        response = SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
            defer=AsyncMock(),
            is_done=Mock(return_value=False)
        )
        channel = SimpleNamespace(id=self.channel_id, send=AsyncMock())
        guild = SimpleNamespace(
            id=12345,
            owner_id=self.owner_id,
            get_channel=Mock(return_value=channel),
            fetch_channel=AsyncMock(return_value=channel)
        )
        user = SimpleNamespace(
            id=uid,
            mention=f"<@{uid}>",
            display_name=f"User{uid}",
            guild_permissions=discord.Permissions(administrator=is_staff),
            roles=[]
        )
        return SimpleNamespace(
            user=user,
            guild=guild,
            channel=channel,
            response=response,
            followup=SimpleNamespace(send=AsyncMock())
        )

    async def test_assets_page_embed_and_modals(self):
        panel = staff_panel.AdminPanel(self.mock_bot, self.db, lambda i: True, self.owner_id, page="assets", target_user_id=self.target_user_id)
        embed = panel.build_embed()
        self.assertIn("Player Economy & Asset Manager", embed.title)
        self.assertIn(f"<@{self.target_user_id}>", embed.description)
        self.assertIn("1,000", embed.fields[0].value)  # XC Wallet
        self.assertIn("2,000", embed.fields[0].value)  # XC Bank

        # Test AssetEditMoneyModal
        modal = staff_panel.AssetEditMoneyModal(panel)
        modal.currency.default = "money"
        modal.currency._value = "money"
        modal.amount.default = "+250"
        modal.amount._value = "+250"
        modal.reason.default = "Reward testing"
        modal.reason._value = "Reward testing"
        submit_interaction = self.make_interaction()
        await modal.on_submit(submit_interaction)
        submit_interaction.response.defer.assert_awaited_once()

        row = self.db.execute("SELECT money FROM players WHERE user_id=?", (self.target_user_id,)).fetchone()
        self.assertEqual(row["money"], 750)

        # Test AssetSpawnItemModal
        item_row = self.db.execute("SELECT id, name FROM items LIMIT 1").fetchone()
        item_id = item_row["id"] if item_row else "iron_ingot"
        spawn_modal = staff_panel.AssetSpawnItemModal(panel)
        spawn_modal.item_id.default = str(item_id)
        spawn_modal.item_id._value = str(item_id)
        spawn_modal.quantity.default = "5"
        spawn_modal.quantity._value = "5"
        spawn_modal.reason.default = "Spawn test"
        spawn_modal.reason._value = "Spawn test"
        spawn_interaction = self.make_interaction()
        await spawn_modal.on_submit(spawn_interaction)
        spawn_interaction.response.defer.assert_awaited_once()

        inv = self.db.execute("SELECT quantity FROM inventories WHERE user_id=? AND item_id=?", (self.target_user_id, item_id)).fetchone()
        self.assertIsNotNone(inv)
        self.assertGreaterEqual(inv["quantity"], 5)

        # Test AssetRemoveItemModal
        remove_modal = staff_panel.AssetRemoveItemModal(panel)
        remove_modal.item_id.default = str(item_id)
        remove_modal.item_id._value = str(item_id)
        remove_modal.quantity.default = "2"
        remove_modal.quantity._value = "2"
        remove_interaction = self.make_interaction()
        await remove_modal.on_submit(remove_interaction)
        remove_interaction.response.defer.assert_awaited_once()

        inv2 = self.db.execute("SELECT quantity FROM inventories WHERE user_id=? AND item_id=?", (self.target_user_id, item_id)).fetchone()
        self.assertEqual(inv2["quantity"], inv["quantity"] - 2)

        # Test user select
        user_select = next((child for child in panel.walk_children() if isinstance(child, staff_panel.AdminUserSelect)), None)
        self.assertIsNotNone(user_select)
        new_target = SimpleNamespace(id=99999)
        user_select._values = [new_target]
        interaction = self.make_interaction()
        await user_select.callback(interaction)
        interaction.response.edit_message.assert_awaited_once()
        new_view = interaction.response.edit_message.call_args.kwargs["view"]
        self.assertEqual(new_view.target_user_id, 99999)

    async def test_war_tools_page_embed_and_modals(self):
        panel = staff_panel.AdminPanel(self.mock_bot, self.db, lambda i: True, self.owner_id, page="war_tools", target_user_id=self.target_user_id)
        embed = panel.build_embed()
        self.assertIn("Armed Forces & Military Manager", embed.title)
        self.assertIn("Republic of Test", embed.fields[0].value)
        self.assertIn("TestCity", embed.fields[0].value)

        # Test WarEditCapitalModal
        capital_modal = staff_panel.WarEditCapitalModal(panel)
        capital_modal.capital_name.default = "New Capital"
        capital_modal.capital_name._value = "New Capital"
        capital_modal.health.default = "85"
        capital_modal.health._value = "85"
        capital_interaction = self.make_interaction()
        await capital_modal.on_submit(capital_interaction)
        capital_interaction.response.defer.assert_awaited_once()

        row = self.db.execute("SELECT capital_name, capital_health FROM players WHERE user_id=?", (self.target_user_id,)).fetchone()
        self.assertEqual(row["capital_name"], "New Capital")
        self.assertEqual(row["capital_health"], 85)

        # Test WarEditResourcesModal
        res_modal = staff_panel.WarEditResourcesModal(panel)
        res_modal.gold.default = "500"
        res_modal.gold._value = "500"
        res_modal.iron.default = "600"
        res_modal.iron._value = "600"
        res_modal.oil.default = "700"
        res_modal.oil._value = "700"
        res_interaction = self.make_interaction()
        await res_modal.on_submit(res_interaction)
        res_interaction.response.defer.assert_awaited_once()

        row_res = self.db.execute("SELECT gold, iron, oil FROM players WHERE user_id=?", (self.target_user_id,)).fetchone()
        self.assertEqual(row_res["gold"], 500)
        self.assertEqual(row_res["iron"], 600)
        self.assertEqual(row_res["oil"], 700)

        # Test war_toggle_status action
        toggle_interaction = self.make_interaction()
        await panel.handle_action(toggle_interaction, "war_toggle_status")
        toggle_interaction.response.edit_message.assert_awaited_once()

    async def test_server_page_channel_select_and_actions(self):
        panel = staff_panel.AdminPanel(self.mock_bot, self.db, lambda i: True, self.owner_id, page="server", target_channel_id=self.channel_id)
        embed = panel.build_embed()
        self.assertIn("Server Settings", embed.title)

        # Test AdminChannelSelect
        channel_select = next((child for child in panel.walk_children() if isinstance(child, staff_panel.AdminChannelSelect)), None)
        self.assertIsNotNone(channel_select)
        new_channel = SimpleNamespace(id=77777)
        channel_select._values = [new_channel]
        interaction = self.make_interaction()
        await channel_select.callback(interaction)
        interaction.response.edit_message.assert_awaited_once()
        new_view = interaction.response.edit_message.call_args.kwargs["view"]
        self.assertEqual(new_view.target_channel_id, 77777)

        # Test toggle level notices
        toggle_interaction = self.make_interaction()
        await panel.handle_action(toggle_interaction, "server_toggle_level")
        toggle_interaction.response.edit_message.assert_awaited_once()
        setting_val = leveling.setting(self.db, "xp_announcement_enabled")
        self.assertIn(setting_val, {"0", "1"})

        # Test set level channel
        panel.target_channel_id = self.channel_id
        set_chan_interaction = self.make_interaction()
        await panel.handle_action(set_chan_interaction, "server_set_level_channel")
        set_chan_interaction.response.edit_message.assert_awaited_once()
        self.assertEqual(leveling.setting(self.db, "xp_announcement_channel_id"), str(self.channel_id))

        # Test post gaming roles to target channel
        post_roles_interaction = self.make_interaction()
        self.mock_bot.get_channel = Mock(return_value=post_roles_interaction.channel)
        await panel.handle_action(post_roles_interaction, "server_post_gaming_roles")
        post_roles_interaction.channel.send.assert_awaited_once()
        sent_view = post_roles_interaction.channel.send.call_args.kwargs.get("view")
        self.assertIsInstance(sent_view, gaming.GameRolesView)

    async def test_command_routing_to_admin_panel(self):
        guild_id = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
        guild_obj = discord.Object(id=guild_id) if guild_id else None

        # Test economy_adjust with amount=0 routes to AdminPanel assets
        econ_cmd = self.bot_module.bot.tree.get_command("economy_adjust", guild=guild_obj) or self.bot_module.bot.tree.get_command("economy_adjust")
        self.assertIsNotNone(econ_cmd)
        interaction = self.make_interaction()
        user_arg = SimpleNamespace(id=self.target_user_id, mention=f"<@{self.target_user_id}>")
        currency_arg = SimpleNamespace(name="XC Wallet", value="xc")
        await econ_cmd.callback(interaction, user_arg, currency_arg, 0, "open panel")
        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        view = call_kwargs.get("view")
        self.assertIsInstance(view, staff_panel.AdminPanel)
        self.assertEqual(view.page, "assets")
        self.assertEqual(view.target_user_id, self.target_user_id)

        # Test forces_check routes to AdminPanel war_tools
        forces_cmd = self.bot_module.bot.tree.get_command("forces_check", guild=guild_obj) or self.bot_module.bot.tree.get_command("forces_check")
        self.assertIsNotNone(forces_cmd)
        interaction2 = self.make_interaction()
        await forces_cmd.callback(interaction2, user_arg)
        interaction2.response.send_message.assert_awaited_once()
        call2_kwargs = interaction2.response.send_message.call_args.kwargs
        view2 = call2_kwargs.get("view")
        self.assertIsInstance(view2, staff_panel.AdminPanel)
        self.assertEqual(view2.page, "war_tools")
        self.assertEqual(view2.target_user_id, self.target_user_id)

        # Test server_settings routes to AdminPanel server
        server_cmd = self.bot_module.bot.tree.get_command("server_settings", guild=guild_obj) or self.bot_module.bot.tree.get_command("server_settings")
        self.assertIsNotNone(server_cmd)
        interaction3 = self.make_interaction()
        await server_cmd.callback(interaction3)
        interaction3.response.send_message.assert_awaited_once()
        call3_kwargs = interaction3.response.send_message.call_args.kwargs
        view3 = call3_kwargs.get("view")
        self.assertIsInstance(view3, staff_panel.AdminPanel)
        self.assertEqual(view3.page, "server")


if __name__ == "__main__":
    unittest.main()
