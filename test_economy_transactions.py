"""Quantity previews and settlement: disposable DBs, no Discord login or live assets."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands
import economy
import economy_extra
import advanced_systems
import economy_journey as journey
import economy_transactions as trades
from economy_trade_ui import TradeView, TradeButton, TradeNav, AmountModal
import tier6
import test_economy_journey as fixtures


def interaction(uid):
    return SimpleNamespace(user=SimpleNamespace(id=uid),message=None,
        response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock(),defer=AsyncMock(),send_modal=AsyncMock()),
        edit_original_response=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))


class TransactionTests(unittest.TestCase):
    fund=fixtures.JourneyTests.fund
    cash=fixtures.JourneyTests.cash

    def setUp(self):
        fixtures.JourneyTests.setUp(self)
        economy_extra.initialise(self.db)
        self.seller=self.uid+1
        self.db.execute('INSERT INTO players(user_id,nation_name,xc) VALUES(?,?,?)',(self.seller,'Seller',1000))
        self.stone=self.db.execute("SELECT id FROM items WHERE name='Stone'").fetchone()[0]
        self.company=self.db.execute('SELECT id FROM tier6_stock_companies ORDER BY id LIMIT 1').fetchone()[0]
        self.db.execute('UPDATE items SET price=5,stock=100,shop_visible=1,enabled=1,tradeable=1,sellable=1,sell_price=2,currency=\'xc\' WHERE id=?',(self.stone,))
        for uid in (self.uid,self.seller):
            self.db.execute('INSERT INTO inventories VALUES(?,?,200) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=200',(uid,self.stone))
        for key,value in {'economy_shop_enabled':1,'market_enabled':1,'tier6_economy_enabled':1,'tier6_stock_enabled':1,'tier6_production_enabled':1,'recipes_enabled':1,'tier6_stock_price_impact':0,'market_fee_percent':5,'tier6_stock_fee_percent':5,'tier6_stock_holding_limit':10000,'tier6_market_max_listings':50,'tier6_production_queue_limit':50}.items():
            self.db.execute('INSERT OR REPLACE INTO economy_settings VALUES(?,?)',(key,str(value)))
        self.db.execute('UPDATE tier6_stock_companies SET last_update=?,price=10,previous_price=10,available_shares=10000,enabled=1 WHERE id=?',(int(time.time()),self.company))
        self.db.commit()

    def listing(self,quantity=5,price=10):
        trades.settle(self.db,self.seller,'list',self.stone,quantity,price)
        return self.db.execute('SELECT MAX(id) FROM market_listings WHERE seller_id=?',(self.seller,)).fetchone()[0]

    def qty(self,uid=None,item=None):return journey.owned(self.db,uid or self.uid,item or self.stone)['quantity']

    def race(self,functions):
        def run(fn):
            with closing(sqlite3.connect(self.path,timeout=5)) as db:
                db.row_factory=sqlite3.Row
                try:return fn(db)
                except ValueError:return None
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:return list(pool.map(run,functions))

    def test_invalid_quantity_limits_and_infinite_max(self):
        for n in (0,-1,1.5,'1',True):
            with self.assertRaises(ValueError):trades.quote(self.db,self.uid,'shop',self.stone,n)
        self.assertEqual(100,trades.quote(self.db,self.uid,'shop',self.stone)['maximum'])
        self.db.execute('UPDATE players SET xc=24 WHERE user_id=?',(self.uid,));self.db.commit()
        self.assertEqual(4,trades.quote(self.db,self.uid,'shop',self.stone)['maximum'])
        self.db.execute('UPDATE items SET stock=-1,price=0 WHERE id=?',(self.stone,));self.db.commit()
        self.assertIsNone(trades.quote(self.db,self.uid,'shop',self.stone)['maximum'])
        self.assertIsNotNone(trades.quote(self.db,self.uid,'list',self.iid)['reason'])
        with self.assertRaises(ValueError):trades.quote(self.db,self.uid,'use',self.stone,2)

    def test_shop_and_sell_receipts_price_gate_and_stock(self):
        q=trades.quote(self.db,self.uid,'shop',self.stone,5)
        trades.settle(self.db,self.uid,'shop',self.stone,5,expected=q['fingerprint'],token='buy')
        self.assertEqual((975,205),(self.cash(),self.qty()))
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'shop',self.stone,5,token='buy')
        sale=trades.quote(self.db,self.uid,'sell',self.stone,5)
        self.db.execute('UPDATE items SET sell_price=3 WHERE id=?',(self.stone,));self.db.commit()
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'sell',self.stone,5,expected=sale['fingerprint'])
        trades.settle(self.db,self.uid,'sell',self.stone,5,token='sell')
        self.assertEqual((990,200),(self.cash(),self.qty()))
        self.db.execute("UPDATE economy_settings SET value='0' WHERE key='economy_shop_enabled'");self.db.commit()
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'shop',self.stone)
        self.assertEqual(990,self.cash())

    def test_shop_inventory_competition_and_rollbacks(self):
        self.db.execute('UPDATE items SET stock=1 WHERE id=?',(self.stone,));self.db.commit()
        results=self.race([lambda db:trades.settle(db,self.uid,'shop',self.stone),lambda db:trades.settle(db,self.seller,'shop',self.stone)])
        self.assertEqual(1,sum(x is not None for x in results))
        self.assertEqual(0,self.db.execute('SELECT stock FROM items WHERE id=?',(self.stone,)).fetchone()[0])
        before=(self.cash(),self.qty())
        self.db.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON economy_logs WHEN NEW.action='journey_receipt' BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        with self.assertRaises(sqlite3.Error):trades.settle(self.db,self.uid,'sell',self.stone,2,token='fail')
        self.assertEqual(before,(self.cash(),self.qty()))

    def test_market_fee_purchase_and_cancel_preview_revalidation(self):
        lid=self.listing()
        q=trades.quote(self.db,self.uid,'market_buy',lid,2)
        old=trades.quote(self.db,self.seller,'market_cancel',lid)
        self.assertEqual((20,1),(q['total'],q['fee']))
        trades.settle(self.db,self.uid,'market_buy',lid,2,expected=q['fingerprint'],token='market-buy')
        self.assertEqual((980,202),(self.cash(),self.qty()))
        self.assertEqual(1019,self.db.execute('SELECT xc FROM players WHERE user_id=?',(self.seller,)).fetchone()[0])
        with self.assertRaises(ValueError):trades.settle(self.db,self.seller,'market_cancel',lid,expected=old['fingerprint'])
        self.assertEqual(195,self.qty(self.seller))
        trades.settle(self.db,self.seller,'market_cancel',lid,token='cancel')
        self.assertEqual(198,self.qty(self.seller))
        with self.assertRaises(ValueError):trades.settle(self.db,self.seller,'market_cancel',lid)
        self.assertEqual(1,self.db.execute('SELECT COUNT(*) FROM tier6_market_trades WHERE listing_id=?',(lid,)).fetchone()[0])

    def test_market_permissions_price_change_closure_and_missing_seller(self):
        lid=self.listing()
        for uid,kind in ((self.seller,'market_buy'),(self.uid,'market_cancel')):
            with self.assertRaises(ValueError):trades.settle(self.db,uid,kind,lid)
        q=trades.quote(self.db,self.uid,'market_buy',lid,5)
        self.db.execute("UPDATE economy_settings SET value='10' WHERE key='market_fee_percent'");self.db.commit()
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'market_buy',lid,5,expected=q['fingerprint'])
        self.db.execute("UPDATE economy_settings SET value='0' WHERE key='market_enabled'");self.db.commit()
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'market_buy',lid)
        self.db.execute("UPDATE economy_settings SET value='1' WHERE key='market_enabled'")
        self.db.execute('DELETE FROM players WHERE user_id=?',(self.seller,));self.db.commit()
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'market_buy',lid)
        self.assertEqual(1000,self.cash())

    def test_market_buy_cancel_expiry_conserve_escrow(self):
        lid=self.listing(5)
        self.race([lambda db:trades.settle(db,self.uid,'market_buy',lid,5),lambda db:trades.settle(db,self.seller,'market_cancel',lid)])
        self.assertEqual(400,self.qty()+self.qty(self.seller))
        lid=self.listing(5)
        self.db.execute('UPDATE market_listings SET created_at=1 WHERE id=?',(lid,));self.db.commit()
        self.race([lambda db:trades.settle(db,self.uid,'market_buy',lid,5),lambda db:trades.settle(db,self.seller,'market_cancel',lid),lambda db:economy_extra.expire_market_listings(db)])
        self.assertEqual(400,self.qty()+self.qty(self.seller))
        economy_extra.expire_market_listings(self.db)
        self.assertEqual(400,self.qty()+self.qty(self.seller))

    def test_market_receipt_failure_rolls_back_both_players_and_listing(self):
        lid=self.listing()
        self.db.execute("CREATE TRIGGER reject_trade BEFORE INSERT ON tier6_market_trades BEGIN SELECT RAISE(ABORT,'failure'); END;")
        with self.assertRaises(sqlite3.Error):trades.settle(self.db,self.uid,'market_buy',lid,5)
        self.assertEqual((1000,200,195),(self.cash(),self.qty(),self.qty(self.seller)))
        self.assertEqual((5,1),tuple(self.db.execute('SELECT quantity,active FROM market_listings WHERE id=?',(lid,)).fetchone()))

    def test_listing_replay_limit_invalid_price_and_no_resource_pack(self):
        for price in (0,-1,1000001,1.5):
            with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'list',self.stone,1,price)
        trades.settle(self.db,self.uid,'list',self.stone,5,10,token='list')
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'list',self.stone,5,10,token='list')
        self.assertEqual(195,self.qty())
        self.db.execute("UPDATE economy_settings SET value='1' WHERE key='tier6_market_max_listings'")
        self.db.execute('INSERT INTO inventories VALUES(?,?,1)',(self.uid,self.iid));self.db.commit()
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'list',self.stone,1,10)
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'list',self.iid,1,10)
        self.assertEqual(1000,self.cash())

    def test_stock_max_fees_price_change_and_duplicate(self):
        q=trades.quote(self.db,self.uid,'stock_buy',self.company)
        maximum=q['maximum']
        self.assertLessEqual(tier6.stock_quote(self.db,self.uid,self.company,'buy',maximum)['total'],1000)
        self.assertGreater(tier6.stock_quote(self.db,self.uid,self.company,'buy',maximum+1)['total'],1000)
        q=trades.quote(self.db,self.uid,'stock_buy',self.company,5)
        trades.settle(self.db,self.uid,'stock_buy',self.company,5,expected=q['fingerprint'],token='stock')
        self.assertEqual(1000-q['total'],self.cash())
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'stock_buy',self.company,5,token='stock')
        sale=trades.quote(self.db,self.uid,'stock_sell',self.company,5)
        self.assertEqual(5,sale['maximum'])
        self.db.execute('UPDATE tier6_stock_companies SET price=11 WHERE id=?',(self.company,));self.db.commit()
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'stock_sell',self.company,5,expected=sale['fingerprint'])
        sale=trades.quote(self.db,self.uid,'stock_sell',self.company,5)
        trades.settle(self.db,self.uid,'stock_sell',self.company,5)
        self.assertEqual(1000-q['total']+sale['total'],self.cash())

    def test_stock_tick_never_commits_outer_asset_transaction(self):
        self.db.execute('UPDATE tier6_stock_companies SET last_update=0 WHERE id=?',(self.company,));self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute('UPDATE players SET xc=xc-50 WHERE user_id=?',(self.uid,))
        self.assertGreater(tier6.update_stock_prices(self.db),0)
        with closing(sqlite3.connect(self.path)) as other:
            self.assertEqual(1000,other.execute('SELECT xc FROM players WHERE user_id=?',(self.uid,)).fetchone()[0])
        self.db.rollback()
        self.assertEqual(1000,self.cash())
        self.assertEqual(0,self.db.execute('SELECT last_update FROM tier6_stock_companies WHERE id=?',(self.company,)).fetchone()[0])
        self.db.execute("CREATE TRIGGER reject_stock BEFORE INSERT ON tier6_stock_trades BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        with self.assertRaises(sqlite3.Error):trades.settle(self.db,self.uid,'stock_buy',self.company,1)
        self.assertEqual(1000,self.cash())
        self.assertEqual(0,self.db.execute('SELECT COUNT(*) FROM tier6_stock_holdings WHERE user_id=?',(self.uid,)).fetchone()[0])
        self.assertEqual(0,self.db.execute('SELECT last_update FROM tier6_stock_companies WHERE id=?',(self.company,)).fetchone()[0])

    def test_busy_replay_and_competing_sales_across_connections(self):
        self.db.execute('BEGIN IMMEDIATE')
        with closing(sqlite3.connect(self.path,timeout=.01)) as other:
            other.row_factory=sqlite3.Row
            with self.assertRaises(sqlite3.OperationalError):trades.settle(other,self.uid,'sell',self.stone)
        self.db.rollback()
        results=self.race([lambda db:trades.settle(db,self.uid,'sell',self.stone,200),lambda db:trades.settle(db,self.uid,'sell',self.stone,200)])
        self.assertEqual(1,sum(x is not None for x in results))
        self.assertEqual((1400,0),(self.cash(),self.qty()))

    def test_production_live_quote_full_cancel_and_collect(self):
        self.fund(4)
        q=trades.quote(self.db,self.uid,'queue',self.rid,2)
        self.assertEqual(4,q['maximum'])
        trades.settle(self.db,self.uid,'queue',self.rid,2,expected=q['fingerprint'],token='queue')
        jid=self.db.execute('SELECT MAX(id) FROM tier6_production_queue WHERE user_id=?',(self.uid,)).fetchone()[0]
        self.db.execute("UPDATE economy_settings SET value='1' WHERE key='tier6_production_queue_limit'");self.db.commit()
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'queue',self.rid)
        preview=trades.quote(self.db,self.uid,'production_cancel',jid)
        with self.assertRaises(ValueError):trades.settle(self.db,self.seller,'production_cancel',jid)
        trades.settle(self.db,self.uid,'production_cancel',jid,expected=preview['fingerprint'])
        self.assertEqual(1000,self.cash())
        with self.assertRaises(ValueError):trades.settle(self.db,self.uid,'production_cancel',jid)
        trades.settle(self.db,self.uid,'queue',self.rid,2)
        self.db.execute('UPDATE tier6_production_queue SET ready_at=0 WHERE user_id=?',(self.uid,));self.db.commit()
        self.race([lambda db:tier6.claim_production(db,self.uid),lambda db:tier6.claim_production(db,self.uid)])
        self.assertEqual(2,self.qty(item=self.iid))

    def test_quantity_modal_ownership_result_and_live_back(self):
        async def run():
            bot=SimpleNamespace(xbot_player_panel_builders={})
            view=TradeView(bot,self.db,self.uid,'shop',self.stone,back=lambda:TradeView(bot,self.db,self.uid,'shop',self.stone))
            custom=AmountModal(view,'custom');custom.value._value='5'
            outside=interaction(self.seller);await custom.on_submit(outside)
            outside.response.edit_message.assert_not_called()
            i=interaction(self.uid);await custom.on_submit(i)
            preview=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual((1000,200),(self.cash(),self.qty()))
            self.assertEqual(5,preview.quantity)
            self.assertIn('Buy 5 · 25 XC',str(preview.to_components()))
            button=next(x for x in preview.walk_children() if isinstance(x,TradeButton) and x.action==('execute',))
            await button.callback(outside);self.assertEqual(1000,self.cash())
            await button.callback(i);await button.callback(i)
            self.assertEqual((975,205),(self.cash(),self.qty()))
            result=i.edit_original_response.call_args.kwargs['view']
            self.assertEqual((5,self.stone,True),(result.quantity,result.target,result.used))
            await next(x for x in result.walk_children() if isinstance(x,TradeNav) and x.key=='back').callback(i)
            self.assertEqual(975,i.response.edit_message.call_args.kwargs['view'].quote['wallet'])
            invalid=AmountModal(view,'custom');invalid.value._value='-1'
            denied=interaction(self.uid);await invalid.on_submit(denied)
            denied.response.edit_message.assert_not_called()
        asyncio.run(run())

    def test_stale_ui_quote_updates_without_spending(self):
        async def run():
            v=TradeView(SimpleNamespace(),self.db,self.uid,'shop',self.stone,5)
            self.db.execute('UPDATE items SET price=6 WHERE id=?',(self.stone,));self.db.commit()
            i=interaction(self.uid);await v.act(i,('execute',))
            new=i.edit_original_response.call_args.kwargs['view']
            self.assertEqual((1000,200),(self.cash(),self.qty()))
            self.assertEqual((30,5,False),(new.quote['total'],new.quantity,new.used))
            self.assertIn('confirm again',str(new.to_components()))
            await new.act(i,('execute',));self.assertEqual(970,self.cash())
        asyncio.run(run())

    def test_all_trade_pages_limits_fixed_actions_and_batch_route(self):
        async def run():
            self.fund(20);lid=self.listing()
            bot=SimpleNamespace(xbot_player_panel_builders={})
            for kind,target,uid in [('shop',self.stone,self.uid),('sell',self.stone,self.uid),('use',self.stone,self.uid),('list',self.stone,self.uid),('market_buy',lid,self.uid),('market_cancel',lid,self.seller),('craft',self.rid,self.uid),('queue',self.rid,self.uid),('stock_buy',self.company,self.uid),('stock_sell',self.company,self.uid)]:
                for complete in (False,True):
                    view=TradeView(bot,self.db,uid,kind,target,complete=complete)
                    view.to_components();self.assertLessEqual(view.total_children_count,40,kind)
                    for child in view.walk_children():
                        if isinstance(child,discord.ui.ActionRow):self.assertLessEqual(len(child.children),5)
                        if isinstance(child,discord.ui.TextDisplay):self.assertLessEqual(len(child.content),4000)
                        if isinstance(child,discord.ui.Button):self.assertLessEqual(len(child.label or ''),80)
                    self.assertFalse(any(getattr(x,'label',None)=='Help' for x in view.walk_children()))
            before=self.cash()
            v=TradeView(bot,self.db,self.uid,'craft',self.rid)
            i=interaction(self.uid);await v.act(i,('mode','queue'))
            queued=i.response.edit_message.call_args.kwargs['view']
            await queued.act(i,('quantity',10))
            self.assertEqual('queue',queued.kind);self.assertEqual(before,self.cash())
            self.assertEqual(0,journey.activity(self.db,self.uid)['active'])
        asyncio.run(run())

    def test_registered_list_pages_and_selected_returns(self):
        async def run():
            bot=commands.Bot(command_prefix='!',intents=discord.Intents.none())
            create=lambda user:self.db.execute('SELECT * FROM players WHERE user_id=?',(user.id,)).fetchone()
            economy.register_commands(bot,self.db,create)
            economy_extra.register_commands(bot,self.db,create,economy.find_item)
            tier6.register_commands(bot,self.db,create)
            advanced_systems.register_commands(bot,self.db,create)
            try:
                i=interaction(self.uid)
                for key in ('inventory','shop','market','market_mine','production','stock','craft'):
                    v=bot.xbot_player_panel_builders[key](self.uid)
                    v.to_components();self.assertLessEqual(v.total_children_count,40,key)
                inv=bot.xbot_player_panel_builders['inventory'](self.uid)
                await next(x for x in inv.walk_children() if getattr(x,'action',None)=='sell').callback(i)
                selected=i.response.edit_message.call_args.kwargs['view'];self.assertEqual('sell',selected.kind)
                await next(x for x in selected.walk_children() if isinstance(x,TradeNav) and x.key=='back').callback(i)
                self.assertIn('Stone',str(i.response.edit_message.call_args.kwargs['view'].to_components()))
                for _ in range(7):self.listing(1)
                mine=bot.xbot_player_panel_builders['market_mine'](self.seller)
                s=interaction(self.seller)
                await next(x for x in mine.walk_children() if getattr(x,'label',None)=='Next').callback(s)
                second=s.response.edit_message.call_args.kwargs['view'];self.assertEqual(1,second.page)
                await next(x for x in second.walk_children() if getattr(x,'label',None)=='Review / Cancel').callback(s)
                detail=s.response.edit_message.call_args.kwargs['view']
                await next(x for x in detail.walk_children() if isinstance(x,TradeNav) and x.key=='back').callback(s)
                self.assertEqual(1,s.response.edit_message.call_args.kwargs['view'].page)
                self.fund(20)
                for _ in range(12):tier6.start_production(self.db,self.uid,self.rid,1)
                production=bot.xbot_player_panel_builders['production'](self.uid)
                await next(x for x in production.walk_children() if getattr(x,'label',None)=='Next Jobs').callback(i)
                second=i.response.edit_message.call_args.kwargs['view'];self.assertEqual(1,second.page)
                selector=next(x for x in second.walk_children() if x.__class__.__name__=='ProductionRecipeSelect')
                selector._values=[str(self.rid)];await selector.callback(i)
                detail=i.response.edit_message.call_args.kwargs['view'];self.assertEqual('queue',detail.kind)
                await next(x for x in detail.walk_children() if isinstance(x,TradeNav) and x.key=='back').callback(i)
                back=i.response.edit_message.call_args.kwargs['view'];self.assertEqual((1,self.rid),(back.page,back.selected))
                self.db.execute('UPDATE tier6_production_queue SET ready_at=0 WHERE user_id=?',(self.uid,));self.db.commit()
                production=bot.xbot_player_panel_builders['production'](self.uid)
                await next(x for x in production.walk_children() if getattr(x,'label',None)=='Collect All Ready').callback(i)
                collected=i.edit_original_response.call_args.kwargs['view']
                self.assertEqual('products',collected.page)
                self.assertEqual((self.iid,),collected.product_ids)
                self.assertEqual([str(self.iid)],[o.value for x in collected.walk_children() if isinstance(x,discord.ui.Select) for o in x.options])
                await collected.act(i,('back',))
                self.assertEqual('ProductionView',i.response.edit_message.call_args.kwargs['view'].__class__.__name__)
            finally:await bot.close()
        asyncio.run(run())

    def test_result_next_steps_preserve_result_and_do_not_trade(self):
        async def run():
            self.fund(3);bot=SimpleNamespace(xbot_player_panel_builders={})
            v=TradeView(bot,self.db,self.uid,'craft',self.rid)
            i=interaction(self.uid);await v.act(i,('execute',))
            result=i.edit_original_response.call_args.kwargs['view']
            self.assertNotIn('Next order reference',str(result.to_components()))
            self.assertFalse(any(isinstance(x,TradeButton) and x.action[0] in {'quantity','custom','execute'} for x in result.walk_children()))
            sell=next(x for x in result.walk_children() if getattr(x,'label',None)=='Sell Product')
            self.assertEqual(discord.ButtonStyle.success,sell.style)
            before=self.cash();await sell.callback(i)
            preview=i.response.edit_message.call_args.kwargs['view'];self.assertEqual('sell',preview.kind)
            self.assertEqual(before,self.cash())
            await next(x for x in preview.walk_children() if isinstance(x,TradeNav) and x.key=='back').callback(i)
            restored=i.response.edit_message.call_args.kwargs['view']
            self.assertTrue(restored.complete);self.assertTrue(restored.used)
            self.assertEqual(result.notice,restored.notice)
            await restored.act(i,('refresh',))
            again=i.response.edit_message.call_args.kwargs['view']
            self.assertFalse(again.complete);self.assertEqual(before,self.cash())
            proceeds=preview.quote['total']
            await preview.act(i,('execute',))
            sold=i.edit_original_response.call_args.kwargs['view']
            self.assertTrue(sold.complete)
            self.assertEqual(before+proceeds,self.cash())
            self.assertIn('Mine Again',str(sold.to_components()))
            await preview.act(i,('execute',))
            self.assertEqual(before+proceeds,self.cash())
        asyncio.run(run())

    def test_selected_quantity_and_purchase_use_preview(self):
        async def run():
            bot=SimpleNamespace(xbot_player_panel_builders={})
            v=TradeView(bot,self.db,self.uid,'shop',self.stone,5)
            highlighted=[x for x in v.walk_children() if isinstance(x,TradeButton) and x.action[0]=='quantity' and x.style==discord.ButtonStyle.primary]
            self.assertEqual(['5'],[x.label for x in highlighted])
            self.db.execute("UPDATE items SET effect='xc_reward',effect_value=1 WHERE id=?",(self.stone,));self.db.commit()
            i=interaction(self.uid);await v.act(i,('execute',))
            result=i.edit_original_response.call_args.kwargs['view'];before=self.cash()
            use=next(x for x in result.walk_children() if getattr(x,'label',None)=='Use Product')
            await use.callback(i)
            self.assertEqual('use',i.response.edit_message.call_args.kwargs['view'].kind)
            self.assertEqual(before,self.cash())
        asyncio.run(run())

    def test_shortcut_back_returns_exact_quantity_and_owner(self):
        async def run():
            def page(uid):
                v=discord.ui.LayoutView();v.add_item(discord.ui.TextDisplay('Backpack'));return v
            bot=SimpleNamespace(xbot_player_panel_builders={'inventory':page})
            v=TradeView(bot,self.db,self.uid,'sell',self.stone,10)
            i=interaction(self.uid);await v.act(i,('nav','inventory'))
            child=i.response.edit_message.call_args.kwargs['view']
            back=next(x for x in child.walk_children() if isinstance(x,TradeNav))
            denied=interaction(self.seller);await back.callback(denied);denied.response.edit_message.assert_not_called()
            await back.callback(i)
            returned=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual(('sell',self.stone,10),(returned.kind,returned.target,returned.quantity))
            self.assertEqual(1000,self.cash())
        asyncio.run(run())

    def test_claimed_products_survive_recipe_removal_and_filter_old_items(self):
        self.fund(2);tier6.start_production(self.db,self.uid,self.rid,1)
        self.db.execute('UPDATE tier6_production_queue SET ready_at=0 WHERE user_id=?',(self.uid,))
        self.db.execute('DELETE FROM recipes WHERE id=?',(self.rid,));self.db.commit()
        notice,items=tier6.claim_production(self.db,self.uid,return_items=True)
        self.assertEqual((self.iid,),items)
        async def run():
            view=journey.JourneyView(SimpleNamespace(),self.db,self.uid,page='products',product_ids=items,notice=notice)
            self.assertEqual([str(self.iid)],[o.value for x in view.walk_children() if isinstance(x,discord.ui.Select) for o in x.options])
        asyncio.run(run())
        self.assertEqual((),tier6.claim_production(self.db,self.uid,return_items=True)[1])


if __name__=='__main__':unittest.main()
