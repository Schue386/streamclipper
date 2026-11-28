"""Read-only recording diagnostics, independent of recording authorization and scheduling."""
import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

FIELDS = ('buffered_s','analyzed_s','segments','analyzed_segments','judged_moments',
          'selected_moments','clips','failed_or_skipped','gate_pass','reconnects',
          'last_segment_at','last_analysis_at','working_sample_peak_bytes',
          'production_waiting','production_active','production_deferred')
PHASES = {'starting','recording','stopping','finishing','finished'}
CURRENT_SQL = '''SELECT j.id,j.channel,j.status,j.attempts,j.started_at,j.finished_at,
    j.error_message,j.output_dir,w.heartbeat_at,w.deadline_at,w.ended_at,w.exit_code
    FROM clip_jobs j LEFT JOIN worker_runs w ON w.job_id=j.id
    ORDER BY CASE WHEN j.status='running' THEN 0 WHEN j.status='queued' THEN 1 ELSE 2 END,j.id DESC LIMIT 15'''
SCHEMA = '''CREATE TABLE IF NOT EXISTS recording_diagnostics_v1 (
    job_id INTEGER NOT NULL, attempt INTEGER NOT NULL, observed_at REAL NOT NULL,
    progress_json TEXT NOT NULL, PRIMARY KEY(job_id,attempt))'''
UPSERT = '''INSERT INTO recording_diagnostics_v1(job_id,attempt,observed_at,progress_json)
    VALUES(?,?,?,?) ON CONFLICT(job_id,attempt) DO UPDATE SET
    observed_at=excluded.observed_at,progress_json=excluded.progress_json
    WHERE excluded.observed_at >= recording_diagnostics_v1.observed_at'''
HISTORY_SQL = '''SELECT c.job_id,c.attempt,c.channel,c.status,c.started_at,c.finished_at,
    c.error_message,c.heartbeat_at,c.ended_at,c.exit_code,d.progress_json
    FROM recording_cycles c LEFT JOIN recording_diagnostics_v1 d
    ON d.job_id=c.job_id AND d.attempt=c.attempt
    ORDER BY c.job_id DESC,c.attempt DESC LIMIT 60'''
COUNT_SQL = '''SELECT job_id,COUNT(*) FROM recording_cycles WHERE status='completed' AND job_id IN (
    SELECT id FROM clip_jobs ORDER BY CASE WHEN status='running' THEN 0 WHEN status='queued' THEN 1 ELSE 2 END,id DESC LIMIT 15) GROUP BY job_id'''
# Current and archived rows can overlap momentarily during a requeue; the UI deduplicates.
LATEST_SQL = '''SELECT d.job_id,d.attempt,d.progress_json FROM recording_diagnostics_v1 d
    JOIN clip_jobs j ON j.id=d.job_id AND j.attempts=d.attempt
    ORDER BY j.id DESC LIMIT 60'''

def epoch(value):
    try:
        t = datetime.fromisoformat(value)
        return t.timestamp() if t.tzinfo is not None else None
    except (ValueError,TypeError,OverflowError):
        return None

def clean_progress(value):
    if not isinstance(value,dict): return {}
    result={}
    for key in FIELDS+('updated_at',):
        x=value.get(key)
        if type(x) in (float,int) and math.isfinite(x) and 0<=x<1e15:
            result[key]=x
    if isinstance(value.get('phase'),str) and value.get('phase') in PHASES: result['phase']=value['phase']
    return result

def read_json(path, limit=256*1024):
    try:
        with Path(path).open('rb') as f: raw=f.read(limit+1)
        if len(raw)>limit: return {}
        value=json.loads(raw)
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,TypeError): return {}

def atomic_json(path, value):
    path=Path(path); tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,separators=(',',':'),allow_nan=False),encoding='utf-8')
    tmp.replace(path)

def progress_from_files(output_dir, started_at, status):
    start=epoch(started_at)
    if not output_dir or start is None: return {}
    directory=Path(output_dir)
    p=clean_progress(read_json(directory/'live_progress.json',8192))
    if not start-1<=p.get('updated_at',0)<=time.time()+5: p={}
    # Final summary is available for old versions too. Never infer counters that were not recorded.
    if status in ('completed','failed'):
        try: updated=(directory/'live_summary.json').stat().st_mtime
        except OSError: updated=0
        if start-1<=updated<=time.time()+5:
            final=clean_progress(read_json(directory/'live_summary.json',32768))
            if 'buffered_s' in final and 'clips' in final:
                p.update(final,updated_at=updated,phase='finished')
    return p

