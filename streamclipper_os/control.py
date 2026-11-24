"""Local operator controls and status; no remote HTTP endpoint or public exposure."""
from __future__ import annotations
from contextlib import closing
import argparse,json,sqlite3
from stream_monitor import connect,now

SCHEMA="""CREATE TABLE IF NOT EXISTS system_control (
 id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0
 CHECK(enabled IN (0,1)), updated_at TEXT NOT NULL
);"""

def initialize(db:sqlite3.Connection)->None:
    db.executescript(SCHEMA)
    with db:db.execute('INSERT OR IGNORE INTO system_control(id,enabled,updated_at) VALUES(1,0,?)',(now(),))

def enabled(db:sqlite3.Connection)->bool:
    initialize(db)
    return bool(db.execute('SELECT enabled FROM system_control WHERE id=1').fetchone()[0])

def set_enabled(db:sqlite3.Connection,value:bool)->None:
    initialize(db)
    with db:db.execute('UPDATE system_control SET enabled=?,updated_at=? WHERE id=1',(int(value),now()))

def status(db:sqlite3.Connection)->dict:
    initialize(db)
    from clip_jobs import prepare
    prepare(db)
    from clip_catalog import prepare as prepare_catalog
    prepare_catalog(db)
    states={s:n for s,n in db.execute('SELECT status,COUNT(*) FROM clip_jobs GROUP BY status')}
    return {'autopilot_enabled':enabled(db),
            'active_jobs':states.get('running',0),
            'queued_jobs':states.get('queued',0),
            'failed_jobs':states.get('failed',0),
            'clips':db.execute('SELECT COUNT(*) FROM clips').fetchone()[0]}

def main():
    p=argparse.ArgumentParser(description='StreamClipper OS local control')
    p.add_argument('action',choices=('start','stop','status'))
    p.add_argument('--db',default='streamclipper_os.sqlite3')
    args=p.parse_args()
    with closing(connect(args.db)) as db:
        if args.action!='status':set_enabled(db,args.action=='start')
        print(json.dumps(status(db),indent=2))
if __name__=='__main__':main()
