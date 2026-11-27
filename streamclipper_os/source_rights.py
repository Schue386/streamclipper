"""Persistent recording and archival permissions."""
import re
from turso_store import execute, StoreError
from stream_monitor import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_rights (
    channel TEXT PRIMARY KEY,
    approved_at TEXT NOT NULL
)
"""

def prepare():
    execute([(SCHEMA, ())])

def approved_channels():
    result = execute([
        ("""
            SELECT r.channel
            FROM source_rights r
            JOIN streamers s
              ON s.platform='twitch'
             AND s.channel=r.channel
        """, ())
    ])[0]

    channels = {
        row[0]["value"] for row in result["rows"]
    }

    if any(not re.fullmatch(
        r"[a-z0-9_]{1,25}", c
    ) for c in channels):
        raise StoreError("Invalid source permissions")

    return channels

def set_approval(platform, channel, approved):
    channel = channel.strip().lower()

    if platform != "twitch" or not re.fullmatch(
        r"[a-z0-9_]{1,25}", channel
    ):
        raise ValueError("Invalid source")

    if approved:
        result = execute([
            ("""
            INSERT INTO source_rights
                (channel, approved_at)
            SELECT channel, ?
            FROM streamers
            WHERE platform='twitch' AND channel=?
            ON CONFLICT(channel) DO UPDATE SET
                approved_at=excluded.approved_at
            RETURNING channel
            """, (now(), channel))
        ])[0]

        if not result["rows"]:
            raise ValueError("Unknown channel")

    else:
        execute([
            ("DELETE FROM source_rights WHERE channel=?",
             (channel,))
        ])
