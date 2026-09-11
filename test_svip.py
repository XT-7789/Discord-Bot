"""SVIP role enforcement, capacity and timing. Disposable databases only."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_economy_transactions as fixtures
import svip
import tier6
import economy_transactions as trades
from economy_trade_ui import TradeView,TradeButton

class SVIPTests(unittest.TestCase):
    def setUp(self):
        fixtures.TransactionTests.setUp(self)
        self.db.execute("INSERT OR REPLACE INTO economy_settings VALUES('server_svip_role_id','12345')")
        self.db.execute("UPDATE economy_settings SET value='5' WHERE key='tier6_production_queue_limit'")
        self.db.execute("UPDATE economy_settings SET value='20' WHERE key='tier6_market_max_listings'");self.db.commit()
        self.member=SimpleNamespace(id=self.uid,guild=SimpleNamespace(id=9),roles=[SimpleNamespace(id=12345)])
    fund=fixtures.TransactionTests.fund

    def test_role_not_paid_vip_or_wrong_owner_and_context_reset(self):
        self.assertFalse(svip.benefits(self.db,self.uid)['active'])
        with svip.context(self.member):
            self.assertEqual((7,25),(svip.production_limit(self.db,self.uid),svip.market_limit(self.db,self.uid)))
            self.assertEqual(5,svip.production_limit(self.db,self.seller))
        self.assertEqual(5,svip.production_limit(self.db,self.uid))
        for member in (None,SimpleNamespace(id=self.uid,roles=self.member.roles),SimpleNamespace(id=self.uid,guild=self.member.guild,roles=[])):
            with svip.context(member):self.assertFalse(svip.benefits(self.db,self.uid)['active'])

    def test_extra_jobs_speed_floor_and_role_loss_preserve_saved_orders(self):
        self.fund(10)
        with svip.context(self.member):
            for _ in range(7):self.assertTrue(tier6.start_production(self.db,self.uid,self.rid,1)[0])
            self.assertFalse(tier6.start_production(self.db,self.uid,self.rid,1)[0])
        jobs=[tuple(r) for r in self.db.execute('SELECT id,ready_at,started_at FROM tier6_production_queue WHERE user_id=?',(self.uid,))]
        self.assertTrue(all(r[1]-r[2]==270 for r in jobs))
        self.assertFalse(tier6.start_production(self.db,self.uid,self.rid,1)[0])
        self.assertEqual(jobs,[tuple(r) for r in self.db.execute('SELECT id,ready_at,started_at FROM tier6_production_queue WHERE user_id=?',(self.uid,))])
        self.db.execute("UPDATE tier6_production_queue SET status='cancelled' WHERE user_id=?",(self.uid,))
        self.db.execute("UPDATE economy_settings SET value='30' WHERE key='tier6_production_seconds_per_item'");self.db.commit()
        with svip.context(self.member):
            with patch('tier8.discounted',return_value=15):self.assertTrue(tier6.start_production(self.db,self.uid,self.rid,1)[0])
        self.assertEqual(30,self.db.execute('SELECT ready_at-started_at FROM tier6_production_queue WHERE user_id=? ORDER BY id DESC LIMIT 1',(self.uid,)).fetchone()[0])

    def test_listing_capacity_role_loss_and_config_change_requote(self):
        with svip.context(self.member):
            for _ in range(25):trades.settle(self.db,self.uid,'list',self.stone,1,10)
            with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'list',self.stone,1,10)
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'list',self.stone,1,10)
        self.assertEqual(25,self.db.execute('SELECT COUNT(*) FROM market_listings WHERE seller_id=? AND active=1',(self.uid,)).fetchone()[0])
        self.fund(2)
        with svip.context(self.member):q=trades.quote(self.db,self.uid,'queue',self.rid)
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'queue',self.rid,expected=q['fingerprint'])
        with svip.context(self.member):
            self.db.execute("UPDATE economy_settings SET value='12' WHERE key='server_svip_production_percent'");self.db.commit()
            with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'queue',self.rid,expected=q['fingerprint'])

    def test_click_checks_fresh_member_not_preview_member(self):
        async def run():
            self.fund(3)
            with svip.context(self.member):v=TradeView(SimpleNamespace(),self.db,self.uid,'queue',self.rid)
            click=fixtures.interaction(self.uid);click.user=SimpleNamespace(id=self.uid,guild=self.member.guild,roles=[])
            button=next(x for x in v.walk_children() if isinstance(x,TradeButton) and x.action==('execute',))
            await button.callback(click)
            self.assertEqual(0,self.db.execute('SELECT COUNT(*) FROM tier6_production_queue WHERE user_id=?',(self.uid,)).fetchone()[0])
            preview=click.edit_original_response.call_args.kwargs['view']
            click.user=self.member
            button=next(x for x in preview.walk_children() if isinstance(x,TradeButton) and x.action==('execute',))
            await button.callback(click)  # regaining the role also changes the quote
            preview=click.edit_original_response.call_args.kwargs['view']
            await next(x for x in preview.walk_children() if isinstance(x,TradeButton) and x.action==('execute',)).callback(click)
            self.assertEqual(1,self.db.execute('SELECT COUNT(*) FROM tier6_production_queue WHERE user_id=?',(self.uid,)).fetchone()[0])
            self.assertFalse(svip.benefits(self.db,self.uid)['active'])
        asyncio.run(run())

    def test_defaults_do_not_overwrite_admin_values(self):
        self.db.execute("UPDATE economy_settings SET value='3' WHERE key='server_svip_production_slots'");self.db.commit()
        tier6.initialise(self.db)
        with svip.context(self.member):self.assertEqual(8,svip.production_limit(self.db,self.uid))

    def test_production_discount_applies_once_after_research(self):
        self.fund(2)
        with svip.context(self.member),patch('tier8.discounted',return_value=240):
            trades.settle(self.db,self.uid,'queue',self.rid,token='once')
            with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'queue',self.rid,token='once')
        self.assertEqual(216,self.db.execute('SELECT ready_at-started_at FROM tier6_production_queue WHERE user_id=?',(self.uid,)).fetchone()[0])

if __name__=='__main__':unittest.main()
