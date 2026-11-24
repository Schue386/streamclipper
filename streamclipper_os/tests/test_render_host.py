import unittest, tempfile, threading, urllib.request, urllib.error, base64
from http.server import ThreadingHTTPServer
from render_host import handler
class RenderTest(unittest.TestCase):
    def test_http(self):
        with tempfile.TemporaryDirectory() as d:
            with ThreadingHTTPServer(('127.0.0.1',0),handler(d+'/test.db','a'*24)) as s:
                t=threading.Thread(target=s.serve_forever); t.start()
                try:
                    url=f'http://127.0.0.1:{s.server_port}'
                    self.assertEqual(urllib.request.urlopen(url+'/health').status,200)
                    with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(url+'/')
                    self.assertEqual(e.exception.code,401)
                    auth='Basic '+base64.b64encode(('admin:'+'a'*24).encode()).decode()
                    self.assertEqual(urllib.request.urlopen(urllib.request.Request(url+'/',headers={'Authorization':auth})).status,200)
                    with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(urllib.request.Request(url+'/control',data=b'action=start',headers={'Authorization':auth}))
                    self.assertEqual(e.exception.code,405)
                finally: s.shutdown(); t.join()
