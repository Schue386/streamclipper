from contextlib import closing
import tempfile,unittest
from pathlib import Path
from stream_monitor import connect
from control import enabled,set_enabled,status
from supervisor import Supervisor

class ControlTests(unittest.TestCase):
    def test_default_off_and_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            with closing(connect(str(Path(tmp)/'test.sqlite'))) as db:
                self.assertFalse(enabled(db))
                set_enabled(db,True)
                self.assertTrue(status(db)['autopilot_enabled'])
                set_enabled(db,False)
                self.assertFalse(enabled(db))
    def test_supervisor_respects_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            with closing(connect(str(Path(tmp)/'test.sqlite'))) as db:
                supervisor=Supervisor(db,execute=True)
                self.assertFalse(supervisor.tick()['enabled'])
                self.assertEqual(supervisor.tick()['launched'],[])
