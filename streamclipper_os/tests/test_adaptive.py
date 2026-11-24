import unittest
from unittest.mock import patch
from contextlib import closing
from datetime import datetime,timezone
from stream_monitor import connect,add_channel,record_live
from clip_jobs import prepare,enqueue_detected,plan_next
from adaptive import choose
class AdaptiveTests(unittest.TestCase):
    def test_priority_and_fairness(self):
        with closing(connect(':memory:')) as db:
            prepare(db)
            for c in ('new','pro'):
                add_channel(db,c);record_live(db,[{'id':c,'user_login':c}])
            enqueue_detected(db)
            rows=db.execute('SELECT id,channel,stream_id,platform,created_at FROM clip_jobs ORDER BY id').fetchall()
            with patch('adaptive.analyze',return_value={'channels':[{'platform':'twitch','channel':'pro','samples':5,'median_views_per_day':100}]}):
                self.assertEqual(choose(db,rows)[1],'new')
                db.execute('UPDATE clip_jobs SET attempts=1 WHERE id=1')
                self.assertEqual(choose(db,rows)[1],'pro')
                db.execute('UPDATE streamers SET enabled=0 WHERE channel="pro"')
                self.assertEqual(plan_next(db)['channel'],'new')
                db.execute('UPDATE streams SET status="offline"')
                self.assertIsNone(plan_next(db))
    def test_oldest_and_small_sample(self):
        with closing(connect(':memory:')) as db:
            prepare(db)
            rows=[(1,'new','a','twitch','2026-11-24T00:00:00+00:00'),(2,'pro','b','twitch','2026-11-24T00:01:00+00:00')]
            with patch('adaptive.analyze',return_value={'channels':[]}):
                self.assertEqual(choose(db,rows,datetime(2026,11,24,1,tzinfo=timezone.utc)),rows[0])
