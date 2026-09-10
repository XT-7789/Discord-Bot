"""Offline command-wizard tests: real signatures, mocked command bodies/network."""
import ast
import asyncio
import copy
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Optional
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands
import staff_tools as tools


def catalogue(calls):
    commands = {}
    for filename in ('bot.py', 'economy.py', 'war_tier.py', 'leveling.py', 'casino.py'):
        for node in ast.walk(ast.parse(Path(__file__).with_name(filename).read_text(encoding='utf-8-sig'))):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            command_name = next((k.value.value for d in node.decorator_list
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'command'
                for k in d.keywords if k.arg == 'name' and isinstance(k.value, ast.Constant)), None)
            if command_name not in tools.TOOLS:
                continue
            fn = copy.deepcopy(node)
            fn.decorator_list = [d for d in fn.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in {'choices', 'default_permissions', 'describe'}]
            fn.body = ast.parse("calls.append((interaction.command.name, locals().copy()))\nawait interaction.response.send_message('Done', ephemeral=True)").body
            module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
            scope = dict(discord=discord, app_commands=app_commands, Optional=Optional, calls=calls)
            exec(compile(module, filename, 'exec'), scope)
            commands[command_name] = app_commands.Command(name=command_name, description='Offline test', callback=scope[fn.name])
    return commands


class StaffToolsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.commands = catalogue(self.calls)
        self.db = sqlite3.connect(':memory:'); self.db.row_factory = sqlite3.Row
        source = ast.parse(Path(__file__).with_name('advanced_systems.py').read_text(encoding='utf-8-sig'))
        for node in ast.walk(source):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lstrip().startswith('CREATE TABLE IF NOT EXISTS'):
                if any(f' {name}(' in node.value for name in ('command_permissions', 'command_usage_logs', 'log_settings')):
                    self.db.execute(node.value)
        self.db.executescript('CREATE TABLE items(name TEXT, enabled INTEGER); CREATE TABLE alliances(name TEXT); CREATE TABLE lottery_rounds(id INTEGER, active INTEGER); CREATE TABLE wars(id INTEGER, active INTEGER);')
        for node in ast.walk(source):
            if isinstance(node, ast.Dict):
                try:
                    fields = ast.literal_eval(node)
                except (ValueError, TypeError):
                    continue
                if 'cooldown_bypass_role_ids' in fields:
                    for name, definition in fields.items():
                        self.db.execute(f'ALTER TABLE command_permissions ADD COLUMN {name} {definition}')
        self.db.executemany('INSERT INTO items VALUES(?,1)', [(f'Item {i:02}',) for i in range(30)])
        self.db.executemany('INSERT INTO alliances VALUES(?)', [(f'Alliance {i:02}',) for i in range(30)])
        self.db.executemany('INSERT INTO command_permissions(command_name) VALUES(?)', [(n,) for n in self.commands])
        self.actor = SimpleNamespace(id=10, display_name='Staff', guild_permissions=discord.Permissions(administrator=True), roles=[])
        self.member = SimpleNamespace(id=20, display_name='Player', roles=[])
        self.role = SimpleNamespace(id=30, name='Test role')
        self.allowed = True
        async def access(i):
            if i.user.id != 10 or not self.allowed:
                await i.response.send_message('Denied', ephemeral=True)
                return False
            return True
        self.root = SimpleNamespace(db=self.db, bot=SimpleNamespace(tree=SimpleNamespace(get_command=lambda n, **k:self.commands.get(n))),
            interaction_check=access, staff_check=lambda i:self.allowed, clone=lambda **k:discord.ui.LayoutView(), on_error=AsyncMock())

    async def asyncTearDown(self):
        self.db.close()

    def interaction(self, uid=10):
        response = SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock(), is_done=Mock(return_value=False))
        guild = SimpleNamespace(fetch_member=AsyncMock(side_effect=lambda uid:self.actor if uid==10 else self.member), fetch_roles=AsyncMock(return_value=[self.role]))
        return SimpleNamespace(user=self.actor if uid==10 else self.member, guild=guild, guild_id=1, channel_id=2,
            response=response, followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    def values(self, command):
        result = {}
        for p in command.parameters:
            if p.type == discord.AppCommandOptionType.user:
                result[p.name] = self.member
            elif p.type == discord.AppCommandOptionType.role:
                result[p.name] = self.role
            elif p.choices:
                result[p.name] = p.choices[0]
            elif p.type == discord.AppCommandOptionType.integer:
                result[p.name] = max(1, p.min_value or 1)
            elif p.type == discord.AppCommandOptionType.channel:
                continue  # Optional current-channel destination.
            else:
                result[p.name] = 'Item 00' if p.name=='item' else 'Alliance 00' if 'alliance' in p.name else 'Test reason / message'
        return result

    async def test_catalogue_coverage_and_every_field_serializes(self):
        source = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
        retained = next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='STAFF_SLASH_COMMANDS' for t in n.targets))
        self.assertEqual(set(self.commands), set(tools.TOOLS))
        self.assertGreaterEqual(len((set(tools.TOOLS)|{'admin'}) & retained)/len(retained), .90)
        for command in self.commands.values():
            for step in range(max(1,len(command.parameters))):
                w=tools.ToolView(self.root,command,step=step)
                w.to_components(); self.assertLessEqual(w.total_children_count,40)
                if command.parameters and command.parameters[step].name in {'item','attacker_alliance','defender_alliance','winner_alliance'}:
                    w.offset=25; second=w.rebuild(); second.to_components()
                    select=next(x for x in second.walk_children() if isinstance(x,tools.OptionsSelect))
                    self.assertEqual(len(select.options),5)
                    select._values=['0']; i=self.interaction(); await select.callback(i)
                    self.assertIn('25',second.values[select.parameter.name])

    async def test_all_command_adapters_and_duplicate_confirmation(self):
        for command in self.commands.values():
            w=tools.ToolView(self.root,command,self.values(command))
            confirmation=tools.ConfirmView(w); confirmation.to_components()
            i=self.interaction()
            await confirmation.execute(i)
            self.assertEqual(self.calls[-1][0],command.name)
            count=len(self.calls)
            await confirmation.execute(self.interaction())
            self.assertEqual(len(self.calls),count)
            result=i.edit_original_response.call_args.kwargs
            self.assertIsInstance(result['view'],discord.ui.LayoutView)
            self.assertEqual(result['allowed_mentions'].to_dict(),discord.AllowedMentions.none().to_dict())
        self.assertEqual(len(self.calls),len(tools.TOOLS))

    async def test_category_buttons_and_return_keep_origin(self):
        for name in tools.TOOLS:
            button=tools.ToolButton(self.root,name)
            i=self.interaction();await button.callback(i)
            wizard=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual(wizard.command.name,name)
            self.assertFalse(self.calls)
        self.root.clone=Mock(return_value=discord.ui.LayoutView())
        back=tools.ReturnButton(self.root)
        await back.callback(self.interaction())
        self.root.clone.assert_called_once_with()
        i=self.interaction(uid=20);await tools.ToolButton(self.root,'spawn').callback(i)
        i.response.edit_message.assert_not_awaited()

    async def test_permission_revoked_owner_disabled_and_defaults(self):
        command=self.commands['setlevel']; w=tools.ToolView(self.root,command,self.values(command))
        await tools.ConfirmView(w).execute(self.interaction(uid=20)); self.assertFalse(self.calls)
        self.allowed=False
        await tools.ConfirmView(w).execute(self.interaction()); self.assertFalse(self.calls)
        self.allowed=True; self.actor.guild_permissions=discord.Permissions.none()
        await tools.ConfirmView(w).execute(self.interaction()); self.assertFalse(self.calls)
        self.actor.guild_permissions=discord.Permissions(administrator=True)
        self.db.execute("UPDATE command_permissions SET enabled=0 WHERE command_name='setlevel'")
        await tools.ConfirmView(w).execute(self.interaction()); self.assertFalse(self.calls)
        self.db.execute("UPDATE command_permissions SET enabled=1 WHERE command_name='setlevel'")
        self.root.staff_check=lambda i:False  # Access revoked after panel check / member refresh.
        await tools.ConfirmView(w).execute(self.interaction()); self.assertFalse(self.calls)

    async def test_limits_modal_and_missing_required(self):
        command=self.commands['economy_adjust']
        p=next(p for p in command.parameters if p.name=='amount')
        for raw in ('0','100000001','-100000001','2.5','hello',''):
            with self.assertRaises(ValueError): tools.parse_text(p,raw)
        self.assertEqual(tools.parse_text(p,'-10'),-10)
        w=tools.ToolView(self.root,command)
        i=self.interaction(); await tools.StepButton(w,'Review','review').callback(i)
        i.response.send_message.assert_awaited(); self.assertFalse(self.calls)
        modal=tools.ValueModal(w,p); modal.entry._value='12'
        i=self.interaction(); await modal.on_submit(i)
        self.assertEqual(w.values['amount'],12)
        self.allowed=False; modal.entry._value='99'
        await modal.on_submit(self.interaction()); self.assertEqual(w.values['amount'],12)

    async def test_stale_role_round_and_callback_checks(self):
        command=self.commands['role']; w=tools.ToolView(self.root,command,self.values(command))
        confirm=tools.ConfirmView(w); self.member.roles=[self.role]
        await confirm.execute(self.interaction()); self.assertFalse(self.calls)
        command=self.commands['lottery_draw']; w=tools.ToolView(self.root,command)
        confirm=tools.ConfirmView(w); self.db.execute('INSERT INTO lottery_rounds VALUES(1,1)')
        await confirm.execute(self.interaction()); self.assertFalse(self.calls)
        command.checks.append(lambda i:False)
        await tools.ConfirmView(w).execute(self.interaction()); self.assertFalse(self.calls)

    async def test_select_entrypoint_and_unavailable(self):
        select=tools.ToolSelect(self.root); select._values=['inrole']
        i=self.interaction(); await select.callback(i)
        self.assertIsInstance(i.response.edit_message.call_args.kwargs['view'],tools.ToolView)
        self.root.bot.tree.get_command=lambda *a,**k:None
        i=self.interaction(); await select.callback(i); i.response.send_message.assert_awaited()

    async def test_dashboard_roles_channels_and_cooldown_policy(self):
        command=self.commands['spawn']; w=tools.ToolView(self.root,command,self.values(command))
        self.actor.guild_permissions=discord.Permissions.none()
        self.db.execute("UPDATE command_permissions SET access_mode='roles',allowed_role_ids='30' WHERE command_name='spawn'")
        await tools.ConfirmView(w).execute(self.interaction()); self.assertFalse(self.calls)
        self.actor.roles=[self.role]
        self.db.execute("UPDATE command_permissions SET allowed_channel_ids='999' WHERE command_name='spawn'")
        await tools.ConfirmView(w).execute(self.interaction()); self.assertFalse(self.calls)
        self.db.execute("UPDATE command_permissions SET allowed_channel_ids='',cooldown_seconds=60 WHERE command_name='spawn'")
        await tools.ConfirmView(w).execute(self.interaction()); self.assertEqual(len(self.calls),1)
        await tools.ConfirmView(w).execute(self.interaction()); self.assertEqual(len(self.calls),1)

    async def test_native_selection_defaults_and_clear(self):
        command=self.commands['role']; w=tools.ToolView(self.root,command)
        p=command.parameters[0]; select=tools.ResourceSelect.build(w,p)
        select._values=[self.member]
        with patch.object(type(select),'values',new=property(lambda s:[self.member])):
            await select.callback(self.interaction())
        replacement=w.rebuild(); replacement.to_components()
        selected=next(x for x in replacement.walk_children() if isinstance(x,discord.ui.UserSelect))
        self.assertEqual(selected.default_values[0].id,self.member.id)
        command=self.commands['war_end']; w=tools.ToolView(self.root,command,{'winner_alliance':'Alliance 00'})
        await tools.StepButton(w,'Use default','clear').callback(self.interaction())
        self.assertNotIn('winner_alliance',w.values)

    async def test_field_jump_preserves_values_without_execution(self):
        command=self.commands['economy_adjust']
        values=self.values(command)
        wizard=tools.ToolView(self.root,command,values)
        selector=next(c for c in wizard.walk_children() if isinstance(c,tools.FieldSelect))
        selector._values=[str(len(command.parameters)-1)]
        i=self.interaction();await selector.callback(i)
        rebuilt=i.response.edit_message.call_args.kwargs['view']
        self.assertEqual(rebuilt.step,len(command.parameters)-1)
        self.assertEqual(rebuilt.values,values)
        self.assertFalse(self.calls)
        rebuilt.to_components();self.assertLessEqual(rebuilt.total_children_count,40)
        i=self.interaction(uid=20);await selector.callback(i);i.response.edit_message.assert_not_awaited()

    async def test_concurrent_confirmations_only_execute_once(self):
        command=self.commands['economy_adjust']; w=tools.ToolView(self.root,command,self.values(command))
        confirmation=tools.ConfirmView(w)
        await asyncio.gather(confirmation.execute(self.interaction()),confirmation.execute(self.interaction()))
        self.assertEqual(len(self.calls),1)


if __name__=='__main__':
    unittest.main()
