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
from runtime_recovery import recover, recovery_details, write_state, clear_confirmation
from turso_store import StoreError
from job_diagnostics import Diagnostics
from maintenance import Maintenance

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

def run_archive_cycle(db_path):
    try:
        from clip_archive import archive_next

        with closing(connect(db_path)) as db:
            result = archive_next(db)

        print(
            "ARCHIVE:", json.dumps(result),
            flush=True
        )

    except Exception as exc:
        print(
            "ARCHIVE: CYCLE FAILED",
            type(exc).__name__,
            flush=True
        )


def production_configured():
    import os

    return (
        os.getenv("STREAMCLIPPER_PRODUCTION") == "1"
        and os.getenv("STREAMCLIPPER_DATA_SYNC") == "1"
        and os.getenv("STREAMCLIPPER_AUTO_ARCHIVE") == "1"
        and os.getenv("STREAMCLIPPER_V58_TEST") != "1"
        and not os.getenv("STREAMCLIPPER_VERIFY_CLIP_ID")
        and not os.getenv("STREAMCLIPPER_WEBHOOK")
    )


def queue_production(db, supervisor, result):
    import os
    from clip_jobs import enqueue_production, enqueue_followup

    if not supervisor.persistent_jobs:
        return 0

    if not supervisor.execute or supervisor.maintenance_blocked or not control_enabled(db):
        return 0

    if os.getenv("STREAMCLIPPER_DATA_SYNC") != "1":
        return 0

    if os.getenv("STREAMCLIPPER_V58_TEST") == "1":
        return 0

    if result.get("poll") != "ok":
        return 0

    if result.get("history", {}).get("status") != "ok":
        return 0

    for table in (
        "history_pending",
        "jobs_pending",
        "catalog_pending"
    ):
        if db.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]:
            return 0

    from source_rights import approved_channels
    from turso_store import StoreError

    try:
        channels = approved_channels()
    except StoreError:
        result["queue_error"] = "Source rights unavailable"
        return 0

    # No new recording may race the archive thread or pending sync.
    if supervisor.archive_busy or supervisor.active:
        return 0
    created = enqueue_production(db, channels)
    if created:
        return created
    try:
        return enqueue_followup(db, channels)
    except (StoreError, sqlite3.Error) as exc:
        # Remote UPDATE may have succeeded before a connection failed. Fail closed.
        supervisor.execute = False
        result["queue_error"] = "Cycle state uncertain; restart required"
        log.warning("Followup stopped: %s", type(exc).__name__)
        return 0


