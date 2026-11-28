"""Loopback-only dashboard for StreamClipper OS. No remote access or uploads."""
from __future__ import annotations
from contextlib import closing
import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from stream_monitor import connect
from streamer_manager import overview as streamer_overview
from streamer_ui import render_streamers
from control import status, set_enabled
from youtube_metrics import latest
from performance import analyze
from learning import recommend
from tiktok_metrics import latest as latest_tiktok
from tiktok_status import overview
from clip_catalog import prepare as prepare_catalog, summary
from job_diagnostics import snapshot as diagnostic_snapshot, render_diagnostics
import sqlite3

MAX_BODY = 2048

def snapshot(db_path: str) -> dict:
    with closing(connect(db_path)) as db:
        prepare_catalog(db)
        result = status(db)
        result.update(summary(db))
        result['youtube_metrics'] = latest(db)
        result['performance'] = analyze(db)
        result['learning'] = recommend(db)
        result['tiktok_performance'] = analyze(db,destination='tiktok')
        result['tiktok_metrics'] = latest_tiktok(db)
        result['tiktok_status'] = overview(db)
        result['streamers'] = streamer_overview(db)
        result['recent_jobs'] = [
            {'id': jid, 'channel': channel, 'status': state}
            for jid, channel, state in db.execute(
                'SELECT id,channel,status FROM clip_jobs ORDER BY id DESC LIMIT 15')
        ]
        try:
            result['recording_diagnostics'] = diagnostic_snapshot(db,db_path)
        except (sqlite3.Error,OSError,ValueError,TypeError):
            result['recording_diagnostics'] = None
        return result

def render(data: dict, message: str = '') -> bytes:
    def e(value): return escape(str(value), quote=True)
    flag = data['autopilot_enabled']
    metrics = [('Streams',data['streams']),('Clips',data['clips']),
               ('Qualität bestanden',data['quality_passed']),
               ('Upload bereit',data['uploads_ready']),('Uploads erfasst',data['uploads_completed']),
               ('Jobs aktiv',data['active_jobs']),('Jobs wartend',data['queued_jobs']),
               ('Jobs fehlgeschlagen',data['failed_jobs'])]
    yt=data.get('youtube_metrics',{})
    metrics.extend((label, yt.get(key) if yt.get(key) is not None else '—') for label,key in [('YouTube-Aufrufe','views'),('YouTube-Likes','likes'),('YouTube-Kommentare','comments'),('Interaktion (%)','engagement')])
    tm=data.get('tiktok_metrics',{})
    metrics.extend((label, tm.get(key) if tm.get(key) is not None else '—') for label,key in [('TikTok-Aufrufe','views'),('TikTok-Likes','likes'),('TikTok-Kommentare','comments'),('TikTok-Shares','shares')])
    cards = ''.join(f'<article><small>{e(label)}</small><strong>{e(value)}</strong></article>' for label,value in metrics)
    streamers = render_streamers(data["streamers"])
    jobs = ''.join(f'<li>#{e(item["id"])} {e(item["channel"])} – {e(item["status"])}</li>' for item in data['recent_jobs']) or '<li>Keine Aufträge</li>'
    job_section = render_diagnostics(data.get('recording_diagnostics')) or f'<section><h2>Letzte Aufträge</h2><ul>{jobs}</ul></section>'
    learning_rows = ''.join(f'<li>{e(platform)} / {e(dimension)}: {e(info["preferred"] or "Weitere Daten sammeln")}</li>' for platform, dimensions in data.get('learning',{}).items() for dimension, info in dimensions.items())
    rankings = ''.join(f'<li>{e(x["channel"])} – {e(x["median_views_per_day"])} Aufrufe/Tag (Median), {e(x["samples"])} Clips – {"Priorität prüfen" if x["recommendation"]=="review_priority" else "Weitere Daten sammeln"}</li>' for x in data.get('performance',{}).get('channels',[])[:20]) or '<li>Noch keine vergleichbaren öffentlichen Clips</li>'
    rankings += ''.join(f'<li>TikTok: {e(x["channel"])} – {e(x["median_views_per_day"])} Aufrufe/Tag (Median), {e(x["samples"])} Clips</li>' for x in data.get('tiktok_performance',{}).get('channels',[])[:20])
    tt = ''.join(f'<li>#{e(x["queue_id"])} – {e(x["state"])} · {e(x["updated_at"])}</li>' for x in data.get('tiktok_status',[])) or '<li>Keine TikTok-Übertragungen</li>'
    action = 'stop'   if flag else 'start'
    label = 'Autopilot stoppen' if flag else 'Autopilot freigeben'
    html = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StreamClipper OS</title><style>
