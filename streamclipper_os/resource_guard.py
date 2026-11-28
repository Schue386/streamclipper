"""Render v60.6.6: cgroup telemetry and emergency recording-group protection.
No new configuration. Metrics are instance-scoped; events are best-effort persisted.
"""
import json
import os
import queue
import shutil
import signal
import threading
import time
import uuid
from pathlib import Path

MIB = 1024**2
ROOT = Path('/sys/fs/cgroup')
PROFILE_LIMIT = 512 * MIB


def cloud_profile():
    return bool(os.getenv('RENDER_EXTERNAL_HOSTNAME') and
                os.getenv('STREAMCLIPPER_PRODUCTION') == '1' and os.name == 'posix')


def integer(path):
    try:
        value = int(path.read_text().strip())
        return value if value >= 0 else None
    except (OSError, ValueError):
        return None


def counters(path):
    result = {}
    try:
        for line in path.read_text().splitlines():
            key, value = line.split()
            result[key] = int(value)
    except (OSError, ValueError):
        pass
    return result


def sample(root=ROOT):
    current = integer(root / 'memory.current')
    actual = integer(root / 'memory.max')
    limit = min(actual, PROFILE_LIMIT) if actual and cloud_profile() else actual
    if not limit and cloud_profile():
        limit = PROFILE_LIMIT
    inactive = max(0, counters(root / 'memory.stat').get('inactive_file', 0))
    working = max(0, current - inactive) if current is not None else None
    cpu = counters(root / 'cpu.stat').get('usage_usec')
    quota = None
    try:
        a, b = (root / 'cpu.max').read_text().split()
        quota = int(a) / int(b)
        if quota <= 0:
            quota = None
    except (OSError, ValueError, ZeroDivisionError):
        pass
    events = counters(root / 'memory.events')
    return {'at': time.time(), 'current_bytes': current, 'working_bytes': working,
            'limit_bytes': limit, 'kernel_peak_bytes': integer(root / 'memory.peak'),
            'cpu_usage_usec': cpu, 'cpu_quota': quota,
            'oom_kill': events.get('oom_kill'), 'oom': events.get('oom')}


def level(s):
    w, c, limit = (s.get(k) for k in ('working_bytes', 'current_bytes', 'limit_bytes'))
    if w is None or c is None or not limit:
        return 'unknown'
    # Inactive file cache is normally reclaimable; don't kill on cache alone.
    if w >= limit * (450/512) or (c >= limit * (480/512) and w >= limit * (400/512)):
        return 'critical'
    if w >= limit * (400/512):
        return 'blocked'
    if w >= limit * (300/512):
        return 'warning'
    return 'normal'


def may_launch():
    return level(sample()) in ('normal', 'warning')


def metric_path(db_path):
    return Path(str(db_path) + '.resources.json')


def read_metrics(db_path):
    try:
        data = json.loads(metric_path(db_path).read_text(encoding='utf-8'))
        if 0 <= time.time() - data['at'] <= 15:
            return data
    except (OSError, ValueError, TypeError, KeyError):
        pass
    data = sample()
    data.update(level=level(data), stale=True)
    return data


def event(kind, data, job_id=None):
    row = {'id': uuid.uuid4().hex, 'at': int(time.time()), 'kind': kind,
           'job_id': job_id, 'data': data}
    print('RESOURCE:', json.dumps(row, separators=(',', ':')), flush=True)
    return row


def persist(row):
    # Separate diagnostic table; no job/rights/history tables are modified.
    from turso_store import execute
    execute([("""CREATE TABLE IF NOT EXISTS resource_events_v6066 (
        id TEXT PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL,
        job_id INTEGER, data TEXT NOT NULL)""", ())])
    execute([('INSERT OR IGNORE INTO resource_events_v6066 VALUES(?,?,?,?,?)',
              (row['id'], row['at'], row['kind'], row['job_id'], json.dumps(row['data'], separators=(',', ':')))),
             ('DELETE FROM resource_events_v6066 WHERE ts < ?', (int(time.time()) - 7*86400,)),
             ('DELETE FROM resource_events_v6066 WHERE id IN (SELECT id FROM resource_events_v6066 ORDER BY ts DESC,id DESC LIMIT -1 OFFSET 1000)', ())])


