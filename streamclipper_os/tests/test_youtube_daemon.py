import unittest
from unittest.mock import Mock, patch
from youtube_daemon import tick
class DaemonTests(unittest.TestCase):
    @patch('youtube_daemon.run_next')
    @patch('youtube_daemon.enabled',return_value=True)
    def test_default_disabled(self,enabled,run):
        self.assertEqual(tick(Mock()),{'status':'disabled'})
        run.assert_not_called()
    @patch('youtube_daemon.run_next')
    @patch('youtube_daemon.enabled',return_value=False)
    def test_stop(self,enabled,run):
        self.assertEqual(tick(Mock(),True),{'status':'disabled'})
        run.assert_not_called()
    @patch('youtube_daemon.run_next',return_value={'status':'uploaded'})
    @patch('youtube_daemon.enabled',return_value=True)
    def test_dispatch(self,enabled,run):
        db=Mock(); sender=Mock()
        self.assertEqual(tick(db,True,sender)['status'],'uploaded')
        run.assert_called_once_with(db,execute=True,sender=sender)
