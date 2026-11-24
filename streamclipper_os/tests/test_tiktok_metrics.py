import unittest
from contextlib import closing
from stream_monitor import connect
from tiktok_metrics import collect,latest
from performance import analyze
class TikTokMetricsTests(unittest.TestCase):
    def test_public_only_and_missing(self):
        with closing(connect(':memory:')) as db:
            db.execute("INSERT INTO streams(platform,stream_id,channel,detected_at,last_seen_at) VALUES('twitch','s','demo','x','x')")
            for i in range(2):
                db.execute("INSERT INTO clips(clip_id,platform,stream_id,created_at) VALUES(?,'twitch','s','x')",(str(i),))
                db.execute("INSERT INTO publications(clip_id,destination,status,remote_id,published_at) VALUES(?,'tiktok',?,?, '2026-11-20T00:00:00+00:00')",(str(i),'published' if i==0 else 'pending',str(i)))
            def fetch(ids):
                self.assertEqual(ids,['0']);return {'videos':[{'id':'0','view_count':0,'share_count':2}]}
            self.assertEqual(collect(db,fetch),1)
            self.assertEqual(latest(db),dict(tracked=1,views=0,likes=None,comments=None,shares=2))
            self.assertEqual(analyze(db)['clips'],[])
    def test_empty(self):
        with closing(connect(':memory:')) as db:
            self.assertEqual(collect(db,lambda _:self.fail('unexpected request')),0)
            self.assertIsNone(latest(db)['views'])
