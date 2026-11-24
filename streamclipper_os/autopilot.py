"""Central single-cycle coordinator for StreamClipper OS.

By default no recording or publication is started. A local long-running host
is required for --loop. Run one cycle with --demo-file for offline testing.
"""
from __future__ import annotations
from contextlib import closing
import argparse
import json
import logging
import sqlite3
import time
import urllib.error
from pathlib import Path
from stream_monitor import connect, check, now
from clip_jobs import prepare as prepare_jobs, run_next
from clip_importer import import_completed
from clip_catalog import prepare as prepare_catalog

log = logging.getLogger('streamclipper.autopilot')

def enqueue_events(db: sqlite3.Connection, events: list[dict]) -> int:
    """Queue only stream IDs newly observed in this poll, never stale live rows."""
    prepare_jobs(db)
    inserted = 0
    with db:
        for event in events:
            if event.get('platform') != 'twitch':
                continue
            row = db.execute("""SELECT enabled FROM streamers
                WHERE platform=? AND channel=?""",
                ('twitch', event['channel'])).fetchone()
            if row is None or row[0] != 1:
                continue
            cur = db.execute("""INSERT OR IGNORE INTO clip_jobs
                (platform,stream_id,channel,created_at) VALUES(?,?,?,?)""",
                ('twitch',event['stream_id'],event['channel'],now()))
            inserted += cur.rowcount
    return inserted

def summary(db: sqlite3.Connection) -> dict:
    prepare_jobs(db)
    prepare_catalog(db)
    def grouped(table):
        return {status:count for status,count in db.execute(
            f'SELECT status,COUNT(*) FROM {table} GROUP BY status')}
    return {
        'streamers_enabled':db.execute('SELECT COUNT(*) FROM streamers WHERE enabled=1').fetchone()[0],
        'streams':db.execute('SELECT COUNT(*) FROM streams').fetchone()[0],
        'jobs':grouped('clip_jobs'),
        'clips':grouped('clips'),
        'publications':grouped('publications'),
    }

def cycle(db: sqlite3.Connection, *, demo_file: str|None=None,
          execute: bool=False, output_root: str='live_clips',
          timeout_seconds: int=7200) -> dict:
    """Poll, queue new streams, optionally process ONE job, import completed clips.

    Any poll failure aborts before queue execution to avoid acting on stale data.
    """
    events = check(db,demo_file)
    queued = enqueue_events(db,events)
    action = run_next(db,output_root,execute,timeout_seconds)
    imported = import_completed(db)
    return {'detected':len(events),'queued':queued,'job':action,
            'imports':imported,'summary':summary(db)}

def main() -> None:
    p=argparse.ArgumentParser(description='StreamClipper OS v5 coordinator (no publishing)')
    p.add_argument('--db',default='streamclipper_os.sqlite3')
    p.add_argument('--demo-file',help='Local sample Twitch response (no network)')
    p.add_argument('--execute',action='store_true',help='Actually run the live clipping CLI')
    p.add_argument('--out',default='live_clips')
    p.add_argument('--timeout',type=int,default=7200)
    p.add_argument('--loop',action='store_true',help='Keep polling on a running host')
    p.add_argument('--interval',type=int,default=120)
    args=p.parse_args()
    if args.interval<60:p.error('--interval must be at least 60 seconds')
    if args.timeout<60:p.error('--timeout must be at least 60 seconds')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    with closing(connect(args.db)) as db:
        while True:
            try:
                result=cycle(db,demo_file=args.demo_file,execute=args.execute,
                    output_root=args.out,timeout_seconds=args.timeout)
                print(json.dumps(result,indent=2),flush=True)
            except (ValueError,sqlite3.Error,OSError,urllib.error.URLError,TimeoutError) as exc:
                log.error('Cycle failed: %s',exc)
                if not args.loop:raise
            if not args.loop:break
            time.sleep(args.interval)
if __name__=='__main__':main()
