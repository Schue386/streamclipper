import unittest
from unittest.mock import patch
from stream_monitor import connect
from learning import recommend

class LearningTest(unittest.TestCase):
    def test_empty(self):
        with connect(':memory:') as db:
            self.assertIsNone(recommend(db)['youtube']['length']['preferred'])
    def test_platform_separation_and_minimum(self):
        db=connect(':memory:')
        self.addCleanup(db.close)
        db.execute("INSERT INTO streams(platform,stream_id,channel,detected_at,last_seen_at) VALUES('twitch','s','c','x','x')")
        clips=[]
        for i in range(10):
            cid=str(i)
            db.execute("INSERT INTO clips(clip_id,platform,stream_id,duration_seconds,created_at) VALUES(?,'twitch','s',?,'x')",(cid,65 if i<5 else 85))
            db.execute("INSERT INTO publications(clip_id,destination,published_at) VALUES(?,'youtube','2026-11-20T12:00:00+00:00')",(cid,))
            clips.append(dict(clip_id=cid,views_per_day=200 if i<5 else 100))
        with patch('learning.analyze',side_effect=[{'clips':clips},{'clips':[]}]):
            result=recommend(db)
        self.assertEqual(result['youtube']['length']['preferred'],'61–70 s')
        self.assertIsNone(result['youtube']['time_utc']['preferred'])
        self.assertIsNone(result['tiktok']['length']['preferred'])
