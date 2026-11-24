from test_clip_catalog import CatalogTests
from clip_catalog import register,approve_rights,queue_upload
from youtube_worker import run_next
from unittest.mock import Mock

class WorkerTests(CatalogTests):
    def ready(self):
        c=register(self.db,'twitch','s1',str(self.mp4),{'duration':75,'width':1080,'height':1920})
        approve_rights(self.db,c['clip_id'],'Permission granted by creator')
        queue_upload(self.db,c['clip_id'],'youtube')
    def test_preview(self):
        self.ready();send=Mock();self.assertEqual(run_next(self.db,sender=send)['status'],'preview');send.assert_not_called()
    def test_success_once(self):
        self.ready();send=Mock(return_value={'video_id':'abcdefghijk'});self.assertEqual(run_next(self.db,True,send)['status'],'uploaded');self.assertEqual(run_next(self.db,True,send)['status'],'empty');send.assert_called_once()
    def test_uncertain_no_retry(self):
        self.ready();send=Mock(side_effect=TimeoutError());self.assertEqual(run_next(self.db,True,send)['status'],'uncertain');self.assertEqual(run_next(self.db,True,send)['status'],'empty');send.assert_called_once()
    def test_changed_file(self):
        self.ready();self.mp4.write_bytes(b'changed');send=Mock();self.assertEqual(run_next(self.db,True,send)['status'],'blocked');send.assert_not_called()
