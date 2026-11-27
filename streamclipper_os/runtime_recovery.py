"""Runtime heartbeat and fail-closed recovery of interrupted production recordings."""
import hashlib
import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone

RECOVERY_REASON = 'Previous recording interrupted; stop confirmed'

# Preserve previous worker evidence atomically, never invent a successful exit.
RECOVERY_TRIGGER = """CREATE TRIGGER IF NOT EXISTS archive_interrupted_cycle_v6064
BEFORE UPDATE OF status ON clip_jobs
WHEN OLD.status='running' AND NEW.status IN ('queued','failed')
 AND NEW.error_message='Previous recording interrupted; stop confirmed'
BEGIN
    SELECT CASE WHEN OLD.attempts<1 OR OLD.started_at IS NULL
        OR NEW.attempts != OLD.attempts + CASE WHEN NEW.status='queued' THEN 1 ELSE 0 END
        OR NOT EXISTS (SELECT 1 FROM worker_runs w WHERE w.job_id=OLD.id)
        THEN RAISE(ABORT,'invalid interrupted cycle') END;
    INSERT INTO recording_cycles
        (job_id,attempt,platform,stream_id,channel,status,output_dir,error_message,
         created_at,started_at,finished_at,pid,worker_started_at,heartbeat_at,
         deadline_at,exit_code,ended_at,log_path)
        SELECT OLD.id,OLD.attempts,OLD.platform,OLD.stream_id,OLD.channel,
               'interrupted',OLD.output_dir,NEW.error_message,OLD.created_at,
               OLD.started_at,OLD.finished_at,w.pid,w.started_at,w.heartbeat_at,
               w.deadline_at,w.exit_code,w.ended_at,w.log_path
        FROM worker_runs w WHERE w.job_id=OLD.id;
    DELETE FROM worker_runs WHERE job_id=OLD.id;
    DELETE FROM job_reservations WHERE platform=OLD.platform AND stream_id=OLD.stream_id;
END"""

# Exact remote compare-and-set, plus the same global reservation and archive gates.
RECOVERY_SQL = """UPDATE clip_jobs AS j
SET status=?, attempts=attempts+?, started_at=NULL, output_dir=NULL,
    finished_at=?, error_message=?
WHERE id=? AND platform='twitch' AND stream_id=? AND channel=? AND status='running'
  AND attempts=? AND started_at=?
  AND EXISTS (SELECT 1 FROM worker_runs w WHERE w.job_id=j.id
      AND w.heartbeat_at=? AND w.ended_at IS ? AND w.log_path=?
      AND w.started_at IS ? AND w.pid IS ?)
  AND NOT EXISTS (SELECT 1 FROM clip_jobs a WHERE a.id!=j.id AND a.status IN ('queued','running'))
  AND NOT EXISTS (SELECT 1 FROM job_reservations r LEFT JOIN clip_jobs x
      ON x.platform=r.platform AND x.stream_id=r.stream_id
      WHERE (x.id IS NULL OR x.status NOT IN ('completed','failed'))
        AND (x.id IS NULL OR x.id!=j.id))
  AND NOT EXISTS (SELECT 1 FROM clips c JOIN clip_review r ON r.clip_id=c.clip_id
      LEFT JOIN clip_media m ON m.clip_id=c.clip_id
      WHERE c.platform=j.platform AND c.stream_id=j.stream_id
        AND r.quality_status='passed' AND m.clip_id IS NULL)
RETURNING id,attempts"""

OLD_ROWS = """SELECT j.id,j.attempts,j.platform,j.stream_id,j.channel,j.started_at,
    w.heartbeat_at,w.ended_at,w.log_path,w.started_at,w.pid
    FROM clip_jobs j LEFT JOIN worker_runs w ON w.job_id=j.id
    WHERE j.status='running' ORDER BY j.id"""

def recovery_key(row):
    jid,attempt,platform,sid,channel,started = row[:6]
    identity = json.dumps([jid,attempt,platform,sid,channel,started],separators=(',',':'))
    return f'{jid}:{attempt}:' + hashlib.sha256(identity.encode()).hexdigest()[:16]

def age_seconds(value):
    try:
        return (datetime.now(timezone.utc)-datetime.fromisoformat(value)).total_seconds()
    except (ValueError,TypeError):
        return -1

def recovery_details(db):
    return [{'job_id':row[0], 'attempt':row[1], 'channel':row[4],
             'key':recovery_key(row), 'worker_heartbeat':row[6],
             'worker_end_recorded':row[7] is not None}
            for row in db.execute(OLD_ROWS).fetchall()]

