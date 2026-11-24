import unittest
from contextlib import closing
from datetime import datetime,timezone
from stream_monitor import connect
from performance import analyze
class PerformanceTests(unittest.TestCase):
    def test_empty(self):
        with closing(connect(':memory:')) as db: self.assertEqual(analyze(db),{'clips':[],'channels':[]})
    def test_age_and_private_gate(self):
        with closing(connect(':memory:')) as db:
            db.execute("INSERT INTO streams(platform,stream_id,channel,detected_at,last_seen_at) VALUES('twitch','s','demo','x','x')")
            for i in range(7):
                db.execute("INSERT INTO clips(clip_id,platform,stream_id,created_at) VALUES(?,'twitch','s','x')",(str(i),))
                p=db.execute("INSERT INTO publications(clip_id,destination,status,published_at) VALUES(?,'youtube',?,'2026-11-20T00:00:00+00:00')",(str(i),'published' if i<6 else 'uploaded_private')).lastrowid
                db.execute("INSERT INTO metrics(publication_id,captured_at,views,likes,comments) VALUES(?,?,?,?,?)",(p,'2026-11-22T00:00:00+00:00' if i!=5 else '2026-11-20T01:00:00+00:00',200,10,None))
            r=analyze(db,datetime(2026,11,23,tzinfo=timezone.utc));self.assertEqual(len(r['clips']),5);self.assertEqual(r['channels'][0]['median_views_per_day'],100);self.assertEqual(r['channels'][0]['recommendation'],'review_priority');self.assertIsNone(r['clips'][0]['engagement'])
