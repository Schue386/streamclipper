"""Public health endpoint; authenticated read-only dashboard for Render pilot."""
import os, hmac, base64, json, shutil, subprocess, sys, threading, tempfile, time
from html import escape
from runtime_recovery import (read_state, heartbeat_path, clear_confirmation,
                              recovery_form, handle_recovery_request)
import secrets
from pathlib import Path
from resource_guard import start_metrics, read_metrics, display
from http.server import ThreadingHTTPServer
from dashboard import make_handler, snapshot, render
from streamer_http import handle_streamers

def monitoring_command(production=False):
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "service.py"),
        "--db", "/tmp/streamclipper.sqlite3",
        "--out", "/tmp/live_clips",
        "--workers", "1",
        "--interval", "120",
        "--persistent-history"
    ]

    if production:
        command.extend(["--execute", "--timeout", "2700"])

    return command


def handler(db, password, production=False, monitoring=None, launched_at=None):
    def monitor_state():
        return read_state(db, monitoring, launched_at)
    def health_status():
        state=monitor_state()
        return (200 if state["phase"] in ("running","starting") else 503), state
    if len(password)<10: raise ValueError('Dashboard password must have at least 10 characters')
    expected='Basic '+base64.b64encode(('admin:'+password).encode()).decode()
    recovery_csrf = secrets.token_urlsafe(32)
    lock=threading.Lock()
    result={'status':'not_run'}
    preview=Path(tempfile.mkdtemp(prefix='sc-preview-'))/'clip.mp4'
    def pilot(upload=None):
        stop=threading.Event(); samples=[]
        def monitor():
            while not stop.is_set():
                try: samples.append(int(Path('/sys/fs/cgroup/memory.current').read_text()))
                except (OSError,ValueError): pass
                stop.wait(0.25)
        watcher=threading.Thread(target=monitor,daemon=True); watcher.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                source=upload or str(Path(d)/'test.mp4')
                if upload is None: subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=24','-t','75','-c:v','libx264','-threads','1','-preset','ultrafast',source],check=True,timeout=300)
                code='import json,sys,os; from render_benchmark import benchmark; from highlight_scan import scan; r=benchmark(sys.argv[1],bounded=False); r["render_cloud_tested"]=bool(os.environ.get("RENDER_EXTERNAL_HOSTNAME")); r["highlights"]=scan(sys.argv[1]) if sys.argv[2]=="1" else None; print(json.dumps(r))'
                output=subprocess.check_output([sys.executable,'-c',code,source,'1' if upload else '0'],cwd=str(Path(__file__).resolve().parent),timeout=1100)
                measurement=json.loads(output)
                if upload:
                    code='import json,sys,os; from clip_preview import produce; print(json.dumps(produce(sys.argv[1],sys.argv[2],json.loads(sys.argv[3]),os.environ.get("ASR_MODEL_DIR"))))'
                    raw=subprocess.check_output([sys.executable,'-c',code,source,str(preview),json.dumps(measurement['highlights']['candidates'])],cwd=str(Path(__file__).resolve().parent),timeout=1600)
                    measurement['preview']=json.loads(raw)
                    measurement['asr']=measurement['preview']['asr']
                    measurement['asr_tested']=bool(measurement['asr'] and 'captions' in measurement['asr'])
                result.update(status='passed',measurement=measurement)
        except Exception as exc:
            preview.unlink(missing_ok=True)
            result.update(status='failed',error=type(exc).__name__)
        finally:
            stop.set(); watcher.join()
            result["sampled_cgroup_peak_bytes"]=max(samples,default=None)
            if upload is not None: Path(upload).unlink(missing_ok=True)
            lock.release()
    class Handler(make_handler(db)):
        def do_HEAD(self):
            if self.path.split("?",1)[0] == "/health":
                code, _ = health_status()
                self.send_response(code)
                self.send_header("Content-Type","application/json")
                self.send_header("Cache-Control","no-store")
                self.end_headers()
            else:
                self.send_response(405)
                self.end_headers()
        def do_GET(self):
            self.path=self.path.split("?",1)[0]
            if self.path=='/health':
                code,state=health_status()
                return self.respond(code,json.dumps({'status':'ok' if code==200 else 'unhealthy',
                    'monitor':state['phase'],'recording':state['recording']}).encode(),'application/json')
            if not hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode()):
                self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="StreamClipper"'); self.send_header('Content-Length','0'); self.end_headers(); return
            if self.path=='/clip.mp4':
                if result.get('status')!='passed' or not result.get('measurement',{}).get('preview') or not preview.is_file(): return self.respond(404,b'No preview')
                try:
                    with preview.open('rb') as f:
                        self.send_response(200); self.send_header('Content-Type','video/mp4'); self.send_header('Content-Length',str(os.fstat(f.fileno()).st_size)); self.send_header('Content-Disposition','attachment; filename="streamclipper-preview.mp4"'); self.send_header('Cache-Control','no-store'); self.end_headers(); shutil.copyfileobj(f,self.wfile,65536)
                except (OSError,ConnectionError): pass
                return
            if self.path=='/pilot':
                return self.respond(200,json.dumps(result).encode(),'application/json')
            if self.path=='/resources':
                memory={}
                for key in ('memory.current','memory.max'):
                    try:
                        with open('/sys/fs/cgroup/'+key) as f: memory[key]=f.read().strip()
                    except OSError: memory[key]=None
                return self.respond(200,json.dumps({'memory':memory,'disk_free':shutil.disk_usage('/tmp').free,'storage':'ephemeral','recording':None if production else False,'production_enabled':production,'telemetry':read_metrics(db)}).encode(),'application/json')
            if self.path!='/': return self.respond(404,b'Not found')
            data = snapshot(db)
            from source_rights import approved_channels
            from turso_store import StoreError

            state = monitor_state()
            labels = {'running':'aktiv','starting':'startet','stopped':'ausgefallen','stale':'keine aktuelle Rückmeldung'}
            modes = {'enabled':'freigegeben','blocked':'gesperrt','disabled':'deaktiviert'}
            message = 'Überwachung: '+labels.get(state['phase'],'unbekannt')+' | Aufnahme: '+modes.get(state['recording'],'unbekannt')
            if state.get('reason'):
                message += ' | '+str(state['reason'])
            if state.get('unresolved'):
                message += ' | Alte Aufträge: '+', '.join('#'+str(x['job_id'])+' '+x['channel']+' (Bestätigung: '+x['key']+')' for x in state['unresolved'])
            try:
                allowed = approved_channels()
                for item in data["streamers"]:
                    if item["platform"] == "twitch":
                        item["recording_approved"] = (
                            item["channel"] in allowed
                        )
            except StoreError:
                message += " | Aufnahmefreigaben nicht abrufbar"

            message += ' | ' + display(db)
            if production:
                message += ' | RAM-Sparprofil: Audio-Hoehepunkte ohne Live-ASR'
            page=render(data, message).replace(b'Lokales Dashboard \xc2\xb7 keine Cloud-Verbindung',b'Render Cloud - fluechtiger Speicher').replace(b'<form method="post" action="/control"',b'<form hidden method="post" action="/control"')
            page=page.replace(b'</body>',b'<p><a href="/pilot">Pilot-Ergebnis</a> | <a href="/clip.mp4">Clip herunterladen</a> | <a href="/resources">Ressourcen</a></p><form method="post" action="/pilot"><button>75-Sekunden-Test starten</button></form></body>')
            if production:
                form = recovery_form(db, state, recovery_csrf)
                if form:
                    page = page.replace(b'<div class="grid">', form+b'<div class="grid">', 1)
            return self.respond(200,page)
        def do_POST(self):
            if self.path not in ('/pilot','/video-test','/streamers','/recover-recording'): return self.respond(405,b'Read-only pilot')
            if not hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode()): return self.respond(401,b'Unauthorized')
            if self.path=='/streamers': return handle_streamers(self, db)
            if self.path=='/recover-recording':
                if not production: return self.respond(409,b'Production disabled')
                return handle_recovery_request(self, db, monitor_state, recovery_csrf)

            if production and self.path in (
                "/pilot", "/video-test"
            ):
                return self.respond(
                    409,b"Production mode active"
                )

            if self.path=='/video-test':
                if self.headers.get('X-Pilot-Confirm')!='video-only': return self.respond(403,b'Confirmation required')
                if self.headers.get('Content-Type')!='video/mp4' or self.headers.get('Transfer-Encoding'): return self.respond(415,b'MP4 required')
                try: size=int(self.headers.get('Content-Length','0'))
                except ValueError: return self.respond(400,b'Invalid length')
                if not 0<size<=50*1024**2: return self.respond(413,b'Maximum 50 MiB')
                if not lock.acquire(blocking=False): return self.respond(409,b'Pilot running')
                upload=None
                try:
                    self.connection.settimeout(30)
                    with tempfile.NamedTemporaryFile(suffix='.mp4',delete=False) as f:
                        upload=f.name
                        remaining=size
                        while remaining:
                            chunk=self.rfile.read(min(65536,remaining))
                            if not chunk: raise ValueError('Incomplete upload')
                            f.write(chunk); remaining-=len(chunk)
                    preview.unlink(missing_ok=True)
                    result.clear(); result.update(status='running',source='uploaded_video')
                    threading.Thread(target=pilot,args=(upload,),daemon=True).start()
                except Exception:
                    if upload: Path(upload).unlink(missing_ok=True)
                    lock.release()
                    return self.respond(400,b'Upload failed')
                return self.respond(202,b'Video test started')
            if self.headers.get('X-Pilot-Confirm')!='synthetic-only' and not (self.headers.get('Origin') and self.headers.get('Origin')=='https://'+os.environ.get('RENDER_EXTERNAL_HOSTNAME','invalid') and self.headers.get('Sec-Fetch-Site')=='same-origin'): return self.respond(403,b'Confirmation required')
            if not lock.acquire(blocking=False): return self.respond(409,b'Pilot running')
            result.clear(); result.update(status='running')
            threading.Thread(target=pilot,daemon=True).start()
            return self.respond(202,b'Pilot started')
    return Handler

