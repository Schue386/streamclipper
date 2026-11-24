"""Public health endpoint; authenticated read-only dashboard for Render pilot."""
import os, hmac, base64, json, shutil, subprocess, sys, threading, tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer
from dashboard import make_handler, snapshot, render

def handler(db, password):
    if len(password)<10: raise ValueError('Dashboard password must have at least 10 characters')
    expected='Basic '+base64.b64encode(('admin:'+password).encode()).decode()
    lock=threading.Lock()
    result={'status':'not_run'}
    def pilot():
        try:
            with tempfile.TemporaryDirectory() as d:
                source=str(Path(d)/'test.mp4')
                subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=24','-t','75','-c:v','libx264','-threads','1','-preset','ultrafast',source],check=True,timeout=300)
                code='import json,sys; from render_benchmark import benchmark; print(json.dumps(benchmark(sys.argv[1],bounded=False)))'
                output=subprocess.check_output([sys.executable,'-c',code,source],cwd=str(Path(__file__).resolve().parent),timeout=660)
                result.update(status='passed',measurement=json.loads(output))
        except Exception as exc:
            result.update(status='failed',error=type(exc).__name__)
        finally: lock.release()
    class Handler(make_handler(db)):
        def do_GET(self):
            if self.path=='/health':
                return self.respond(200,b'{"status":"ok"}','application/json')
            if not hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode()):
                self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="StreamClipper"'); self.send_header('Content-Length','0'); self.end_headers(); return
            if self.path=='/pilot':
                return self.respond(200,json.dumps(result).encode(),'application/json')
            if self.path=='/resources':
                memory={}
                for key in ('memory.current','memory.max'):
                    try:
                        with open('/sys/fs/cgroup/'+key) as f: memory[key]=f.read().strip()
                    except OSError: memory[key]=None
                return self.respond(200,json.dumps({'memory':memory,'disk_free':shutil.disk_usage('/tmp').free,'storage':'ephemeral','recording':False}).encode(),'application/json')
            if self.path!='/': return self.respond(404,b'Not found')
            page=render(snapshot(db)).replace(b'Lokales Dashboard \xc2\xb7 keine Cloud-Verbindung',b'Render Pilot - nur lesend, fluechtiger Speicher').replace(b'<form method="post"',b'<form hidden method="post"')
            page=page.replace(b'</body>',b'<p><a href="/pilot">Pilot-Ergebnis</a> | <a href="/resources">Ressourcen</a></p><form method="post" action="/pilot"><button>75-Sekunden-Test starten</button></form></body>')
            return self.respond(200,page)
        def do_POST(self):
            if self.path!='/pilot': return self.respond(405,b'Read-only pilot')
            if not hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode()): return self.respond(401,b'Unauthorized')
            if self.headers.get('X-Pilot-Confirm')!='synthetic-only' and not (self.headers.get('Origin') and self.headers.get('Origin')=='https://'+os.environ.get('RENDER_EXTERNAL_HOSTNAME','invalid') and self.headers.get('Sec-Fetch-Site')=='same-origin'): return self.respond(403,b'Confirmation required')
            if not lock.acquire(blocking=False): return self.respond(409,b'Pilot running')
            result.clear(); result.update(status='running')
            threading.Thread(target=pilot,daemon=True).start()
            return self.respond(202,b'Pilot started')
    return Handler

if __name__=='__main__':
    os.umask(0o077)
    with ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','10000'))),handler('/tmp/streamclipper.sqlite3',os.environ['DASHBOARD_PASSWORD'])) as server:
        server.serve_forever()
