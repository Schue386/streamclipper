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
        SELECT j.id,j.channel,j.stream_id,j.platform,j.created_at FROM clip_jobs j
        JOIN streamers w ON w.platform=j.platform AND w.channel=j.channel
        JOIN streams s ON s.platform=j.platform AND s.stream_id=j.stream_id
        WHERE j.status='queued' AND w.enabled=1 AND s.status='live' ORDER BY j.id
    """).fetchall()
    row = choose(db, rows)
    if row is None:
        return None
    job_id, channel, stream_id = row[:3]
    output = Path(output_root) / f"{channel}_{stream_id}"
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