if __name__=='__main__':
    os.umask(0o077)
    from contextlib import closing
    from stream_monitor import connect
    from turso_store import initialize_streamers

    with closing(connect("/tmp/streamclipper.sqlite3")) as db:
        initialize_streamers(db)

    print("TURSO: PERSISTENT STORAGE READY", flush=True)
    from source_rights import prepare as prepare_rights
    prepare_rights()
    print("TURSO: SOURCE RIGHTS READY", flush=True)

    from service import production_configured

    production = production_configured()

    print(
        "PRODUCTION MODE:",
        "ENABLED" if production else "DISABLED",
        flush=True
    )

    from storage_probe import probe

    if (
        not production
        and os.getenv("STREAMCLIPPER_V58_TEST") != "1"
        and not os.getenv("STREAMCLIPPER_VERIFY_CLIP_ID")
    ):
        threading.Thread(
            target=probe,
            daemon=True
        ).start()

    heartbeat_path('/tmp/streamclipper.sqlite3').unlink(missing_ok=True)
    clear_confirmation('/tmp/streamclipper.sqlite3')
    launched_at = time.time()
    start_metrics('/tmp/streamclipper.sqlite3', production)
    monitoring = subprocess.Popen(monitoring_command(production))
    with ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','10000'))),handler('/tmp/streamclipper.sqlite3',os.environ['DASHBOARD_PASSWORD'],production,monitoring,launched_at)) as server:
        server.serve_forever()
