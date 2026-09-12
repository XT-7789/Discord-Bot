"""Regression coverage for the native text overview (formerly image-backed)."""
import asyncio
import unittest
import discord
import player_overview as overview
import test_economy_transactions as fixtures

class TextOverviewTests(unittest.TestCase):
    def setUp(self):fixtures.TransactionTests.setUp(self)

    def test_three_by_three_text_grid_and_no_attachment(self):
        async def run():
            overview.save_layout(self.db,self.uid,list(overview.BLOCKS))
            view=overview.OverviewView(None,self.db,self.uid)
            tiles=view.tiles(self.db.execute('SELECT * FROM players WHERE user_id=?',(self.uid,)).fetchone())
            expected=[overview.tile_text(tiles[start:start+3]) for start in range(0,len(tiles),3)]
            grids=[x for x in view.walk_children() if isinstance(x,discord.ui.TextDisplay) and x.content in expected]
            self.assertEqual(3,len(grids))
            self.assertTrue(all(len(grid.content.splitlines())==3 for grid in grids))
            self.assertTrue(all('```' not in grid.content for grid in grids))
            self.assertTrue(all('|' not in grid.content for grid in grids))
            self.assertFalse(any(isinstance(x,discord.ui.MediaGallery) for x in view.walk_children()))
            result=await overview.image_options({'view':view})
            self.assertEqual([],result['attachments'])
            labels=[x.label for x in view.walk_children() if isinstance(x,discord.ui.Button)]
            self.assertEqual(['Finance','Mine','Production'],labels[3:6])
        asyncio.run(run())

    def test_refresh_is_text_only_and_keeps_actions(self):
        async def run():
            view=overview.OverviewView(None,self.db,self.uid)
            i=fixtures.interaction(self.uid)
            await view.act(i,('refresh',))
            result=i.edit_original_response.call_args.kwargs
            self.assertEqual([],result['attachments'])
            self.assertIn('MY OVERVIEW',str(result['view'].to_components()))
            self.assertTrue(any(getattr(x,'action',None)==('continue',) for x in result['view'].walk_children()))
        asyncio.run(run())

if __name__=='__main__':unittest.main()
