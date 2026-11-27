"""Idempotent queue for launching the existing StreamClipper live CLI.

No upload or publication is performed. Real execution is explicit opt-in.
"""
from __future__ import annotations
from contextlib import closing
import argparse
import json
import sqlite3
import subprocess
from pathlib import Path
from stream_monitor import connect, now

JOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS clip_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  stream_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK(status IN ('queued','running','completed','failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  output_dir TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  UNIQUE(platform,stream_id),
  FOREIGN KEY(platform,stream_id) REFERENCES streams(platform,stream_id)
);
"""

def prepare(db: sqlite3.Connection) -> None:
    db.executescript(JOB_SCHEMA)

def enqueue_detected(db: sqlite3.Connection) -> int:
    """Queue one job per Twitch stream, without duplicates."""
    with db:
        cursor = db.execute("""
            INSERT OR IGNORE INTO clip_jobs(platform,stream_id,channel,created_at)
            SELECT s.platform,s.stream_id,s.channel,?
            FROM streams s JOIN streamers w
              ON s.platform=w.platform AND s.channel=w.channel
            WHERE s.platform='twitch' AND w.enabled=1 AND s.status='live'
        """, (now(),))
    return cursor.rowcount

def plan_next(db: sqlite3.Connection, output_root: str = "live_clips"):
    from adaptive import choose
    rows = db.execute("""
        SELECT j.id,j.channel,j.stream_id,j.platform,j.created_at,j.attempts FROM clip_jobs j
        JOIN streamers w ON w.platform=j.platform AND w.channel=j.channel
        JOIN streams s ON s.platform=j.platform AND s.stream_id=j.stream_id
        WHERE j.status='queued' AND w.enabled=1 AND s.status='live' ORDER BY j.id
    """).fetchall()
    row = choose(db, rows)
    if row is None:
        return None
    job_id, channel, stream_id = row[:3]
    suffix = f"_cycle{row[5]}" if row[5] >= 2 else ""
    output = Path(output_root) / f"{channel}_{stream_id}{suffix}"
    command = ["streamclipper", "live", "--url",
               f"https://www.twitch.tv/{channel}", "--out", str(output), "--render", "--no-draft", "--long-clips"]
    return {"job_id": job_id, "channel": channel, "stream_id": stream_id,
            "output_dir": str(output), "command": command}

def run_next(db: sqlite3.Connection, output_root: str = "live_clips",
             execute: bool = False, timeout_seconds: int = 7200):
    """Default: preview only. Execution requires --execute and a local StreamClipper installation."""
    job = plan_next(db, output_root)
    if job is None:
        return {"status": "empty"}
    if not execute:
        return {"status": "preview", **job}
    if timeout_seconds < 60:
        raise ValueError("timeout_seconds must be at least 60")
    with db:
        claimed = db.execute("""
            UPDATE clip_jobs SET status='running',attempts=attempts+1,
              started_at=?,output_dir=?
            WHERE id=? AND status='queued'
        """, (now(), job["output_dir"], job["job_id"]))
    if claimed.rowcount != 1:
        return {"status": "already_claimed"}
    Path(job["output_dir"]).mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(job["command"], timeout=timeout_seconds,
                                capture_output=True, text=True, check=False)
        status = "completed" if result.returncode == 0 else "failed"
        error = (result.stderr or "")[-1500:] if result.returncode else None
    except (OSError, subprocess.TimeoutExpired) as exc:
        status, error = "failed", str(exc)[:1500]
    with db:
        db.execute("""UPDATE clip_jobs SET status=?,error_message=?,finished_at=?
                      WHERE id=?""", (status, error, now(), job["job_id"]))
    return {"status": status, "job_id": job["job_id"], "error": error}

def main():
    parser = argparse.ArgumentParser(description="StreamClipper OS job queue")
    parser.add_argument("--db", default="streamclipper_os.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("enqueue")
    run = sub.add_parser("run-next")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--out", default="live_clips")
    run.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()
    with closing(connect(args.db)) as db:
        prepare(db)
        if args.command == "enqueue":
            print(json.dumps({"queued": enqueue_detected(db)}))
        else:
            print(json.dumps(run_next(db, args.out, args.execute, args.timeout), indent=2))
if __name__ == "__main__":
    main()


def production_candidates(db, authorized_channels):
    """Select fresh, authorized streams without creating jobs."""
    import re
    from streamer_status import resolve

    allowed = {
        channel.strip().lower()
        for channel in authorized_channels
        if channel.strip()
    }

    if any(
        not re.fullmatch(r"[a-z0-9_]{1,25}", c)
        for c in allowed
    ):
        raise ValueError("Invalid authorized channel")

    if not allowed:
        return []

    rows = db.execute("""
        SELECT s.platform,s.stream_id,s.channel,
               s.last_seen_at,p.status,p.checked_at
        FROM streams s
        JOIN streamers w
          ON w.platform=s.platform
         AND w.channel=s.channel
        JOIN streamer_status p
          ON p.platform=s.platform
         AND p.channel=s.channel
        WHERE s.platform='twitch'
          AND s.status='live'
          AND w.enabled=1
          AND NOT EXISTS (
              SELECT 1 FROM clip_jobs j
              WHERE j.platform=s.platform
                AND j.stream_id=s.stream_id
          )
        ORDER BY s.last_seen_at DESC,s.stream_id
    """).fetchall()

    candidates = []
    seen = set()

    for platform, sid, channel, last_seen, status, checked in rows:

        if channel not in allowed or channel in seen:
            continue

        if not re.fullmatch(r"[0-9]{1,40}", sid):
            continue

        if resolve(status, checked, 180) != "live":
            continue

        if resolve("live", last_seen, 180) != "live":
            continue

        seen.add(channel)

        candidates.append({
            "platform": platform,
            "stream_id": sid,
            "channel": channel
        })

    return candidates


def enqueue_production(db, authorized_channels):
    """Queue one authorized production job."""
    with db:
        db.execute("BEGIN IMMEDIATE")

        active = db.execute("""
            SELECT COUNT(*) FROM clip_jobs
            WHERE status IN ('queued','running')
        """).fetchone()[0]

        if active:
            return 0

        candidates = production_candidates(
            db, authorized_channels
        )

        if not candidates:
            return 0

        item = candidates[0]

        cursor = db.execute("""
            INSERT INTO clip_jobs
            (platform,stream_id,channel,created_at)
            VALUES (?,?,?,?)
            ON CONFLICT(platform,stream_id)
            DO NOTHING
        """, (
            item["platform"],
            item["stream_id"],
            item["channel"],
            now()
        ))

        return cursor.rowcount


def enqueue_followup(db, authorized_channels):
    """Resume a completed stream session only after remote atomic requeue."""
    import re
    from streamer_status import resolve
    from turso_jobs import requeue_completed, StoreError

    allowed = {c.strip().lower() for c in authorized_channels if c.strip()}
    if any(not re.fullmatch(r"[a-z0-9_]{1,25}", c) for c in allowed):
        raise ValueError("Invalid authorized channel")
    if not allowed:
        return 0
    # The caller runs only on a fresh successful poll with all pending data saved.
    if db.execute("SELECT 1 FROM clip_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
        return 0
    rows = db.execute("""
        SELECT j.id,j.stream_id,j.channel,j.attempts,s.last_seen_at,p.status,p.checked_at
        FROM clip_jobs j
        JOIN worker_runs w ON w.job_id=j.id
        JOIN streams s ON s.platform=j.platform AND s.stream_id=j.stream_id AND s.channel=j.channel
        JOIN streamers u ON u.platform=j.platform AND u.channel=j.channel
        JOIN streamer_status p ON p.platform=j.platform AND p.channel=j.channel
        WHERE j.platform='twitch' AND j.status='completed' AND j.attempts>=1
          AND j.finished_at IS NOT NULL AND w.exit_code=0 AND w.ended_at IS NOT NULL
          AND s.status='live' AND u.enabled=1
          AND NOT EXISTS (
              SELECT 1 FROM clips c JOIN clip_review r ON r.clip_id=c.clip_id
              LEFT JOIN clip_media m ON m.clip_id=c.clip_id
              WHERE c.platform=j.platform AND c.stream_id=j.stream_id
                AND r.quality_status='passed' AND m.clip_id IS NULL
          )
        ORDER BY j.finished_at,j.id
    """).fetchall()
    for jid,sid,channel,attempt,last_seen,status,checked in rows:
        if channel not in allowed or not re.fullmatch(r"[0-9]{1,40}", sid):
            continue
        if resolve(status,checked,180) != "live" or resolve("live",last_seen,180) != "live":
            continue
        if not requeue_completed(jid,sid,channel,attempt):
            # Remote and local state may differ, never overwrite blindly.
            continue
        with db:
            changed = db.execute("""
                UPDATE clip_jobs SET status='queued',attempts=attempts+1,
                    started_at=NULL,finished_at=NULL,output_dir=NULL,error_message=NULL
                WHERE id=? AND status='completed' AND attempts=?
            """, (jid,attempt)).rowcount
            if changed != 1:
                raise StoreError("Local cycle state conflict; restart required")
            db.execute("DELETE FROM worker_runs WHERE job_id=?", (jid,))
        print("PRODUCTION: FOLLOWUP QUEUED", jid, channel, "cycle", attempt+1, flush=True)
        return 1
    return 0