def cache_path(db_path): return Path(str(db_path)+'.recording-diagnostics.json')

def error_text(value):
    if not value: return None
    # Do not publish arbitrary exceptions, URLs, paths or source credentials.
    value=str(value)
    if value.startswith('Clipping exited with code '):
        m=re.fullmatch(r'Clipping exited with code (-?\d+)',value)
        return value if m else 'Workerfehler (Details nicht freigegeben)'
    if value.startswith('Live capture evidence: '):
        tail=value.split(': ',1)[1]
        if tail in ('no_media','capture_incomplete','summary_missing','summary_incomplete','summary_missing_or_invalid'):
            return value
    if value in ('Clipping timeout exceeded','RAM-Schutz: Aufnahmegruppe notgestoppt',
        'Source rights unavailable','Source permission revoked or disabled',
        'Previous recording interrupted; stop confirmed','Recording blocked before spawn',
        'Supervisor stopped; inspect output before retry'):
        return value
    return 'Fehler vorhanden (Details nicht freigegeben)'

def key(jid,attempt): return f'{jid}:{attempt}'

def current_rows(db):
    return db.execute(CURRENT_SQL).fetchall()

def sample_rows(db, peaks, read_metrics, db_path, active):
    result={}
    for jid,channel,state,attempt,start,finish,error,out,hb,deadline,ended,code in current_rows(db):
        if attempt<1 or state not in ('running','completed','failed'): continue
        k=key(jid,attempt); p=progress_from_files(out,start,state)
        if state=='running' and start and jid in active:
            try:
                m=read_metrics(db_path) or {}; w=m.get('working_bytes'); at=m.get('at')
                if type(w) is int and w>=0 and type(at) in (int,float) and epoch(start) is not None and at>=epoch(start):
                    peaks[k]=max(peaks.get(k,0),w)
            except (OSError,ValueError,TypeError): pass
        if k in peaks: p['working_sample_peak_bytes']=peaks[k]
        if p: result[k]=p
    return result

class Diagnostics:
    """Single background network writer. Main loop only reads local evidence and publishes a small cache."""
    def __init__(self,db_path):
        self.db_path=db_path; self.pending={}; self.current={}; self.peaks={}
        self.history=[]; self.saved={}; self.counts={}; self.remote_at=None; self.error=None
        self.thread=None; self.response=None; self.next_sync=0.; self.prepared=False
        self.last_publish=0.; self.last_sent={}

    def _network(self, batch):
        from turso_store import execute
        from turso_jobs import decode_value
        try:
            if not self.prepared: execute([(SCHEMA,())])
            if batch:
                execute([(UPSERT,(int(k.split(':')[0]),int(k.split(':')[1]),at,json.dumps(p,separators=(',',':'))))
                         for k,(at,p) in batch.items()])
            rows=execute([(HISTORY_SQL,()),(COUNT_SQL,()),(LATEST_SQL,())])
            h=[]; counts={}; saved={}
            for row in rows[0]['rows']:
                jid,attempt,ch,state,start,finish,error,hb,ended,code,p=map(decode_value,row)
                h.append(dict(id=jid,attempt=attempt,channel=ch,status=state,started_at=start,finished_at=finish,
                              error=error_text(error),heartbeat_at=hb,ended_at=ended,exit_code=code,
                              progress=clean_progress(json.loads(p)) if p else {}))
            for row in rows[1]['rows']:
                jid,n=map(decode_value,row); counts[str(jid)]=n
            for row in rows[2]['rows']:
                jid,attempt,p=map(decode_value,row); saved[key(jid,attempt)]=clean_progress(json.loads(p))
            self.response=(batch,h,counts,saved,time.time(),None)
        except Exception as exc:
            self.response=(batch,None,None,None,None,type(exc).__name__)

    def tick(self, db, active=(), finished=False):
        from resource_guard import read_metrics
        now=time.time()
        if self.thread is not None and not self.thread.is_alive():
            self.thread.join(); self.thread=None
            if self.response:
                batch,h,c,s,at,error=self.response; self.response=None; self.error=error
                if error is None:
                    self.prepared=True; self.history=h; self.counts=c; self.saved=s; self.remote_at=at
                    for k,v in batch.items():
                        self.last_sent[k]=v[1]
                        if self.pending.get(k)==v: self.pending.pop(k,None)
        rows=sample_rows(db,self.peaks,read_metrics,self.db_path,active)
        self.current=rows
        for k,p in rows.items():
            if p!=self.last_sent.get(k) and (k not in self.pending or p!=self.pending[k][1]):
                self.pending[k]=(now,p)
        # Bounded even during a prolonged network outage. Oldest unsaved diagnostics may be lost.
        if len(self.pending)>120:
            for k in sorted(self.pending,key=lambda k:self.pending[k][0])[:-120]: self.pending.pop(k,None)
            self.error='diagnostics_queue_limit'
        keep=set(rows)|set(self.pending)
        self.peaks={k:v for k,v in self.peaks.items() if k in keep}
        self.last_sent={k:v for k,v in self.last_sent.items() if k in keep}
        if self.thread is None and (now>=self.next_sync or finished):
            self.next_sync=now+60
            self.thread=threading.Thread(target=self._network,args=(dict(self.pending),),daemon=True,name='recording-diagnostics')
            self.thread.start()
        # Avoid redundant disk writes; cache never contains source URL, raw log or credential.
        if now-self.last_publish>=5 or finished:
            self.last_publish=now
            atomic_json(cache_path(self.db_path),dict(updated_at=now,remote_at=self.remote_at,error=self.error,
                pending=len(self.pending),current=self.current,saved=self.saved,history=self.history,counts=self.counts))

