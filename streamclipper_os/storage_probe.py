"""Non-blocking MEGA storage verification."""
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path


def run(*args):
    result = subprocess.run(
        ["rclone", *args],
        capture_output=True,
        text=True,
        timeout=45,
        check=True
    )
    return result.stdout


def probe():
    remote = "mega:Streamclipper/_probe_" + uuid.uuid4().hex
    uploaded = False

    try:
        info = json.loads(run("about", "mega:", "--json"))

        print("MEGA: CONNECTION OK", flush=True)
        print("MEGA: TOTAL BYTES", info["total"], flush=True)
        print("MEGA: FREE BYTES", info["free"], flush=True)

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "test.bin"
            target = Path(tmp) / "download.bin"

            source.write_bytes(os.urandom(1024 * 1024))

            run("copyto", str(source), remote + "/test.bin")
            uploaded = True
            print("MEGA: UPLOAD OK", flush=True)

            run("copyto", remote + "/test.bin", str(target))
            print("MEGA: DOWNLOAD OK", flush=True)

            if source.read_bytes() != target.read_bytes():
                raise RuntimeError("Integrity check failed")

            print("MEGA: INTEGRITY OK", flush=True)

        run("deletefile", remote + "/test.bin")
        uploaded = False
        run("rmdir", remote)

        print("MEGA: DELETE OK", flush=True)
        print("MEGA: CLOUD STORAGE TEST PASSED", flush=True)

    except Exception as exc:
        print("MEGA: TEST FAILED", type(exc).__name__, flush=True)

        if uploaded:
            try:
                run("deletefile", remote + "/test.bin")
                run("rmdir", remote)
            except Exception:
                print("MEGA: CLEANUP REQUIRED", flush=True)
