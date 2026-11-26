"""Local MP4 catalog, explicit rights gate, and non-executing publication queue.

No API credentials, network upload or automatic approval is implemented here.
"""
from __future__ import annotations
from contextlib import closing
import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from stream_monitor import connect, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS clip_review (
 clip_id TEXT PRIMARY KEY REFERENCES clips(clip_id),
 file_sha256 TEXT NOT NULL,
 file_size INTEGER NOT NULL,
 width INTEGER, height INTEGER,
 duration_seconds REAL,
 quality_status TEXT NOT NULL CHECK(quality_status IN ('passed','rejected','unverified')),
 quality_reason TEXT NOT NULL,
 rights_approved INTEGER NOT NULL DEFAULT 0 CHECK(rights_approved IN (0,1)),
 rights_note TEXT NOT NULL DEFAULT '',
 reviewed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS upload_queue (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 clip_id TEXT NOT NULL REFERENCES clips(clip_id),
 destination TEXT NOT NULL CHECK(destination IN ('youtube','tiktok')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','blocked','ready','uploaded','failed')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(clip_id,destination)
);
"""

def prepare(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)

def probe_mp4(path: Path) -> dict:
    """Use ffprobe if available; failures remain unverified, never approved."""
    try:
        result = subprocess.run([
            'ffprobe','-v','error','-show_entries',
            'format=duration:stream=codec_type,width,height',
            '-of','json',str(path)],capture_output=True,text=True,timeout=20,check=True)
        data=json.loads(result.stdout)
        video=next((s for s in data.get('streams',[]) if s.get('codec_type')=='video'),{})
        return {'duration':float(data.get('format',{}).get('duration',0)),
                'width':int(video.get('width',0)), 'height':int(video.get('height',0))}
    except (OSError,ValueError,KeyError,subprocess.SubprocessError,StopIteration):
        return {}

def inspect(path: Path, metadata: dict | None = None) -> dict:
    """Metadata injection is for deterministic tests, not a production trust source."""
    if not path.is_file() or path.suffix.lower()!='.mp4':
        raise ValueError('Expected an existing .mp4 file')
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            digest.update(chunk)
    info=probe_mp4(path) if metadata is None else metadata
    duration=float(info.get('duration') or 0)
    width=int(info.get('width') or 0); height=int(info.get('height') or 0)
    if not info or not duration or not width or not height:
        status,reason='unverified','Video metadata unavailable'
    elif not (61 <= duration <= 90):
        status,reason='rejected','Duration outside 61-90 seconds'
    elif height <= width:
        status,reason='rejected','Not a vertical video'
    else:
        status,reason='passed','Duration and vertical dimensions verified'
    return {'sha256':digest.hexdigest(),'size':path.stat().st_size,
            'duration':duration,'width':width,'height':height,
            'status':status,'reason':reason}

def register(db:sqlite3.Connection,platform:str,stream_id:str,path:str,
             metadata:dict|None=None)->dict:
    """A clip is registered only against an existing stream; changes invalidate rights."""
    source=Path(path).resolve(strict=True)
    result=inspect(source,metadata)
    clip_id=hashlib.sha256(f'{platform}:{stream_id}:{result["sha256"]}'.encode()).hexdigest()[:32]
    with db:
        if not db.execute('SELECT 1 FROM streams WHERE platform=? AND stream_id=?',
                          (platform,stream_id)).fetchone():
            raise ValueError('Unknown stream; register it before its clips')
        db.execute("""INSERT OR IGNORE INTO clips
           (clip_id,platform,stream_id,file_path,duration_seconds,status,created_at)
           VALUES(?,?,?,?,?,?,?)""",
           (clip_id,platform,stream_id,str(source),result['duration'],result['status'],now()))
        db.execute("""INSERT OR IGNORE INTO clip_review
           (clip_id,file_sha256,file_size,width,height,duration_seconds,
            quality_status,quality_reason,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?)""",
           (clip_id,result['sha256'],result['size'],result['width'],result['height'],
            result['duration'],result['status'],result['reason'],now()))
    return {'clip_id':clip_id,**result}

def approve_rights(db:sqlite3.Connection,clip_id:str,note:str)->None:
    """Approval requires a human-entered explanation of redistribution permission."""
    if len(note.strip()) < 12:
        raise ValueError('Provide a meaningful rights/permission note (>=12 characters)')
    with db:
        row=db.execute('SELECT quality_status FROM clip_review WHERE clip_id=?',(clip_id,)).fetchone()
        if not row or row[0]!='passed':
            raise ValueError('Clip must pass the technical review first')
        db.execute('UPDATE clip_review SET rights_approved=1,rights_note=?,reviewed_at=? WHERE clip_id=?',
                   (note.strip(),now(),clip_id))

def queue_upload(db:sqlite3.Connection,clip_id:str,destination:str)->dict:
    """Create a record, never send content to any platform."""
    if destination not in ('youtube','tiktok'):
        raise ValueError('Unsupported destination')
    with db:
        row=db.execute("""SELECT r.quality_status,r.rights_approved,c.file_path,r.file_sha256
          FROM clip_review r JOIN clips c ON c.clip_id=r.clip_id WHERE r.clip_id=?""",(clip_id,)).fetchone()
        if not row:
            raise ValueError('Unknown clip')
        quality,rights,path,sha=row
        # Recheck file integrity before approving the queue entry.
        file_ok=False
        try:
            if not path:
                raise FileNotFoundError("Clip file unavailable")
            with open(path,'rb') as handle:
                digest=hashlib.file_digest(handle,'sha256').hexdigest()
            file_ok=digest==sha
        except OSError:
            pass
        status='ready' if quality=='passed' and rights and file_ok else 'blocked'
        db.execute("""INSERT INTO upload_queue(clip_id,destination,status,created_at,updated_at)
          VALUES(?,?,?,?,?) ON CONFLICT(clip_id,destination) DO UPDATE SET
          status=CASE WHEN upload_queue.status='uploaded' THEN 'uploaded' ELSE excluded.status END,
          updated_at=excluded.updated_at""",(clip_id,destination,status,now(),now()))
    return {'clip_id':clip_id,'destination':destination,'status':status,'uploaded':False}

def summary(db:sqlite3.Connection)->dict:
    def count(table,condition='1=1'):
        return db.execute(f'SELECT COUNT(*) FROM {table} WHERE {condition}').fetchone()[0]
    return {'streams':count('streams'),'clips':count('clips'),
            'quality_passed':count('clip_review',"quality_status='passed'"),
            'rights_approved':count('clip_review','rights_approved=1'),
            'uploads_ready':count('upload_queue',"status='ready'"),
            'uploads_completed':count('upload_queue',"status='uploaded'")}

def main():
    parser=argparse.ArgumentParser(description='Local catalog; no automatic uploads')
    parser.add_argument('--db',default='streamclipper_os.sqlite3')
    sub=parser.add_subparsers(dest='command',required=True)
    r=sub.add_parser('register');r.add_argument('--stream-id',required=True);r.add_argument('--file',required=True)
    a=sub.add_parser('approve-rights');a.add_argument('--clip-id',required=True);a.add_argument('--note',required=True)
    q=sub.add_parser('queue');q.add_argument('--clip-id',required=True);q.add_argument('--platform',choices=['youtube','tiktok'],required=True)
    sub.add_parser('summary')
    args=parser.parse_args()
    with closing(connect(args.db)) as db:
        prepare(db)
        if args.command=='register': out=register(db,'twitch',args.stream_id,args.file)
        elif args.command=='approve-rights':
            approve_rights(db,args.clip_id,args.note);out={'approved':True}
        elif args.command=='queue':out=queue_upload(db,args.clip_id,args.platform)
        else:out=summary(db)
        print(json.dumps(out,indent=2))
if __name__=='__main__': main()