def snapshot(db,db_path):
    cache=read_json(cache_path(db_path)); current=[]
    for jid,channel,state,attempt,start,finish,error,out,hb,deadline,ended,code in current_rows(db):
        k=key(jid,attempt)
        p=clean_progress(cache.get('saved',{}).get(k,{})); p.update(clean_progress(cache.get('current',{}).get(k,{})))
        p.update(progress_from_files(out,start,state))
        current.append(dict(id=jid,channel=channel,status=state,attempt=attempt,started_at=start,finished_at=finish,
            error=error_text(error),heartbeat_at=hb,deadline_at=deadline,ended_at=ended,exit_code=code,progress=p))
    return dict(current=current,history=cache.get('history',[]),counts=cache.get('counts',{}),
                remote_at=cache.get('remote_at'),updated_at=cache.get('updated_at'),error=cache.get('error'),pending=cache.get('pending'))

TZ=ZoneInfo('Europe/Berlin')
def date(value):
    t=value if type(value) in (int,float) else epoch(value)
    try: return datetime.fromtimestamp(t,TZ).strftime('%d.%m. %H:%M:%S') if t is not None else '–'
    except (ValueError,TypeError,OverflowError,OSError): return '–'
def duration(v):
    if type(v) not in (int,float) or not math.isfinite(v) or v<0:return '–'
    v=int(v); return f'{v//60} Min. {v%60:02d} Sek.'
def age(value,now):
    t=value if type(value) in (int,float) else epoch(value)
    return duration(now-t)+' her' if t is not None and 0<=now-t else '–'
def label(state): return {'running':'Aktiv','completed':'Abgeschlossen','failed':'Fehlgeschlagen','queued':'Wartend','interrupted':'Unterbrochen'}.get(state,state)
def line(name,value): return f'<div><small>{escape(str(name))}</small><br><b>{escape(str(value if value is not None else "–"))}</b></div>'

