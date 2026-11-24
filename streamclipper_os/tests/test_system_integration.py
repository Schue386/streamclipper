import unittest, tempfile, subprocess, shutil
from pathlib import Path
from contextlib import closing
from unittest.mock import Mock
from stream_monitor import connect, add_channel
from control import set_enabled
from supervisor import Supervisor
from service import cycle
from clip_jobs import plan_next
from clip_importer import import_job
from clip_catalog import approve_rights, queue_upload
from youtube_metrics import collect
from dashboard import snapshot, render

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class SystemIntegration(unittest.TestCase):
    def test_offline_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=str(root/'db.sqlite')
            with closing(connect(path)) as db:
                add_channel(db,'example'); set_enabled(db,True)
                sample=root/'live.json'; sample.write_text('[{"id":"s1","user_login":"example","title":"test"}]')
                sup=Supervisor(db,execute=False)
                self.assertEqual(cycle(db,sup,demo_file=str(sample))['queued'],1)
                self.assertEqual(cycle(db,sup,demo_file=str(sample))['queued'],0)
                job=plan_next(db,tmp); out=Path(job['output_dir']); out.mkdir()
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=90x160:rate=2','-t','75','-c:v','mpeg4',str(out/'test.mp4')],check=True,timeout=20)
                with db: db.execute("UPDATE clip_jobs SET status='completed',output_dir=? WHERE id=?",(str(out),job['job_id']))
                self.assertEqual(import_job(db,job['job_id'])['passed'],1)
                cid=db.execute('SELECT clip_id FROM clips').fetchone()[0]
                self.assertEqual(queue_upload(db,cid,'youtube')['status'],'blocked')
                approve_rights(db,cid,'Synthetic test fixture, no external content')
                queue_upload(db,cid,'youtube')
                send=Mock(return_value={'video_id':'abcdefghijk'})
                set_enabled(db,False)
                self.assertEqual(cycle(db,sup,poll=False,upload=True,sender=send)['youtube']['status'],'disabled')
                set_enabled(db,True)
                self.assertEqual(cycle(db,sup,poll=False,upload=True,sender=send)['youtube']['status'],'uploaded')
                self.assertEqual(cycle(db,sup,poll=False,upload=True,sender=send)['youtube']['status'],'empty')
                send.assert_called_once()
                self.assertEqual(collect(db,lambda ids:[{'id':ids[0],'statistics':{'viewCount':'100','likeCount':'10','commentCount':'2'}}]),1)
                data=snapshot(path)
                self.assertEqual(data['youtube_metrics']['views'],100)
                self.assertEqual(data['performance']['clips'],[])
                self.assertIn(b'YouTube-Aufrufe',render(data))
