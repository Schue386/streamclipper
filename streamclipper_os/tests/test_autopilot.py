from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from stream_monitor import connect,add_channel
from autopilot import cycle,summary,enqueue_events

class AutopilotTests(unittest.TestCase):
    def test_new_stream_once_and_preview_only(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(connect(str(Path(folder)/'test.sqlite'))) as db:
                add_channel(db,'zarbex')
                demo=Path(folder)/'live.json'
                demo.write_text('[{"id":"live-99","user_login":"zarbex","title":"Game"}]')
                first=cycle(db,demo_file=str(demo))
                self.assertEqual((first['detected'],first['queued']),(1,1))
                self.assertEqual(first['job']['status'],'preview')
                second=cycle(db,demo_file=str(demo))
                self.assertEqual((second['detected'],second['queued']),(0,0))
                self.assertEqual(summary(db)['jobs']['queued'],1)
    def test_disabled_channel_never_queues(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(connect(str(Path(folder)/'test.sqlite'))) as db:
                add_channel(db,'zarbex')
                db.execute('UPDATE streamers SET enabled=0')
                self.assertEqual(enqueue_events(db,[{'platform':'twitch','channel':'zarbex','stream_id':'x'}]),0)
    def test_poll_error_does_not_start_job(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(connect(str(Path(folder)/'test.sqlite'))) as db:
                with patch('autopilot.check',side_effect=RuntimeError('network error')):
                    with patch('autopilot.run_next') as run:
                        with self.assertRaises(RuntimeError):cycle(db,execute=True)
                        run.assert_not_called()
    def test_no_jobs_no_uploads(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(connect(str(Path(folder)/'test.sqlite'))) as db:
                add_channel(db,'zarbex')
                demo=Path(folder)/'empty.json';demo.write_text('[]')
                result=cycle(db,demo_file=str(demo))
                self.assertEqual(result['job'],{'status':'empty'})
                self.assertEqual(result['summary']['publications'],{})
