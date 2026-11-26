"""Persistent stream history."""
from turso_store import execute, StoreError

STREAM_COLUMNS = (
    "platform, stream_id, channel, title, "
    "started_at, detected_at, last_seen_at, status"
)

def prepare():
    execute([
        ("""CREATE TABLE IF NOT EXISTS streams (
            platform TEXT NOT NULL,
            stream_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            title TEXT,
            started_at TEXT,
            detected_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'live',
            PRIMARY KEY(platform, stream_id)
        )""", ())
    ])


def save_streams(db):
    rows = db.execute("""
        SELECT s.platform, s.stream_id,
               s.channel, s.title, s.started_at,
               s.detected_at, s.last_seen_at,
               s.status, p.revision
        FROM history_pending p
        JOIN streams s
          ON s.platform=p.platform
         AND s.stream_id=p.stream_id
        ORDER BY s.platform, s.stream_id
        LIMIT 500
    """).fetchall()

    sql = """
        INSERT INTO streams (
            platform, stream_id, channel, title,
            started_at, detected_at, last_seen_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, stream_id)
        DO UPDATE SET
            channel=excluded.channel,
            title=excluded.title,
            started_at=COALESCE(
                excluded.started_at, streams.started_at
            ),
            detected_at=MIN(
                streams.detected_at, excluded.detected_at
            ),
            last_seen_at=excluded.last_seen_at,
            status=excluded.status
        WHERE excluded.last_seen_at >= streams.last_seen_at
    """

    for i in range(0, len(rows), 100):
        batch = rows[i:i+100]

        execute([
            (sql, tuple(row[:8]))
            for row in batch
        ])

        with db:
            db.executemany("""
                DELETE FROM history_pending
                WHERE platform=?
                  AND stream_id=?
                  AND revision=?
            """, [
                (row[0], row[1], row[8])
                for row in batch
            ])

    if rows:
        print(
            "TURSO: STREAM HISTORY SAVED",
            len(rows), flush=True
        )

    return len(rows)


def restore_streams(db):
    prepare()

    result = execute([
        ("SELECT " + STREAM_COLUMNS + " FROM streams", ())
    ])[0]

    rows = [
        tuple(
            None if cell["type"] == "null"
            else cell["value"]
            for cell in row
        )
        for row in result["rows"]
    ]

    for row in rows:
        if len(row) != 8:
            raise StoreError("Invalid stream history")

        if row[0] not in ("twitch", "kick", "youtube"):
            raise StoreError("Invalid platform")

        if not all(row[i] for i in (1, 2, 5, 6, 7)):
            raise StoreError("Invalid stream data")

    with db:
        db.executemany("""
            INSERT INTO streams (
                platform, stream_id, channel, title,
                started_at, detected_at,
                last_seen_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, stream_id)
            DO NOTHING
        """, rows)

    print(
        "TURSO: STREAM HISTORY RESTORED",
        len(rows), flush=True
    )

    return len(rows)


def prepare_tracking(db):
    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS history_pending (
                platform TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(platform, stream_id)
            )
        """)

        for event in ("INSERT", "UPDATE"):
            db.execute(f"""
                CREATE TRIGGER IF NOT EXISTS
                history_track_{event.lower()}
                AFTER {event} ON streams
                BEGIN
                    INSERT INTO history_pending
                        (platform, stream_id, revision)
                    VALUES
                        (NEW.platform, NEW.stream_id, 1)
                    ON CONFLICT(platform, stream_id)
                    DO UPDATE SET revision=revision+1;
                END
            """)


def mark_existing(db):
    with db:
        db.execute("""
            INSERT OR IGNORE INTO history_pending
                (platform, stream_id)
            SELECT platform, stream_id FROM streams
        """)