def recover(db, db_path=None):
    """Only called after a successful live poll and persisted history sync.
    A stale heartbeat alone is NOT evidence that a worker stopped. Operator
    confirmation must be bound to the exact run, or its end must be persisted.
    """
    from turso_jobs import decode_value
    from turso_store import execute, StoreError
    from streamer_status import resolve
    from source_rights import approved_channels
    from stream_monitor import now
    local = db.execute(OLD_ROWS).fetchall()
    if len(local)!=1:
        return 'multiple_or_no_unresolved_jobs'
    row = local[0]
    jid,attempt,platform,sid,channel,started,heartbeat,ended,logpath,worker_started,pid = row
    if platform!='twitch' or attempt<1 or not started or not heartbeat or not logpath or not worker_started:
        return 'incomplete_worker_evidence'
    # Compare local evidence with the current remote state before making changes.
    remote = execute([(OLD_ROWS, ())])[0]['rows']
    remote = [tuple(decode_value(cell) for cell in item) for item in remote]
    if remote != local:
        return 'remote_state_changed_restart_required'
    if ended is None:
        if db_path is None or not confirmation_matches(db_path, recovery_key(row), heartbeat):
            return 'previous_worker_stop_not_confirmed'
        if age_seconds(heartbeat)<120:
            return 'previous_worker_heartbeat_too_recent'
    elif age_seconds(ended)<0:
        return 'invalid_worker_end_timestamp'
    # Never restart an unauthorized, disabled, stale or offline source.
    source = db.execute("""SELECT s.status,s.last_seen_at,p.status,p.checked_at,u.enabled
        FROM streams s JOIN streamers u ON u.platform=s.platform AND u.channel=s.channel
        LEFT JOIN streamer_status p ON p.platform=s.platform AND p.channel=s.channel
        WHERE s.platform=? AND s.stream_id=? AND s.channel=?""",(platform,sid,channel)).fetchone()
    if not source or not source[4]:
        resume = False
    else:
        current = resolve(source[2],source[3],180)
        if current not in ('live','offline'):
            return 'awaiting_fresh_source_status'
        resume = (current=='live' and source[0]=='live' and
                  resolve('live',source[1],180)=='live' and channel in approved_channels())
    state = 'queued' if resume else 'failed'
    finish = None if resume else now()
    delta = int(resume)
    execute([(RECOVERY_TRIGGER, ())])
    rows = execute([(RECOVERY_SQL,(state,delta,finish,RECOVERY_REASON,jid,sid,channel,
        attempt,started,heartbeat,ended,logpath,worker_started,pid))])[0]['rows']
    if not rows:
        return 'remote_recovery_gate_blocked'
    if len(rows)!=1 or tuple(decode_value(c) for c in rows[0])!=(jid,attempt+delta):
        raise StoreError('Invalid recovery response')
    # Remote commit first. If local commit fails, fail closed; restore on restart.
    with db:
        changed=db.execute("""UPDATE clip_jobs SET status=?,attempts=attempts+?,
            started_at=NULL,output_dir=NULL,finished_at=?,error_message=?
            WHERE id=? AND status='running' AND attempts=? AND started_at=?""",
            (state,delta,finish,RECOVERY_REASON,jid,attempt,started)).rowcount
        if changed!=1:
            raise StoreError('Local recovery state conflict')
        db.execute('DELETE FROM worker_runs WHERE job_id=?',(jid,))
    print('PRODUCTION: INTERRUPTED RUN RECOVERED',jid,'attempt',attempt,'next',state,flush=True)
    return 'recovered_'+state

def heartbeat_path(db_path):
    return Path(str(db_path)+'.monitor.json')

def write_state(db_path, phase, recording, details=(), reason=''):
    path=heartbeat_path(db_path)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps({'updated':time.time(),'phase':phase,'recording':recording,
        'unresolved':list(details),'reason':reason}),encoding='utf-8')
    tmp.replace(path)

def read_state(db_path, process=None, launched_at=None):
    if process is not None and process.poll() is not None:
        return {'phase':'stopped','recording':'blocked','unresolved':[], 'reason':'monitor_process_exited'}
    try:
        value=json.loads(heartbeat_path(db_path).read_text(encoding='utf-8'))
        age=time.time()-float(value['updated'])
        if 0<=age<=300:
            return value
    except (OSError,ValueError,TypeError,KeyError):
        pass
    if launched_at is not None and 0<=time.time()-launched_at<=120:
        return {'phase':'starting','recording':'blocked','unresolved':[], 'reason':'initializing'}
    return {'phase':'stale','recording':'blocked','unresolved':[], 'reason':'monitor_heartbeat_missing_or_stale'}


# Instance-local, short-lived operator confirmation. Never a replacement for
# verifying that the previous Render instance and its recording worker stopped.
CONFIRM_TTL = 600


def confirmation_path(db_path):
    return Path(str(db_path) + '.recovery-confirm.json')


def clear_confirmation(db_path):
    confirmation_path(db_path).unlink(missing_ok=True)


