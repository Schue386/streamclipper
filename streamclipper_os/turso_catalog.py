"""Persistent clip catalog metadata."""

import sqlite3

from turso_store import execute, StoreError
from stream_monitor import SCHEMA as BASE_SCHEMA
from clip_catalog import SCHEMA as CATALOG_SCHEMA

CLIP_COLUMNS = (
    "clip_id", "platform", "stream_id",
    "file_path", "duration_seconds",
    "quality_score", "status", "created_at"
)

REVIEW_COLUMNS = (
    "clip_id", "file_sha256", "file_size",
    "width", "height", "duration_seconds",
    "quality_status", "quality_reason",
    "rights_approved", "rights_note",
    "reviewed_at"
)

QUEUE_COLUMNS = (
    "id", "clip_id", "destination",
    "status", "created_at", "updated_at"
)

TABLES = (
    ("clips", "clip_id", CLIP_COLUMNS),
    ("clip_review", "clip_id", REVIEW_COLUMNS),
    ("upload_queue", "id", QUEUE_COLUMNS)
)


def prepare():
    from turso_history import prepare as prepare_history

    prepare_history()

    statements = []

    for schema in (BASE_SCHEMA, CATALOG_SCHEMA):
        for statement in schema.split(";"):
            sql = statement.strip()

            if not sql:
                continue

            for table, _, _ in TABLES:
                if sql.startswith(
                    "CREATE TABLE IF NOT EXISTS "
                    + table + " ("
                ):
                    statements.append((sql, ()))

    if len(statements) != 3:
        raise StoreError("Incomplete catalog schema")

    execute(statements)

    print("TURSO: CLIP STORAGE READY", flush=True)


def prepare_tracking(db):
    from clip_catalog import prepare as prepare_local

    prepare_local(db)

    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS catalog_pending (
                table_name TEXT NOT NULL,
                row_id TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(table_name, row_id)
            )
        """)

        for table, key, _ in TABLES:
            for event in ("INSERT", "UPDATE"):
                db.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS
                    catalog_track_{table}_{event.lower()}
                    AFTER {event} ON {table}
                    BEGIN
                        INSERT INTO catalog_pending
                            (table_name, row_id, revision)
                        VALUES
                            ('{table}', CAST(NEW.{key} AS TEXT), 1)
                        ON CONFLICT(table_name, row_id)
                        DO UPDATE SET revision=revision+1;
                    END
                """)


def mark_existing(db):
    with db:
        for table, key, _ in TABLES:
            db.execute(f"""
                INSERT OR IGNORE INTO catalog_pending
                    (table_name, row_id)
                SELECT '{table}', CAST({key} AS TEXT)
                FROM {table}
            """)


def save_pending(db):
    from turso_jobs import decode_value

    saved = 0

    for table, key, columns in TABLES:
        fields = ", ".join(columns)
        selected = ", ".join(
            "t." + column for column in columns
        )
        placeholders = ", ".join("?" for _ in columns)

        rows = db.execute(f"""
            SELECT {selected}, p.revision
            FROM catalog_pending p
            JOIN {table} t
              ON CAST(t.{key} AS TEXT)=p.row_id
            WHERE p.table_name=?
            ORDER BY p.row_id
            LIMIT 100
        """, (table,)).fetchall()

        if not rows:
            continue

        identical = " AND ".join(
            f"{table}.{column} IS excluded.{column}"
            for column in columns
        )

        sql = f"""
            INSERT INTO {table} ({fields})
            VALUES ({placeholders})
            ON CONFLICT({key})
            DO UPDATE SET {key}=excluded.{key}
            WHERE {identical}
            RETURNING {key}
        """

        statements = []

        for row in rows:
            values = list(row[:-1])

            if table == "clips":
                values[columns.index("file_path")] = None

            elif table == "clip_review":
                values[columns.index("rights_approved")] = 0
                values[columns.index("rights_note")] = ""

            elif table == "upload_queue":
                status_index = columns.index("status")

                if values[status_index] == "uploaded":
                    raise StoreError(
                        "Publication reconciliation required"
                    )

                values[status_index] = "blocked"

            statements.append((sql, tuple(values)))

        results = execute(statements)

        if len(results) != len(rows) or any(
            len(result["rows"]) != 1
            or decode_value(result["rows"][0][0]) != row[0]
            for result, row in zip(results, rows)
        ):
            raise StoreError("Catalog data conflict")

        with db:
            db.executemany("""
                DELETE FROM catalog_pending
                WHERE table_name=?
                  AND row_id=?
                  AND revision=?
            """, [
                (table, str(row[0]), row[-1])
                for row in rows
            ])

        saved += len(rows)

    if saved:
        print(
            "TURSO: CATALOG DATA SAVED",
            saved, flush=True
        )

    return saved


def restore_catalog(db):
    from clip_catalog import prepare as prepare_local
    from turso_jobs import decode_value

    prepare_local(db)
    prepare()

    results = execute([
        ("SELECT " + ", ".join(columns) +
         " FROM " + table, ())
        for table, _, columns in TABLES
    ])

    if len(results) != len(TABLES):
        raise StoreError("Incomplete catalog response")

    counts = {}

    with db:
        db.execute("BEGIN IMMEDIATE")

        for table, _, _ in TABLES:
            if db.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]:
                raise StoreError(
                    "Restore requires empty catalog"
                )

        for (table, key, columns), result in zip(
            TABLES, results
        ):
            rows = []

            for row in result["rows"]:
                values = [
                    decode_value(cell) for cell in row
                ]

                if len(values) != len(columns):
                    raise StoreError("Invalid catalog row")

                if table == "clips":
                    values[columns.index("file_path")] = None

                elif table == "clip_review":
                    values[columns.index("rights_approved")] = 0
                    values[columns.index("rights_note")] = ""

                elif table == "upload_queue":
                    index = columns.index("status")
                    if values[index] != "uploaded":
                        values[index] = "blocked"

                rows.append(tuple(values))

            fields = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)

            db.executemany(
                f"INSERT INTO {table} ({fields}) "
                f"VALUES ({placeholders})",
                rows
            )

            counts[table] = len(rows)

    for table, count in counts.items():
        print(
            "TURSO:", table.upper(),
            "RESTORED", count, flush=True
        )

    return counts
