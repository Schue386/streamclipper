import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from http.server import ThreadingHTTPServer
from dashboard import make_handler, snapshot, render

class DashboardTests(unittest.TestCase):
    def test_snapshot_and_escaping(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'test.db')
            data=snapshot(path)
            self.assertFalse(data['autopilot_enabled'])
            self.assertIn(b'StreamClipper OS',render(data))
            self.assertIn(b'&lt;script&gt;',render(data,'<script>'))

    def test_http_origin_gate_and_control(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'test.db')
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(path))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                def request(method, endpoint, body=None, headers=None):
                    conn=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                    conn.request(method,endpoint,body,headers or {})
                    resp=conn.getresponse()
                    result=(resp.status,resp.read())
                    conn.close()
                    return result
                self.assertEqual(request('GET','/')[0],200)
                self.assertEqual(request('GET','/missing')[0],404)
                body='action=start'
                base={'Content-Type':'application/x-www-form-urlencoded'}
                self.assertEqual(request('POST','/control',body,base)[0],403)
                base['Origin']=f'http://127.0.0.1:{server.server_port}'
                self.assertEqual(request('POST','/control',body,base)[0],303)
                self.assertTrue(snapshot(path)['autopilot_enabled'])
                self.assertEqual(request('POST','/control','action=stop',base)[0],303)
                self.assertFalse(snapshot(path)['autopilot_enabled'])
                self.assertEqual(request('POST','/control','action=delete',base)[0],400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
