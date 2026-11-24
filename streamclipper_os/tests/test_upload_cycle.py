import unittest
from unittest.mock import Mock, patch
from test_youtube_worker import WorkerTests
from service import cycle
from control import set_enabled

class UploadCycleTests(WorkerTests):
    def test_cycle_gate_and_publication(self):
        self.ready()
        supervisor=Mock(); sender=Mock(return_value={'video_id':'abcdefghijk'})
        set_enabled(self.db,False)
        self.assertEqual(cycle(self.db,supervisor,poll=False,upload=True,sender=sender)['youtube']['status'],'disabled')
        set_enabled(self.db,True)
        self.assertEqual(cycle(self.db,supervisor,poll=False,sender=sender)['youtube']['status'],'disabled')
        sender.assert_not_called()
        self.assertEqual(cycle(self.db,supervisor,poll=False,upload=True,sender=sender)['youtube']['status'],'uploaded')
        self.assertEqual(self.db.execute('SELECT remote_id,status FROM publications').fetchone(),('abcdefghijk','uploaded_private'))
        self.assertEqual(cycle(self.db,supervisor,poll=False,upload=True,sender=sender)['youtube']['status'],'empty')
        sender.assert_called_once()
