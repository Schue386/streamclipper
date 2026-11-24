"""Private-only YouTube queue adapter; uncertain attempts never auto-retry."""
import argparse
from contextlib import closing
import hashlib
from pathlib import Path
import re
from stream_monitor import connect, now
from clip_catalog import prepare as prepare_catalog

def prepare(db):
    prepare_catalog(db)
    db.execute('''CREATE TABLE IF NOT EXISTS youtube_attempts (
      queue_id INTEGER PRIMARY KEY REFERENCES upload_queue(id),
      file_sha256 TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
      video_id TEXT, updated_at TEXT NOT NULL)''')
    db.commit()

def run_next(db, execute=False, sender=None):
    prepare(db)
    row=db.execute('''SELECT q.id,c.file_path,r.file_sha256,s.channel
      FROM upload_queue q JOIN clips c ON c.clip_id=q.clip_id
      JOIN clip_review r ON r.clip_id=c.clip_id
      JOIN streams s ON s.platform=c.platform AND s.stream_id=c.stream_id
      WHERE q.destination='youtube' AND q.status='ready'
      AND r.rights_approved=1 AND r.quality_status='passed'
      AND NOT EXISTS(SELECT 1 FROM youtube_attempts a WHERE a.queue_id=q.id OR a.file_sha256=r.file_sha256)
      ORDER BY q.id LIMIT 1''').fetchone()
    if not row: return {'status':'empty'}
    qid,path,sha,channel=row
    try:
        with open(path,'rb') as f:
            digest=hashlib.sha256()
            for chunk in iter(lambda:f.read(1024*1024),b''): digest.update(chunk)
        valid=digest.hexdigest()==sha
    except OSError: valid=False
    if not valid:
        with db: db.execute("UPDATE upload_queue SET status='blocked',updated_at=? WHERE id=?",(now(),qid))
        return {'status':'blocked'}
    meta={'title':f'Gaming-Highlight | {channel}'[:100], 'description':f'Gaming-Highlight aus dem Stream von {channel}. #Gaming #Shorts', 'tags':['Gaming','Shorts']}
    if not execute: return {'status':'preview','queue_id':qid,'metadata':meta,'privacy':'private'}
    if sender is None:
        from streamclipper.publish import youtube
        from streamclipper.publish.http import make_transport
        transport=make_transport()
        token=youtube.get_token(transport)
        sender=lambda p,m:youtube.upload(p,youtube.video_body(m,privacy='private'),transport,token)
    with db:
        claim=db.execute('''INSERT OR IGNORE INTO youtube_attempts(queue_id,file_sha256,state,updated_at)
          SELECT q.id,r.file_sha256,'sending',? FROM upload_queue q JOIN clip_review r ON r.clip_id=q.clip_id
          WHERE q.id=? AND q.status='ready' AND r.rights_approved=1 AND r.quality_status='passed' AND r.file_sha256=?''',(now(),qid,sha))
    if claim.rowcount!=1: return {'status':'already_claimed'}
    try:
        result=sender(Path(path),meta)
        vid=result.get('video_id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid): raise ValueError('Missing valid video ID')
    except Exception:
        with db: db.execute("UPDATE youtube_attempts SET state='uncertain',updated_at=? WHERE queue_id=?",(now(),qid))
        return {'status':'uncertain','queue_id':qid}
    with db:
        db.execute("UPDATE youtube_attempts SET state='uploaded',video_id=?,updated_at=? WHERE queue_id=?",(vid,now(),qid))
        db.execute("UPDATE upload_queue SET status='uploaded',updated_at=? WHERE id=?",(now(),qid))
        db.execute("""INSERT INTO publications(clip_id,destination,remote_id,status,published_at)
          SELECT clip_id,'youtube',?,'uploaded_private',? FROM upload_queue WHERE id=?
          ON CONFLICT(clip_id,destination) DO UPDATE SET remote_id=excluded.remote_id,
          status=excluded.status,published_at=excluded.published_at""",(vid,now(),qid))
    return {'status':'uploaded','video_id':vid,'queue_id':qid}

if __name__=='__main__':
    import json
    p=argparse.ArgumentParser();p.add_argument('--db',default='streamclipper_os.sqlite3');p.add_argument('--execute',action='store_true');a=p.parse_args()
    with closing(connect(a.db)) as db: print(json.dumps(run_next(db,a.execute)))
