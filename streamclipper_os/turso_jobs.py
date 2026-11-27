"""Persistent clip job and worker metadata."""
from turso_store import execute, StoreError
from clip_jobs import JOB_SCHEMA
from supervisor import SCHEMA as WORKER_SCHEMA

JOB_COLUMNS = (
    "id", "platform", "stream_id", "channel",
    "status", "attempts", "output_dir",
    "error_message", "created_at",
    "started_at", "finished_at"
)

WORKER_COLUMNS = (
    "job_id", "pid", "started_at",
    "heartbeat_at", "deadline_at",
    "exit_code", "ended_at", "log_path"
)


def prepare():
    from turso_history import prepare as prepare_history

    prepare_history()

    execute([
        (JOB_SCHEMA.strip(), ()),
        (WORKER_SCHEMA.strip(), ())
    ])

    print("TURSO: JOB STORAGE READY", flush=True)


def decode_value(cell):
    kind = cell["type"]

    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    if kind == "text":
        return cell["value"]

    raise StoreError("Unsupported stored value")


def prepare_tracking(db):
    from supervisor import initialize

    initialize(db)

    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS jobs_pending (
                table_name TEXT NOT NULL,
                row_id INTEGER NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(table_name, row_id)
            )
        """)

        for table, key in (
            ("clip_jobs", "id"),
            ("worker_runs", "job_id")
        ):
            for event in ("INSERT", "UPDATE"):
                db.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS
                    track_{table}_{event.lower()}
                    AFTER {event} ON {table}
                    BEGIN
                        INSERT INTO jobs_pending
                            (table_name, row_id, revision)
                        VALUES
                            ('{table}', NEW.{key}, 1)
                        ON CONFLICT(table_name, row_id)
                        DO UPDATE SET revision=revision+1;
                    END
                """)


def mark_existing(db):
    with db:
        for table, key in (
            ("clip_jobs", "id"),
            ("worker_runs", "job_id")
        ):
            db.execute(f"""
                INSERT OR IGNORE INTO jobs_pending
                    (table_name, row_id)
                SELECT '{table}', {key} FROM {table}
            """)


def save_pending(db):
    saved = 0

    for table, key, columns in (
        ("clip_jobs", "id", JOB_COLUMNS),
        ("worker_runs", "job_id", WORKER_COLUMNS)
    ):
        fields = ", ".join(columns)
        selected = ", ".join(
            "t." + column for column in columns
        )
        placeholders = ", ".join("?" for _ in columns)

        updates = ", ".join(
            f"{column}=excluded.{column}"
            for column in columns if column != key
        )

        rows = db.execute(f"""
            SELECT {selected}, p.revision
            FROM jobs_pending p
            JOIN {table} t ON t.{key}=p.row_id
            WHERE p.table_name=?
            ORDER BY p.row_id
            LIMIT 100
        """, (table,)).fetchall()

        sql = f"""
            INSERT INTO {table} ({fields})
            VALUES ({placeholders})
            ON CONFLICT({key})
            DO UPDATE SET {updates}
        """

        if not rows:
            continue

        if table == "clip_jobs":
            updates = ", ".join(
                f"{column}=excluded.{column}"
                for column in columns
                if column not in (
                    "id", "platform", "stream_id", "channel"
                )
            )

            sql = f"""
                INSERT INTO clip_jobs ({fields})
                VALUES ({placeholders})
                ON CONFLICT(id)
                DO UPDATE SET {updates}
                WHERE clip_jobs.platform=excluded.platform
                  AND clip_jobs.stream_id=excluded.stream_id
                  AND clip_jobs.channel=excluded.channel
                  AND excluded.attempts >= clip_jobs.attempts
                  AND (
                      clip_jobs.status=excluded.status
                      OR (
                          clip_jobs.status='queued'
                          AND excluded.status IN (
                              'running','completed','failed'
                          )
                      )
                      OR (
                          clip_jobs.status='running'
                          AND excluded.status IN (
                              'completed','failed'
                          )
                      )
                  )
                  AND (
                      clip_jobs.status NOT IN (
                          'completed','failed'
                      )
                      OR (
                          excluded.attempts=clip_jobs.attempts
                          AND excluded.finished_at
                              IS clip_jobs.finished_at
                          AND excluded.error_message
                              IS clip_jobs.error_message
                          AND excluded.started_at
                              IS clip_jobs.started_at
                          AND excluded.output_dir
                              IS clip_jobs.output_dir
                          AND excluded.created_at
                              IS clip_jobs.created_at
                      )
                  )
                RETURNING id
            """

            results = execute([
                (sql, tuple(row[:-1]))
                for row in rows
            ])

            if len(results) != len(rows) or any(
                len(result["rows"]) != 1
                or decode_value(result["rows"][0][0]) != row[0]
                for result, row in zip(results, rows)
            ):
                raise StoreError("Job identity conflict")

        else:
            sql = f"""
                INSERT INTO worker_runs ({fields})
                SELECT {placeholders}
                WHERE EXISTS (SELECT 1 FROM clip_jobs j
                    WHERE j.id=? AND j.output_dir || '/supervisor.log' = ?
                      AND j.status IN ('running','completed','failed'))
                ON CONFLICT(job_id)
                DO UPDATE SET {updates}
                WHERE worker_runs.pid IS excluded.pid
                  AND worker_runs.started_at=excluded.started_at
                  AND worker_runs.deadline_at=excluded.deadline_at
                  AND worker_runs.log_path=excluded.log_path
                  AND excluded.heartbeat_at >= worker_runs.heartbeat_at
                  AND (
                      worker_runs.ended_at IS NULL
                      OR (
                          excluded.ended_at IS worker_runs.ended_at
                          AND excluded.exit_code IS worker_runs.exit_code
                          AND excluded.heartbeat_at=worker_runs.heartbeat_at
                      )
                  )
                RETURNING job_id
            """

            results = execute([
                (sql, (*row[:-1], row[0], row[7]))
                for row in rows
            ])

            if len(results) != len(rows) or any(
                len(result["rows"]) != 1
                or decode_value(result["rows"][0][0]) != row[0]
                for result, row in zip(results, rows)
            ):
                raise StoreError("Worker state conflict")

        with db:
            db.executemany("""
                DELETE FROM jobs_pending
                WHERE table_name=?
                  AND row_id=?
                  AND revision=?
            """, [
                (table, row[0], row[-1])
                for row in rows
            ])

        saved += len(rows)

    if saved:
        print("TURSO: JOB DATA SAVED", saved, flush=True)

    return saved


