"""Read-only metrics for explicitly mapped, published TikTok video IDs.
Publish IDs are not video IDs. Missing mappings are never guessed.
"""
import argparse,json
from contextlib import closing
from stream_monitor import connect,now
from tiktok_worker import request
from oauth_refresh import configured_token
URL='https://open.tiktokapis.com/v2/video/query/?fields=id,view_count,like_count,comment_count,share_count'

def collect(db,fetch):
    rows=db.execute("SELECT id,remote_id FROM publications WHERE destination='tiktok' AND status='published' AND remote_id IS NOT NULL").fetchall()
    saved=0
    for offset in range(0,len(rows),20):
        batch=rows[offset:offset+20]
        data=fetch(list(dict.fromkeys(v for _,v in batch)))
        by_id={str(v['id']):v for v in data.get('videos',[])}
        with db:
            for pid,vid in batch:
                if vid not in by_id: continue
                values=[by_id[vid].get(k) for k in ('view_count','like_count','comment_count','share_count')]
                if any(v is not None and (type(v) is not int or v<0) for v in values): raise ValueError('Invalid metric')
                if all(v is None for v in values): continue
                db.execute('INSERT INTO metrics(publication_id,captured_at,views,likes,comments,shares) VALUES(?,?,?,?,?,?)',(pid,now(),*values));saved+=1
    return saved

def latest(db):
    rows=db.execute("SELECT views,likes,comments,shares FROM metrics m JOIN publications p ON p.id=m.publication_id WHERE p.destination='tiktok' AND p.status='published' AND m.id=(SELECT MAX(id) FROM metrics WHERE publication_id=p.id)").fetchall()
    return dict(tracked=len(rows),**{k:sum(r[i] for r in rows if r[i] is not None) if any(r[i] is not None for r in rows) else None for i,k in enumerate(('views','likes','comments','shares'))})

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    with closing(connect(a.db)) as db:
        if not a.execute: print(json.dumps(latest(db)));return
        token=configured_token('tiktok','TIKTOK')
        if not token: raise SystemExit('Authorization required: video.list')
        print(json.dumps({'saved':collect(db,lambda ids:request(URL,token,{'filters':{'video_ids':ids}}))}))
if __name__=='__main__':main()
