import tempfile
import unittest
from pathlib import Path
from stream_monitor import connect,add_channel,record_live
from autopilot import enqueue_events
from supervisor import Supervisor,reconcile_after_restart
from control import set_enabled

class FakeProc:
    pid=43210
    returncode=None
    def __init__(self):self.code=None
    def poll(self):return self.code
    def terminate(self):self.code=-15;self.returncode=-15
    def wait(self,timeout=None):return self.code
    def kill(self):self.code=-9;self.returncode=-9

class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=connect(str(Path(self.tmp.name)/'db.sqlite'));self.addCleanup(self.db.close)
        add_channel(self.db,'streamer')
        events=record_live(self.db,[{'id':'one','user_login':'streamer','title':'test'}])
        enqueue_events(self.db,events)
        set_enabled(self.db,True)

    def test_preview_does_not_start_process(self):
        sup=Supervisor(self.db,output_root=self.tmp.name,execute=False)
        self.assertEqual(sup.tick()['launched'],[])
        self.assertEqual(self.db.execute('SELECT status FROM clip_jobs').fetchone()[0],'queued')

    def test_launch_then_complete_without_duplicate(self):
        proc=FakeProc();commands=[]
        def fake_spawn(cmd,**kwargs):commands.append(cmd);return proc
        sup=Supervisor(self.db,output_root=self.tmp.name,execute=True,popen_factory=fake_spawn)
        self.assertEqual(len(sup.tick()['launched']),1)
        self.assertEqual(sup.tick()['launched'],[])
        proc.code=0;proc.returncode=0
        result=sup.tick()
        self.assertEqual(result['finished'][0]['status'],'completed')
        self.assertEqual(self.db.execute('SELECT status FROM clip_jobs').fetchone()[0],'completed')
        self.assertEqual(len(commands),1)

    def test_shutdown_marks_failed(self):
        proc=FakeProc()
        sup=Supervisor(self.db,output_root=self.tmp.name,execute=True,popen_factory=lambda *a,**kw:proc)
        sup.tick();sup.shutdown()
        self.assertEqual(self.db.execute('SELECT status FROM clip_jobs').fetchone()[0],'failed')
        self.assertEqual(proc.returncode,-15)

    def test_recovery_never_requeues(self):
        proc=FakeProc()
        sup=Supervisor(self.db,output_root=self.tmp.name,execute=True,popen_factory=lambda *a,**kw:proc)
        sup.tick()
        self.assertEqual(reconcile_after_restart(self.db),1)
        self.assertEqual(self.db.execute('SELECT status FROM clip_jobs').fetchone()[0],'failed')
        # Mock process remains alive to simulate unknown orphan: no duplicate launch.
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM clip_jobs WHERE status='queued'").fetchone()[0],0)
        sup.shutdown()

    def test_invalid_worker_limit(self):
        with self.assertRaises(ValueError):Supervisor(self.db,max_workers=0)
