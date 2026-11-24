"""Read-only remote status reconciliation; never retries an upload."""
import argparse, os, json
from contextlib import closing
from stream_monitor import connect, now
from tiktok_worker import prepare, request

URL='https://open.tiktokapis.com/v2/post/publish/status/fetch/'
STATES={'PROCESSING_UPLOAD','PROCESSING_DOWNLOAD','SEND_TO_USER_INBOX','PUBLISH_COMPLETE','FAILED'}

def refresh(db, token, fetch=request):
    prepare(db)
    if not token: return {'status':'authorization_required','checked':0}
    rows=db.execute("SELECT queue_id,publish_id FROM tiktok_attempts WHERE publish_id IS NOT NULL AND state NOT IN ('PUBLISH_COMPLETE','FAILED') ORDER BY updated_at LIMIT 20").fetchall()
    checked=errors=0
    for qid,pid in rows:
        try:
            data=fetch(URL,token,{'publish_id':pid})
            state=data.get('status')
            if state not in STATES: raise ValueError('Unknown status')
            with db:
                db.execute('UPDATE tiktok_attempts SET state=?,updated_at=? WHERE queue_id=? AND publish_id=?',(state,now(),qid,pid))
            checked+=1
        except Exception:
            errors+=1
    return {'status':'partial' if errors else 'ok','checked':checked,'errors':errors}

def overview(db):
    prepare(db)
    return [{'queue_id':q,'state':s,'updated_at':t} for q,s,t in db.execute('SELECT queue_id,state,updated_at FROM tiktok_attempts ORDER BY updated_at DESC LIMIT 30')]

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--db',required=True); a=p.parse_args()
    from oauth_refresh import configured_token
    with closing(connect(a.db)) as db: print(json.dumps(refresh(db,configured_token('tiktok','TIKTOK'))))
