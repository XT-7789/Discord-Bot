"""Admin UI and settings integration. All writes use disposable databases."""
import asyncio
from contextlib import closing
import importlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
import casino
import economy_extra
import economy_settings_admin as settings
import staff_panel
import tester_feedback
import tier6
import tier8


class AdminDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.TemporaryDirectory(prefix='xbot-admin-tests-')
        cls.seed = Path(cls.root.name) / 'seed.db'
        connect = sqlite3.connect
        with closing(connect(cls.seed)) as target:
            with closing(connect(f"file:{Path(__file__).with_name('xwar.db').as_posix()}?mode=ro", uri=True)) as source:
                source.backup(target)
        def isolated(path, *args, **kwargs):
            if str(path).endswith('xwar.db'):
                path = cls.seed
            return connect(path, *args, **kwargs)
        with patch('dotenv.load_dotenv'), patch.dict(os.environ, {
            'DASHBOARD_PASSWORD': 'local-fixture-only', 'DASHBOARD_SECRET': 'test-secret',
            'DISCORD_TOKEN': '', 'DISCORD_CLIENT_ID': '', 'DISCORD_CLIENT_SECRET': '',
            'DISCORD_GUILD_ID': '', 'DASHBOARD_OWNER_ID': ''}), patch('sqlite3.connect', isolated):
            cls.dashboard = importlib.import_module('dashboard')
            cls.dashboard.startup_db.close()
        cls.dashboard.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cls.root.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=self.root.name)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'test.db'
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        with closing(sqlite3.connect(self.seed)) as source:
            source.backup(self.db)
        tester_feedback.initialise(self.db)
        self.dashboard.DATABASE_PATH = self.path
        self.client = self.dashboard.app.test_client()
        self.login('admin')
        for name in ('discord_server_roles', 'discord_server_members', 'discord_server_text_channels'):
            mocker = patch.object(self.dashboard, name, return_value=[])
            mocker.start()
            self.addCleanup(mocker.stop)
        blocker = patch('urllib.request.urlopen', side_effect=AssertionError('Network not allowed in test'))
        blocker.start()
        self.addCleanup(blocker.stop)

    def login(self, role):
        with self.client.session_transaction() as session:
            session['dashboard_role'] = role
            session['discord_user_id'] = 'codex-admin-test'
            session['discord_name'] = 'Codex Test'
            session['research_csrf'] = 'test-csrf'

    def values(self):
        return dict(self.db.execute('SELECT key,value FROM economy_settings'))

    def flashes(self):
        with self.client.session_transaction() as session:
            return list(session.get('_flashes', []))

    def test_two_connections_save_and_bot_readers(self):
        before = self.values()
        assets = list(self.db.execute('SELECT user_id,xc,money,bank_xc FROM players'))
        response = self.client.post('/tier6-economy/settings', data={
            'daily_reward': '61', 'tier6_production_queue_limit': '7', 'market_enabled': '0'})
        self.assertEqual(302, response.status_code)
        self.assertEqual(61, economy_extra.setting(self.db, 'daily_reward'))
        self.assertEqual(7, tier6.setting(self.db, 'tier6_production_queue_limit'))
        self.assertEqual(0, economy_extra.setting(self.db, 'market_enabled'))
        audit = self.db.execute('SELECT * FROM dashboard_audit_logs ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual('codex-admin-test', audit['actor_id'])
        detail = json.loads(audit['detail'])
        self.assertEqual({'before': before['daily_reward'], 'after': '61'}, detail['daily_reward'])
        self.assertEqual(assets, list(self.db.execute('SELECT user_id,xc,money,bank_xc FROM players')))
        self.assertEqual('success', self.flashes()[-1][0])

    def test_shared_validation_all_entrypoints(self):
        cases = [('/settings', {'daily_reward': '-1'}),
            ('/settings', {'daily_reward': '22', 'market_enabled': '2'}),
            ('/tier6-economy/settings', {'daily_reward': '22', 'tier6_stock_fee_percent': '101'}),
            ('/tier6-economy/settings', {'tier6_stock_update_seconds': '1'}),
            ('/tier6-economy/settings', {'tier6_production_seconds_per_item': '1'}),
            ('/settings', {'daily_reward': '1.2'}), ('/market', {'market_enabled': 'banana'}),
            ('/system/casino_enabled/toggle', {'enabled': 'banana'}),
            ('/mining/settings', {'mining_energy_enabled': '1', 'mining_max_energy': '0'}),
            ('/casino', {'action': 'save-vip', 'casino_vip_cooldown_percent': '96'}),
            ('/research', {'action': 'settings', 'csrf': 'test-csrf', 'enabled': '1', 'queue_limit': '7'}),
            ('/settings', {'transfer_min': '500', 'transfer_max': '1'}),
            ('/settings', {'daily_reward': '999999999999999999999999999'}),
            ('/settings', {'token': 'do-not-log-this-secret'})]
        for url, data in cases:
            with self.subTest(url=url, data=data):
                before = self.values()
                response = self.client.post(url, data=data)
                self.assertEqual(302, response.status_code)
                self.assertEqual(before, self.values())
                self.assertEqual('error', self.flashes()[-1][0])
        self.assertFalse(self.db.execute("SELECT 1 FROM dashboard_audit_logs WHERE detail LIKE '%do-not-log-this-secret%'").fetchone())

    def test_casino_and_research_readback(self):
        self.client.post('/casino', data={'action': 'save-game', 'game': 'dice', 'enabled': '1',
            'min_bet': '10', 'max_bet': '100', 'cooldown_seconds': '12'})
        self.assertEqual(12, casino.cooldown_info(self.db, SimpleNamespace(id=990088, roles=[]), 'dice')['seconds'])
        self.client.post('/casino', data={'action': 'save-vip', 'casino_vip_daily_cost': '105'})
        self.assertEqual(105, casino.setting(self.db, 'casino_vip_daily_cost'))
        self.client.post('/casino', data={'casino_enabled': '0'})
        self.assertEqual(0, casino.setting(self.db, 'casino_enabled'))
        self.client.post('/research', data={'action': 'settings', 'csrf': 'test-csrf', 'enabled': '0', 'queue_limit': '3'})
        self.assertFalse(tier8.enabled(self.db))
        before_jobs = list(self.db.execute('SELECT * FROM tier8_research_jobs'))
        self.client.post('/research', data={'csrf': 'test-csrf', 'code': 'mining', 'max_level': '5',
            'base_cost': '110', 'seconds': '360', 'bonus_per_level': '5', 'bonus_cap': '25', 'enabled': '1'})
        self.assertEqual(110, self.db.execute("SELECT base_cost FROM tier8_technologies WHERE code='mining'").fetchone()[0])
        self.assertEqual(before_jobs, list(self.db.execute('SELECT * FROM tier8_research_jobs')))
        audit = json.loads(self.db.execute('SELECT detail FROM dashboard_audit_logs ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('before', audit['mining.base_cost'])

    def test_casino_saved_inheritance_survives_initialisation(self):
        self.client.post('/casino', data={'action': 'save-game', 'game': 'crash', 'enabled': '1',
            'min_bet': '0', 'max_bet': '0', 'cooldown_seconds': '15'})
        casino.initialise(self.db)
        row = self.db.execute("SELECT min_bet,max_bet,cooldown_seconds FROM casino_game_settings WHERE game='crash'").fetchone()
        self.assertEqual((0, 0, 15), tuple(row))

    def test_other_economy_switches_and_mining_readback(self):
        import economy
        import advanced_systems
        for key in settings.TOGGLE_KEYS:
            self.client.post(f'/system/{key}/toggle', data={'enabled': '0'})
            self.assertEqual('0', self.values()[key])
        self.client.post('/mining/settings', data={'mining_max_energy': '120', 'mining_energy_regen_seconds': '600'})
        self.assertEqual(120, economy.setting(self.db, 'mining_max_energy'))
        self.assertEqual(0, advanced_systems.setting(self.db, 'income_enabled'))

    def test_permission_policy_and_csrf_unchanged(self):
        for role in ('viewer', 'economy_manager'):
            self.login(role)
            before = self.values()
            for url, data in [('/settings', {'daily_reward': '900'}),
                ('/tier6-economy/settings', {'daily_reward': '900'}),
                ('/casino', {'casino_enabled': '0'}), ('/research', {'action': 'settings'})]:
                self.assertEqual(403, self.client.post(url, data=data).status_code)
            self.assertEqual(before, self.values())
        self.login('admin')
        self.assertEqual(403, self.client.post('/research', data={'action': 'settings', 'csrf': 'wrong'}).status_code)

    def test_failure_during_audit_rolls_back_entire_save(self):
        self.db.execute("CREATE TRIGGER fail_test_audit BEFORE INSERT ON dashboard_audit_logs BEGIN SELECT RAISE(ABORT,'test failure'); END")
        self.db.commit()
        before = self.values()
        self.client.post('/tier6-economy/settings', data={'daily_reward': '111', 'market_enabled': '0'})
        self.assertEqual(before, self.values())
        self.assertEqual('error', self.flashes()[-1][0])
        self.assertFalse(self.db.in_transaction)

    def test_busy_database_shows_retry_and_changes_nothing(self):
        def short_connection():
            db = sqlite3.connect(self.path, timeout=.01)
            db.row_factory = sqlite3.Row
            return db
        before = self.values()
        self.db.execute('BEGIN IMMEDIATE')
        with patch.object(self.dashboard, 'get_db', short_connection):
            response = self.client.post('/tier6-economy/settings', data={'daily_reward': '999'})
        self.db.rollback()
        self.assertEqual(302, response.status_code)
        self.assertEqual(before, self.values())
        self.assertIn('busy', self.flashes()[-1][1])

    def test_all_dashboard_navigation_destinations_render(self):
        from dashboard_ui import GROUPS
        for endpoint in set().union(*GROUPS.values()) - {'logout'}:
            with self.subTest(endpoint=endpoint):
                with self.dashboard.app.test_request_context():
                    url = self.dashboard.url_for(endpoint)
                response = self.client.get(url)
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn('admin-system.css', html)
                self.assertIn('setting-metadata', html)
                for group in GROUPS:
                    self.assertIn(f'<h2>{group}</h2>', html)
        import dashboard_ui
        old_links = re.findall(r'<a\b[^>]*>.*?</a>', self.dashboard.HEADER, re.S)
        new = dashboard_ui.header(self.dashboard.HEADER)
        for link in old_links:
            self.assertIn(link, new)

    def seed_admin(self):
        self.db.execute('DELETE FROM application_submissions')
        self.db.execute('DELETE FROM tester_feedback')
        self.db.execute('DELETE FROM reward_codes')
        for i in range(30):
            self.db.execute('INSERT OR IGNORE INTO application_forms(name) VALUES(?)', (f'Test Form {i:02}',))
        form = self.db.execute('SELECT id FROM application_forms ORDER BY id LIMIT 1').fetchone()[0]
        for i in range(30):
            self.db.execute('INSERT INTO application_submissions(form_id,user_id,user_name,created_at) VALUES(?,?,?,?)', (form, 990088, f'Applicant {i}', i))
            self.db.execute("INSERT INTO tester_feedback(user_id,user_name,kind,title,details,created_at) VALUES(?,?,'bug',?,?,?)", (990088, f'Tester {i}', 'Test report', 'Test details', i))
            self.db.execute('INSERT INTO reward_codes(code,reward_xc,created_by,created_at) VALUES(?,?,?,?)', (f'TEST{i}', 1, 990088, i))
        self.db.commit()
        return form

    def interaction(self, uid=990088):
        response = SimpleNamespace(is_done=Mock(return_value=False), send_message=AsyncMock(),
            edit_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock())
        async def defer():
            response.is_done.return_value = True
        response.defer.side_effect = defer
        return SimpleNamespace(user=SimpleNamespace(id=uid, display_name='Admin Test', mention='<@990088>'),
            response=response, followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
            guild=None, channel=SimpleNamespace(send=AsyncMock()), channel_id=123)

    def test_all_admin_pages_selectors_history_and_pagination(self):
        form = self.seed_admin()
        async def check():
            application = self.db.execute('SELECT id FROM application_submissions LIMIT 1').fetchone()[0]
            code = self.db.execute('SELECT id FROM reward_codes LIMIT 1').fetchone()[0]
            feedback = self.db.execute('SELECT id FROM tester_feedback LIMIT 1').fetchone()[0]
            for page in staff_panel.PAGES:
                async def war_end(interaction):
                    pass
                command = discord.app_commands.Command(name='war_end', description='Test command', callback=war_end)
                panel = staff_panel.AdminPanel(SimpleNamespace(tree=SimpleNamespace(get_command=lambda *a, **k: command)), self.db, lambda i: True, 990088,
                    page=page, selected_form_id=form, selected_application_id=application,
                    selected_code_id=code, selected_feedback_id=feedback, notice='Saved')
                self.assertIsInstance(panel, discord.ui.LayoutView)
                self.assertLessEqual(panel.total_children_count, 40, page)
                panel.to_components()
                actions = [x.action for x in panel.walk_children() if isinstance(x, staff_panel.AdminActionButton)]
                if page == 'home':
                    self.assertNotIn('backup_now', actions)
                    self.assertNotIn('tier6_repair', actions)
                    self.assertEqual(actions.count('page:home'), 1)
                if page == 'maintenance':
                    self.assertIn('backup_now', actions)
                    self.assertIn('tier6_repair', actions)
                for item in panel.walk_children():
                    if isinstance(item, (discord.ui.Select, discord.ui.Button)):
                        self.assertIs(item.view, panel)
                    if isinstance(item, discord.ui.Select):
                        item._values = [item.options[-1].value]
                        i = self.interaction()
                        await item.callback(i)
                        replacement = i.response.edit_message.call_args.kwargs['view']
                        self.assertIsInstance(replacement, discord.ui.LayoutView)
                        self.assertIsNone(i.response.edit_message.call_args.kwargs['embed'])
                i = self.interaction()
                await panel.handle_action(i, 'page:economy')
                target = i.response.edit_message.call_args.kwargs['view']
                i = self.interaction()
                await target.handle_action(i, 'back')
                self.assertEqual(page if page != 'economy' else 'home', i.response.edit_message.call_args.kwargs['view'].page)
            panel = staff_panel.AdminPanel(SimpleNamespace(), self.db, lambda i: True, 990088, page='applications', selected_form_id=form, selected_application_id=application)
            i = self.interaction()
            await panel.handle_action(i, 'list:applications:1')
            target = i.response.edit_message.call_args.kwargs['view']
            self.assertEqual(25, target.offsets['applications'])
            self.assertEqual(application, target.selected_application_id)
            self.assertLessEqual(target.total_children_count, 40)
        asyncio.run(check())

    def test_admin_revoked_permission_and_modal_submission(self):
        self.seed_admin()
        async def check():
            permission = {'allow': True}
            panel = staff_panel.AdminPanel(SimpleNamespace(), self.db, lambda i: permission['allow'], 990088)
            for modal in (staff_panel.RewardCodeModal(panel), staff_panel.FeedbackRewardModal(panel),
                          staff_panel.ApplicationDecisionModal(panel, 'accepted')):
                permission['allow'] = False
                before = self.db.total_changes
                i = self.interaction()
                await modal.on_submit(i)
                self.assertEqual(before, self.db.total_changes)
                i.response.send_message.assert_awaited_once()
            permission['allow'] = True
            i = self.interaction(uid=123)
            before = self.values()
            await panel.handle_action(i, 'toggle_verification')
            self.assertEqual(before, self.values())
            i.response.send_message.assert_awaited_once()
        asyncio.run(check())

    def test_admin_results_and_maintenance_services_are_mocked(self):
        self.seed_admin()
        async def check():
            bot = SimpleNamespace(xbot_tier4_health=Mock(return_value='Healthy'),
                xbot_tier5_repair=Mock(return_value='Repair fixture'), xbot_tier6_repair=Mock(return_value='Repair fixture'),
                xbot_tier4_backup=Mock(return_value=Path('fixture-only.db')))
            panel = staff_panel.AdminPanel(bot, self.db, lambda i: True, 990088)
            for action in ('system_status', 'tier5_repair', 'tier6_repair', 'backup_now', 'post_application', 'post_verification', 'post_tester_feedback'):
                i = self.interaction()
                await panel.handle_action(i, action)
                if action in {'tier5_repair','tier6_repair','backup_now'}:
                    confirmation=i.response.edit_message.call_args.kwargs['view']
                    service={'tier5_repair':bot.xbot_tier5_repair,'tier6_repair':bot.xbot_tier6_repair,'backup_now':bot.xbot_tier4_backup}[action]
                    service.assert_not_called()
                    confirmation.to_components()
                    i=self.interaction()
                    await confirmation.handle_action(i,'confirm_maintenance:'+action)
                    service.assert_called_once()
                    await confirmation.handle_action(self.interaction(),'confirm_maintenance:'+action)
                    service.assert_called_once()
                i.response.defer.assert_awaited_once()
                self.assertIsInstance(i.edit_original_response.call_args.kwargs['view'], discord.ui.LayoutView)
            modal = staff_panel.RewardCodeModal(panel)
            modal.code._value = 'FIXTURECODE'
            for field in (modal.xc, modal.war_credits, modal.xcrystals, modal.max_uses):
                field._value = '1'
            i = self.interaction()
            await modal.on_submit(i)
            self.assertIn('Created', str(i.response.edit_message.call_args.kwargs['view'].to_components()))
            i = self.interaction()
            await modal.on_submit(i)
            self.assertIn('already exists', str(i.response.edit_message.call_args.kwargs['view'].to_components()))
            self.assertFalse(self.db.in_transaction)
        asyncio.run(check())

    def test_admin_categories_and_maintenance_cancel_or_revocation(self):
        self.seed_admin()
        async def check():
            import staff_tools
            allowed={'value':True}
            repair=Mock()
            bot=SimpleNamespace(xbot_tier6_repair=repair)
            root=staff_panel.AdminPanel(bot,self.db,lambda i:allowed['value'],990088)
            found=set()
            for page in staff_panel.TOOL_GROUPS:
                view=root.clone(page=page)
                view.to_components()
                for button in view.walk_children():
                    if isinstance(button,staff_tools.ToolButton):found.add(button.name)
            self.assertEqual(found,set(staff_tools.TOOLS))
            i=self.interaction();await root.handle_action(i,'tier6_repair')
            confirmation=i.response.edit_message.call_args.kwargs['view']
            i=self.interaction();await confirmation.handle_action(i,'cancel_maintenance')
            self.assertIsNone(i.response.edit_message.call_args.kwargs['view'].pending_action)
            repair.assert_not_called()
            allowed['value']=False
            await confirmation.handle_action(self.interaction(),'confirm_maintenance:tier6_repair')
            repair.assert_not_called()
        asyncio.run(check())

    def test_reward_code_filters_preview_and_navigation_are_readonly(self):
        self.seed_admin()
        self.db.execute("UPDATE reward_codes SET enabled=0 WHERE code='TEST0'")
        self.db.execute("UPDATE reward_codes SET max_uses=1,uses=1 WHERE code='TEST1'");self.db.commit()
        async def check():
            root=staff_panel.AdminPanel(SimpleNamespace(),self.db,lambda i:True,990088,page='codes')
            before=self.db.total_changes
            self.assertIsNotNone(root.selected_code())
            self.assertIn('Reward per redemption',str(root.to_components()))
            i=self.interaction();await root.handle_action(i,'filter_codes:disabled')
            view=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual(view.selected_code()['code'],'TEST0')
            self.assertIn('Enable Code',[getattr(c,'label',None) for c in view.walk_children()])
            refreshed=view.clone();self.assertEqual(refreshed.code_filter,'disabled')
            self.assertEqual(refreshed.selected_code_id,view.selected_code_id)
            i=self.interaction();await view.handle_action(i,'page:members')
            group=i.response.edit_message.call_args.kwargs['view']
            i=self.interaction();await group.handle_action(i,'back')
            restored=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual(restored.selected_code_id,view.selected_code_id)
            self.assertEqual(restored.code_filter,'disabled')
            self.assertEqual(self.db.total_changes,before)
            row=self.db.execute("SELECT * FROM reward_codes WHERE code='TEST1'").fetchone()
            self.assertEqual(staff_panel.code_status(row),'Fully redeemed')
            # No-match lists remain usable and can still create a code.
            self.db.execute('DELETE FROM reward_codes');self.db.commit()
            empty=root.clone();empty.to_components()
            self.assertIn('No code selected',str(empty.to_components()))
            self.assertIn('create_code',[getattr(c,'action',None) for c in empty.walk_children()])
        asyncio.run(check())

    def test_complete_report_and_application_readers_keep_owner_and_selection(self):
        self.seed_admin()
        import staff_sections
        application=self.db.execute('SELECT id FROM application_submissions LIMIT 1').fetchone()[0]
        report=self.db.execute('SELECT id FROM tester_feedback LIMIT 1').fetchone()[0]
        full='START '+('Long answer and detail. '*450)+' END'
        self.db.execute('INSERT INTO application_answers VALUES(?,?,?,?)',(application,990099,'Question',full))
        self.db.execute('UPDATE tester_feedback SET details=? WHERE id=?',(full,report));self.db.commit()
        async def check():
            allowed={'value':True}
            for page in ('applications','tester'):
                root=staff_panel.AdminPanel(SimpleNamespace(),self.db,lambda i:allowed['value'],990088,page=page,selected_application_id=application,selected_feedback_id=report)
                button=next(c for c in root.walk_children() if isinstance(c,staff_sections.DetailsButton))
                before=self.db.total_changes
                i=self.interaction();await button.callback(i)
                view=i.response.edit_message.call_args.kwargs['view'];parts=[]
                while True:
                    view.to_components();self.assertLessEqual(view.total_children_count,40)
                    texts=[c.content for c in view.walk_children() if isinstance(c,discord.ui.TextDisplay)]
                    parts.append(texts[1])
                    forward=next((c for c in view.walk_children() if isinstance(c,staff_sections.DetailNav) and c.direction==1),None)
                    if forward is None or forward.disabled:break
                    i=self.interaction();await forward.callback(i);view=i.response.edit_message.call_args.kwargs['view']
                self.assertIn(discord.utils.escape_markdown(full),''.join(parts))
                back=next(c for c in view.walk_children() if isinstance(c,staff_sections.DetailNav) and c.direction==0)
                i=self.interaction();await back.callback(i)
                restored=i.response.edit_message.call_args.kwargs['view']
                self.assertEqual(restored.selected_application_id,application)
                self.assertEqual(restored.selected_feedback_id,report)
                self.assertEqual(self.db.total_changes,before)
                allowed['value']=False;i=self.interaction();await back.callback(i)
                i.response.edit_message.assert_not_awaited();allowed['value']=True
                i=self.interaction(uid=123);await button.callback(i);i.response.edit_message.assert_not_awaited()
        asyncio.run(check())

    def test_admin_review_toggle_close_and_readonly_economy(self):
        self.seed_admin()
        self.db.execute('INSERT OR REPLACE INTO players(user_id,nation_name,xc,money) VALUES(?,?,?,?)', (990088, 'Fixture', 100, 100))
        self.db.commit()
        async def check():
            bot = SimpleNamespace(get_channel=Mock(return_value=None))
            panel = staff_panel.AdminPanel(bot, self.db, lambda i: True, 990088)
            before = self.db.total_changes
            economy_panel = panel.clone(page='economy')
            await economy_panel.handle_action(self.interaction(), 'refresh')
            self.assertEqual(before, self.db.total_changes)
            for action, key in [('toggle_applications', 'applications_enabled'), ('toggle_verification', 'verification_enabled')]:
                before = self.values()[key]
                await panel.handle_action(self.interaction(), action)
                self.assertNotEqual(before, self.values()[key])
            application = self.db.execute('SELECT id FROM application_submissions LIMIT 1').fetchone()[0]
            for decision in ('accepted', 'hold', 'denied'):
                self.db.execute("UPDATE application_submissions SET status='pending' WHERE id=?", (application,))
                self.db.commit()
                modal = staff_panel.ApplicationDecisionModal(panel.clone(page='applications', selected_application_id=application), decision)
                modal.reason._value = 'Fixture review'
                i = self.interaction()
                await modal.on_submit(i)
                self.assertEqual(decision, self.db.execute('SELECT status FROM application_submissions WHERE id=?', (application,)).fetchone()[0])
                self.assertIsInstance(i.edit_original_response.call_args.kwargs['view'], discord.ui.LayoutView)
            reports = self.db.execute('SELECT id FROM tester_feedback ORDER BY id LIMIT 2').fetchall()
            modal = staff_panel.FeedbackRewardModal(panel.clone(page='tester', selected_feedback_id=reports[0][0]))
            modal.xc._value, modal.war_credits._value, modal.note._value = '3', '2', 'Fixture'
            await modal.on_submit(self.interaction())
            self.assertEqual((103, 102), tuple(self.db.execute('SELECT xc,money FROM players WHERE user_id=990088').fetchone()))
            await panel.clone(page='tester', selected_feedback_id=reports[1][0]).handle_action(self.interaction(), 'reject_feedback')
            self.assertEqual('rejected', self.db.execute('SELECT status FROM tester_feedback WHERE id=?', (reports[1][0],)).fetchone()[0])
            code = self.db.execute('SELECT id FROM reward_codes LIMIT 1').fetchone()[0]
            await panel.clone(page='codes', selected_code_id=code).handle_action(self.interaction(), 'toggle_code')
            self.assertEqual(0, self.db.execute('SELECT enabled FROM reward_codes WHERE id=?', (code,)).fetchone()[0])
            i = self.interaction()
            await panel.handle_action(i, 'close')
            self.assertIn('closed', str(i.response.edit_message.call_args.kwargs['view'].to_components()))
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