:root{{color-scheme:dark}}body{{font:16px system-ui,sans-serif;margin:0;background:#10141e;color:#f2f4f8}}
main{{max-width:960px;margin:auto;padding:20px}}header{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin:20px 0}}
article,section{{background:#1d2635;border:1px solid #334156;border-radius:14px;padding:16px}}
article small{{display:block;color:#a9b8ce}}article strong{{display:block;font-size:30px;margin-top:6px}}
button{{padding:12px 16px;border:0;border-radius:9px;background:#a8dbff;color:#09131e;font-weight:700}}
ul{{padding-left:22px;line-height:1.9}}.sections{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}}
.note{{color:#bdc9d9}}.on{{color:#80e4aa}}.off{{color:#f4c77b}}</style><meta http-equiv="refresh" content="30"></head>
<body><main><header><div><h1>StreamClipper OS</h1><p class="note">Lokales Dashboard · keine Cloud-Verbindung</p></div>
<div><strong class="{'on' if flag else 'off'}">{'Freigegeben' if flag else 'Gestoppt'}</strong>
<form method="post" action="/control"><input type="hidden" name="action" value="{action}"><button type="submit">{label}</button></form></div></header>
<p class="note">{e(message)}</p><div class="grid">{cards}</div>{job_section}<div class="sections">
<section><h2>Streamer</h2>{streamers}</section></div>
<section><h2>TikTok-Status</h2><p>Posteingang ist keine öffentliche Veröffentlichung.</p><ul>{tt}</ul></section>
<section><h2>Erfolgsanalyse</h2><p>Öffentliche Clips, Messalter 1–7 Tage; mindestens 5 Clips für Prioritätsprüfung.</p><ul>{rankings}</ul><h3>Lernstrategie (UTC, Empfehlungen)</h3><ul>{learning_rows}</ul></section>
<p class="note">Die Freigabe startet keinen Server oder Supervisor. Der Supervisor muss separat mit --execute laufen.
Uploads werden hier nicht ausgeführt.</p></main></body></html>"""
    return html.encode('utf-8')

def make_handler(db_path: str):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code: int, body: bytes, content_type='text/html; charset=utf-8'):
            self.send_response(code)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if self.path != '/':
                return self.respond(404,b'Not found','text/plain; charset=utf-8')
            self.respond(200,render(snapshot(db_path)))
        def do_POST(self):
            if self.path != '/control':
                return self.respond(404,b'Not found','text/plain; charset=utf-8')
            # Browser origin check prevents cross-site POSTs to loopback service.
            expected=f'http://127.0.0.1:{self.server.server_port}'
            if self.headers.get('Origin') != expected:
                return self.respond(403,b'Invalid origin','text/plain; charset=utf-8')
            try:
                length=int(self.headers.get('Content-Length','0'))
            except ValueError:
                return self.respond(400,b'Invalid length','text/plain; charset=utf-8')
            if not 1 <= length <= MAX_BODY or self.headers.get('Content-Type','').split(';')[0].strip() != 'application/x-www-form-urlencoded':
                return self.respond(400,b'Invalid request','text/plain; charset=utf-8')
            fields=parse_qs(self.rfile.read(length).decode('utf-8',errors='replace'))
            if fields.get('action') not in (['start'],['stop']):
                return self.respond(400,b'Invalid action','text/plain; charset=utf-8')
            with closing(connect(db_path)) as db:
                set_enabled(db,fields['action'][0]=='start')
            self.send_response(303)
            self.send_header('Location','/')
            self.send_header('Content-Length','0')
            self.end_headers()
    return Handler

def main():
    parser=argparse.ArgumentParser(description='Local-only StreamClipper OS dashboard')
    parser.add_argument('--db',default='streamclipper_os.sqlite3')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(args.db)) as server:
        print(f'Dashboard: http://127.0.0.1:{server.server_port}/',flush=True)
        server.serve_forever()
if __name__=='__main__':main()
