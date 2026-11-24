import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from stream_monitor import connect, add_channel, record_live
from clip_jobs import prepare as prepare_jobs, enqueue_detected
from clip_importer import import_job, import_completed
from clip_catalog import prepare as prepare_catalog, summary

class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(str(self.root / 'db.sqlite'))
        add_channel(self.db, 'example')
        record_live(self.db, [{'id':'s1','user_login':'example'}])
        prepare_jobs(self.db)
        prepare_catalog(self.db)
        enqueue_detected(self.db)
        self.out = self.root / 'output'
        self.out.mkdir()
        self.mp4 = self.out / 'clip.mp4'
        self.mp4.write_bytes(b'not-a-real-video')
        self.db.execute('UPDATE clip_jobs SET output_dir=? WHERE id=1',(str(self.out),))
        self.db.commit()
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def test_not_completed_never_imported(self):
        self.assertEqual(import_job(self.db, 1)['status'], 'skipped')
        self.assertEqual(summary(self.db)['clips'], 0)
    def test_completed_imports_but_unverified_blocks_upload(self):
        self.db.execute("UPDATE clip_jobs SET status='completed' WHERE id=1")
        self.db.commit()
        with patch('clip_catalog.probe_mp4', return_value={}):
            result = import_job(self.db, 1)
            self.assertEqual(result['registered'], 1)
            self.assertEqual(result['unverified'], 1)
            self.assertEqual(import_job(self.db, 1)['already_known'], 1)
        self.assertEqual(summary(self.db)['quality_passed'], 0)
        self.assertEqual(summary(self.db)['uploads_ready'], 0)
    def test_missing_output_and_batch(self):
        self.db.execute("UPDATE clip_jobs SET status='completed',output_dir=NULL WHERE id=1")
        self.db.commit()
        self.assertEqual(import_completed(self.db)[0]['reason'], 'no output directory')
    def test_unknown_job(self):
        with self.assertRaises(ValueError):import_job(self.db,999)
