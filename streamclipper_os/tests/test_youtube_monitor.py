import unittest
from stream_monitor import connect
from youtube_monitor import add_channel,check,_last_poll
class YouTubeMonitorTest(unittest.TestCase):
    def setUp(self):
        _last_poll.clear(); self.db=connect(':memory:'); self.addCleanup(self.db.close)
        self.channel='UC'+'a'*22; add_channel(self.db,self.channel)
    def test_lifecycle(self):
        row={'id':{'videoId':'abcdefghijk'},'snippet':{'channelId':self.channel,'liveBroadcastContent':'live','title':'Gaming'}}
        self.assertEqual(len(check(self.db,lambda *a:[row],clock=lambda:0)),1)
        self.assertEqual(check(self.db,lambda *a:self.fail('quota'),clock=lambda:1),[])
        self.assertEqual(check(self.db,lambda *a:[row],clock=lambda:3600),[])
        check(self.db,lambda *a:[],clock=lambda:7200)
        self.assertEqual(self.db.execute('SELECT status FROM streams').fetchone()[0],'offline')
    def test_failure_preserves_live(self):
        row={'id':{'videoId':'abcdefghijk'},'snippet':{'channelId':self.channel,'liveBroadcastContent':'live'}}
        check(self.db,lambda *a:[row],clock=lambda:0)
        with self.assertRaises(ValueError): check(self.db,lambda *a:[{}] if False else [{'id':{'videoId':'bad'},'snippet':{'channelId':self.channel}}],clock=lambda:3600)
        self.assertEqual(self.db.execute('SELECT status FROM streams').fetchone()[0],'live')
