"""Read-only YouTube statistics collection. Separate from upload permissions."""
import argparse
import json
import os
import time
from contextlib import closing
from urllib.parse import urlencode
from stream_monitor import connect, now


def collect(db, fetch):
    rows=db.execute("SELECT id,remote_id FROM publications WHERE destination='youtube' AND remote_id IS NOT NULL AND status IN ('uploaded_private','published','uploaded')").fetchall()
    saved=0
    for offset in range(0,len(rows),50):
        batch=rows[offset:offset+50]
        items=fetch(list(dict.fromkeys(v for _,v in batch)))
        by_id={i['id']:i.get('statistics',{}) for i in items}
        with db:
            for pid,vid in batch:
                if vid not in by_id: continue
                stats=by_id[vid]
                values=[int(stats[k]) if k in stats else None for k in ('viewCount','likeCount','commentCount')]
                if any(v is not None and v<0 for v in values): raise ValueError('Negative metric')
                if all(v is None for v in values): continue
                db.execute('INSERT INTO metrics(publication_id,captured_at,views,likes,comments) VALUES(?,?,?,?,?)',(pid,now(),*values))
                saved+=1
    return saved


def latest(db):
    rows=db.execute('''SELECT m.views,m.likes,m.comments,m.captured_at FROM metrics m JOIN publications p ON p.id=m.publication_id WHERE p.destination='youtube' AND m.id=(SELECT MAX(x.id) FROM metrics x WHERE x.publication_id=m.publication_id)''').fetchall()
    totals=[sum(r[i] for r in rows if r[i] is not None) if any(r[i] is not None for r in rows) else None for i in range(3)]
    eligible=[r for r in rows if all(v is not None for v in r[:3])]
    views=sum(r[0] for r in eligible)
    return dict(views=totals[0],likes=totals[1],comments=totals[2],tracked=len(rows),engagement=round(100*sum(r[1]+r[2] for r in eligible)/views,2) if views else None,updated=max((r[3] for r in rows),default=None))


def fetch_api(ids, token, transport):
    status,_,body=transport.request('GET','https://www.googleapis.com/youtube/v3/videos?'+urlencode({'part':'statistics','id':','.join(ids)}),{'Authorization':'Bearer '+token})
    if status!=200: raise RuntimeError(f'YouTube statistics HTTP {status}')
    return json.loads(body)['items']


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='streamclipper_os.sqlite3');p.add_argument('--execute',action='store_true');p.add_argument('--interval',type=int,default=3600);p.add_argument('--once',action='store_true');a=p.parse_args()
    if a.interval<300:p.error('interval must be >=300')
    if not a.execute:
        with closing(connect(a.db)) as db: print(json.dumps(latest(db)))
        return
    from streamclipper.publish.http import make_transport
    while True:
        try:
            from oauth_refresh import configured_token
            token=configured_token('youtube','YOUTUBE_READ')
            if not token: raise RuntimeError('Authorization required')
            with closing(connect(a.db)) as db: print('Metrics saved:',collect(db,lambda ids:fetch_api(ids,token,make_transport())),flush=True)
        except Exception as exc: print('Metrics collection failed:',type(exc).__name__,flush=True)
        if a.once:break
        time.sleep(a.interval)
if __name__=='__main__':main()
