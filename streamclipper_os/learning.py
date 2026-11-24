"""Conservative, platform-specific observational recommendations."""
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from performance import analyze


def recommend(db, current=None):
    result = {}
    for destination in ('youtube', 'tiktok'):
        groups = defaultdict(list)
        for clip in analyze(db, current, destination)['clips']:
            duration, published = db.execute('''SELECT c.duration_seconds,p.published_at
                FROM clips c JOIN publications p ON p.clip_id=c.clip_id
                WHERE c.clip_id=? AND p.destination=?''', (clip['clip_id'], destination)).fetchone()
            score = clip['views_per_day']
            if duration is not None and 61 <= duration <= 90:
                groups['length', '61–70 s' if duration <= 70 else '71–80 s' if duration <= 80 else '81–90 s'].append(score)
            try:
                stamp = datetime.fromisoformat(published)
                if stamp.tzinfo is not None:
                    hour = stamp.astimezone(timezone.utc).hour // 6 * 6
                    groups['time_utc', f'{hour:02d}:00–{hour+6:02d}:00'].append(score)
            except (ValueError, TypeError):
                pass
        dimensions = {}
        for dimension in ('length', 'time_utc'):
            buckets = sorted([dict(bucket=b, samples=len(v), median_views_per_day=round(median(v),2)) for (d,b),v in groups.items() if d==dimension], key=lambda x:(-x['median_views_per_day'],x['bucket']))
            eligible = [b for b in buckets if b['samples'] >= 5]
            preferred = eligible[0]['bucket'] if len(eligible)>=2 and eligible[0]['median_views_per_day']>1.2*eligible[1]['median_views_per_day'] else None
            dimensions[dimension] = dict(buckets=buckets, preferred=preferred)
        result[destination] = dimensions
    return result
