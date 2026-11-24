import unittest
from vod_import import canonical_url,plan
class VodTests(unittest.TestCase):
    def test_link(self):
        self.assertEqual(canonical_url('https://youtu.be/OABmyAGqMCQ?is=abc'),'https://www.youtube.com/watch?v=OABmyAGqMCQ')
    def test_invalid(self):
        for url in ['https://youtube.com.evil.test/watch?v=OABmyAGqMCQ','file:///etc/passwd']:
            with self.assertRaises(ValueError):canonical_url(url)
    def test_plan(self):
        d,r,f=plan('https://youtu.be/OABmyAGqMCQ',60,900,'work')
        self.assertEqual(d[d.index('--end')+1],'960');self.assertIn('--no-draft',r);self.assertEqual(r[r.index('--min-dur')+1],'61')
    def test_bounds(self):
        with self.assertRaises(ValueError):plan('https://youtu.be/OABmyAGqMCQ',0,3600,'work')
