"""Explicitly authorized, bounded VOD import using the existing engine."""
import argparse
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import re

def canonical_url(url):
    p=urlparse(url)
    if p.scheme!='https' or p.netloc not in ('youtu.be','www.youtube.com','youtube.com'):
        raise ValueError('Only HTTPS YouTube video links are supported')
    video=p.path.strip('/') if p.netloc=='youtu.be' else (parse_qs(p.query).get('v',[''])[0] if p.path=='/watch' else '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video):
        raise ValueError('Invalid video ID')
    return 'https://www.youtube.com/watch?v='+video

def plan(url,start,duration,out):
    url=canonical_url(url)
    if start<0 or not 90<=duration<=1800:
        raise ValueError('Choose a nonnegative start and 90–1800 seconds')
    folder=Path(out)/url[-11:]/f'{start}_{duration}'
    source=folder/'source.mp4'
    return ([ 'streamclipper','download-vod','--url',url,'--start',str(start),'--end',str(start+duration),'--max-height','720','--out',str(source)],['streamclipper','clip-stream','--input',str(source),'--out',str(folder/'clips'),'--min-dur','61','--target-dur','75','--max-dur','90','--render','--no-draft','--max-clips','5'],folder)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('url');p.add_argument('--start',type=int,default=0);p.add_argument('--duration',type=int,default=900);p.add_argument('--out',default='vod_work');p.add_argument('--execute',action='store_true');p.add_argument('--authorized',action='store_true')
    a=p.parse_args();download,render,folder=plan(a.url,a.start,a.duration,a.out)
    if not a.execute:
        print(download);print(render);return
    if not a.authorized:p.error('Permission to retrieve and process the source is required')
    folder.mkdir(parents=True,exist_ok=True)
    subprocess.run(download,check=True,timeout=1800)
    if not (folder/'source.mp4').is_file():raise RuntimeError('Download produced no source file')
    subprocess.run(render,check=True,timeout=7200)
if __name__=='__main__':main()
