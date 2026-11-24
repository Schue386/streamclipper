from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from stream_monitor import connect, add_channel, record_live
from clip_jobs import prepare, enqueue_detected, run_next

class ClipJobsTests(unittest.TestCase):
    def test_one_job_per_stream_and_safe_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(connect(str(Path(folder) / "test.db"))) as db:
                add_channel(db, "zarbex")
                record_live(db, [{"id":"stream-001","user_login":"zarbex","title":"GTA"}])
                prepare(db)
                self.assertEqual(enqueue_detected(db), 1)
                self.assertEqual(enqueue_detected(db), 0)
                result = run_next(db)
                self.assertEqual(result["status"], "preview")
                self.assertEqual(result["command"][0:2], ["streamclipper","live"])
                self.assertEqual(db.execute("SELECT status FROM clip_jobs").fetchone()[0], "queued")

    def test_empty_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(connect(str(Path(folder) / "test.db"))) as db:
                prepare(db)
                self.assertEqual(run_next(db), {"status":"empty"})
