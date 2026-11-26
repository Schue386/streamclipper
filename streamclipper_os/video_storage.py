"""Verified MEGA storage for completed clips."""

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

REMOTE = "mega:Streamclipper/clips"
LIMIT = 18 * 1024**3
MAX_FILE = 100 * 1024**2
LOCK = threading.Lock()


class StorageError(RuntimeError):
    pass


def run(*args):
    try:
        result = subprocess.run(
            ["rclone", *args,
             "--retries", "1",
             "--low-level-retries", "2"],
            capture_output=True,
            text=True,
            timeout=180,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        print(
            "STORAGE: COMMAND FAILED",
            args[0],
            "EXIT", exc.returncode,
            flush=True
        )
        raise StorageError("Transfer command failed") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise StorageError(type(exc).__name__) from None


def usage():
    data = json.loads(run("about", "mega:", "--json"))

    total = int(data["total"])
    free = int(data["free"])

    if not 0 <= free <= total:
        raise StorageError("Invalid storage quota")

    used = total - free

    return {
        "total": total,
        "used": used,
        "free": free,
        "budget": max(0, min(free, LIMIT - used))
    }


def digest(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def store_clip(path, expected_sha256):
    source = Path(path)

    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise StorageError("Invalid checksum")

    if (source.is_symlink() or not source.is_file()
            or source.suffix.lower() != ".mp4"):
        raise StorageError("Invalid video file")

    size = source.stat().st_size

    if not 0 < size <= MAX_FILE:
        raise StorageError("File size outside limit")

    with LOCK:
        quota = usage()

        if size > quota["budget"]:
            raise StorageError("Storage limit reached")

        if shutil.disk_usage(tempfile.gettempdir()).free < (
            2 * size + 64 * 1024**2
        ):
            raise StorageError("Insufficient local storage")

        remote = REMOTE + "/" + expected_sha256 + ".mp4"

        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "source.mp4"
            verified = Path(tmp) / "verified.mp4"

            shutil.copyfile(source, staged)

            if (staged.stat().st_size != size
                    or digest(staged) != expected_sha256):
                raise StorageError("Source checksum mismatch")

            run("copyto", str(staged), remote, "--immutable")

            run("copyto", remote, str(verified))

            if (verified.stat().st_size != size
                    or digest(verified) != expected_sha256):
                raise StorageError("Remote integrity failed")

        return {
            "status": "verified",
            "provider": "mega",
            "remote_path": remote,
            "sha256": expected_sha256,
            "size": size
        }
