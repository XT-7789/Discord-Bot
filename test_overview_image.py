import asyncio
import io
import time
import unittest
from unittest.mock import patch
from PIL import Image
import overview_image as renderer
import player_overview as overview
import test_economy_transactions as fixtures

class ImageTests(unittest.TestCase):
    def setUp(self):
        fixtures.TransactionTests.setUp(self)
        renderer.CACHE.clear()

    def test_layout_cache_and_size(self):
        async def run():
            for keys in (tuple(overview.BLOCKS),('vip',),()):
                overview.save_layout(self.db,self.uid,keys)
                view=overview.OverviewView(None,self.db,self.uid)
                before=self.db.total_changes
                start=time.perf_counter();data=await asyncio.to_thread(renderer.render,view.snapshot)
                elapsed=time.perf_counter()-start
                self.assertLess(len(data),500*1024)
                self.assertEqual(960,Image.open(io.BytesIO(data)).width)
                self.assertEqual(before,self.db.total_changes)
                start=time.perf_counter();self.assertEqual(data,renderer.render(view.snapshot))
                print(f'Overview {len(view.snapshot["tiles"])} tiles: {elapsed:.3f}s, cached {time.perf_counter()-start:.4f}s, {len(data)} bytes')
                result=await overview.image_options({'view':view,'attachments':[]})
                self.assertEqual(1,len(result['attachments']))
                self.assertLessEqual(view.total_children_count,40)
                self.assertNotIn('```',str(view.to_components()))
        asyncio.run(run())

    def test_cache_bounds_expiry_and_fallback(self):
        snapshot={'owner':1,'goal':'A'*500,'upgrade':'B'*500,'tiles':[]}
        renderer.render(snapshot)
        with patch.object(renderer.time,'monotonic',return_value=time.monotonic()+61),patch.object(renderer,'FONT',renderer.FONT.with_name('missing.ttf')):
            self.assertEqual(960,Image.open(io.BytesIO(renderer.render(snapshot))).width)
        for uid in range(34):renderer.render(dict(snapshot,owner=uid))
        self.assertEqual(32,len(renderer.CACHE))
        async def run():
            view=overview.OverviewView(None,self.db,self.uid)
            with patch.object(renderer,'render',side_effect=OSError('font')):
                result=await overview.image_options({'view':view})
            self.assertEqual([],result['attachments'])
            self.assertIn('Continue',str(view.to_components()))
            self.assertIn('Text mode (OV-IMG-2)',str(view.to_components()))
            self.assertNotIn('attachment://',str(view.to_components()))
        asyncio.run(run())

    def test_termux_without_freetype_still_sends_image(self):
        async def run():
            overview.save_layout(self.db,self.uid,list(overview.BLOCKS))
            view=overview.OverviewView(None,self.db,self.uid)
            with patch.object(renderer.ImageFont,'truetype',side_effect=ImportError('cannot import _imagingft')):
                result=await overview.image_options({'view':view})
            self.assertEqual(1,len(result['attachments']))
            data=result['attachments'][0].fp.getvalue()
            image=Image.open(io.BytesIO(data))
            self.assertEqual((960,940),image.size)
            self.assertLess(len(data),500*1024)
            self.assertIn('attachment://overview.png',str(view.to_components()))
        asyncio.run(run())

    def test_refresh_acknowledges_and_replaces_attachment(self):
        async def run():
            view=overview.OverviewView(None,self.db,self.uid)
            i=fixtures.interaction(self.uid)
            await view.act(i,('refresh',))
            i.response.defer.assert_awaited_once()
            result=i.edit_original_response.call_args.kwargs
            self.assertEqual('overview.png',result['attachments'][0].filename)
            before=result['view'].snapshot
            self.db.execute('UPDATE players SET xc=4321 WHERE user_id=?',(self.uid,));self.db.commit()
            await view.act(i,('refresh',))
            self.assertNotEqual(before,i.edit_original_response.call_args.kwargs['view'].snapshot)
            await view.act(i,('customize',))
            self.assertEqual([],i.response.edit_message.call_args.kwargs['attachments'])
            denied=fixtures.interaction(self.seller)
            button=next(x for x in view.walk_children() if getattr(x,'label',None)=='Refresh')
            await button.callback(denied)
            denied.response.defer.assert_not_called()
            denied.edit_original_response.assert_not_called()
        asyncio.run(run())
