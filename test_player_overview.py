"""Personal overview and growth guidance. Temporary DBs; no network or login."""
import asyncio
from contextlib import closing
import sqlite3
from types import SimpleNamespace
import unittest
import discord
import economy_progress
import player_overview as overview
import test_economy_transactions as fixtures

class OverviewTests(unittest.TestCase):
    def test_workshop_has_one_navigation_row_and_direct_recommendation(self):
        import economy_journey as journey
        view=journey.JourneyView(SimpleNamespace(xbot_player_panel_builders={}),self.db,self.uid)
        labels=[getattr(x,'label',None) for x in view.walk_children()]
        self.assertEqual(1,labels.count('‹ Back'))
        self.assertEqual(1,labels.count('⌂ Menu'))
        self.assertEqual(1,labels.count('× Close'))
        self.assertTrue(any((label or '').startswith('Open Recommended · ') for label in labels))
        self.assertIn('recipes available',str(view.to_components()))
        self.assertTrue(view.xbot_managed_navigation)

    def test_mines_navigation_returns_to_live_recipe_without_spending(self):
        import economy_journey as journey
        async def run():
            bot=SimpleNamespace(xbot_player_panel_builders={'mining':lambda uid:discord.ui.LayoutView()})
            rid=next(q['recipe']['id'] for q in journey.quotes(self.db,self.uid) if q['recipe']['name']=='Resource Pack')
            source=journey.JourneyView(bot,self.db,self.uid,page='areas',rid=rid)
            before=self.balance();i=fixtures.interaction(self.uid)
            await source.act(i,('nav','mining'))
            mines=i.response.edit_message.call_args.kwargs['view']
            back=next(x for x in mines.walk_children() if getattr(x,'label',None)=='Back to Materials')
            await back.callback(i)
            restored=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual((rid,'areas'),(restored.rid,restored.page))
            self.assertIsNot(source,restored)
            self.assertEqual(before,self.balance())
        asyncio.run(run())

    def test_beginner_missing_materials_has_actionable_route(self):
        import economy_journey as journey
        async def run():
            q=next(q for q in journey.quotes(self.db,self.uid) if q['recipe']['name']=='Resource Pack')
            self.db.execute('DELETE FROM inventories WHERE user_id=?',(self.uid,));self.db.commit()
            view=journey.JourneyView(SimpleNamespace(),self.db,self.uid,page='detail',rid=q['recipe']['id'])
            labels=[getattr(x,'label',None) for x in view.walk_children()]
            self.assertIn('Find missing materials',labels)
            self.assertNotIn('Craft 1',labels)
            self.assertIn('What you still need',str(view.to_components()))
            area=journey.JourneyView(SimpleNamespace(),self.db,self.uid,page='areas',rid=q['recipe']['id'])
            self.assertIn('Back to Recipe',str(area.to_components()))
            self.assertLessEqual(area.total_children_count,40)
        asyncio.run(run())

    def test_action_labels_describe_preview_destinations(self):
        import economy_journey as journey
        self.assertEqual('Choose Product to Sell',journey.next_step_label({'page':'products'}))
        self.assertEqual('Review Material Sales',journey.next_step_label({'page':'materials'}))
        self.assertEqual('Review Craft',journey.next_step_label({'page':'detail'},{'craftable':True}))
        self.assertEqual('Materials & Recipe',journey.next_step_label({'page':'detail'},{'craftable':False}))

    def setUp(self):fixtures.TransactionTests.setUp(self)
    def balance(self):return self.db.execute('SELECT xc FROM players WHERE user_id=?',(self.uid,)).fetchone()[0]

    def test_layout_persists_and_invalid_input_rolls_back(self):
        self.assertEqual(overview.DEFAULT,overview.layout(self.db,self.uid))
        overview.save_layout(self.db,self.uid,['mining','vip'])
        with closing(sqlite3.connect(self.path)) as other:
            other.row_factory=sqlite3.Row
            self.assertEqual(('mining','vip'),overview.layout(other,self.uid))
        with self.assertRaises(ValueError):overview.save_layout(self.db,self.uid,['invalid'])
        self.assertEqual(('mining','vip'),overview.layout(self.db,self.uid))
        self.db.execute("CREATE TRIGGER reject_layout BEFORE INSERT ON economy_logs WHEN NEW.action='overview_layout' BEGIN SELECT RAISE(ABORT,'failure'); END;")
        with self.assertRaises(sqlite3.Error):overview.save_layout(self.db,self.uid,[])
        self.assertEqual(('mining','vip'),overview.layout(self.db,self.uid))
        self.assertEqual(1000,self.balance())

    def test_all_sections_mobile_component_budget_and_empty(self):
        async def run():
            bot=SimpleNamespace(xbot_player_panel_builders={})
            for keys in (overview.DEFAULT,tuple(overview.BLOCKS),(),('production','market','wallet','mining')):
                overview.save_layout(self.db,self.uid,keys)
                for page in (0,1,99):
                    view=overview.OverviewView(bot,self.db,self.uid,page)
                    view.to_components();self.assertLessEqual(view.total_children_count,40)
                    text=[x.content for x in view.walk_children() if isinstance(x,discord.ui.TextDisplay)]
                    self.assertLessEqual(sum(map(len,text)),4000)
                    self.assertIn('Current goal',' '.join(text))
                    self.assertEqual(1,view.pages)
                    self.assertEqual(0,view.page)
                    self.assertNotIn('Previous',[getattr(x,'label',None) for x in view.walk_children()])
                    rows=[x for x in view.box.children if isinstance(x,discord.ui.ActionRow)]
                    self.assertTrue(all(len(row.children)<=3 for row in rows))
                    destinations=[x.action[1] for x in view.walk_children() if isinstance(x,overview.OverviewButton) and x.action[0]=='nav']
                    if 'market' in keys:self.assertIn('stock',destinations)
                    if 'vip' in keys:self.assertIn('vip',destinations)
                    tiles=view.tiles(self.db.execute('SELECT * FROM players WHERE user_id=?',(self.uid,)).fetchone())
                    self.assertEqual([tile[4] for tile in tiles],destinations)
                    children=list(view.box.children)
                    expected=[overview.tile_text(tiles[start:start+3]) for start in range(0,len(tiles),3)]
                    grids=[x for x in children if isinstance(x,discord.ui.TextDisplay) and x.content in expected]
                    self.assertEqual((len(tiles)+2)//3,len(grids))
                    for grid in grids:
                        self.assertEqual(3,len(grid.content.splitlines()))
                        self.assertNotIn('```',grid.content)
                        self.assertNotIn('|',grid.content)
                        self.assertTrue(grid.content.splitlines()[0].startswith('**'))
                        self.assertIsInstance(children[children.index(grid)+1],discord.ui.ActionRow)
                    if len(keys)==7:
                        self.assertEqual(9,len(tiles))
                        self.assertEqual(['Wallet','Mines','Production'],[t[0] for t in tiles[:3]])
                    self.assertNotIn('Help',[getattr(x,'label',None) for x in view.walk_children()])
                    self.assertLessEqual(len([x for x in view.walk_children() if isinstance(x,discord.ui.Separator)]),2)
            self.assertEqual(1000,self.balance())
        asyncio.run(run())

    def test_custom_draft_cancel_owner_and_save(self):
        async def run():
            bot=SimpleNamespace(xbot_player_panel_builders={})
            view=overview.OverviewView(bot,self.db,self.uid,editing=True)
            select=next(x for x in view.walk_children() if isinstance(x,overview.LayoutSelect));select._values=['vip','mining']
            denied=fixtures.interaction(self.seller);await select.callback(denied)
            denied.response.edit_message.assert_not_called()
            i=fixtures.interaction(self.uid);await select.callback(i)
            draft=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual(overview.DEFAULT,overview.layout(self.db,self.uid))
            await draft.act(i,('cancel',));self.assertEqual(overview.DEFAULT,overview.layout(self.db,self.uid))
            save=next(x for x in draft.walk_children() if getattr(x,'label',None)=='Save')
            await save.callback(denied);self.assertEqual(overview.DEFAULT,overview.layout(self.db,self.uid))
            await save.callback(i);self.assertEqual(('mining','vip'),overview.layout(self.db,self.uid))
            self.assertEqual(1000,self.balance())
        asyncio.run(run())

    def test_growth_gap_changes_and_tool_comparison_is_readonly(self):
        text,item=economy_progress.growth(self.db,self.uid)
        self.assertIsNotNone(item);self.assertIn('Next tool',text)
        self.assertIn('Power:',economy_progress.tool_comparison(self.db,self.uid,item))
        self.db.execute('UPDATE players SET xc=0 WHERE user_id=?',(self.uid,));self.db.commit()
        text,_=economy_progress.growth(self.db,self.uid);self.assertIn('Need',text)
        self.db.execute('UPDATE players SET xc=1000000 WHERE user_id=?',(self.uid,));self.db.commit()
        self.assertIn('Affordable now',economy_progress.growth(self.db,self.uid)[0])
        self.assertEqual(1000000,self.balance())

    def test_continue_upgrade_and_return_never_purchase(self):
        async def run():
            bot=SimpleNamespace(xbot_player_panel_builders={})
            view=overview.OverviewView(bot,self.db,self.uid)
            i=fixtures.interaction(self.uid);await view.act(i,('continue',))
            journey=i.response.edit_message.call_args.kwargs['view']
            await journey.act(i,('back',))
            self.assertIsInstance(i.response.edit_message.call_args.kwargs['view'],overview.OverviewView)
            await view.act(i,('upgrade',))
            detail=i.response.edit_message.call_args.kwargs['view']
            self.assertEqual('shop',detail.kind)
            self.assertIn('Equipped → Selected',str(detail.to_components()))
            self.assertEqual(1000,self.balance())
        asyncio.run(run())

if __name__=='__main__':unittest.main()
