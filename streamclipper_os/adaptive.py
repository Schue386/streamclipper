"""Bounded adaptive scheduling. No changes to rights, quality or publishing gates."""
from datetime import datetime, timezone
from performance import analyze

def choose(db, rows, current=None):
    if not rows: return None
    current = current or datetime.now(timezone.utc)
    scores={(r['platform'],r['channel']):r['median_views_per_day'] for r in analyze(db,current)['channels'] if r['samples']>=5}
    # Every third attempted job uses FIFO; jobs waiting 15 minutes also use FIFO.
    attempted=db.execute('SELECT COALESCE(SUM(attempts),0) FROM clip_jobs').fetchone()[0]
    oldest=rows[0]
    try: waiting=(current-datetime.fromisoformat(oldest[4])).total_seconds()
    except (ValueError,TypeError): return oldest
    if attempted%3==0 or waiting>=900: return oldest
    return max(rows,key=lambda r:scores.get((r[3],r[1]),0))
