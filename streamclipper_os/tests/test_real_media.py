import unittest, tempfile, subprocess, shutil
from pathlib import Path
from contextlib import closing
from stream_monitor import connect, add_channel, record_live
from clip_jobs import prepare, enqueue_detected, plan_next
from clip_importer import import_job
from clip_catalog import queue_upload

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class RealMediaTest(unittest.TestCase):
    def test_real_mp4_import(self):
        with tempfile.TemporaryDirectory() as d, closing(connect(str(Path(d)/'db.sqlite'))) as db:
            add_channel(db,'example'); record_live(db,[{'id':'s1','user_login':'example'}]); prepare(db); enqueue_detected(db)
            job=plan_next(db,d); self.assertIn('--long-clips',job['command']); self.assertIn('--no-draft',job['command'])
            out=Path(job['output_dir']); out.mkdir(); (out/'windows').mkdir()
            video=out/'LIVE_01.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=size=90x160:rate=2','-t','70','-c:v','mpeg4',str(video)],check=True,timeout=20)
            shutil.copy(video,out/'windows'/'internal.mp4')
            db.execute("UPDATE clip_jobs SET status='completed',output_dir=? WHERE id=1",(str(out),));db.commit()
            result=import_job(db,1);self.assertEqual(result['passed'],1);self.assertEqual(result['files_found'],1)
            clip=db.execute('SELECT clip_id FROM clips').fetchone()[0]
            self.assertEqual(queue_upload(db,clip,'youtube')['status'],'blocked')
