"""Read-only local deployment checks; never uploads or starts services."""
import argparse, json, os, shutil, sys, tempfile
from pathlib import Path

def inspect(state, *, environ=None, which=shutil.which, disk=shutil.disk_usage):
    env=os.environ if environ is None else environ
    state=Path(state)
    checks={ 'python':sys.version_info >= (3,10) }
    for tool in ('ffmpeg','ffprobe','streamclipper','streamlink','yt-dlp'):
        checks[tool]=bool(which(tool))
    try:
        with tempfile.NamedTemporaryFile(dir=state): pass
        checks['state_writable']=True
    except OSError: checks['state_writable']=False
    try: free=disk(state).free//(1024**3)
    except OSError: free=None
    checks['disk_minimum']=free is not None and free>=20
    credentials={p:all(bool(env.get(k)) for k in keys) for p,keys in {
        'twitch':('TWITCH_CLIENT_ID','TWITCH_ACCESS_TOKEN'),
        'kick':('KICK_ACCESS_TOKEN',), 'youtube_live':('YOUTUBE_DATA_API_KEY',)}.items()}
    return {'local_ready':all(checks.values()),'checks':checks,'free_gib':free,'cpu_count':os.cpu_count(), 'credentials_present':credentials,'live_verified':False}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--state',required=True); a=p.parse_args()
    result=inspect(a.state); print(json.dumps(result,indent=2)); return 0 if result['local_ready'] else 1
if __name__=='__main__': raise SystemExit(main())