def render_diagnostics(data,now=None):
    now=time.time() if now is None else now
    if not data: return ''
    h=[]; counts=data.get('counts',{}); history=data.get('history',[])
    history_keys={(r['id'],r['attempt']) for r in history}
    for j in data.get('current',[]):
        p=j.get('progress',{}); st=j['status']; started=epoch(j.get('started_at')); ended=epoch(j.get('finished_at'))
        elapsed=(ended-started if ended is not None else now-started) if started is not None and (st=='running' or ended is not None) else None
        seen=(j['id'],j['attempt']) in history_keys
        count=counts.get(str(j['id']))
        if count is not None or data.get('remote_at') is not None:
            count=(count or 0)+int(st=='completed' and not seen)
        phase={'starting':'Startet','recording':'Aufnahme/Analyse','stopping':'Aufnahme wird beendet','finishing':'Clip-Verarbeitung','finished':'Worker-Zusammenfassung vorhanden'}.get(p.get('phase'),'Noch keine Fortschrittsmessung')
        if st=='running' and p.get('updated_at') is not None and now-p['updated_at']>90:
            phase+=' · Fortschrittsmeldung älter als 90 Sek.'
        if st!='running': phase=label(st)
        ram=p.get('working_sample_peak_bytes')
        fields=[('Zyklus',j['attempt'] or '–'),('Abgeschlossene Zyklen (Turso-Stand)',count),('Start (MEZ/MESZ)',date(j.get('started_at'))),
            ('Laufzeit des Auftrags',duration(elapsed)),('Phase',phase),('Worker-Heartbeat',date(j.get('heartbeat_at'))),
            ('Worker-Deadline',date(j.get('deadline_at'))),('Erfasste Mediendauer',duration(p.get('buffered_s'))),
            ('Analysierte Mediendauer',duration(p.get('analyzed_s'))),('Erfasste / analysierte Segmente',f"{p.get('segments','–')} / {p.get('analyzed_segments','–')}"),
            ('Bewertete / ausgewählte Momente',f"{p.get('judged_moments','–')} / {p.get('selected_moments','–')}"),
            ('Clips / Qualitätsprüfung bestanden',f"{p.get('clips','–')} / {p.get('gate_pass','–')}"),
            ('Fehlgeschlagene / übersprungene Clips',p.get('failed_or_skipped')),('Letztes fertiges Segment',date(p.get('last_segment_at'))),
            ('Letzte Segmentanalyse',date(p.get('last_analysis_at'))),('Fortschrittsmeldung',date(p.get('updated_at'))),
            ('Arbeitssatz-Peak (Instanz, Stichprobe im Zyklus)',f'{ram/1048576:.1f} MiB' if ram is not None else '–'),
            ('Render-Freigabe wartet',p.get('production_waiting')),('Render-Freigabe aktiv',p.get('production_active')),
            ('Render-Freigabe nicht erhalten (lokales Fenster)',p.get('production_deferred')),
            ('Wiederanlauf-Hinweis' if st in ('running','queued') and j.get('error')=='Previous recording interrupted; stop confirmed' else 'Fehler',j.get('error')),('Exit-Code',j.get('exit_code'))]
        content=''.join(line(a,b) for a,b in fields)
        heading=escape(f"#{j['id']} – {j['channel']} · {label(st)}",quote=True)
        h.append(f'<details {"open" if st=="running" else ""} style="margin:10px 0;padding:12px;border:1px solid #334156;border-radius:10px"><summary>{heading}</summary><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:10px;margin-top:12px;overflow-wrap:anywhere">{content}</div></details>')
    history_html=[]
    for r in history:
        p=r.get('progress',{})
        title=f"#{r['id']} – {r['channel']} · Zyklus {r['attempt']} · {label(r['status'])}"
        fields=[('Start',date(r.get('started_at'))),('Ende',date(r.get('finished_at') or r.get('ended_at'))),
            ('Erfasste Medien',duration(p.get('buffered_s'))),('Bewertete Momente',p.get('judged_moments')),
            ('Clips',p.get('clips')),('Fehler',r.get('error')),('Exit-Code',r.get('exit_code'))]
        history_html.append('<details><summary>'+escape(title)+'</summary>'+''.join(line(a,b) for a,b in fields)+'</details>')
    sync='Turso-Historie: '+(date(data.get('remote_at')) if data.get('remote_at') else 'noch nicht geladen')
    if data.get('error'): sync+=' · Synchronisierung gestört'
    if data.get('pending'): sync+=' · '+str(data['pending'])+' Fortschrittsstände noch nicht bestätigt'
    return '<section><h2>Letzte Aufträge</h2><p class="note">'+escape(sync)+' · Strich = nicht gemessen. Mediendauer ist nicht gleich lückenlose Streamabdeckung.</p>'+(''.join(h) or '<p>Keine Aufträge</p>')+'<details style="margin-top:16px"><summary>Gespeicherte Aufnahmehistorie (bis zu 60 ältere Zyklen)</summary>'+(''.join(history_html) or '<p>Keine älteren Zyklen geladen.</p>')+'</details></section>'
