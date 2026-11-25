"""Bounded preview production, no publishing. Caption times relative to clip."""
import json, subprocess, tempfile, math
from pathlib import Path

def plan(duration, candidates):
    if not math.isfinite(duration) or duration<=0: raise ValueError('Invalid duration')
    length=min(75,duration)
    peak=max(candidates,key=lambda c:c['score']) if candidates else None
    start=max(0,min(duration-length,peak['second']-20)) if peak else 0
    return {'start':round(start,3),'duration':round(length,3),'selection':'heuristic' if peak else 'preview_only_no_highlight','publishable':False}

def timestamp(t):
    ms=round(t*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def captions_srt(captions,duration):
    lines=[]
    for c in captions:
        text=' '.join(c['text'].split()).replace('<','').replace('>','')
        a=max(0,float(c['start'])); b=min(duration,float(c['end']))
        if not any(x.isalnum() for x in text) or not math.isfinite(a+b) or b<=a: continue
        lines.append(f'{len(lines)+1}\n{timestamp(a)} --> {timestamp(b)}\n{text}\n')
    return '\n'.join(lines)

def produce(source, output, candidates, model=None):
    source=str(Path(source).resolve()); output=str(Path(output).resolve())
    duration=float(subprocess.check_output(['ffprobe','-v','error','-protocol_whitelist','file','-f','mov','-show_entries','format=duration','-of','csv=p=0',source],timeout=30))
    selection=plan(duration,candidates)
    with tempfile.TemporaryDirectory() as d:
        base=['ffmpeg','-nostdin','-v','error','-threads','1','-protocol_whitelist','file','-f','mov','-ss',str(selection['start']),'-i',source,'-t',str(selection['duration'])]
        cut=Path(d)/'cut.mp4'
        vf='scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2'
        enc=['-filter_threads','1','-c:v','libx264','-threads','1','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart']
        subprocess.run(base+['-vf',vf]+enc+[str(cut)],check=True,timeout=600)
        asr=None
        if model:
            try:
                import sys
                asr=json.loads(subprocess.check_output([sys.executable,str(Path(__file__).with_name('asr_benchmark.py')),str(cut),model],timeout=300))
            except (OSError,ValueError,subprocess.SubprocessError) as e: asr={'status':'failed','error':type(e).__name__}
        srt=captions_srt((asr or {}).get('captions',[]),selection['duration'])
        if srt:
            (Path(d)/'captions.srt').write_text(srt,encoding='utf-8')
            subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-i',str(cut),'-vf','subtitles=captions.srt']+enc+[output],cwd=d,check=True,timeout=600)
        else:
            import shutil
            shutil.copyfile(cut,output)
        return {'selection':selection,'asr':asr,'captions_burned':bool(srt),'caption_coverage_seconds':min(30,selection['duration']) if asr and 'captions' in asr else 0,'bytes':Path(output).stat().st_size}
