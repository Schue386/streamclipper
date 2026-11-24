"""Independent private-upload worker; never blocks the recording coordinator."""
import argparse
import logging
import time
from contextlib import closing
from stream_monitor import connect
from control import enabled
from youtube_worker import run_next

def tick(db, execute=False, sender=None):
    if not execute or not enabled(db):
        return {'status':'disabled'}
    return run_next(db, execute=True, sender=sender)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',default='streamclipper_os.sqlite3')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--interval',type=int,default=30)
    p.add_argument('--once',action='store_true')
    a=p.parse_args()
    if a.interval<5: p.error('interval must be >=5')
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            with closing(connect(a.db)) as db:
                logging.info('YouTube: %s',tick(db,a.execute)['status'])
        except Exception as exc:
            logging.warning('YouTube worker: %s',type(exc).__name__)
        if a.once: break
        time.sleep(a.interval)
if __name__=='__main__': main()