def read_confirmation(db_path):
    try:
        data = json.loads(confirmation_path(db_path).read_text(encoding='utf-8'))
        age = time.time() - float(data['confirmed_at'])
        if 0 <= age <= CONFIRM_TTL and isinstance(data['key'], str) and isinstance(data['worker_heartbeat'], str):
            return data
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def confirmation_matches(db_path, key, heartbeat):
    data = read_confirmation(db_path)
    return bool(data and data['key'] == key and data['worker_heartbeat'] == heartbeat)


def confirmable_run(state):
    if state.get('phase') != 'running' or state.get('recording') != 'blocked':
        return None
    if state.get('reason') not in ('previous_worker_stop_not_confirmed', 'previous_worker_heartbeat_too_recent', 'awaiting_confirmation'):
        return None
    unresolved = state.get('unresolved') or []
    if len(unresolved) != 1:
        return None
    run = unresolved[0]
    if run.get('worker_end_recorded') or not run.get('key') or age_seconds(run.get('worker_heartbeat')) < 120:
        return None
    return run


def store_confirmation(db_path, state, key):
    import tempfile
    import hmac
    run = confirmable_run(state)
    if run is None or not hmac.compare_digest(key.encode('utf-8'), run['key'].encode('utf-8')):
        return False
    target = confirmation_path(db_path)
    data = {'key': run['key'], 'worker_heartbeat': run['worker_heartbeat'], 'confirmed_at': time.time()}
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, prefix=target.name+'.', delete=False) as f:
            tmp = Path(f.name)
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(target)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return True


def recovery_form(db_path, state, csrf):
    from html import escape
    run = confirmable_run(state)
    if run is None:
        return b''
    key = run['key']
    if confirmation_matches(db_path, key, run['worker_heartbeat']):
        return ('<section><h2>Auftragswiederherstellung</h2><p>Bestätigung eingegangen. '
                'StreamClipper prüft den Auftrag beim nächsten erfolgreichen Live- und Datenbank-Prüfzyklus. '
                'Die Aufnahme bleibt bis dahin gesperrt.</p></section>').encode('utf-8')
    return (f'<section><h2>Auftragswiederherstellung</h2>'
            f'<p>Unterbrochener Auftrag #{escape(str(run["job_id"]))} – {escape(run["channel"])}</p>'
            '<p class="note">Nur bestätigen, wenn die vorherige Render-Instanz einschließlich ihres '
            'Aufnahmeprozesses nachweislich beendet ist. Ein altes Worker-Signal allein genügt nicht.</p>'
            '<form method="post" action="/recover-recording">'
            f'<input type="hidden" name="key" value="{escape(key, quote=True)}">'
            f'<input type="hidden" name="csrf" value="{escape(csrf, quote=True)}">'
            '<p><label><input type="checkbox" name="stopped" value="yes" required> '
            'Ich habe das Ende der vorherigen Instanz und ihrer Aufnahme überprüft.</label></p>'
            '<button type="submit">Aufnahme sicher wiederherstellen</button></form></section>').encode('utf-8')


def handle_recovery_request(handler, db_path, state_reader, csrf):
    """Called only behind the existing authenticated dashboard POST handler."""
    import hmac
    from urllib.parse import parse_qs
    hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    if not hostname or handler.headers.get('Origin') != 'https://' + hostname:
        return handler.respond(403, b'Invalid origin')
    if handler.headers.get('Transfer-Encoding'):
        return handler.respond(400, b'Invalid request')
    if handler.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/x-www-form-urlencoded':
        return handler.respond(415, b'Invalid content type')
    try:
        size = int(handler.headers.get('Content-Length', '0'))
        if not 1 <= size <= 2048:
            raise ValueError('Invalid size')
        handler.connection.settimeout(15)
        body = handler.rfile.read(size)
        if len(body) != size:
            raise ValueError('Incomplete body')
        fields = parse_qs(body.decode('utf-8'), strict_parsing=True, max_num_fields=3, keep_blank_values=True)
        if set(fields) != {'key', 'csrf', 'stopped'} or any(len(v) != 1 for v in fields.values()):
            raise ValueError('Invalid fields')
    except (ValueError, UnicodeError, OSError):
        return handler.respond(400, b'Invalid confirmation request')
    if not hmac.compare_digest(fields['csrf'][0].encode('utf-8'), csrf.encode('utf-8')) or fields['stopped'] != ['yes']:
        return handler.respond(403, b'Confirmation required')
    try:
        if not store_confirmation(db_path, state_reader(), fields['key'][0]):
            return handler.respond(409, b'Recovery state changed or not eligible; reload dashboard')
    except OSError:
        return handler.respond(503, b'Confirmation unavailable')
    print('PRODUCTION: DASHBOARD RECOVERY CONFIRMED', fields['key'][0], flush=True)
    handler.send_response(303)
    handler.send_header('Location', '/')
    handler.send_header('Content-Length', '0')
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
