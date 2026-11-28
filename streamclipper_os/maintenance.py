"""Durable, fail-closed maintenance drain. Only the coordinator writes Turso."""
import hmac
import json
import os
import sqlite3
import tempfile
import time
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

SCHEMA = """CREATE TABLE IF NOT EXISTS maintenance_v1 (
 id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL CHECK(paused IN (0,1)),
 changed_at REAL NOT NULL, ready_at REAL)"""
INIT = 'INSERT OR IGNORE INTO maintenance_v1(id,paused,changed_at) VALUES(1,0,?)'
READ = 'SELECT paused FROM maintenance_v1 WHERE id=1'
SET = 'UPDATE maintenance_v1 SET paused=?,changed_at=?,ready_at=NULL WHERE id=1 RETURNING paused'
UNARCHIVED = """SELECT COUNT(*) FROM clips c JOIN clip_review r ON r.clip_id=c.clip_id
 LEFT JOIN clip_media m ON m.clip_id=c.clip_id WHERE r.quality_status='passed'
 AND (m.clip_id IS NULL OR m.sha256 IS NOT r.file_sha256 OR m.file_size IS NOT r.file_size)"""
READY = """UPDATE maintenance_v1 SET ready_at=? WHERE id=1 AND paused=1
 AND NOT EXISTS(SELECT 1 FROM clip_jobs WHERE status='running')
 AND (""" + UNARCHIVED + """)=0 RETURNING paused"""


def path(db, name):
    return Path(str(db)+'.maintenance-'+name+'.json')


def read_file(p):
    try:
        with p.open('rb') as f: raw=f.read(4097)
        if len(raw)>4096: return {}
        obj=json.loads(raw)
        return obj if isinstance(obj,dict) else {}
    except (OSError,ValueError,TypeError): return {}


