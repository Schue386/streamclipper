import unittest
from stream_monitor import connect
from kick_monitor import add_channel,check
class KickTest(unittest.TestCase):
    def test_lifecycle(self):
        db=connect(':memory:'); self.addCleanup(db.close); add_channel(db,'https://kick.com/test')
        row={'slug':'test','stream_title':'Gaming','stream':{'is_live':True,'start_time':'2026-11-24T00:00:00Z'}}
        getter=lambda *a:[row]
        self.assertEqual(len(check(db,getter)),1); self.assertEqual(check(db,getter),[])
        row['stream']['is_live']=False; check(db,getter)
        self.assertEqual(db.execute('SELECT status FROM streams').fetchone()[0],'offline')
    def test_empty(self):
        db=connect(':memory:'); self.addCleanup(db.close)
        self.assertEqual(check(db,lambda *a:self.fail('unexpected fetch')),[])
