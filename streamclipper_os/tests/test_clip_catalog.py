import tempfile
import unittest
from pathlib import Path
from stream_monitor import connect, add_channel, record_live
from clip_catalog import prepare,register,approve_rights,queue_upload,summary

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=connect(str(Path(self.tmp.name)/'test.sqlite'))
        add_channel(self.db,'zarbex')
        record_live(self.db,[{'id':'s1','user_login':'zarbex'}])
        prepare(self.db)
        self.mp4=Path(self.tmp.name)/'clip.mp4'
        self.mp4.write_bytes(b'test-content')
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def test_pass_and_explicit_rights(self):
        clip=register(self.db,'twitch','s1',str(self.mp4),{'duration':70,'width':1080,'height':1920})
        self.assertEqual(clip['status'],'passed')
        self.assertEqual(queue_upload(self.db,clip['clip_id'],'youtube')['status'],'blocked')
        approve_rights(self.db,clip['clip_id'],'Permission granted by creator')
        self.assertEqual(queue_upload(self.db,clip['clip_id'],'youtube')['status'],'ready')
        self.assertEqual(summary(self.db)['uploads_completed'],0)
    def test_duration_gate(self):
        clip=register(self.db,'twitch','s1',str(self.mp4),{'duration':35,'width':1080,'height':1920})
        self.assertEqual(clip['status'],'rejected')
        with self.assertRaises(ValueError):approve_rights(self.db,clip['clip_id'],'Permission granted by creator')
    def test_idempotence_and_integrity(self):
        data={'duration':75,'width':1080,'height':1920}
        clip=register(self.db,'twitch','s1',str(self.mp4),data)
        register(self.db,'twitch','s1',str(self.mp4),data)
        self.assertEqual(summary(self.db)['clips'],1)
        approve_rights(self.db,clip['clip_id'],'Permission granted by creator')
        self.mp4.write_bytes(b'changed')
        self.assertEqual(queue_upload(self.db,clip['clip_id'],'tiktok')['status'],'blocked')
    def test_missing_metadata_blocks(self):
        clip=register(self.db,'twitch','s1',str(self.mp4),{})
        self.assertEqual(clip['status'],'unverified')
        self.assertEqual(queue_upload(self.db,clip['clip_id'],'youtube')['status'],'blocked')