def prepare_reservations():
    execute([
        ("""
            CREATE TABLE IF NOT EXISTS job_reservations (
                platform TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                reserved_at TEXT NOT NULL,
                PRIMARY KEY(platform, stream_id)
            )
        """, ())
    ])


def reserve_job(platform, stream_id, owner):
    from stream_monitor import now

    if not all(
        isinstance(v, str) and v.strip()
        for v in (platform, stream_id, owner)
    ):
        raise ValueError("Invalid reservation")

    result = execute([
        ("""
            INSERT INTO job_reservations
                (platform, stream_id, owner, reserved_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(platform, stream_id)
            DO NOTHING
            RETURNING owner
        """, (platform, stream_id, owner, now()))
    ])[0]

    rows = result["rows"]

    if not rows:
        return False

    if len(rows) != 1:
        raise StoreError("Invalid reservation response")

    if decode_value(rows[0][0]) != owner:
        raise StoreError("Invalid reservation owner")

    return True


def restore_jobs(db):
    from supervisor import initialize

    initialize(db)
    prepare()
    prepare_reservations()
    prepare_cycles()

    datasets = []

    for table, columns in (
        ("clip_jobs", JOB_COLUMNS),
        ("worker_runs", WORKER_COLUMNS)
    ):
        fields = ", ".join(columns)

        result = execute([
            (f"SELECT {fields} FROM {table}", ())
        ])[0]

        rows = [
            tuple(decode_value(cell) for cell in row)
            for row in result["rows"]
        ]

        if any(len(row) != len(columns) for row in rows):
            raise StoreError("Invalid job data")

        datasets.append((table, columns, rows))

    with db:
        db.execute("BEGIN IMMEDIATE")

        for table, columns, rows in datasets:
            fields = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)

            if db.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]:
                raise StoreError(
                    "Restore requires empty local job tables"
                )

            db.executemany(
                f"INSERT INTO {table} ({fields}) "
                f"VALUES ({placeholders})",
                rows
            )

    counts = {
        table: len(rows)
        for table, _, rows in datasets
    }

    print(
        "TURSO: JOBS RESTORED",
        counts["clip_jobs"], flush=True
    )

    print(
        "TURSO: WORKER RUNS RESTORED",
        counts["worker_runs"], flush=True
    )

    return counts


def reserve_production_job(job_id, stream_id, channel, owner):
    """Atomically reserve one persisted production job."""
    from stream_monitor import now

    result = execute([
        ("""
            INSERT INTO job_reservations
                (platform,stream_id,owner,reserved_at)

            SELECT j.platform,j.stream_id,?,?
            FROM clip_jobs j
            WHERE j.id=?
              AND j.platform='twitch'
              AND j.stream_id=?
              AND j.channel=?
              AND j.status='queued'

              AND NOT EXISTS (
                  SELECT 1 FROM clip_jobs
                  WHERE status='running'
              )

              AND NOT EXISTS (
                  SELECT 1
                  FROM job_reservations r
                  LEFT JOIN clip_jobs x
                    ON x.platform=r.platform
                   AND x.stream_id=r.stream_id
                  WHERE x.id IS NULL
                     OR x.status NOT IN ('completed','failed')
              )

            ON CONFLICT(platform,stream_id)
            DO NOTHING
            RETURNING owner
        """, (owner,now(),job_id,stream_id,channel))
    ])[0]

    rows = result["rows"]

    if not rows:
        return False

    if len(rows) != 1 or decode_value(rows[0][0]) != owner:
        raise StoreError("Invalid production reservation")

    return True


