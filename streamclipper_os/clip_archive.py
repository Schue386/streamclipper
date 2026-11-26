"""Verified archival of authorized completed clips."""

import re
from pathlib import Path

import video_storage as storage
import turso_media as media

from turso_jobs import decode_value


def archive_clip(db, clip_id):

    if not isinstance(clip_id, str) or not re.fullmatch(
        r"[a-f0-9]{32}", clip_id
    ):
        raise storage.StorageError("Invalid clip ID")

    row = db.execute("""
        SELECT c.file_path,
               r.file_sha256,
               r.file_size,
               r.quality_status,
               r.rights_approved
        FROM clips c
        JOIN clip_review r ON r.clip_id=c.clip_id
        WHERE c.clip_id=?
    """, (clip_id,)).fetchone()

    if not row:
        raise storage.StorageError("Unknown clip")

    path, sha, size, quality, rights = row

    if quality != "passed":
        raise storage.StorageError("Quality check required")

    if rights != 1:
        raise storage.StorageError("Permission required")

    if not path:
        raise storage.StorageError("Local video unavailable")

    source = Path(path)

    if source.is_symlink() or not source.is_file():
        raise storage.StorageError("Local video unavailable")

    if source.stat().st_size != size:
        raise storage.StorageError("File size mismatch")

    if storage.digest(source) != sha:
        raise storage.StorageError("File checksum mismatch")

    media.prepare()

    result = media.execute([
        ("""
            SELECT c.clip_id
            FROM clips c
            JOIN clip_review r ON r.clip_id=c.clip_id
            WHERE c.clip_id=?
              AND r.file_sha256=?
              AND r.file_size=?
              AND r.quality_status='passed'
        """, (clip_id, sha, size))
    ])[0]

    rows = result["rows"]

    if len(rows) != 1 or decode_value(rows[0][0]) != clip_id:
        raise media.StoreError("Cloud catalog not synchronized")

    receipt = storage.store_clip(str(source), sha)

    if receipt["size"] != size:
        raise storage.StorageError("Receipt size mismatch")

    media.record_verified(clip_id, receipt)

    print(
        "ARCHIVE: CLIP VERIFIED AND STORED",
        clip_id,
        flush=True
    )

    return {
        "clip_id": clip_id,
        **receipt,
        "local_preserved": True,
        "published": False
    }


def archive_next(db):
    """Archive one eligible clip, without publishing."""
    import sqlite3
    import time
    from stream_monitor import now

    with db:
        db.execute(media.SCHEMA)

        db.execute("""
            CREATE TABLE IF NOT EXISTS archive_retry (
                clip_id TEXT PRIMARY KEY,
                retry_after REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            )
        """)

    for table in (
        "history_pending",
        "jobs_pending",
        "catalog_pending"
    ):
        if db.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]:
            return {"status": "waiting_for_sync"}

    row = db.execute("""
        SELECT c.clip_id
        FROM clips c
        JOIN clip_review r ON r.clip_id=c.clip_id
        LEFT JOIN clip_media m ON m.clip_id=c.clip_id
        LEFT JOIN archive_retry a ON a.clip_id=c.clip_id
        WHERE r.quality_status='passed'
          AND r.rights_approved=1
          AND c.file_path IS NOT NULL
          AND c.file_path!=''
          AND m.clip_id IS NULL
          AND COALESCE(a.retry_after,0)<=?
        ORDER BY c.created_at,c.clip_id
        LIMIT 1
    """, (time.time(),)).fetchone()

    if not row:
        return {"status": "idle"}

    cid = row[0]

    try:
        result = archive_clip(db, cid)

        if result["status"] != "verified":
            raise storage.StorageError(
                "Archive not verified"
            )

        with db:
            db.execute("""
                INSERT INTO clip_media
                (clip_id,provider,remote_path,
                 sha256,file_size,verified_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(clip_id) DO NOTHING
            """, (
                cid,
                result["provider"],
                result["remote_path"],
                result["sha256"],
                result["size"],
                now()
            ))

            stored = db.execute("""
                SELECT provider,remote_path,sha256,file_size
                FROM clip_media WHERE clip_id=?
            """, (cid,)).fetchone()

            expected = (
                result["provider"],
                result["remote_path"],
                result["sha256"],
                result["size"]
            )

            if stored != expected:
                raise media.StoreError(
                    "Local media conflict"
                )

            db.execute(
                "DELETE FROM archive_retry WHERE clip_id=?",
                (cid,)
            )

        return {
            "status": "archived",
            "clip_id": cid,
            "size": result["size"]
        }

    except (
        storage.StorageError,
        media.StoreError,
        sqlite3.Error,
        OSError
    ) as exc:

        with db:
            db.execute("""
                INSERT INTO archive_retry
                (clip_id,retry_after,attempts)
                VALUES (?,?,1)
                ON CONFLICT(clip_id) DO UPDATE SET
                    retry_after=excluded.retry_after,
                    attempts=archive_retry.attempts+1
            """, (cid,time.time()+300))

        return {
            "status": "retry_pending",
            "clip_id": cid,
            "error": type(exc).__name__
        }
