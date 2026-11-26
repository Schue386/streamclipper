"""Per-channel monitoring status."""
from datetime import datetime, timezone

def prepare(db):
    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS streamer_status (
                platform TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                error TEXT,
                PRIMARY KEY(platform, channel),
                FOREIGN KEY(platform, channel)
                    REFERENCES streamers(platform, channel)
                    ON DELETE CASCADE
            )
        """)

def update(db, platform, channels, live=None, error=None):
    prepare(db)
    timestamp = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    live = set(live or [])

    with db:
        for channel in channels:
            state = (
                "error" if error else
                "live" if channel in live else
                "offline"
            )

            db.execute("""
                INSERT INTO streamer_status
                    (platform, channel, status, checked_at, error)
                SELECT platform, channel, ?, ?, ?
                FROM streamers
                WHERE platform=? AND channel=? AND enabled=1
                ON CONFLICT(platform, channel)
                DO UPDATE SET
                    status=excluded.status,
                    checked_at=excluded.checked_at,
                    error=excluded.error
            """, (
                state, timestamp, error,
                platform, channel
            ))

def resolve(status, checked_at, max_age=300):
    if not status or not checked_at:
        return "pending"

    try:
        checked = datetime.fromisoformat(checked_at)
        age = (
            datetime.now(timezone.utc) - checked
        ).total_seconds()

        if age > max_age or age < -30:
            return "stale"

    except (ValueError, TypeError):
        return "stale"

    return status

def report_error(db, platform, error):
    channels = [
        row[0] for row in db.execute(
            "SELECT channel FROM streamers WHERE platform=? AND enabled=1",
            (platform,)
        )
    ]
    update(db, platform, channels, error=error)