CYCLE_SCHEMA = 'CREATE TABLE IF NOT EXISTS recording_cycles (\n    job_id INTEGER NOT NULL, attempt INTEGER NOT NULL,\n    platform TEXT NOT NULL, stream_id TEXT NOT NULL, channel TEXT NOT NULL,\n    status TEXT NOT NULL, output_dir TEXT, error_message TEXT,\n    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,\n    pid INTEGER, worker_started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,\n    deadline_at REAL NOT NULL, exit_code INTEGER, ended_at TEXT,\n    log_path TEXT NOT NULL,\n    PRIMARY KEY(job_id, attempt)\n)'
CYCLE_TRIGGER = "CREATE TRIGGER IF NOT EXISTS archive_recording_cycle_v6063\nBEFORE UPDATE OF status ON clip_jobs\nWHEN OLD.status='completed' AND NEW.status='queued'\nBEGIN\n    SELECT CASE WHEN NEW.attempts != OLD.attempts+1\n        OR OLD.attempts < 1 OR OLD.finished_at IS NULL\n        OR NOT EXISTS (SELECT 1 FROM worker_runs w WHERE w.job_id=OLD.id\n                       AND w.exit_code=0 AND w.ended_at IS NOT NULL)\n        THEN RAISE(ABORT,'invalid cycle transition') END;\n    INSERT INTO recording_cycles\n        (job_id,attempt,platform,stream_id,channel,status,output_dir,error_message,\n         created_at,started_at,finished_at,pid,worker_started_at,heartbeat_at,\n         deadline_at,exit_code,ended_at,log_path)\n        SELECT OLD.id,OLD.attempts,OLD.platform,OLD.stream_id,OLD.channel,\n               OLD.status,OLD.output_dir,OLD.error_message,OLD.created_at,\n               OLD.started_at,OLD.finished_at,w.pid,w.started_at,w.heartbeat_at,\n               w.deadline_at,w.exit_code,w.ended_at,w.log_path\n        FROM worker_runs w WHERE w.job_id=OLD.id;\n    DELETE FROM worker_runs WHERE job_id=OLD.id;\n    DELETE FROM job_reservations WHERE platform=OLD.platform AND stream_id=OLD.stream_id;\nEND"
REQUEUE_SQL = "UPDATE clip_jobs AS j\nSET status='queued', attempts=attempts+1,\n    started_at=NULL, finished_at=NULL, output_dir=NULL, error_message=NULL\nWHERE j.id=? AND j.platform='twitch' AND j.stream_id=? AND j.channel=?\n  AND j.status='completed' AND j.attempts=? AND j.attempts>=1\n  AND j.finished_at IS NOT NULL\n  AND EXISTS (SELECT 1 FROM worker_runs w WHERE w.job_id=j.id\n              AND w.exit_code=0 AND w.ended_at IS NOT NULL)\n  AND NOT EXISTS (SELECT 1 FROM clip_jobs a WHERE a.status IN ('queued','running'))\n  AND NOT EXISTS (\n      SELECT 1 FROM job_reservations r\n      LEFT JOIN clip_jobs x ON x.platform=r.platform AND x.stream_id=r.stream_id\n      WHERE x.id IS NULL OR x.status NOT IN ('completed','failed')\n  )\n  AND NOT EXISTS (\n      SELECT 1 FROM clips c JOIN clip_review r ON r.clip_id=c.clip_id\n      LEFT JOIN clip_media m ON m.clip_id=c.clip_id\n      WHERE c.platform=j.platform AND c.stream_id=j.stream_id\n        AND r.quality_status='passed' AND m.clip_id IS NULL\n  )\nRETURNING id,attempts"


def prepare_cycles():
    execute([(CYCLE_SCHEMA, ()), (CYCLE_TRIGGER, ())])


def requeue_completed(job_id, stream_id, channel, attempt):
    """Atomic remote compare-and-set; old evidence archived by trigger."""
    result = execute([(REQUEUE_SQL, (job_id, stream_id, channel, attempt))])[0]
    rows = result["rows"]
    if not rows:
        return False
    if len(rows) != 1 or tuple(decode_value(c) for c in rows[0]) != (job_id, attempt+1):
        raise StoreError("Invalid cycle reservation response")
    return True
