"""Import completed StreamClipper MP4 files into the existing local catalog.

Conservative: no publication, no auto-rights approval, no trust in file names.
"""
from __future__ import annotations
from contextlib import closing
import argparse
import json
import sqlite3
from pathlib import Path
from clip_catalog import prepare as prepare_catalog, register
from clip_jobs import prepare as prepare_jobs
from stream_monitor import connect


def discover_mp4(output_dir: str) -> list[Path]:
    root = Path(output_dir).resolve()
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob('*') if p.is_file() and p.suffix.lower() == '.mp4'
                  and not p.is_symlink())


def import_job(db: sqlite3.Connection, job_id: int, *, max_files: int = 500) -> dict:
    """Import only completed jobs; ffprobe decides quality, never mocked metadata."""
    prepare_jobs(db)
    prepare_catalog(db)
    row = db.execute("""SELECT platform,stream_id,status,output_dir FROM clip_jobs
                        WHERE id=?""", (job_id,)).fetchone()
    if not row:
        raise ValueError('Unknown job')
    platform, stream_id, status, output_dir = row
    if status != 'completed':
        return {'job_id': job_id, 'status': 'skipped', 'reason': 'job not completed'}
    if not output_dir:
        return {'job_id': job_id, 'status': 'skipped', 'reason': 'no output directory'}
    files = discover_mp4(output_dir)
    if len(files) > max_files:
        return {'job_id': job_id, 'status': 'skipped', 'reason': 'file limit exceeded'}
    counts = {'registered': 0, 'already_known': 0, 'passed': 0,
              'rejected': 0, 'unverified': 0, 'errors': 0}
    for path in files:
        try:
            # A partial write can be discovered during another process's output flush;
            # only completed jobs are considered and ffprobe still validates the file.
            before = db.execute('SELECT COUNT(*) FROM clips').fetchone()[0]
            result = register(db, platform, stream_id, str(path))
            after = db.execute('SELECT COUNT(*) FROM clips').fetchone()[0]
            if after == before:
                counts['already_known'] += 1
            else:
                counts['registered'] += 1
            counts[result['status']] += 1
        except (OSError, ValueError, sqlite3.Error):
            counts['errors'] += 1
    return {'job_id': job_id, 'status': 'scanned', 'files_found': len(files), **counts}


def import_completed(db: sqlite3.Connection) -> list[dict]:
    prepare_jobs(db)
    jobs = db.execute("SELECT id FROM clip_jobs WHERE status='completed' ORDER BY id").fetchall()
    return [import_job(db, job_id) for (job_id,) in jobs]


def main() -> None:
    p = argparse.ArgumentParser(description='Import rendered MP4s; never publish')
    p.add_argument('--db', default='streamclipper_os.sqlite3')
    p.add_argument('--job-id', type=int)
    args = p.parse_args()
    with closing(connect(args.db)) as db:
        results = [import_job(db, args.job_id)] if args.job_id else import_completed(db)
        print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
