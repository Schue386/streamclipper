import unittest
from pathlib import Path
from stream_monitor import connect

ROOT=Path(__file__).resolve().parents[1]

class DeploymentTests(unittest.TestCase):
    def test_dashboard_is_loopback_only(self):
        dashboard=(ROOT/'dashboard.py').read_text()
        self.assertIn("('127.0.0.1',args.port)",dashboard)

    def test_service_starts_without_recording_permission(self):
        unit=(ROOT/'deploy/streamclipper-os.service').read_text()
        self.assertIn('ExecStart=',unit)
        self.assertNotIn('--execute',next(line for line in unit.splitlines() if line.startswith('ExecStart=')))
        self.assertIn('User=streamclipper',unit)

    def test_dashboard_service_is_not_public(self):
        unit=(ROOT/'deploy/streamclipper-dashboard.service').read_text()
        self.assertIn('dashboard.py',unit)
        self.assertNotIn('--host 0.0.0.0',unit)

    def test_sqlite_busy_timeout(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            db=connect(str(Path(d)/'db.sqlite'))
            try:
                self.assertEqual(db.execute('PRAGMA busy_timeout').fetchone()[0],30000)
            finally:
                db.close()
