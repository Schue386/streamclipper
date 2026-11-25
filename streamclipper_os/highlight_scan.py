"""Low-resolution, bounded audiovisual candidate scan; not semantic AI."""
import subprocess, math, statistics

def scan(source):
    base=['ffmpeg','-nostdin','-v','error','-threads','1','-protocol_whitelist','file','-f','mov','-i',str(source),'-t','180']
    frames=subprocess.check_output(base+['-an','-filter_threads','1','-vf','fps=2,scale=64:36,format=gray','-f','rawvideo','-'],timeout=180)
    n=2304
    motion=[sum(abs(a-b) for a,b in zip(frames[i-n:i],frames[i:i+n]))/n/255 for i in range(n,len(frames)-n+1,n)]
    audio=subprocess.check_output(base+['-vn','-map','0:a:0?','-ac','1','-ar','2000','-f','s16le','-'],timeout=180) if has_audio(source) else b''
    import array
    samples=array.array('h'); samples.frombytes(audio)
    loud=[math.sqrt(sum(x*x for x in samples[i:i+1000])/len(samples[i:i+1000]))/32768 for i in range(0,len(samples),1000)]
    return rank(motion,loud)

def has_audio(source):
    return bool(subprocess.check_output(['ffprobe','-v','error','-protocol_whitelist','file','-f','mov','-select_streams','a:0','-show_entries','stream=index','-of','csv=p=0',str(source)],timeout=30).strip())

def rank(motion,loud):
    baseline=statistics.median(motion) if motion else 0
    audio_base=statistics.median(loud) if loud else 0
    candidates=[]; scores=[]
    for i,m in enumerate(motion):
        a=loud[i+1] if i+1<len(loud) else 0
        score=min(1,max(0,m-baseline)*5)+min(1,max(0,a-audio_base)*5)
        scores.append(score)
        if score>=0.15:
            candidates.append({'second':(i+1)/2,'score':round(score,3),'motion':round(m,3),'audio_rms':round(a,3),'scene_change_proxy':m>=0.25})
    chosen=[]
    for c in sorted(candidates,key=lambda c:c['score'],reverse=True):
        if all(abs(c['second']-p['second'])>=10 for p in chosen): chosen.append(c)
        if len(chosen)==5: break
    return {'method':'audiovisual_heuristic','scan_limit_seconds':180,'audio_available':bool(loud),'diagnostics':{'threshold':0.15,'motion_samples':len(motion),'audio_samples':len(loud),'motion_median':round(baseline,4),'audio_median':round(audio_base,4),'max_score':round(max(scores,default=0),4)},'candidates':chosen}
