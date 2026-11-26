"""Central streamer management."""
from stream_monitor import add_channel

def manage(db, platform, channel, action):
    platform = platform.strip().lower()
    channel = channel.strip()

    if platform not in ("twitch", "kick", "youtube"):
        raise ValueError("Invalid platform")

    if not channel or len(channel) > 200:
        raise ValueError("Invalid channel")

    if action == "add":
        if platform == "twitch":
            add_channel(db, channel)
        elif platform == "kick":
            from kick_monitor import add_channel as add_kick
            add_kick(db, channel)
        else:
            from youtube_monitor import add_channel as add_youtube
            add_youtube(db, channel)
        return

    if action not in ("enable", "disable", "remove"):
        raise ValueError("Invalid action")

    with db:
        if action == "remove":
            cursor = db.execute(
                "DELETE FROM streamers WHERE platform=? AND channel=?",
                (platform, channel)
            )
        else:
            cursor = db.execute(
                "UPDATE streamers SET enabled=? WHERE platform=? AND channel=?",
                (int(action == "enable"), platform, channel)
            )

        if cursor.rowcount != 1:
            raise ValueError("Streamer not found")

def overview(db):
    rows = db.execute("""
        SELECT s.platform, s.channel, s.enabled,
               t.status, t.title, t.started_at,
               t.last_seen_at
        FROM streamers s
        LEFT JOIN streams t ON t.rowid = (
            SELECT x.rowid FROM streams x
            WHERE x.platform = s.platform
              AND x.channel = s.channel
            ORDER BY x.last_seen_at DESC, x.rowid DESC
            LIMIT 1
        )
        ORDER BY s.platform, s.channel
    """).fetchall()

    return [
        {
            "platform": r[0],
            "channel": r[1],
            "enabled": bool(r[2]),
            "status": r[3] or "unknown",
            "title": r[4],
            "started_at": r[5],
            "last_seen_at": r[6]
        }
        for r in rows
    ]
