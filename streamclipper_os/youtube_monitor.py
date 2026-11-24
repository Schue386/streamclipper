"""YouTube live discovery using explicitly registered channel IDs. No recording."""
import os, re, json, urllib.request, urllib.parse, time
from stream_monitor import now
_last_poll = {}

def add_channel(db, channel):
    channel=channel.strip().removeprefix('https://www.youtube.com/channel/').strip('/')
    if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}',channel):
        raise ValueError('Use a YouTube UC channel ID')
    with db:
        db.execute("INSERT INTO streamers VALUES('youtube',?,1) ON CONFLICT(platform,channel) DO UPDATE SET enabled=1",(channel,))

def fetch(channel,key):
    if not key: raise ValueError('Set YOUTUBE_DATA_API_KEY')
    params=dict(part='snippet',channelId=channel,eventType='live',type='video',maxResults=50,key=key)
    with urllib.request.urlopen('https://www.googleapis.com/youtube/v3/search?'+urllib.parse.urlencode(params),timeout=20) as response:
        payload=json.load(response)
    if not isinstance(payload.get('items'),list) or payload.get('nextPageToken'):
        raise ValueError('Incomplete YouTube response')
    return payload['items']

def check(db,getter=fetch,interval=3600,clock=time.monotonic):
    events=[]; stamp=now()
    channels=[r[0] for r in db.execute("SELECT channel FROM streamers WHERE platform='youtube' AND enabled=1")]
    for channel in channels:
        current=clock()
        if channel in _last_poll and current-_last_poll[channel]<max(3600,interval): continue
        _last_poll[channel]=current
        rows=getter(channel,os.getenv('YOUTUBE_DATA_API_KEY',''))
        found={}
        for row in rows:
            snippet=row['snippet']; sid=row['id']['videoId']
            if snippet['channelId']!=channel or snippet.get('liveBroadcastContent')!='live' or not re.fullmatch(r'[A-Za-z0-9_-]{11}',sid):
                raise ValueError('Unexpected YouTube search result')
            found[sid]=snippet.get('title','')
        with db:
            for sid,title in found.items():
                cur=db.execute("INSERT OR IGNORE INTO streams VALUES('youtube',?,?,?,?,?,?,'live')",(sid,channel,title,None,stamp,stamp))
                if cur.rowcount: events.append(dict(platform='youtube',channel=channel,stream_id=sid))
                db.execute("UPDATE streams SET status='live',last_seen_at=?,title=? WHERE platform='youtube' AND stream_id=?",(stamp,title,sid))
            for (sid,) in db.execute("SELECT stream_id FROM streams WHERE platform='youtube' AND channel=? AND status='live'",(channel,)).fetchall():
                if sid not in found: db.execute("UPDATE streams SET status='offline' WHERE platform='youtube' AND stream_id=?",(sid,))
    return events
