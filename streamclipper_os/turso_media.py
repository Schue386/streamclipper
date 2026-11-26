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
