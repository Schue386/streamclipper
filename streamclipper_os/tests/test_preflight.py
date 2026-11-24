import tempfile, unittest
from types import SimpleNamespace
from preflight import inspect
class PreflightTests(unittest.TestCase):
    def test_ready_no_secret(self):
        with tempfile.TemporaryDirectory() as d:
            r=inspect(d,environ={'KICK_ACCESS_TOKEN':'secret'},which=lambda x:x,disk=lambda p:SimpleNamespace(free=30*1024**3))
            self.assertTrue(r['local_ready']); self.assertTrue(r['credentials_present']['kick']); self.assertNotIn('secret',str(r)); self.assertFalse(r['live_verified'])
    def test_missing_tools(self):
        with tempfile.TemporaryDirectory() as d:
            r=inspect(d,environ={},which=lambda x:None,disk=lambda p:SimpleNamespace(free=0))
            self.assertFalse(r['local_ready']); self.assertFalse(r['checks']['disk_minimum'])
