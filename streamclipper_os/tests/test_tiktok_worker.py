from test_clip_catalog import CatalogTests
from clip_catalog import register,approve_rights,queue_upload
from tiktok_worker import run_next
from unittest.mock import Mock
class TikTokTests(CatalogTests):
    def ready(self):
        c=register(self.db,'twitch','s1',str(self.mp4),{'duration':75,'width':1080,'height':1920})
        approve_rights(self.db,c['clip_id'],'Permission granted by creator')
        queue_upload(self.db,c['clip_id'],'tiktok')
        return run_next(self.db)['queue_id']
    def test_preview(self):
        self.ready(); s=Mock(); self.assertEqual(run_next(self.db,sender=s)['status'],'preview'); s.assert_not_called()
    def test_inbox_once(self):
        q=self.ready(); s=Mock(return_value='publish123'); self.assertEqual(run_next(self.db,q,sender=s)['status'],'inbox_sent'); self.assertEqual(run_next(self.db,q,sender=s)['status'],'empty'); s.assert_called_once()
    def test_uncertain(self):
        q=self.ready(); s=Mock(side_effect=TimeoutError()); self.assertEqual(run_next(self.db,q,sender=s)['status'],'uncertain'); self.assertEqual(run_next(self.db,q,sender=s)['status'],'empty')
