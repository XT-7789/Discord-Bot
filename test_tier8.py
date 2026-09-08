"""Research transaction, integration and interaction regression tests on temporary DBs."""
import asyncio
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import discord
import tier8
import tier7
import tier6


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'test.db'
        self.db=sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.row_factory=sqlite3.Row
        # Snapshot the schema/fixtures without writing to the real database.
        with closing(sqlite3.connect(f"file:{Path(__file__).with_name('xwar.db').as_posix()}?mode=ro",uri=True)) as source:
            source.backup(self.db)
        tier8.initialise(self.db)
        self.uid=990000000000000088
        self.db.execute('INSERT OR REPLACE INTO players(user_id,nation_name,display_name,xc,money) VALUES(?,?,?,?,?)',
                        (self.uid,'Research Test','Research Test',10000,10000))
        self.db.commit()

    def balance(self):
        return self.db.execute('SELECT xc FROM players WHERE user_id=?',(self.uid,)).fetchone()[0]

    def test_serial_queue_ready_benefits_and_retry(self):
        q=tier8.quote(self.db,self.uid,['mining','production'],1000)
        self.assertEqual(q[0]['ready_at'],q[1]['starts_at'])
        before=self.balance()
        tier8.start(self.db,self.uid,q,'order',1000)
        self.assertEqual(before-100,self.balance())
        tier8.start(self.db,self.uid,q,'order',1000)
        self.assertEqual(before-100,self.balance())
        self.assertEqual(0,tier8.bonus(self.db,self.uid,'mining',1299))
        self.assertEqual(5,tier8.bonus(self.db,self.uid,'mining',1300))
        self.assertEqual(0,tier8.bonus(self.db,self.uid,'production',1300))
        self.assertEqual(5,tier8.bonus(self.db,self.uid,'production',1600))
        with closing(sqlite3.connect(self.path)) as other:
            other.row_factory=sqlite3.Row
            self.assertEqual(5,tier8.bonus(other,self.uid,'mining',1600))

    def test_dashboard_settings_validation_and_auth(self):
        import importlib
        connect=sqlite3.connect
        def temporary_connect(path,*args,**kwargs):
            if str(path).endswith('xwar.db'):
                path=self.path
            return connect(path,*args,**kwargs)
        with patch('sqlite3.connect',side_effect=temporary_connect):
            dashboard=importlib.import_module('dashboard')
            with patch.object(dashboard,'DATABASE_PATH',self.path):
                client=dashboard.app.test_client()
                self.assertEqual(302,client.get('/research').status_code)
                with client.session_transaction() as s:
                    s['dashboard_role']='owner'
                    s['discord_name']='Codex Test'
                response=client.get('/research')
                self.assertEqual(200,response.status_code)
                self.assertIn(b'Mining Methods',response.data)
                self.assertEqual(403,client.post('/research',data={'action':'settings','enabled':'0','queue_limit':'2'}).status_code)
                with client.session_transaction() as s:
                    csrf=s['research_csrf']
                response=client.post('/research',data={'csrf':csrf,'action':'settings','enabled':'1','queue_limit':'3'})
                self.assertEqual(302,response.status_code)
                self.assertEqual('3',self.db.execute("SELECT value FROM economy_settings WHERE key='tier8_queue_limit'").fetchone()[0])
                with client.session_transaction() as s:
                    s['dashboard_role']='viewer'
                self.assertEqual(403,client.get('/research').status_code)

    def test_cancel_refunds_saved_cost_once_and_preserves_finished(self):
        q=tier8.quote(self.db,self.uid,['mining','production'],1000)
        tier8.start(self.db,self.uid,q,'cancel-order',1000)
        self.db.execute("UPDATE tier8_technologies SET base_cost=999 WHERE code='production'")
        self.db.commit()
        tier8.cancel(self.db,self.uid,1400)
        self.assertEqual(9950,self.balance())
        tier8.cancel(self.db,self.uid,1400)
        self.assertEqual(9950,self.balance())
        self.assertEqual(5,tier8.bonus(self.db,self.uid,'mining',1400))
        self.assertEqual(0,tier8.bonus(self.db,self.uid,'production',1800))

    def test_two_connections_cannot_spend_same_research_order_twice(self):
        q=tier8.quote(self.db,self.uid,['mining'],1000)
        def submit():
            with closing(sqlite3.connect(self.path,timeout=5)) as db:
                db.row_factory=sqlite3.Row
                return tier8.start(db,self.uid,q,'same-click',1000)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _:submit(),range(2)))
        self.assertEqual(9950,self.balance())
        self.assertEqual(1,self.db.execute('SELECT COUNT(*) FROM tier8_research_jobs WHERE user_id=?',(self.uid,)).fetchone()[0])
        self.assertTrue(any('already' in r for r in results))

    def test_full_bot_registration_and_panels_without_discord_login(self):
        import importlib
        connect=sqlite3.connect
        def temporary_connect(path,*args,**kwargs):
            if str(path).endswith('xwar.db'):
                path=self.path
            return connect(path,*args,**kwargs)
        with patch('sqlite3.connect',side_effect=temporary_connect), patch('discord.Client.login',new_callable=AsyncMock) as login:
            module=importlib.import_module('bot')
            try:
                async def inspect():
                    bot=module.bot
                    panels=[bot.xbot_player_lobby_builder(self.uid),bot.xbot_tier8_research_builder(self.uid)]
                    for panel in panels:
                        self.assertLessEqual(panel.total_children_count,40)
                        self.assertTrue(panel.to_components())
                    all_button=next(x for x in panels[0].walk_children() if getattr(x,'label',None)=='All Activities')
                    i=SimpleNamespace(user=SimpleNamespace(id=self.uid),response=SimpleNamespace(defer=AsyncMock()),edit_original_response=AsyncMock())
                    await all_button.callback(i)
                    all_view=i.edit_original_response.call_args.kwargs['view']
                    self.assertIn('Backpack',str(all_view.to_components()))
                    guild=discord.Object(id=module._staff_guild_id) if module._staff_guild_id else None
                    for name in ('lobby','research'):
                        command=bot.tree.get_command(name,guild=guild)
                        self.assertIsNotNone(command)
                        self.assertEqual('tier8',command.callback.__module__)
                    for child in all_view.walk_children():
                        destination=getattr(child,'destination','')
                        if destination and ':' not in destination and destination!='lobby':
                            self.assertIn(destination,bot.xbot_player_panel_builders)
                    self.assertEqual('50',module.db.execute("SELECT value FROM economy_settings WHERE key='daily_reward'").fetchone()[0])
                    # Daily stays in the Lobby, and retrying the old button cannot pay twice.
                    module.db.execute('UPDATE players SET last_daily=0 WHERE user_id=?',(self.uid,))
                    module.db.commit()
                    daily=bot.xbot_daily_button_builder(self.uid)
                    before=self.balance()
                    await daily.callback(i)
                    self.assertEqual(before+50,self.balance())
                    daily_view=i.edit_original_response.call_args.kwargs['view']
                    self.assertIn('Your next step',str(daily_view.to_components()))
                    self.assertIn('Next daily reward',str(daily_view.to_components()))
                    await daily.callback(i)
                    self.assertEqual(before+50,self.balance())
                    # Claim existing mission rewards directly, staying on the same message.
                    claim=next(x for x in daily_view.walk_children() if getattr(x,'label',None)=='Claim Ready Rewards')
                    await claim.callback(i)
                    after=self.balance()
                    self.assertGreater(after,before+50)
                    await claim.callback(i)
                    self.assertEqual(after,self.balance())
                    self.assertIn('Your next step',str(i.edit_original_response.call_args.kwargs['view'].to_components()))
                    # The goal button performs a real mine rather than opening another menu.
                    tool=module.db.execute("SELECT id FROM items WHERE effect='mine_tool' AND enabled=1 ORDER BY pickaxe_required_level LIMIT 1").fetchone()[0]
                    module.db.execute('INSERT OR REPLACE INTO inventories(user_id,item_id,quantity) VALUES(?,?,1)',(self.uid,tool))
                    module.db.execute('UPDATE players SET equipped_pickaxe_id=?,mining_level=99,mining_energy=100,last_mine_at=0 WHERE user_id=?',(tool,self.uid))
                    module.db.commit()
                    import tier5
                    tier5.claim_ready(module.db,self.uid,'daily')
                    home=bot.xbot_player_lobby_builder(self.uid)
                    mine_button=next(x for x in home.walk_children() if getattr(x,'label',None)=='Mine Now · Advance Goal')
                    self.assertIn('XP to Level',str(home.to_components()))
                    i.message=SimpleNamespace(id=1)
                    i.response.edit_message=AsyncMock()
                    i.response.send_message=AsyncMock()
                    await mine_button.callback(i)
                    result=i.response.edit_message.call_args.kwargs['view']
                    self.assertIn('Lobby · Claim Rewards',str(result.to_components()))
                    mined=module.db.execute('SELECT total_mines FROM players WHERE user_id=?',(self.uid,)).fetchone()[0]
                    self.assertEqual(1,mined)
                    await mine_button.callback(i)
                    self.assertIn('Mining is recovering',str(i.response.edit_message.call_args.kwargs['view'].to_components()))
                    self.assertEqual(mined,module.db.execute('SELECT total_mines FROM players WHERE user_id=?',(self.uid,)).fetchone()[0])
                    i.response.send_message.assert_not_called()
                    await bot.close()
                asyncio.run(inspect())
                login.assert_not_called()
            finally:
                module.db.close()

    def test_easy_goals_do_not_require_war_or_recruitment(self):
        import tier5
        def mission(title,destination,progress=0):
            return dict(title=title,destination=destination,progress=progress,target=1,claimed=False,
                        description='Do this action',xc=5,credits=10,xp=20)
        pages={'starter':[mission('Recruit','recruit')],
               'daily':[mission('Mine','mining')], 'weekly':[mission('Battle','war')]}
        with patch.object(tier5,'missions_for',side_effect=lambda db,uid,cat:('test',pages[cat])):
            self.assertEqual('mining',tier8.easy_objective(self.db,self.uid)['destination'])
            pages['weekly']=[mission('Battle','war',1)]
            self.assertTrue(tier8.easy_objective(self.db,self.uid)['claim'])
            pages['weekly']=[]
            pages['daily']=[]
            self.assertEqual('city',tier8.easy_objective(self.db,self.uid)['destination'])

    def test_mining_goal_remaining_and_completion(self):
        import tier5
        m=dict(title='Mining Shift',destination='mining',progress=1,target=3,claimed=False,description='Mine three times',xc=20,credits=40,xp=25)
        with patch.object(tier5,'missions_for',return_value=('today',[m])):
            self.assertIn('2 more time(s)',tier8.easy_objective(self.db,self.uid)['detail'])
            text,ready=tier8.mining_goal(self.db,self.uid)
            self.assertFalse(ready)
            self.assertIn('1/3',text)
            m['progress']=3
            text,ready=tier8.mining_goal(self.db,self.uid)
            self.assertTrue(ready)
            self.assertIn('complete',text)
            m['claimed']=True
            self.assertIn('optional',tier8.mining_goal(self.db,self.uid)[0])

    def test_stale_quote_insufficient_funds_and_duplicate_project(self):
        q=tier8.quote(self.db,self.uid,['mining'],1000)
        self.db.execute("UPDATE tier8_technologies SET base_cost=55 WHERE code='mining'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'changed'):
            tier8.start(self.db,self.uid,q,'stale',1000)
        self.assertEqual(10000,self.balance())
        q=tier8.quote(self.db,self.uid,['mining'],1000)
        self.db.execute('UPDATE players SET xc=0 WHERE user_id=?',(self.uid,))
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'need'):
            tier8.start(self.db,self.uid,q,'poor',1000)
        self.assertEqual(0,self.db.execute('SELECT COUNT(*) FROM tier8_research_jobs WHERE user_id=?',(self.uid,)).fetchone()[0])
        self.db.execute('UPDATE players SET xc=10000 WHERE user_id=?',(self.uid,))
        self.db.commit()
        tier8.start(self.db,self.uid,q,'first',1000)
        with self.assertRaisesRegex(ValueError,'already'):
            tier8.start(self.db,self.uid,q,'second',1000)
        self.assertEqual(9945,self.balance())

    def test_real_discounts_and_caps(self):
        for code,*_ in tier8.TECHS:
            self.db.execute('INSERT INTO tier8_levels VALUES(?,?,?)',(self.uid,code,999))
        self.db.commit()
        self.assertEqual(85,tier8.discounted(self.db,self.uid,'construction',100))
        self.assertEqual(22,tier7.mode_config(self.db,'standard',self.uid)['supply_cost'])
        self.assertEqual(10,tier8.bonus(self.db,self.uid,'defence'))
        self.db.execute("UPDATE tier8_technologies SET bonus_cap=999,bonus_per_level=999,max_level=999")
        self.db.commit()
        self.assertEqual(30,tier8.bonus(self.db,self.uid,'mining'))
        self.db.execute("UPDATE economy_settings SET value='0' WHERE key='tier8_enabled'")
        self.db.commit()
        self.assertEqual(0,tier8.bonus(self.db,self.uid,'mining'))
        with self.assertRaisesRegex(ValueError,'closed'):
            tier8.quote(self.db,self.uid,['trade'])

    def test_research_multiselect_confirmation_and_easy_navigation(self):
        class Tree:
            def __init__(self): self.commands={}
            def remove_command(self,name,**kw): self.commands.pop(name,None)
            def command(self,name,**kw):
                def deco(fn): self.commands[name]=fn; return fn
                return deco
        async def check():
            bot=SimpleNamespace(tree=Tree(),xbot_player_panel_builders={},xbot_player_lobby_builder=lambda uid:discord.ui.LayoutView())
            tier8.register_commands(bot,self.db,lambda u:None)
            view=bot.xbot_tier8_research_builder(self.uid)
            select=next(x for x in view.walk_children() if isinstance(x,discord.ui.Select))
            select._values=['mining','trade']
            interaction=SimpleNamespace(user=SimpleNamespace(id=self.uid),response=SimpleNamespace(defer=AsyncMock()),edit_original_response=AsyncMock())
            await select.callback(interaction)
            confirm=interaction.edit_original_response.call_args.kwargs['view']
            self.assertIn('100 XC',str(confirm.to_components()))
            button=next(x for x in confirm.walk_children() if getattr(x,'label',None)=='Confirm & Start')
            await button.callback(interaction)
            await button.callback(interaction)
            self.assertEqual(9900,self.balance())
            queue=interaction.edit_original_response.call_args.kwargs['view']
            self.assertIn('Mining Methods',str(queue.to_components()))
            home=bot.xbot_player_lobby_builder(self.uid)
            self.assertIn('Your next step',str(home.to_components()))
            self.assertLessEqual(home.total_children_count,40)
            self.assertEqual({'lobby','research'},set(bot.tree.commands))
        asyncio.run(check())


if __name__=='__main__':
    unittest.main()