def write_file(p, value, exclusive=False):
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=p.parent,prefix=p.name+'.',delete=False) as f:
            tmp=Path(f.name); json.dump(value,f,allow_nan=False); f.flush()
        if exclusive:
            # Android/Termux may not expose os.link. Serialize writers using a
            # stable lock file, then publish the complete JSON by atomic rename.
            import fcntl
            lock = p.with_name(p.name + '.lock')
            flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(lock, flags, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                if p.exists() or p.is_symlink():
                    raise FileExistsError(str(p))
                tmp.replace(p)
            finally:
                os.close(fd)
        else: tmp.replace(p)
    finally:
        if tmp is not None: tmp.unlink(missing_ok=True)


def remote(statements):
    from turso_store import execute
    return execute(statements)


def scalar(result):
    rows=result['rows']
    if len(rows)!=1 or len(rows[0])!=1 or rows[0][0]['type']!='integer':
        raise ValueError('Invalid maintenance response')
    value=int(rows[0][0]['value'])
    if value not in (0,1): raise ValueError('Invalid maintenance state')
    return bool(value)


def local_blockers(db, active, groups, archive_busy, result, diagnostics, recovery, imports_failed):
    reasons=[]
    if recovery or db.execute("SELECT 1 FROM clip_jobs WHERE status='running' LIMIT 1").fetchone():
        reasons.append('Aufnahmeauftrag noch nicht abgeschlossen oder Wiederherstellung offen')
    if active or groups: reasons.append('Aufnahme-/Produktionsprozesse laufen noch')
    if archive_busy: reasons.append('Archivierung läuft')
    if any(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('history_pending','jobs_pending','catalog_pending')):
        reasons.append('Datenbankänderungen noch nicht synchronisiert')
    if any(result.get(k,{}).get('status')!='ok' for k in ('jobs','catalog')):
        reasons.append('Turso-Synchronisierung nicht bestätigt')
    if diagnostics is None or diagnostics.pending or diagnostics.error or not diagnostics.remote_at or time.time()-diagnostics.remote_at>180:
        reasons.append('Fortschrittsdaten noch nicht synchronisiert')
    count=db.execute(UNARCHIVED).fetchone()[0]
    if count: reasons.append(f'{count} qualitätsgeprüfte Clips ohne bestätigtes Archiv')
    if imports_failed: reasons.append('Clip-Import meldete Fehler; lokale Dateien prüfen')
    return reasons


class Maintenance:
    def __init__(self,db_path, enabled=True):
        self.db_path=db_path; self.enabled=enabled; self.paused=None if enabled else False
        self.ready=False; self.blocked=enabled; self.error=None; self.groups=set(); self.imports_failed=False
        self.last_remote_try=0.; self.next_ready_try=0.
        if enabled:
            # Never reuse a previous process's ready indicator.
            self.publish(['Wartungszustand wird geladen'])

    def publish(self,reasons):
        try: write_file(path(self.db_path,'state'),dict(at=time.time(),paused=self.paused,ready=self.ready,blocked=self.blocked,reasons=reasons,error=self.error))
        except OSError: pass

    def remember(self, supervisor):
        for proc,_ in supervisor.active.values():
            if isinstance(getattr(proc,'pid',None),int) and proc.pid>1:
                self.groups.add(proc.pid)
        remaining=set()
        for pid in self.groups:
            try: os.killpg(pid,0)
            except ProcessLookupError: continue
            except PermissionError: pass
            remaining.add(pid)
        self.groups=remaining

    def before(self,supervisor,recovery):
        if not self.enabled: return
        self.remember(supervisor)
        req=read_file(path(self.db_path,'request'))
        action=req.get('action')
        if action=='pause': self.blocked=True; self.ready=False
        if self.paused is None or action in ('pause','resume'):
            if time.monotonic()-self.last_remote_try>=5:
                self.last_remote_try=time.monotonic()
                try:
                    if self.paused is None:
                        self.paused=scalar(remote([(SCHEMA,()),(INIT,(time.time(),)),(READ,())])[-1])
                    if action in ('pause','resume'):
                        if action=='resume' and (recovery or not supervisor.execute or self.paused is not True):
                            # Never clear a separate recovery or execution safety gate.
                            if self.paused is False and not recovery and supervisor.execute:
                                pass  # idempotent retry after a confirmed write
                            else:
                                raise ValueError('Resume not eligible')
                        else:
                            desired=action=='pause'
                            if scalar(remote([(SET,(int(desired),time.time()))])[0])!=desired:
                                raise ValueError('State mismatch')
                            self.paused=desired
                        path(self.db_path,'request').unlink(missing_ok=True)
                        self.ready=False; self.next_ready_try=0.
                    self.error=None
                except Exception as exc:
                    self.error=type(exc).__name__
                    self.ready=False
                    # An uncertain write must never cause a new launch.
                    self.blocked=True
        self.blocked=bool(self.paused is not False or self.error or path(self.db_path,'request').exists())
        supervisor.maintenance_blocked=self.blocked

    def after(self,db,supervisor,archive_busy,result,diagnostics,recovery):
        if not self.enabled: return
        self.remember(supervisor)
        imports=result.get('supervisor',{}).get('imports',[])
        if isinstance(imports,list) and any(x.get('errors',0) or x.get('reason')=='file limit exceeded' for x in imports if isinstance(x,dict)):
            self.imports_failed=True
        reasons=[]
        if self.blocked:
            if self.paused is not True or self.error or path(self.db_path,'request').exists():
                reasons.append('Wartungszustand noch nicht bestätigt')
            else:
                try: reasons=local_blockers(db,supervisor.active,self.groups,archive_busy,result,diagnostics,recovery,self.imports_failed)
                except (sqlite3.Error,ValueError,TypeError): reasons=['Sicherheitsprüfung nicht verfügbar']
                if not reasons and not self.ready:
                    if time.monotonic()>=self.next_ready_try:
                        self.next_ready_try=time.monotonic()+10
                        try: self.ready=scalar(remote([(READY,(time.time(),))])[0])
                        except Exception: self.ready=False
                    if not self.ready: reasons.append('Cloud-Freigabe noch nicht bestätigt')
            if reasons: self.ready=False
        else: self.ready=False
        self.publish(reasons)
        result['maintenance']={'paused':self.paused,'ready':self.ready,'blocked':self.blocked,'reasons':reasons}


def state(db,monitor):
    s=read_file(path(db,'state'))
    try: fresh=0<=time.time()-float(s['at'])<=45
    except (KeyError,ValueError,TypeError): fresh=False
    pending=path(db,'request').exists()
    if not fresh or monitor.get('phase')!='running':
        return dict(paused=None,ready=False,blocked=True,pending=pending,reasons=['Keine aktuelle Wartungsrückmeldung'])
    s['pending']=pending
    if pending or monitor.get('unresolved'):
        s['ready']=False
    return s


def form(db,monitor,csrf):
    s=state(db,monitor)
    if s.get('pending'): title='Wartungsanforderung wird verarbeitet'
    elif s.get('ready'): title='Wartungsbereit – Deployment möglich'
    elif s.get('paused') is True: title='Wartung vorbereitet – Abschluss läuft'
    elif s.get('paused') is False and not s.get('blocked'): title='Normalbetrieb'
    else: title='Wartungszustand wird geprüft'
    out='<section style="margin-bottom:12px"><h2>Systemwartung</h2><p><strong>'+escape(title)+'</strong></p>'
    for reason in s.get('reasons',[]): out+='<p class="note">'+escape(str(reason))+'</p>'
    if s.get('ready'): out+='<p>Neue Aufnahmen gesperrt. Vor dem Deployment diesen Status prüfen. Nach dem Update den Betrieb erst freigeben, wenn die alte Instanz beendet ist.</p>'
    if not s.get('pending') and s.get('paused') is not None and not monitor.get('unresolved') and monitor.get('phase')=='running':
        action='resume' if s['paused'] else 'pause'
        if action=='pause' or s.get('ready'):
            label='Betrieb wieder freigeben' if action=='resume' else 'Wartung vorbereiten'
            out+='<form method="post" action="/maintenance"><input type="hidden" name="action" value="'+action+'"><input type="hidden" name="csrf" value="'+escape(csrf,quote=True)+'">'
            if action=='resume': out+='<p><label><input type="checkbox" name="confirmed" value="yes" required> Keine alte Instanz nimmt mehr auf; kein Deployment läuft.</label></p>'
            out+='<button type="submit">'+label+'</button></form>'
        elif s.get('paused'):
            out+='<form method="post" action="/maintenance"><input type="hidden" name="action" value="resume"><input type="hidden" name="csrf" value="'+escape(csrf,quote=True)+'"><p><label><input type="checkbox" name="confirmed" value="yes" required> Wartung abbrechen; kein Deployment läuft.</label></p><button>Wartung abbrechen</button></form>'
    return (out+'</section>').encode('utf-8')


def handle(handler,db,monitor_reader,csrf):
    host=os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    if not host or handler.headers.get('Origin')!='https://'+host: return handler.respond(403,b'Invalid origin')
    if handler.headers.get('Transfer-Encoding') or handler.headers.get('Content-Type','').split(';',1)[0].strip().lower()!='application/x-www-form-urlencoded': return handler.respond(400,b'Invalid request')
    try:
        size=int(handler.headers.get('Content-Length','0'))
        if not 1<=size<=2048: raise ValueError()
        handler.connection.settimeout(15); raw=handler.rfile.read(size)
        if len(raw)!=size: raise ValueError()
        fields=parse_qs(raw.decode('utf-8'),strict_parsing=True,max_num_fields=3,keep_blank_values=True)
        if any(len(v)!=1 for v in fields.values()) or set(fields)-{'action','csrf','confirmed'}: raise ValueError()
        action=fields['action'][0]
        if action not in ('pause','resume'): raise ValueError()
        if not hmac.compare_digest(fields['csrf'][0].encode(),csrf.encode()): return handler.respond(403,b'Invalid token')
        if action=='resume' and fields.get('confirmed')!=['yes']: return handler.respond(403,b'Confirmation required')
    except (KeyError,ValueError,UnicodeError,OSError): return handler.respond(400,b'Invalid request')
    monitor=monitor_reader(); s=state(db,monitor)
    if monitor.get('unresolved') or monitor.get('phase')!='running' or s.get('pending') or s.get('paused') is not (action=='resume'):
        return handler.respond(409,b'State changed; reload dashboard')
    try: write_file(path(db,'request'),dict(action=action,at=time.time()),exclusive=True)
    except FileExistsError: return handler.respond(409,b'Request already pending')
    except OSError: return handler.respond(503,b'Request unavailable')
    handler.send_response(303); handler.send_header('Location','/'); handler.send_header('Content-Length','0'); handler.send_header('Cache-Control','no-store'); handler.end_headers()
