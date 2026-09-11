"""Connected Economy regression tests. Disposable databases only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import discord
import economy
import advanced_systems
import economy_journey as j
import tier6


class JourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'test.db'
        self.db=sqlite3.connect(self.path);self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        with closing(sqlite3.connect(f"file:{Path(__file__).with_name('xwar.db').as_posix()}?mode=ro",uri=True)) as source:source.backup(self.db)
        economy.initialise(self.db);advanced_systems.initialise(self.db);tier6.initialise(self.db)
        self.uid=990000000000004321
        self.db.execute('INSERT INTO players(user_id,nation_name,display_name,xc,capital_health) VALUES(?,?,?,?,?)',(self.uid,'Test Nation','Test',1000,50))
        self.rid=self.db.execute("SELECT id FROM recipes WHERE name='Resource Pack'").fetchone()[0]
        self.iid=self.db.execute('SELECT output_item_id FROM recipes WHERE id=?',(self.rid,)).fetchone()[0]
        self.db.commit()

    def fund(self,batches=1):
        for row in self.db.execute('SELECT * FROM recipe_ingredients WHERE recipe_id=?',(self.rid,)).fetchall():
            self.db.execute('INSERT INTO inventories VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=excluded.quantity',(self.uid,row['item_id'],row['quantity']*batches))
        self.db.commit()

    def cash(self):return self.db.execute('SELECT xc FROM players WHERE user_id=?',(self.uid,)).fetchone()[0]

    def test_seed_and_admin_customizations_survive(self):
        q=j.quote(self.db,self.uid,self.rid)
        self.assertTrue(q['recommended']);self.assertGreaterEqual(q['margin'],.15);self.assertLessEqual(q['margin'],.25)
        item=j.owned(self.db,self.uid,self.iid)
        self.assertEqual((item['shop_visible'],item['tradeable'],item['effect']),(0,0,'none'))
        self.db.execute('UPDATE items SET sell_price=77 WHERE id=?',(self.iid,))
        self.db.execute('UPDATE recipes SET xc_cost=9,enabled=0 WHERE id=?',(self.rid,))
        self.db.execute('DELETE FROM recipe_ingredients WHERE recipe_id=?',(self.rid,));self.db.commit()
        advanced_systems.initialise(self.db)
        self.assertEqual(j.owned(self.db,self.uid,self.iid)['sell_price'],77)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM recipe_ingredients WHERE recipe_id=?',(self.rid,)).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT enabled FROM recipes WHERE id=?',(self.rid,)).fetchone()[0],0)

    def test_complete_loop_receipts_and_mission_events(self):
        self.assertIn('1 / 3',j.goal(self.db,self.uid)[0]);self.fund(2)
        self.assertIn('2 / 3',j.goal(self.db,self.uid)[0]);q=j.quote(self.db,self.uid,self.rid)
        j.craft(self.db,self.uid,self.rid,token='craft-once');self.assertIn('3 / 3',j.goal(self.db,self.uid)[0])
        with self.assertRaises(ValueError):j.craft(self.db,self.uid,self.rid,token='craft-once')
        j.sell(self.db,self.uid,self.iid,token='sale-once')
        with self.assertRaises(ValueError):j.sell(self.db,self.uid,self.iid,token='sale-once')
        self.assertEqual(self.cash(),1000-q['cost']+q['proceeds'])
        self.assertIn('Loop complete',j.goal(self.db,self.uid)[0])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM economy_logs WHERE user_id=? AND action IN ('craft','sell_item')",(self.uid,)).fetchone()[0],2)

    def test_missing_balance_and_disabled_are_noops(self):
        with self.assertRaises(ValueError):j.craft(self.db,self.uid,self.rid)
        self.assertEqual(self.cash(),1000);self.fund()
        self.db.execute('UPDATE players SET xc=0 WHERE user_id=?',(self.uid,));self.db.commit()
        with self.assertRaises(ValueError):j.craft(self.db,self.uid,self.rid)
        self.db.execute('UPDATE players SET xc=1000 WHERE user_id=?',(self.uid,))
        self.db.execute("UPDATE economy_settings SET value='0' WHERE key='recipes_enabled'");self.db.commit()
        with self.assertRaises(ValueError):j.craft(self.db,self.uid,self.rid)
        self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],0)

    def test_reprice_and_unsellable_not_recommended(self):
        self.fund();q=j.quote(self.db,self.uid,self.rid)
        self.db.execute('UPDATE items SET sell_price=1 WHERE id=?',(self.iid,));self.db.commit()
        with self.assertRaises(ValueError):j.craft(self.db,self.uid,self.rid,expected=q['fingerprint'])
        self.assertFalse(j.quote(self.db,self.uid,self.rid)['recommended'])
        self.assertIn('No profitable',j.goal(self.db,self.uid)[0])
        self.db.execute('UPDATE items SET sellable=0 WHERE id=?',(self.iid,));self.db.commit()
        j.craft(self.db,self.uid,self.rid)
        with self.assertRaises(ValueError):j.sell(self.db,self.uid,self.iid)

    def test_receipt_failure_rolls_back_assets(self):
        self.fund()
        self.db.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON economy_logs WHEN NEW.action='journey_receipt' BEGIN SELECT RAISE(ABORT,'fail'); END;")
        with self.assertRaises(sqlite3.Error):j.craft(self.db,self.uid,self.rid,token='fail')
        self.assertEqual(self.cash(),1000);self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],0)

    def test_independent_connection_replay_and_busy(self):
        self.fund(2)
        with closing(sqlite3.connect(self.path,timeout=.01)) as other:
            other.row_factory=sqlite3.Row
            j.craft(self.db,self.uid,self.rid,token='global-token')
            with self.assertRaises(ValueError):j.craft(other,self.uid,self.rid,token='global-token')
            self.db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError):j.sell(other,self.uid,self.iid)
            self.db.rollback()
        self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],1)

    def test_queue_full_and_single_collection(self):
        self.fund(3)
        self.db.execute("UPDATE economy_settings SET value='1' WHERE key='tier6_production_queue_limit'");self.db.commit()
        self.assertTrue(tier6.start_production(self.db,self.uid,self.rid,2,token='queue')[0])
        with self.assertRaises(ValueError):tier6.start_production(self.db,self.uid,self.rid,2,token='queue')
        self.assertFalse(tier6.start_production(self.db,self.uid,self.rid,1)[0])
        self.db.execute('UPDATE tier6_production_queue SET ready_at=? WHERE user_id=?',(int(time.time())-1,self.uid));self.db.commit()
        self.assertIn('Collected',tier6.claim_production(self.db,self.uid))
        self.assertIn('No finished',tier6.claim_production(self.db,self.uid))
        self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],2)
        self.assertIn('3 / 3',j.goal(self.db,self.uid)[0])

    def test_concurrent_craft_and_collection(self):
        self.fund(4)
        def craft_once(_):
            with closing(sqlite3.connect(self.path,timeout=5)) as db:
                db.row_factory=sqlite3.Row
                try:
                    j.craft(db,self.uid,self.rid,token='concurrent-craft')
                    return True
                except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(craft_once,range(2))),1)
        self.assertTrue(tier6.start_production(self.db,self.uid,self.rid,2)[0])
        self.db.execute('UPDATE tier6_production_queue SET ready_at=0 WHERE user_id=?',(self.uid,));self.db.commit()
        def claim(_):
            with closing(sqlite3.connect(self.path,timeout=5)) as db:
                db.row_factory=sqlite3.Row
                return tier6.claim_production(db,self.uid)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(claim,range(2)))
        self.assertEqual(sum('Collected' in result for result in results),1)
        self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],3)

    def test_old_inventory_sale_does_not_complete_future_craft(self):
        self.db.execute('INSERT INTO inventories VALUES(?,?,1)',(self.uid,self.iid));self.db.commit()
        j.sell(self.db,self.uid,self.iid,action='backpack_sell')
        self.fund();j.craft(self.db,self.uid,self.rid)
        self.assertIn('3 / 3',j.goal(self.db,self.uid)[0])
        j.sell(self.db,self.uid,self.iid,action='backpack_sell')
        self.assertIn('Loop complete',j.goal(self.db,self.uid)[0])

    def test_optional_war_effects_no_auto_consumption(self):
        for name in ('War Supply Crate','Capital Repair Kit'):
            item=self.db.execute('SELECT * FROM items WHERE name=?',(name,)).fetchone()
            self.db.execute('INSERT INTO inventories VALUES(?,?,2)',(self.uid,item['id']));self.db.commit()
            self.assertIsNone(j.use_reason(self.db,self.uid,item['id']))
            self.assertEqual(j.owned(self.db,self.uid,item['id'])['quantity'],2)
            j.use(self.db,self.uid,item['id'],token=name)
            with self.assertRaises(ValueError):j.use(self.db,self.uid,item['id'],token=name)
            self.assertEqual(j.owned(self.db,self.uid,item['id'])['quantity'],1)
        self.db.execute('UPDATE players SET capital_health=100 WHERE user_id=?',(self.uid,));self.db.commit()
        with self.assertRaises(ValueError):j.use(self.db,self.uid,item['id'])
        self.db.execute("UPDATE players SET nation_name='' WHERE user_id=?",(self.uid,));self.db.commit()
        self.assertIn('Found',j.use_reason(self.db,self.uid,item['id']))

    def test_views_callbacks_and_back(self):
        async def run():
            self.fund(3)
            bot=SimpleNamespace(xbot_player_panel_builders={'economy':lambda uid:discord.ui.LayoutView()},xbot_mine_button_builder=lambda:discord.ui.Button(label='Mine'))
            def interaction(uid=None):return SimpleNamespace(user=SimpleNamespace(id=uid or self.uid),response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock(),defer=AsyncMock(),send_modal=AsyncMock()),edit_original_response=AsyncMock())
            for page in ('activity','batch','recipes','detail','product','products','materials','areas','confirm','result'):
                v=j.JourneyView(bot,self.db,self.uid,page=page,rid=self.rid,iid=self.iid,operation='craft')
                v.to_components();self.assertLessEqual(v.total_children_count,40)
            v=j.JourneyView(bot,self.db,self.uid,rid=self.rid)
            i=interaction();await v.act(i,('recipe',self.rid));detail=i.response.edit_message.call_args.kwargs['view']
            from economy_trade_ui import TradeNav,TradeButton
            i=interaction();await next(x for x in detail.walk_children() if isinstance(x,TradeNav) and x.key=='back').callback(i)
            self.assertEqual(i.response.edit_message.call_args.kwargs['view'].page,v.page)
            confirm=detail
            i=interaction();await confirm.act(i,('execute',));self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],1)
            await confirm.act(interaction(),('execute',));self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],1)
            button=next(x for x in detail.walk_children() if isinstance(x,TradeButton));await button.callback(interaction(self.uid+1))
            self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],1)
        asyncio.run(run())

    def test_goal_persists_and_only_changes_preference(self):
        before=self.cash()
        self.assertEqual(j.selected_goal(self.db,self.uid),'craft')
        for mode in j.GOALS:
            j.set_goal(self.db,self.uid,mode)
            with closing(sqlite3.connect(self.path)) as other:
                other.row_factory=sqlite3.Row
                self.assertEqual(j.selected_goal(other,self.uid),mode)
            self.assertTrue(j.next_step(self.db,self.uid)[0])
        self.assertEqual(self.cash(),before)
        with self.assertRaises(ValueError):j.set_goal(self.db,self.uid,'invalid')

    def test_live_capacity_queue_and_prices(self):
        self.fund(20)
        q=j.quote(self.db,self.uid,self.rid)
        self.assertEqual(j.capacity(q),20)
        self.db.execute('UPDATE players SET xc=? WHERE user_id=?',(q['cost']*3,self.uid));self.db.commit()
        self.assertEqual(j.capacity(j.quote(self.db,self.uid,self.rid)),3)
        self.assertTrue(tier6.start_production(self.db,self.uid,self.rid,2)[0])
        self.assertEqual(j.activity(self.db,self.uid)['active'],1)
        self.db.execute('UPDATE tier6_production_queue SET ready_at=0 WHERE user_id=?',(self.uid,));self.db.commit()
        self.assertEqual(j.activity(self.db,self.uid)['ready'],1)
        self.assertEqual(j.next_step(self.db,self.uid)[1]['page'],'activity')
        tier6.claim_production(self.db,self.uid)
        self.assertEqual(j.activity(self.db,self.uid)['products'],2)
        j.set_goal(self.db,self.uid,'earn')
        self.assertEqual(j.next_step(self.db,self.uid)[1]['page'],'products')

    def test_goal_selector_max_review_and_stale_balance(self):
        async def run():
            self.fund(6)
            bot=SimpleNamespace(xbot_player_panel_builders={})
            def interaction(uid=None):return SimpleNamespace(user=SimpleNamespace(id=uid or self.uid),response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock(),defer=AsyncMock()),edit_original_response=AsyncMock())
            v=j.JourneyView(bot,self.db,self.uid,page='activity')
            selector=next(c for c in v.walk_children() if isinstance(c,j.GoalSelect))
            selector._values=['war']
            await selector.callback(interaction(self.uid+1));self.assertEqual(j.selected_goal(self.db,self.uid),'craft')
            await selector.callback(interaction());self.assertEqual(j.selected_goal(self.db,self.uid),'war')
            v=j.JourneyView(bot,self.db,self.uid,page='detail',rid=self.rid,iid=self.iid)
            i=interaction();await v.act(i,('batch',));batch=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual(batch.kind,'queue')
            i=interaction();await batch.act(i,('quantity',6));confirm=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual(confirm.quantity,6);self.assertEqual(j.activity(self.db,self.uid)['active'],0)
            self.assertIn('After fee',str(confirm.to_components()))
            self.db.execute('UPDATE players SET xc=0 WHERE user_id=?',(self.uid,));self.db.commit()
            await confirm.act(interaction(),('execute',));self.assertEqual(j.activity(self.db,self.uid)['active'],0)
            self.db.execute('UPDATE players SET xc=1000 WHERE user_id=?',(self.uid,));self.db.commit()
            j.craft(self.db,self.uid,self.rid,2)
            v=j.JourneyView(bot,self.db,self.uid,page='product',rid=self.rid,iid=self.iid)
            i=interaction();await v.act(i,('max','sell'));confirm=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual(confirm.quantity,2);self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],2)
            await confirm.act(interaction(),('execute',));await confirm.act(interaction(),('execute',))
            self.assertEqual(j.owned(self.db,self.uid,self.iid)['quantity'],0)
        asyncio.run(run())


if __name__=='__main__':unittest.main()
