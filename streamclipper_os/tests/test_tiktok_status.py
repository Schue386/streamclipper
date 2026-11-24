import unittest, sqlite3
from unittest.mock import patch
from tiktok_status import refresh, overview
class StatusTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE tiktok_attempts(queue_id INTEGER, publish_id TEXT,state TEXT,updated_at TEXT)')
        self.db.execute("INSERT INTO tiktok_attempts VALUES(1,'p','inbox_sent','old')")
        self.db.commit()
        p=patch('tiktok_status.prepare'); p.start(); self.addCleanup(p.stop)
    def test_missing_token(self):
        self.assertEqual(refresh(self.db,None)['status'],'authorization_required')
    def test_inbox_then_complete(self):
        for state in ('SEND_TO_USER_INBOX','PUBLISH_COMPLETE'):
            self.assertEqual(refresh(self.db,'token',lambda *a:{'status':state})['checked'],1)
            self.assertEqual(overview(self.db)[0]['state'],state)
        self.assertEqual(refresh(self.db,'token',lambda *a: self.fail())['checked'],0)
    def test_unknown_preserves(self):
        self.assertEqual(refresh(self.db,'token',lambda *a:{'status':'unknown'})['errors'],1)
        self.assertEqual(overview(self.db)[0]['state'],'inbox_sent')