def sync_persistent_data(db):
    import turso_jobs as jobs
    import turso_catalog as catalog

    result = {
        "jobs": {"status": "waiting_for_history"},
        "catalog": {"status": "waiting_for_history"}
    }

    if db.execute(
        "SELECT COUNT(*) FROM history_pending"
    ).fetchone()[0]:
        return result

    for name, module, table in (
        ("jobs", jobs, "jobs_pending"),
        ("catalog", catalog, "catalog_pending")
    ):
        try:
            saved = module.save_pending(db)

            pending = db.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

            result[name] = {
                "status": "pending" if pending else "ok",
                "saved": saved,
                "pending": pending
            }

        except (module.StoreError, sqlite3.Error) as exc:
            result[name] = {
                "status": "retry_pending",
                "error": type(exc).__name__
            }

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
        if (
            not production_configured()
            or args.workers != 1
            or args.timeout != 2700
            or args.demo_file
            or args.once
        ):
            p.error("Invalid production configuration")
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
        if args.persistent_history:
            from control import set_enabled

            set_enabled(db, False)

            if args.execute:
                if active:
                    supervisor.execute = False
                    print("PRODUCTION: RECOVERY BLOCKED", json.dumps(recovery_details(db)), flush=True)
                else:
                    set_enabled(db, True)
                    print("PRODUCTION: ENABLED", flush=True)
                print("PRODUCTION: v60.7.1 PILOT recording_limit_min=30 worker_timeout_s=2700 workers=1 sequential_cycles=1 dashboard_recovery=1 low_memory_profile=1 job_diagnostics=1 shared_production_slot=1 maintenance=1", flush=True)

        recovery_pending = bool(args.persistent_history and args.execute and active)
        recovery_uncertain = False
        recovery_reason = 'awaiting_confirmation' if recovery_pending else ''
        write_state(args.db, 'running', 'blocked' if recovery_pending else ('enabled' if supervisor.execute else 'disabled'),
                    recovery_details(db) if recovery_pending else (), recovery_reason)

        next_poll=0.0
        archive_thread = None
        diagnostics = Diagnostics(args.db) if args.persistent_history and args.execute else None
        maintenance = Maintenance(args.db, enabled=bool(args.persistent_history and args.execute))
        try:
            while True:
                current=time.monotonic()
                do_poll=current>=next_poll
                # Discovery remains active independently of recording.
                supervisor.archive_busy = (
                    archive_thread is not None
                    and archive_thread.is_alive()
                )

                maintenance.before(supervisor,recovery_pending)
                result=cycle(db,supervisor,demo_file=args.demo_file,poll=do_poll,upload=False)
                # Capture final evidence before a follow-up clears the old output path.
                if diagnostics is not None:
                    try:
                        diagnostics.tick(db, active=tuple(supervisor.active),
                                         finished=bool(result.get('supervisor',{}).get('finished')))
                    except Exception as exc:
                        log.warning('Recording diagnostics unavailable: %s',type(exc).__name__)
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

                if (
                    args.persistent_history
                    and os.getenv("STREAMCLIPPER_DATA_SYNC") == "1"
                    and os.getenv("STREAMCLIPPER_V58_TEST") != "1"
                ):
                    if recovery_pending and do_poll and result.get('poll') == 'ok' and result.get('history',{}).get('status') == 'ok':
                        if not recovery_uncertain:
                            try:
                                if any(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                       for table in ('history_pending','jobs_pending','catalog_pending')):
                                    recovery_reason = 'pending_sync'
                                else:
                                    recovery_reason = recover(db, args.db)
                                    if recovery_reason.startswith('recovered_'):
                                        # Exact job transition persisted first; sync local copy before launching.
                                        recovered_sync = sync_persistent_data(db)
                                        if recovered_sync.get('jobs',{}).get('status') == 'ok':
                                            recovery_pending = False
                                            supervisor.execute = True
                                            set_enabled(db, True)
                                            try:
                                                clear_confirmation(args.db)
                                            except OSError:
                                                log.warning('Expired recovery confirmation will be ignored')
                                            print('PRODUCTION: RECORDING RESUMED', flush=True)
                                        else:
                                            recovery_uncertain = True
                                            recovery_reason = 'recovered_sync_uncertain_restart_required'
                            except (StoreError, sqlite3.Error, ValueError, OSError) as exc:
                                recovery_uncertain = True
                                recovery_reason = 'recovery_uncertain_restart_required'
                                log.warning('Recovery stopped: %s',type(exc).__name__)
                        result['recovery'] = {'status':recovery_reason,'jobs':recovery_details(db)}
                    if do_poll and not args.demo_file and not recovery_pending:
                        try:
                            result["queued"] = queue_production(
                                db, supervisor, result
                            )
                        except (ValueError, sqlite3.Error) as exc:
                            result["queue_error"] = type(exc).__name__

                    if not recovery_pending:
                        result.update(sync_persistent_data(db))

                if (
                    do_poll
                    and args.persistent_history
                    and os.getenv("STREAMCLIPPER_DATA_SYNC") == "1"
                    and os.getenv("STREAMCLIPPER_AUTO_ARCHIVE") == "1"
                    and os.getenv("STREAMCLIPPER_V58_TEST") != "1"
                    and not os.getenv("STREAMCLIPPER_VERIFY_CLIP_ID")
                    and not supervisor.active
                    and not maintenance.ready
                    and result.get("jobs", {}).get("status") == "ok"
                    and result.get("catalog", {}).get("status") == "ok"
                ):
                    if archive_thread is None or not archive_thread.is_alive():
                        archive_thread = Thread(
                            target=run_archive_cycle,
                            args=(args.db,),
                            daemon=True
                        )
                        archive_thread.start()

                maintenance.after(db,supervisor,bool(archive_thread and archive_thread.is_alive()),result,diagnostics,recovery_pending)
                reason = recovery_reason if recovery_pending or not maintenance.blocked else ('maintenance_ready' if maintenance.ready else 'maintenance_draining')
                write_state(args.db,'running',
                    'blocked' if recovery_pending or maintenance.blocked or (args.execute and not supervisor.execute) else ('enabled' if supervisor.execute and control_enabled(db) else 'disabled'),
                    recovery_details(db) if recovery_pending else (), reason)
                if do_poll:next_poll=current+args.interval
                print(json.dumps(result,ensure_ascii=False),flush=True)
                if args.once:break
                time.sleep(args.tick)
        except KeyboardInterrupt:
            pass
        finally:
            supervisor.shutdown()
if __name__=='__main__':main()
