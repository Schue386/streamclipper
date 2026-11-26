"""Single-host StreamClipper OS coordinator. No publishing and no public HTTP exposure.

One service process owns the supervisor. Dashboard can run in a second process,
sharing the same local SQLite file. No distributed deployment support.
"""
from __future__ import annotations
from contextlib import closing
import argparse
import json
import logging
import sqlite3
import time
from youtube_worker import run_next as youtube_next
import urllib.error
from stream_monitor import connect, check
from kick_monitor import check as check_kick
from youtube_monitor import check as check_youtube
from clip_jobs import enqueue_detected
from supervisor import Supervisor
from control import enabled as control_enabled

log = logging.getLogger('streamclipper.service')

def cycle(db, supervisor, *, demo_file=None, poll=True, upload=False, sender=None):
    """Continue supervising existing jobs even when stream discovery fails."""
    result={'poll':'skipped','new_streams':0,'queued':0}
    if poll:
        try:
            events=check(db,demo_file)
            result.update(poll='ok',new_streams=len(events),queued=enqueue_detected(db) if control_enabled(db) and not supervisor.persistent_jobs else 0)
        except (ValueError,KeyError,TypeError,sqlite3.Error,urllib.error.URLError,
                TimeoutError,OSError,json.JSONDecodeError) as exc:
            result.update(poll='failed',poll_error=f'{type(exc).__name__}: {exc}'[:300])
            log.warning('Stream poll failed: %s',exc)
    if poll and not demo_file:
        try:
            result['kick']={'status':'ok','new_streams':len(check_kick(db))}
        except (ValueError,KeyError,TypeError,sqlite3.Error,OSError) as exc:
            result['kick']={'status':'failed','error':type(exc).__name__}
    if poll and not demo_file:
        try:
            result['youtube_live']={'status':'ok','new_streams':len(check_youtube(db))}
        except (ValueError,KeyError,TypeError,sqlite3.Error,OSError) as exc:
            result['youtube_live']={'status':'failed','error':type(exc).__name__}
    result['supervisor']=supervisor.tick()
    if upload and control_enabled(db):
        try:
            result['youtube']=youtube_next(db,execute=True,sender=sender)
        except Exception as exc:
            log.warning('YouTube worker failed: %s',type(exc).__name__)
            result['youtube']={'status':'error'}
    else:
        result['youtube']={'status':'disabled'}
    return result

def main():
    p=argparse.ArgumentParser(description='Single-host StreamClipper OS v9 service')
    p.add_argument('--db',default='streamclipper_os.sqlite3')
    p.add_argument('--out',default='live_clips')
    p.add_argument('--demo-file')
    p.add_argument('--execute',action='store_true',help='Permit real recording if control is ON')
    p.add_argument('--youtube-upload',action='store_true',help='Deprecated; use youtube_daemon.py instead')
    p.add_argument('--workers',type=int,default=1)
    p.add_argument('--timeout',type=int,default=7200)
    p.add_argument('--interval',type=int,default=120,help='Twitch poll seconds, >=60')
    p.add_argument('--tick',type=int,default=10,help='Supervisor tick seconds, >=1')
    p.add_argument('--persistent-history', action='store_true')
    p.add_argument('--once',action='store_true',help='Run one cycle and exit')
    args=p.parse_args()
    if args.persistent_history and args.execute:
        p.error("Persistent recording not yet enabled")
    if args.youtube_upload: p.error('Use youtube_daemon.py for independent uploads')
    if args.interval<60:p.error('--interval must be >=60')
    if args.tick<1:p.error('--tick must be >=1')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    with closing(connect(args.db)) as db:
        if args.persistent_history:
            import turso_history as history
            history.restore_streams(db)
            history.prepare_tracking(db)
            import turso_jobs as jobs
            jobs.restore_jobs(db)
            jobs.prepare_tracking(db)
            import turso_catalog as catalog
            catalog.restore_catalog(db)
            import turso_media as media
            media.restore_media(db)

            import os
            from threading import Thread

            verify_id = os.getenv(
                "STREAMCLIPPER_VERIFY_CLIP_ID", ""
            )

            if verify_id and os.getenv(
                "STREAMCLIPPER_V58_TEST"
            ) != "1":
                Thread(
                    target=media.verify_on_start,
                    args=(args.db, verify_id),
                    daemon=True
                ).start()


            catalog.prepare_tracking(db)

            import os
            if os.getenv("STREAMCLIPPER_V58_TEST") == "1":
                from threading import Thread
                from archive_probe import probe

                Thread(target=probe, daemon=True).start()


        supervisor=Supervisor(db,args.out,args.workers,args.timeout,args.execute)
        supervisor.persistent_jobs = args.persistent_history
        active=db.execute("SELECT COUNT(*) FROM clip_jobs WHERE status='running'").fetchone()[0]
        if active and not args.persistent_history:p.error(f'{active} running jobs from earlier process; inspect PIDs before --recover')
        next_poll=0.0
        try:
            while True:
                current=time.monotonic()
                do_poll=current>=next_poll
                # Discovery remains active independently of recording.
                result=cycle(db,supervisor,demo_file=args.demo_file,poll=do_poll,upload=False)
                if do_poll and args.persistent_history:
                    try:
                        saved = history.save_streams(db)
                        result["history"] = {
                            "status": "ok",
                            "saved": saved
                        }
                    except (history.StoreError, sqlite3.Error) as exc:
                        result["history"] = {
                            "status": "retry_pending",
                            "error": type(exc).__name__
                        }

                if args.persistent_history:
                    result["jobs"] = {
                        "status": "read_only",
                        "pending": db.execute(
                            "SELECT COUNT(*) FROM jobs_pending"
                        ).fetchone()[0]
                    }

                if args.persistent_history:
                    result["catalog"] = {
                        "status": "read_only",
                        "pending": db.execute(
                            "SELECT COUNT(*) FROM catalog_pending"
                        ).fetchone()[0]
                    }

                if do_poll:next_poll=current+args.interval
                print(json.dumps(result,ensure_ascii=False),flush=True)
                if args.once:break
                time.sleep(args.tick)
        except KeyboardInterrupt:
            pass
        finally:
            supervisor.shutdown()
if __name__=='__main__':main()
