"""Opt-in OAuth refresh for a single credential owner on Linux."""
import json, os, time, tempfile, fcntl, urllib.request
from pathlib import Path
from urllib.parse import urlencode

ENDPOINTS={'tiktok':'https://open.tiktokapis.com/v2/oauth/token/','youtube':'https://oauth2.googleapis.com/token'}

def exchange(platform, fields):
    req=urllib.request.Request(ENDPOINTS[platform],data=urlencode(fields).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req,timeout=30) as response:
        return json.load(response)

def access_token(platform, path, client_id, client_secret, fetch=exchange, clock=time.time):
    if platform not in ENDPOINTS: raise ValueError('Unknown provider')
    path=Path(path)
    if path.parent.stat().st_mode & 0o077: raise PermissionError('Credential directory must be private')
    with os.fdopen(os.open(str(path)+'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600),'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'r') as source:
            if os.fstat(source.fileno()).st_mode & 0o077: raise PermissionError('Credential file must be private')
            state=json.load(source)
        if state.get('platform')!=platform: raise ValueError('Provider mismatch')
        if state.get('access_token') and state.get('expires_at',0)>clock()+120: return state['access_token']
        fields={'grant_type':'refresh_token','refresh_token':state['refresh_token'],'client_secret':client_secret,'client_key' if platform=='tiktok' else 'client_id':client_id}
        try:
            result=fetch(platform,fields)
            token=result['access_token']; ttl=int(result['expires_in'])
            if not isinstance(token,str) or not token or ttl<=120 or result.get('error'): raise ValueError()
        except Exception:
            raise RuntimeError('OAuth refresh failed; authorization required or retry later') from None
        state.update(access_token=token,expires_at=clock()+ttl,refresh_token=result.get('refresh_token') or state['refresh_token'])
        fd,tmp=tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd,'w') as out:
                json.dump(state,out);out.flush();os.fsync(out.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return token

def configured_token(platform, prefix):
    path=os.getenv(prefix+'_TOKEN_FILE')
    if not path: return os.getenv(prefix+'_ACCESS_TOKEN')
    return access_token(platform,path,os.environ[prefix+'_CLIENT_ID'],os.environ[prefix+'_CLIENT_SECRET'])