def persist_safely(row):
    try:
        persist(row)
    except Exception as exc:
        print('RESOURCE: EVENT PERSIST FAILED', type(exc).__name__, flush=True)


def start_metrics(db_path, production):
    """Independent sampler; network writes cannot block sampling or health requests."""
    pending = queue.Queue(maxsize=32)
    def writer():
        while True:
            persist_safely(pending.get())
    def emit(kind, data):
        row = event(kind, data)
        if production:
            try:
                pending.put_nowait(row)
            except queue.Full:
                print('RESOURCE: EVENT QUEUE FULL', flush=True)
    if production:
        threading.Thread(target=writer, daemon=True, name='resource-event-writer').start()
    def monitor():
        last, last_level, peak, last_log = None, None, 0, 0.0
        while True:
            s = sample()
            work = s['working_bytes']
            if work is not None:
                peak = max(peak, work)
            s['working_peak_bytes'] = peak if work is not None else None
            s['level'] = level(s)
            s['cpu_percent_quota'] = None
            if last and s['cpu_quota'] and s['cpu_usage_usec'] is not None and last['cpu_usage_usec'] is not None:
                elapsed = s['at'] - last['at']
                delta = s['cpu_usage_usec'] - last['cpu_usage_usec']
                if elapsed > 0 and delta >= 0:
                    s['cpu_percent_quota'] = round(100 * delta / (elapsed*1e6*s['cpu_quota']), 1)
            try:
                s['disk_free_bytes'] = shutil.disk_usage('/tmp').free
                path = metric_path(db_path)
                tmp = path.with_suffix('.tmp')
                tmp.write_text(json.dumps(s), encoding='utf-8')
                tmp.replace(path)
            except OSError:
                pass
            if s['level'] != last_level:
                emit('boot' if last_level is None else 'pressure_'+s['level'], s)
                last_level = s['level']
                last_log = time.monotonic()
            elif time.monotonic()-last_log >= 60:
                print('RESOURCE: SAMPLE', json.dumps(s, separators=(',', ':')), flush=True)
                last_log = time.monotonic()
            last = s
            time.sleep(1)
    threading.Thread(target=monitor, daemon=True, name='resource-sampler').start()


def watch_worker(proc, job_id, *, read=sample, kill_group=None, get_group=None, sleep=time.sleep):
    """Abort only the verified production worker group, never the web/service group.
    A guard stop is a failed cycle (never a completed clip or automatic retry).
    """
    kill_group = kill_group or os.killpg
    get_group = get_group or os.getpgid
    while proc.poll() is None:
        s = read()
        if level(s) == 'critical':
            try:
                if proc.pid <= 1 or get_group(proc.pid) != proc.pid or proc.poll() is not None:
                    return
                proc._sc_memory_stop = True
                kill_group(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError as exc:
                proc._sc_memory_stop = False
                print('RESOURCE: GUARD SIGNAL FAILED', type(exc).__name__, flush=True)
                return
            persist_safely(event('worker_memory_stop', s, job_id))
            return
        sleep(0.5)


def start_worker_watch(proc, job_id):
    proc._sc_memory_stop = False
    threading.Thread(target=watch_worker, args=(proc, job_id), daemon=True,
                     name='recording-memory-guard').start()


def display(db_path):
    s = read_metrics(db_path)
    def mb(key):
        x = s.get(key)
        return f'{x/MIB:.1f} MiB' if isinstance(x, (int,float)) else 'unbekannt'
    cpu = s.get('cpu_percent_quota')
    cpu = f'{cpu:.1f}% des CPU-Limits' if cpu is not None else 'unbekannt'
    names = {'normal':'normal', 'warning':'erhoeht', 'blocked':'Aufnahmestart gesperrt',
             'critical':'kritisch', 'unknown':'unbekannt'}
    return ('RAM '+mb('current_bytes')+' / '+mb('limit_bytes')+
            ' | Arbeitssatz '+mb('working_bytes')+' | Arbeitssatz-Peak '+mb('working_peak_bytes')+' | RAM-Peak '+mb('kernel_peak_bytes')+
            ' | CPU '+cpu+' | Frei '+mb('disk_free_bytes')+
            ' | RAM-Schutz: '+names.get(s.get('level'),'unbekannt')+
            (' (Messwert nicht aktuell)' if s.get('stale') else ''))
