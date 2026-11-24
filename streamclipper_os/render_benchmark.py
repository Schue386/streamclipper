"""Offline pilot: bounded FFmpeg subprocess, no upload or network."""
import argparse, json, resource, subprocess, tempfile, time
from pathlib import Path

def limit():
    # Address-space ceiling for child only; not equivalent to container RSS.
    resource.setrlimit(resource.RLIMIT_AS, (512*1024**2,512*1024**2))

def benchmark(source, seconds=75, bounded=True):
    if not 1 <= seconds <= 90: raise ValueError('seconds must be 1..90')
    start=time.monotonic()
    with tempfile.TemporaryDirectory() as d:
        out=Path(d)/'pilot.mp4'
        cmd=['ffmpeg','-nostdin','-xerror','-v','error','-threads','1','-protocol_whitelist','file','-f','mov','-i',str(source),'-t',str(seconds),'-filter_threads','1','-vf','scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2','-c:v','libx264','-threads','1','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(out)]
        subprocess.run(cmd,check=True,timeout=600,preexec_fn=limit if bounded else None)
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(out)],timeout=30))
        return {'elapsed_seconds':round(time.monotonic()-start,2),'duration':float(info['format']['duration']),'bytes':out.stat().st_size,'child_peak_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,'address_space_limit_mib':512 if bounded else None,'asr_tested':False,'render_cloud_tested':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('source'); p.add_argument('--seconds',type=int,default=75); a=p.parse_args(); print(json.dumps(benchmark(a.source,a.seconds),indent=2))
