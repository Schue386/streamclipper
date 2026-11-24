import unittest,tempfile,json,os
from pathlib import Path
from oauth_refresh import access_token
class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name)/'token.json';self.p.write_text(json.dumps(dict(platform='tiktok',access_token='old',refresh_token='r',expires_at=0)));self.p.chmod(0o600)
    def test_rotation_and_cache(self):
        def fetch(p,f):
            self.assertEqual(f['client_key'],'id');return dict(access_token='new',refresh_token='rotated',expires_in=3600)
        self.assertEqual(access_token('tiktok',self.p,'id','secret',fetch,lambda:100),'new')
        self.assertEqual(json.loads(self.p.read_text())['refresh_token'],'rotated')
        self.assertEqual(access_token('tiktok',self.p,'id','secret',lambda *a:self.fail(),lambda:100),'new')
    def test_failure_preserves_state(self):
        before=self.p.read_bytes()
        with self.assertRaises(RuntimeError): access_token('tiktok',self.p,'id','secret',lambda *a:{'error':'invalid_grant'})
        self.assertEqual(before,self.p.read_bytes())
    def test_permissions(self):
        self.p.chmod(0o644)
        with self.assertRaises(PermissionError): access_token('tiktok',self.p,'id','secret')
