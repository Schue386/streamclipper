"""TikTok inbox upload: explicit per-clip consent; never direct-posts."""
import hashlib, json, urllib.request
from pathlib import Path
from stream_monitor import now
from clip_catalog import prepare as catalog_prepare

def prepare(db):
    catalog_prepare(db)
    db.execute('''CREATE TABLE IF NOT EXISTS tiktok_attempts (
      queue_id INTEGER PRIMARY KEY REFERENCES upload_queue(id),
      sha TEXT UNIQUE NOT NULL, state TEXT NOT NULL, publish_id TEXT,
      updated_at TEXT NOT NULL)''')
    db.commit()

def request(url, token, payload):
    req=urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Authorization':'Bearer '+token,'Content-Type':'application/json; charset=UTF-8'})
    with urllib.request.urlopen(req,timeout=30) as response: result=json.load(response)
    if result.get('error',{}).get('code')!='ok': raise RuntimeError('TikTok API rejected request')
    return result['data']

def send_inbox(path, token):
    # Single-chunk uploads limited to 64 MiB in this adapter.
    size=path.stat().st_size
    if not 0<size<=64*1024*1024: raise ValueError('Unsupported file size')
    data=request('https://open.tiktokapis.com/v2/post/publish/inbox/video/init/',token,{'source_info':{'source':'FILE_UPLOAD','video_size':size,'chunk_size':size,'total_chunk_count':1}})
    from urllib.parse import urlsplit
    url=data['upload_url']
    if urlsplit(url).scheme!='https': raise ValueError('Invalid upload URL')
    req=urllib.request.Request(url,data=path.read_bytes(),method='PUT',headers={'Content-Type':'video/mp4','Content-Range':f'bytes 0-{size-1}/{size}'})
    with urllib.request.urlopen(req,timeout=120) as response:
        if response.status!=201: raise RuntimeError('Upload not confirmed')
    return data['publish_id']

def run_next(db, approved_queue_id=None, token=None, sender=None):
    prepare(db)
    row=db.execute('''SELECT q.id,c.file_path,r.file_sha256 FROM upload_queue q
      JOIN clips c ON c.clip_id=q.clip_id JOIN clip_review r ON r.clip_id=c.clip_id
      WHERE q.destination='tiktok' AND q.status='ready' AND r.rights_approved=1
      AND r.quality_status='passed' AND NOT EXISTS(SELECT 1 FROM tiktok_attempts a WHERE a.queue_id=q.id OR a.sha=r.file_sha256)
      AND (? IS NULL OR q.id=?) ORDER BY q.id LIMIT 1''',(approved_queue_id,approved_queue_id)).fetchone()
    if not row: return {'status':'empty'}
    qid,path,sha=row; path=Path(path)
    try:
        with path.open('rb') as f: valid=hashlib.file_digest(f,'sha256').hexdigest()==sha
    except OSError: valid=False
    if not valid: return {'status':'blocked'}
    if approved_queue_id is None: return {'status':'preview','queue_id':qid,'mode':'inbox','file':str(path)}
    if not token and sender is None: return {'status':'authorization_required'}
    with db:
        claim=db.execute('INSERT OR IGNORE INTO tiktok_attempts VALUES(?,?,\'sending\',NULL,?)',(qid,sha,now()))
    if claim.rowcount!=1: return {'status':'already_claimed'}
    try:
        pid=(sender or send_inbox)(path,token)
        if not isinstance(pid,str) or not pid: raise ValueError('Missing publish ID')
    except Exception:
        with db: db.execute("UPDATE tiktok_attempts SET state='uncertain',updated_at=? WHERE queue_id=?",(now(),qid))
        return {'status':'uncertain'}
    with db: db.execute("UPDATE tiktok_attempts SET state='inbox_sent',publish_id=?,updated_at=? WHERE queue_id=?",(pid,now(),qid))
    return {'status':'inbox_sent','publish_id':pid,'queue_id':qid}
