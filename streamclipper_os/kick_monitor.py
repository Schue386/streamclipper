"""Kick public API discovery; recording remains disabled pending validation."""
import json, os, re, urllib.request, urllib.parse
from stream_monitor import now

def add_channel(db, channel):
    channel=channel.strip().lower().removeprefix('https://kick.com/').strip('/')
    if not re.fullmatch(r'[a-z0-9_-]+',channel): raise ValueError('Invalid Kick channel')
    with db:
        db.execute("INSERT INTO streamers VALUES('kick',?,1) ON CONFLICT(platform,channel) DO UPDATE SET enabled=1",(channel,))

def fetch(channels, token):
    if not token: raise ValueError('Set KICK_ACCESS_TOKEN')
    for channel in channels:
        url='https://api.kick.com/public/v1/channels?'+urllib.parse.urlencode({'slug':channel})
        req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=20) as response: payload=json.load(response)
        if not isinstance(payload.get('data'),list): raise ValueError('Invalid Kick response')
        yield from payload['data']

def check(db, getter=fetch):
    channels=[r[0] for r in db.execute("SELECT channel FROM streamers WHERE platform='kick' AND enabled=1")]
    if not channels: return []
    rows=list(getter(channels,os.getenv('KICK_ACCESS_TOKEN','')))
    events=[]; stamp=now()
    with db:
        for row in rows:
            channel=row.get('slug'); stream=row.get('stream') or {}
            if channel not in channels or not isinstance(stream.get('is_live'),bool): continue
            if not stream['is_live']:
                db.execute("UPDATE streams SET status='offline' WHERE platform='kick' AND channel=?",(channel,)); continue
            start=stream.get('start_time')
            if not start: continue
            sid=channel+':'+start
            db.execute("UPDATE streams SET status='offline' WHERE platform='kick' AND channel=? AND stream_id<>?",(channel,sid))
            cur=db.execute("INSERT OR IGNORE INTO streams VALUES('kick',?,?,?,?,?,?,'live')",(sid,channel,row.get('stream_title',''),start,stamp,stamp))
            if cur.rowcount: events.append(dict(platform='kick',channel=channel,stream_id=sid))
            db.execute("UPDATE streams SET last_seen_at=?,status='live',title=? WHERE platform='kick' AND stream_id=?",(stamp,row.get('stream_title',''),sid))
    return events
