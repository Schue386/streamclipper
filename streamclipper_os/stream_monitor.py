"""Twitch stream discovery and SQLite event register. No recording or publishing."""
from __future__ import annotations
from contextlib import closing
import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from twitch_auth import get_token
from streamer_status import update as update_status

SCHEMA = """
CREATE TABLE IF NOT EXISTS streamers (
  platform TEXT NOT NULL, channel TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(platform, channel)
);
CREATE TABLE IF NOT EXISTS streams (
  platform TEXT NOT NULL, stream_id TEXT NOT NULL, channel TEXT NOT NULL,
  title TEXT, started_at TEXT, detected_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'live',
  PRIMARY KEY(platform, stream_id)
);
CREATE TABLE IF NOT EXISTS clips (
  clip_id TEXT PRIMARY KEY, platform TEXT NOT NULL, stream_id TEXT NOT NULL,
  file_path TEXT, duration_seconds REAL, quality_score REAL,
  status TEXT NOT NULL DEFAULT 'detected', created_at TEXT NOT NULL,
  FOREIGN KEY(platform, stream_id) REFERENCES streams(platform, stream_id)
);
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id TEXT NOT NULL,
  destination TEXT NOT NULL, remote_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
  published_at TEXT, error_message TEXT,
  UNIQUE(clip_id, destination), FOREIGN KEY(clip_id) REFERENCES clips(clip_id)
);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, publication_id INTEGER NOT NULL,
  captured_at TEXT NOT NULL, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
  FOREIGN KEY(publication_id) REFERENCES publications(id)
);
"""

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=30)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    db.executescript(SCHEMA)
    return db

def add_channel(db: sqlite3.Connection, channel: str) -> None:
    channel = channel.strip().lower().removeprefix('https://www.twitch.tv/').strip('/')
    if not channel or not all(c.isalnum() or c == '_' for c in channel):
        raise ValueError('Invalid Twitch channel')
    with db:
        db.execute('INSERT INTO streamers(platform,channel,enabled) VALUES (?,?,1) '
                   'ON CONFLICT(platform,channel) DO UPDATE SET enabled=1', ('twitch', channel))

def fetch_twitch_live(channels: list[str], client_id: str, access_token: str) -> list[dict]:
    if not channels:
        return []
    if not client_id or not access_token:
        raise ValueError('Set TWITCH_CLIENT_ID and TWITCH_ACCESS_TOKEN')
    result = []
    for i in range(0, len(channels), 100):
        params = urllib.parse.urlencode([('first', 100)] + [('user_login', c) for c in channels[i:i+100]])
        request = urllib.request.Request('https://api.twitch.tv/helix/streams?' + params,
                    headers={'Client-Id': client_id, 'Authorization': 'Bearer ' + access_token})
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        if not isinstance(payload.get('data'), list) or payload.get('pagination', {}).get('cursor'):
            raise ValueError('Incomplete Twitch response')
        result.extend(payload['data'])
    return result

def record_live(db: sqlite3.Connection, streams: list[dict]) -> list[dict]:
    new = []
    timestamp = now()
    with db:
        for s in streams:
            stream_id, channel = str(s['id']), str(s['user_login']).lower()
            exists = db.execute('SELECT 1 FROM streams WHERE platform=? AND stream_id=?',
                                ('twitch', stream_id)).fetchone()
            db.execute('''INSERT INTO streams(platform,stream_id,channel,title,started_at,detected_at,last_seen_at,status)
                          VALUES(?,?,?,?,?,?,?,'live') ON CONFLICT(platform,stream_id)
                          DO UPDATE SET last_seen_at=excluded.last_seen_at,status='live',title=excluded.title''',
                       ('twitch', stream_id, channel, s.get('title', ''), s.get('started_at'), timestamp, timestamp))
            if not exists:
                new.append({'platform':'twitch','channel':channel,'stream_id':stream_id,
                            'title':s.get('title',''),'started_at':s.get('started_at')})
    return new

def _check(db: sqlite3.Connection, demo_file: str | None = None) -> list[dict]:
    channels = [r[0] for r in db.execute("SELECT channel FROM streamers WHERE platform='twitch' AND enabled=1")]
    if demo_file:
        streams = json.loads(Path(demo_file).read_text(encoding='utf-8'))
    else:
        client_id = os.getenv("TWITCH_CLIENT_ID", "")
        if os.getenv("TWITCH_CLIENT_SECRET"):
            token = get_token()
            try:
                streams = fetch_twitch_live(channels, client_id, token)
            except urllib.error.HTTPError as exc:
                if exc.code != 401:
                    raise
                streams = fetch_twitch_live(channels, client_id, get_token(force=True))
        else:
            streams = fetch_twitch_live(channels, client_id, os.getenv("TWITCH_ACCESS_TOKEN", ""))
    # Ignore streams from channels not in the configured watchlist.
    allowed = set(channels)
    current = [s for s in streams if str(s.get("user_login","")).lower() in allowed]
    events = record_live(db, current)
    active = {str(s["id"]) for s in current}
    with db:
        for sid, channel in db.execute("SELECT stream_id,channel FROM streams WHERE platform=? AND status=?", ("twitch","live")).fetchall():
            if channel in allowed and sid not in active:
                db.execute("UPDATE streams SET status=? WHERE platform=? AND stream_id=?", ("offline","twitch",sid))
    if not demo_file:
        update_status(db, "twitch", channels, live={str(s["user_login"]).lower() for s in current})
    return events

def check(db: sqlite3.Connection, demo_file: str | None = None) -> list[dict]:
    try:
        return _check(db, demo_file)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        if not demo_file:
            from streamer_status import report_error
            report_error(db, "twitch", type(exc).__name__)
        raise

def main() -> None:
    p = argparse.ArgumentParser(description='StreamClipper OS Twitch monitor (no uploads/orders)')
    p.add_argument('--db', default='streamclipper_os.sqlite3')
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('add'); a.add_argument('channel'); a.add_argument('--platform', choices=['twitch','kick','youtube'], default='twitch')
    sub.add_parser('list')
    c = sub.add_parser('check'); c.add_argument('--demo-file')
    r = sub.add_parser('run'); r.add_argument('--interval', type=int, default=120)
    args = p.parse_args()
    with closing(connect(args.db)) as db:
        if args.command == 'add':
            if args.platform == 'youtube':
                from youtube_monitor import add_channel as add_youtube
                add_youtube(db,args.channel)
            elif args.platform == "kick":
                from kick_monitor import add_channel as add_kick
                add_kick(db,args.channel)
            else:
                add_channel(db, args.channel)
            print('Channel added')
        elif args.command == 'list':
            print(json.dumps([{'platform':r[0],'channel':r[1],'enabled':bool(r[2])}
                for r in db.execute('SELECT platform,channel,enabled FROM streamers')], indent=2))
        elif args.command == 'check':
            print(json.dumps({'new_streams':check(db,args.demo_file)},indent=2))
        elif args.command == 'run':
            if args.interval < 60: p.error('--interval must be >= 60 seconds')
            while True:
                try:
                    events = check(db)
                    if events: print(json.dumps({'new_streams':events}),flush=True)
                except (ValueError,urllib.error.URLError,TimeoutError) as e:
                    print(f'Poll failed: {e}',flush=True)
                time.sleep(args.interval)

if __name__ == '__main__':
    main()
