"""Read-only, age-normalized recommendations; no changes to scheduling."""
from datetime import datetime, timezone
from statistics import median
import json
import argparse
from contextlib import closing
from stream_monitor import connect

def analyze(db, current=None, destination="youtube"):
    current=current or datetime.now(timezone.utc)
    rows=db.execute('''SELECT s.platform,s.channel,c.clip_id,p.published_at,m.captured_at,m.views,m.likes,m.comments FROM publications p JOIN clips c ON c.clip_id=p.clip_id JOIN streams s ON s.platform=c.platform AND s.stream_id=c.stream_id JOIN metrics m ON m.publication_id=p.id WHERE p.destination=? AND p.status='published' AND m.id=(SELECT MAX(id) FROM metrics WHERE publication_id=p.id)''',(destination,)).fetchall()
    clips=[]; groups={}
    for platform,channel,cid,published,captured,views,likes,comments in rows:
        try:
            age=(datetime.fromisoformat(captured)-datetime.fromisoformat(published)).total_seconds()/3600
            freshness=(current-datetime.fromisoformat(captured)).total_seconds()/3600
        except (TypeError,ValueError): continue
        if not 24<=age<=168 or not 0<=freshness<=48 or views is None or views<0: continue
        item=dict(platform=platform,channel=channel,clip_id=cid,views=views,views_per_day=round(views*24/age,2),engagement=round(100*(likes+comments)/views,2) if views and likes is not None and comments is not None else None)
        clips.append(item); groups.setdefault((platform,channel),[]).append(item['views_per_day'])
    channels=[dict(platform=p,channel=c,samples=len(v),median_views_per_day=round(median(v),2),recommendation='review_priority' if len(v)>=5 else 'collect_more_data') for (p,c),v in groups.items()]
    return dict(clips=sorted(clips,key=lambda x:-x['views_per_day']),channels=sorted(channels,key=lambda x:(x['samples']<5,-x['median_views_per_day'])))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',default='streamclipper_os.sqlite3');a=p.parse_args()
    with closing(connect(a.db)) as db: print(json.dumps(analyze(db),indent=2))
