"""Non-blocking process supervisor for StreamClipper jobs (single host).

No publishing. Fail closed after unexpected restarts; no automatic duplicate recordings.
"""
from __future__ import annotations
from contextlib import closing
import argparse
import json
import os
import signal
import uuid
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from stream_monitor import connect, now
from clip_jobs import prepare, plan_next
from clip_importer import import_completed
from control import enabled as control_enabled

SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_runs (
 job_id INTEGER PRIMARY KEY REFERENCES clip_jobs(id),
 pid INTEGER, started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
 deadline_at REAL NOT NULL, exit_code INTEGER, ended_at TEXT,
 log_path TEXT NOT NULL
);
"""

def initialize(db):
    prepare(db)
    db.executescript(SCHEMA)

def reconcile_after_restart(db):
    """Mark orphaned runs as requiring operator review, not safe to auto-retry.

    The previous process may still exist. We intentionally do not kill or restart it.
    """
    initialize(db)
    with db:
        rows=db.execute("SELECT id FROM clip_jobs WHERE status='running'").fetchall()
        for (job_id,) in rows:
            db.execute("UPDATE clip_jobs SET status='failed',error_message=?,finished_at=? WHERE id=?",
                       ('Supervisor restarted: check prior PID and output before retry',now(),job_id))
            db.execute("UPDATE worker_runs SET ended_at=COALESCE(ended_at,?) WHERE job_id=?",(now(),job_id))
    return len(rows)

def production_allowed(db, plan, status="queued"):
    import re
    from streamer_status import resolve

    if os.getenv("STREAMCLIPPER_DATA_SYNC") != "1":
        return False

    if not control_enabled(db):
        return False

    from source_rights import approved_channels
    from turso_store import StoreError

    try:
        allowed = approved_channels()
    except StoreError:
        return False

    channel = plan["channel"]
    sid = plan["stream_id"]

    if channel not in allowed:
        return False

    if not re.fullmatch(r"[a-z0-9_]{1,25}", channel):
        return False

    if not re.fullmatch(r"[0-9]{1,40}", sid):
        return False

    row = db.execute("""
        SELECT s.last_seen_at,p.status,p.checked_at
        FROM clip_jobs j
        JOIN streams s
          ON s.platform=j.platform
         AND s.stream_id=j.stream_id
         AND s.channel=j.channel
        JOIN streamers w
          ON w.platform=j.platform
         AND w.channel=j.channel
        JOIN streamer_status p
          ON p.platform=j.platform
         AND p.channel=j.channel
        WHERE j.id=?
          AND j.platform='twitch'
          AND j.stream_id=?
          AND j.channel=?
          AND j.status=?
          AND s.status='live'
          AND w.enabled=1
    """, (plan["job_id"],sid,channel,status)).fetchone()

    if not row:
        return False

    return (
        resolve("live",row[0],180) == "live"
        and resolve(row[1],row[2],180) == "live"
    )


class Supervisor:
    def __init__(self,db,output_root='live_clips',max_workers=2,timeout=7200,
                 execute=False,popen_factory=None):
        if not 1<=max_workers<=8:raise ValueError('max_workers must be 1..8')
        if timeout<60:raise ValueError('timeout must be >= 60')
        self.db=db;self.output_root=output_root;self.max_workers=max_workers
        self.timeout=timeout;self.execute=execute
        self.spawn=popen_factory or subprocess.Popen
        self.active={}
        self.reservation_owner = uuid.uuid4().hex
        self.persistent_jobs = False
        self.archive_busy = False
        initialize(db)

    def stop_recording(self, proc):
        if proc.poll() is not None:
            return

        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        proc.wait(timeout=10)

    def enforce_source_rights(self):
        from source_rights import approved_channels
        from turso_store import StoreError

        rows = self.db.execute("""
            SELECT j.id,j.channel,j.status,
                   COALESCE(s.enabled,0)
            FROM clip_jobs j
            LEFT JOIN streamers s
              ON s.platform=j.platform
             AND s.channel=j.channel
            WHERE j.platform='twitch'
              AND j.status IN ('queued','running')
        """).fetchall()

        if not rows:
            return True, []

        try:
            allowed = approved_channels()
            available = True
        except StoreError:
            allowed = set()
            available = False

        stopped = []

        for jid,channel,status,enabled in rows:
            if enabled and channel in allowed:
                continue

            if status == "queued":
                if not available:
                    continue

            elif jid in self.active:
                proc,_ = self.active[jid]
                self.stop_recording(proc)

                with self.db:
                    self.db.execute("""
                        UPDATE worker_runs
                        SET exit_code=?,ended_at=?,
                            heartbeat_at=?
                        WHERE job_id=?
                    """,(proc.returncode,now(),now(),jid))

                del self.active[jid]

            else:
                continue

            reason = (
                "Source rights unavailable"
                if not available
                else "Source permission revoked or disabled"
            )

            with self.db:
                self.db.execute("""
                    UPDATE clip_jobs
                    SET status='failed',
                        error_message=?,finished_at=?
                    WHERE id=?
                """,(reason,now(),jid))

            stopped.append({
                "job_id":jid,
                "status":"failed",
                "reason":reason
            })

            print("SUPERVISOR: SOURCE STOPPED",
                  jid,flush=True)

        return available,stopped

    def tick(self):
        rights_ok = True
        stopped = []

        if self.persistent_jobs and (
            self.execute or self.active
        ):
            rights_ok,stopped = (
                self.enforce_source_rights()
            )

        completed=[]
        for job_id,(proc,deadline) in list(self.active.items()):
            code=proc.poll()
            if code is None and time.monotonic()>=deadline:
                proc.terminate()
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=10)
                code=proc.returncode
                error='Clipping timeout exceeded'
            else:
                error=None if code in (None,0) else f'Clipping exited with code {code}'
            if code is None:
                with self.db:self.db.execute('UPDATE worker_runs SET heartbeat_at=? WHERE job_id=?',(now(),job_id))
                continue
            status='completed' if code==0 and error is None else 'failed'
            with self.db:
                self.db.execute('UPDATE clip_jobs SET status=?,error_message=?,finished_at=? WHERE id=?',
                                (status,error,now(),job_id))
                self.db.execute('UPDATE worker_runs SET exit_code=?,ended_at=?,heartbeat_at=? WHERE job_id=?',
                                (code,now(),now(),job_id))
            completed.append({'job_id':job_id,'status':status})
            del self.active[job_id]
        imported=import_completed(self.db) if completed else {'imported':0}
        launched=[]
        storage_blocked=False
        if self.execute and control_enabled(self.db) and os.environ.get("STREAMCLIPPER_MIN_FREE_GIB"):
            try:
                Path(self.output_root).mkdir(parents=True,exist_ok=True)
                storage_blocked=shutil.disk_usage(self.output_root).free < int(os.environ["STREAMCLIPPER_MIN_FREE_GIB"])*1024**3
            except (OSError,ValueError):
                storage_blocked=True
        if (
            self.execute
            and control_enabled(self.db)
            and not storage_blocked
            and not self.archive_busy
            and rights_ok
        ):
            while len(self.active)<self.max_workers:
                plan=plan_next(self.db,self.output_root)
                if plan is None:break
                if self.persistent_jobs:
                    if not production_allowed(self.db, plan):
                        print("SUPERVISOR: SOURCE BLOCKED", flush=True)
                        break

                    if self.db.execute(
                        "SELECT COUNT(*) FROM jobs_pending"
                    ).fetchone()[0]:
                        break

                job_id=plan['job_id'];out=Path(plan['output_dir']);out.mkdir(parents=True,exist_ok=True)
                logpath=out/'supervisor.log'
                if self.persistent_jobs:
                    from turso_jobs import reserve_production_job
                    from turso_store import StoreError

                    try:
                        if not reserve_production_job(
                            job_id,
                            plan["stream_id"],
                            plan["channel"],
                            self.reservation_owner
                        ):
                            raise StoreError(
                                "Stream already reserved"
                            )

                    except (StoreError, sqlite3.Error) as exc:
                        self.execute = False
                        print(
                            "SUPERVISOR: RESERVATION BLOCKED",
                            type(exc).__name__,
                            flush=True
                        )
                        break

                with self.db:
                    claim=self.db.execute("UPDATE clip_jobs SET status='running',attempts=attempts+1,started_at=?,output_dir=? WHERE id=? AND status='queued'",
                                          (now(),str(out),job_id))
                if claim.rowcount != 1:
                    if self.persistent_jobs:
                        self.execute = False
                        break
                    continue
                if self.persistent_jobs:
                    from turso_jobs import save_pending
                    from turso_store import StoreError

                    try:
                        save_pending(self.db)

                        pending = self.db.execute(
                            "SELECT COUNT(*) FROM jobs_pending"
                        ).fetchone()[0]

                        if pending:
                            raise StoreError("Unsaved job state")

                        if not production_allowed(
                            self.db, plan, "running"
                        ):
                            raise StoreError(
                                "Source authorization changed"
                            )


                    except (StoreError, sqlite3.Error) as exc:
                        self.execute = False

                        with self.db:
                            self.db.execute("""
                                UPDATE clip_jobs
                                SET status='failed',
                                    error_message=?,
                                    finished_at=?
                                WHERE id=?
                            """,(
                                "Recording blocked before spawn",
                                now(),job_id
                            ))

                        print(
                            "SUPERVISOR: RECORDING BLOCKED",
                            type(exc).__name__,
                            flush=True
                        )
                        break

                try:
                    with logpath.open('ab') as log:
                        command = list(plan["command"])

                        if self.persistent_jobs:
                            command.extend([
                                "--stop-after", "5",
                                "--buffer-min", "5",
                                "--max-clips-per-hour", "2",
                                "--ratio", "9:16",
                                "--asr", "full",
                                "--device", "cpu",
                                "--whisper-model",
                                "/opt/whisper-tiny"
                            ])

                        proc=self.spawn(
                            command,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            **({"start_new_session":True}
                               if self.persistent_jobs
                               else {})
                        )
                except OSError as exc:
                    with self.db:self.db.execute('UPDATE clip_jobs SET status=?,error_message=?,finished_at=? WHERE id=?',
                                                 ('failed',str(exc)[:1500],now(),job_id))
                    continue
                deadline=time.monotonic()+self.timeout
                with self.db:
                    self.db.execute('INSERT OR REPLACE INTO worker_runs(job_id,pid,started_at,heartbeat_at,deadline_at,log_path) VALUES(?,?,?,?,?,?)',
                                    (job_id,getattr(proc,'pid',None),now(),now(),time.time()+self.timeout,str(logpath)))
                self.active[job_id]=(proc,deadline)
                launched.append(job_id)
        return {'enabled':control_enabled(self.db),'active':len(self.active),'launched':launched,'finished':stopped+completed,'imports':imported,'storage_blocked':storage_blocked}

    def shutdown(self):
        """Graceful shutdown; do not abandon live children without recording status."""
        for job_id,(proc,_) in list(self.active.items()):
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=10)
            with self.db:
                self.db.execute('UPDATE clip_jobs SET status=?,error_message=?,finished_at=? WHERE id=?',
                                ('failed','Supervisor stopped; inspect output before retry',now(),job_id))
                self.db.execute('UPDATE worker_runs SET exit_code=?,ended_at=? WHERE job_id=?',
                                (proc.returncode,now(),job_id))
            del self.active[job_id]

def main():
    p=argparse.ArgumentParser(description='StreamClipper OS v6 single-host supervisor')
    p.add_argument('--db',default='streamclipper_os.sqlite3')
    p.add_argument('--out',default='live_clips')
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--timeout',type=int,default=7200)
    p.add_argument('--interval',type=int,default=10)
    p.add_argument('--execute',action='store_true',help='Allow real clipping only when control is started')
    p.add_argument('--loop',action='store_true')
    p.add_argument('--recover',action='store_true',help='Mark interrupted jobs for manual review')
    args=p.parse_args()
    if args.interval<1:p.error('interval must be >=1')
    with closing(connect(args.db)) as db:
        if args.recover:
            print(json.dumps({'interrupted_jobs':reconcile_after_restart(db)}))
            return
        supervisor=Supervisor(db,args.out,args.workers,args.timeout,args.execute)
        # Refuse to launch on top of an unverified previous run.
        existing=db.execute("SELECT COUNT(*) FROM clip_jobs WHERE status='running'").fetchone()[0]
        if existing:
            p.error(f'{existing} running jobs exist; inspect host processes and use --recover before resuming')
        try:
            while True:
                print(json.dumps(supervisor.tick()),flush=True)
                if not args.loop:break
                time.sleep(args.interval)
        except KeyboardInterrupt:pass
        finally:supervisor.shutdown()
if __name__=='__main__':main()
