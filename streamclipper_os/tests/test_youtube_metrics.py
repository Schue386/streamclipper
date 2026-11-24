import unittest
from contextlib import closing
from stream_monitor import connect,now
from youtube_metrics import collect,latest,fetch_api
class MetricsTests(unittest.TestCase):
    def test_history_and_missing(self):
        with closing(connect(':memory:')) as db:
            db.execute("INSERT INTO streams VALUES('twitch','1','test',NULL,NULL,?,?,'live')",(now(),now()))
            db.execute("INSERT INTO clips(clip_id,platform,stream_id,created_at) VALUES('c','twitch','1',?)",(now(),))
            db.execute("INSERT INTO publications(clip_id,destination,remote_id,status) VALUES('c','youtube','abcdefghijk','uploaded_private')")
            db.commit()
            self.assertIsNone(latest(db)['views'])
            self.assertEqual(collect(db,lambda ids:[]),0)
            for n in (100,200):
                self.assertEqual(collect(db,lambda ids:[{'id':ids[0],'statistics':{'viewCount':str(n),'likeCount':'10','commentCount':'2'}}]),1)
            self.assertEqual(latest(db)['views'],200)
            self.assertEqual(latest(db)['engagement'],6)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM metrics').fetchone()[0],2)
    def test_api_failure(self):
        class Transport:
            def request(self,*args):return 403,{},b'{}'
        with self.assertRaises(RuntimeError):fetch_api(['abcdefghijk'],'test',Transport())
