"""Explicit offline ASR pilot; never downloads a model or publishes content."""
import os, argparse, json, resource, subprocess, tempfile, time
from pathlib import Path

def benchmark(source, model, seconds=30):
    if not 1 <= seconds <= 30: raise ValueError('seconds must be 1..30')
    model=Path(model)
    if not (model/'model.bin').is_file(): raise ValueError('Local converted Whisper Tiny model required')
    from faster_whisper import WhisperModel
    from streamclipper.asr import read_wav16k
    start=time.monotonic()
    with tempfile.TemporaryDirectory() as d:
        wav=Path(d)/'audio.wav'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-protocol_whitelist','file','-f','mov','-i',str(source),'-t',str(seconds),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],check=True,timeout=120)
        engine=WhisperModel(str(model),device='cpu',compute_type='int8',cpu_threads=1,num_workers=1,local_files_only=True)
        segments,info=engine.transcribe(read_wav16k(wav),beam_size=1,best_of=1,word_timestamps=True,vad_filter=False)
        captions=[]; words=0
        for s in segments:
            words+=len(s.words or [])
            if any(c.isalnum() for c in s.text): captions.append({"start":round(s.start,3),"end":round(s.end,3),"text":s.text.strip()})
        return {'words':words,'captions':captions,'language':info.language,'elapsed_seconds':round(time.monotonic()-start,2),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'cloud_tested':bool(os.environ.get('RENDER_EXTERNAL_HOSTNAME'))}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('source'); p.add_argument('model'); p.add_argument('--seconds',type=int,default=30); a=p.parse_args(); print(json.dumps(benchmark(a.source,a.model,a.seconds)))
