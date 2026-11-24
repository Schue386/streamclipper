from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from stream_monitor import connect,add_channel
from control import set_enabled
from supervisor import Supervisor
from service import cycle

class ServiceTests(unittest.TestCase):
    def test_poll_queue_and_preview_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            with closing(connect(str(Path(tmp)/'db.sqlite'))) as db:
                add_channel(db,'zarbex')
                set_enabled(db,True)
                supervisor=Supervisor(db,execute=False)
                sample=Path(tmp)/'streams.json'
                sample.write_text('[{"id":"live001","user_login":"zarbex","title":"test"}]')
                first=cycle(db,supervisor,demo_file=str(sample))
                self.assertEqual((first['poll'],first['queued']),('ok',1))
                self.assertEqual(first['supervisor']['launched'],[])
                second=cycle(db,supervisor,demo_file=str(sample))
                self.assertEqual(second['queued'],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM clip_jobs').fetchone()[0],1)

    def test_poll_failure_does_not_crash_supervision(self):
        with tempfile.TemporaryDirectory() as tmp:
            with closing(connect(str(Path(tmp)/'db.sqlite'))) as db:
                supervisor=Supervisor(db,execute=False)
                result=cycle(db,supervisor,demo_file=str(Path(tmp)/'missing.json'))
                self.assertEqual(result['poll'],'failed')
                self.assertIn('supervisor',result)

    def test_skipped_poll(self):
        with tempfile.TemporaryDirectory() as tmp:
            with closing(connect(str(Path(tmp)/'db.sqlite'))) as db:
                supervisor=Supervisor(db,execute=False)
                result=cycle(db,supervisor,poll=False)
                self.assertEqual(result['poll'],'skipped')
