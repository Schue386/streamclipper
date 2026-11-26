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
