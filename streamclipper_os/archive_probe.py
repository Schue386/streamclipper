"""Opt-in real cloud archive test using synthetic video."""

import subprocess
import tempfile
import uuid
from pathlib import Path
from contextlib import closing

from stream_monitor import connect, now
from clip_catalog import register, approve_rights
from clip_archive import archive_clip

import turso_history as history
import turso_catalog as catalog


def probe():
    try:
        with tempfile.TemporaryDirectory() as tmp, \
             closing(connect(":memory:")) as db:

            source = Path(tmp) / "test.mp4"

            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error",
                "-f", "lavfi",
                "-i", "testsrc2=size=180x320:rate=15",
                "-t", "75",
                "-c:v", "libx264",
                "-threads", "1",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                str(source)
            ], check=True, timeout=180)

            print("V58: SYNTHETIC VIDEO CREATED", flush=True)

            history.prepare()
            history.prepare_tracking(db)

            catalog.prepare()
            catalog.prepare_tracking(db)

            sid = "streamclipper-v58-test-" + uuid.uuid4().hex
            t = now()

            with db:
                db.execute("""
                    INSERT INTO streams
                    (platform,stream_id,channel,title,
                     detected_at,last_seen_at,status)
                    VALUES
                    ('youtube',?,'streamclipper_test',
                     'SYNTHETIC V58 STORAGE TEST',
                     ?,?,'offline')
                """, (sid,t,t))

            clip = register(
                db, "youtube", sid, str(source)
            )

            if clip["status"] != "passed":
                raise RuntimeError("Quality check failed")

            print("V58: VIDEO QUALITY PASSED", flush=True)

            approve_rights(
                db, clip["clip_id"],
                "Self-generated synthetic test video"
            )

            assert history.save_streams(db) == 1
            assert catalog.save_pending(db) == 2

            print("V58: CLOUD CATALOG SAVED", flush=True)

            result = archive_clip(db, clip["clip_id"])

            assert result["status"] == "verified"
            assert source.is_file()
            assert not result["published"]

            print(
                "V58: VERIFIED VIDEO BYTES",
                result["size"], flush=True
            )

            print(
                "V58: CLOUD ARCHIVE TEST PASSED",
                flush=True
            )

    except Exception as exc:
        print(
            "V58: CLOUD ARCHIVE FAILED",
            type(exc).__name__,
            flush=True
        )
