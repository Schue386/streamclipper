from contextlib import closing
import json
import tempfile
import unittest
from pathlib import Path
from stream_monitor import connect, add_channel, check

class StreamMonitorTests(unittest.TestCase):
    def test_new_stream_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            demo = Path(folder)/'live.json'
            demo.write_text(json.dumps([{'id':'123','user_login':'zarbex','title':'GTA 6','started_at':'2026-11-23T20:00:00Z'},
                                        {'id':'999','user_login':'other','title':'ignore'}]))
            with closing(connect(str(Path(folder)/'db.sqlite'))) as db:
                add_channel(db,'zarbex')
                self.assertEqual(len(check(db,str(demo))),1)
                self.assertEqual(len(check(db,str(demo))),0)
                self.assertEqual(db.execute('SELECT count(*) FROM streams').fetchone()[0],1)
    def test_invalid_channel(self):
        with closing(connect(':memory:')) as db:
            with self.assertRaises(ValueError): add_channel(db,'a/b')

if __name__ == '__main__': unittest.main()
