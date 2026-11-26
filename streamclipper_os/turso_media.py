"""Persistent verified video references."""

import re
from turso_store import execute, StoreError
from turso_jobs import decode_value
from stream_monitor import now
from video_storage import REMOTE, MAX_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS clip_media (
    clip_id TEXT PRIMARY KEY REFERENCES clips(clip_id),
    provider TEXT NOT NULL CHECK(provider='mega'),
    remote_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_size INTEGER NOT NULL CHECK(file_size>0),
    verified_at TEXT NOT NULL
)
"""


def prepare():
    execute([(SCHEMA, ())])


def record_verified(clip_id, receipt):
    sha = receipt.get("sha256")
    size = receipt.get("size")

    if not isinstance(clip_id, str) or not re.fullmatch(
        r"[a-f0-9]{32}", clip_id
    ):
        raise StoreError("Invalid clip identity")

    if (
        receipt.get("status") != "verified"
        or receipt.get("provider") != "mega"
        or not isinstance(sha, str)
        or not re.fullmatch(r"[a-f0-9]{64}", sha)
        or type(size) is not int
        or not 0 < size <= MAX_FILE
    ):
        raise StoreError("Invalid storage receipt")

    remote = REMOTE + "/" + sha + ".mp4"

    if receipt.get("remote_path") != remote:
        raise StoreError("Invalid remote path")

    result = execute([
        ("""
            INSERT INTO clip_media
                (clip_id,provider,remote_path,
                 sha256,file_size,verified_at)
            SELECT c.clip_id,'mega',?,?,?,?
            FROM clips c
            JOIN clip_review r ON r.clip_id=c.clip_id
            WHERE c.clip_id=?
              AND r.file_sha256=?
              AND r.file_size=?
              AND r.quality_status='passed'
            ON CONFLICT(clip_id)
            DO UPDATE SET clip_id=excluded.clip_id
            WHERE clip_media.provider=excluded.provider
              AND clip_media.remote_path=excluded.remote_path
              AND clip_media.sha256=excluded.sha256
              AND clip_media.file_size=excluded.file_size
            RETURNING clip_id
        """, (remote,sha,size,now(),
              clip_id,sha,size))
    ])[0]

    rows = result["rows"]

    if len(rows) != 1 or decode_value(rows[0][0]) != clip_id:
        raise StoreError("Clip media conflict")

    print("TURSO: VERIFIED VIDEO REGISTERED", flush=True)

    return True


def restore_media(db):
    """Restore metadata without trusting old local paths."""
    prepare()

    columns = (
        "clip_id", "provider", "remote_path",
        "sha256", "file_size", "verified_at"
    )

    fields = ", ".join(columns)

    result = execute([
        ("SELECT " + fields + " FROM clip_media", ())
    ])[0]

    rows = [
        tuple(decode_value(cell) for cell in row)
        for row in result["rows"]
    ]

    with db:
        db.execute(SCHEMA)

        if db.execute(
            "SELECT COUNT(*) FROM clip_media"
        ).fetchone()[0]:
            raise StoreError("Restore requires empty media table")

        for row in rows:
            if len(row) != 6:
                raise StoreError("Invalid media row")

            cid, provider, remote, sha, size, verified = row

            if (
                not isinstance(cid, str)
                or not re.fullmatch(r"[a-f0-9]{32}", cid)
                or provider != "mega"
                or not isinstance(sha, str)
                or not re.fullmatch(r"[a-f0-9]{64}", sha)
                or remote != REMOTE + "/" + sha + ".mp4"
                or type(size) is not int
                or not 0 < size <= MAX_FILE
                or not verified
            ):
                raise StoreError("Invalid media reference")

            review = db.execute("""
                SELECT file_sha256,file_size,quality_status
                FROM clip_review
                WHERE clip_id=?
            """, (cid,)).fetchone()

            if review != (sha,size,"passed"):
                raise StoreError("Media catalog conflict")

            db.execute("""
                INSERT INTO clip_media
                (clip_id,provider,remote_path,
                 sha256,file_size,verified_at)
                VALUES (?,?,?,?,?,?)
            """, row)

    print(
        "TURSO: MEDIA REFERENCES RESTORED",
        len(rows), flush=True
    )

    return len(rows)


def verify_media(db, clip_id):
    """Verify archived content without enabling publication."""
    import tempfile
    import shutil
    from pathlib import Path
    import video_storage as storage

    row = db.execute("""
        SELECT m.provider,m.remote_path,
               m.sha256,m.file_size,
               r.file_sha256,r.file_size,r.quality_status
        FROM clip_media m
        JOIN clip_review r ON r.clip_id=m.clip_id
        WHERE m.clip_id=?
    """, (clip_id,)).fetchone()

    if not row:
        raise StoreError("Unknown archived clip")

    provider, remote, sha, size, review_sha, review_size, quality = row

    if (
        provider != "mega"
        or not isinstance(sha, str)
        or not re.fullmatch(r"[a-f0-9]{64}", sha)
        or remote != REMOTE + "/" + sha + ".mp4"
        or type(size) is not int
        or not 0 < size <= MAX_FILE
        or sha != review_sha
        or size != review_size
        or quality != "passed"
    ):
        raise StoreError("Invalid media reference")

    if shutil.disk_usage(tempfile.gettempdir()).free < (
        size + 64 * 1024**2
    ):
        raise storage.StorageError("Insufficient local storage")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "verified.mp4"

        storage.run(
            "copyto", remote, str(target),
            "--max-size", "100M"
        )

        if (
            not target.is_file()
            or target.stat().st_size != size
            or storage.digest(target) != sha
        ):
            raise storage.StorageError("Archived video integrity failed")

    print("MEDIA: ARCHIVED VIDEO VERIFIED", clip_id, flush=True)

    return {
        "clip_id": clip_id,
        "status": "verified",
        "size": size
    }


def verify_on_start(db_path, clip_id):
    import sqlite3
    from pathlib import Path
    from contextlib import closing

    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"

        with closing(sqlite3.connect(uri, uri=True)) as db:
            result = verify_media(db, clip_id)

        print(
            "V59: RECOVERED VIDEO BYTES",
            result["size"], flush=True
        )

        print(
            "V59: CLOUD RECOVERY TEST PASSED",
            flush=True
        )

    except Exception as exc:
        print(
            "V59: CLOUD RECOVERY FAILED",
            type(exc).__name__, flush=True
        )
