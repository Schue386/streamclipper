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
        check=False
    )

    if result.returncode != 0:
        error = result.stderr.lower()

        patterns = {
            "CONFIG_MISSING": (
                "didn't find section",
                "config section",
                "config not found"
            ),
            "PASSWORD_FORMAT": (
                "obscured",
                "base64",
                "failed to reveal",
                "decrypt password"
            ),
            "AUTHENTICATION": (
                "login",
                "invalid password",
                "authentication",
                "invalid credentials",
                "access denied",
                "enoent"
            ),
            "RATE_LIMIT": (
                "too many",
                "rate limit",
                "quota exceeded"
            ),
            "NETWORK": (
                "timeout",
                "connection refused",
                "connection reset",
                "no such host",
                "tls",
                "network"
            ),
            "VERSION_COMPATIBILITY": (
                "unknown flag",
                "not supported",
                "unknown backend"
            )
        }

        matches = [
            label for label, terms in patterns.items()
            if any(term in error for term in terms)
        ]

        print(
            "MEGA: FAILED COMMAND", args[0],
            "EXIT", result.returncode,
            "REASON", ",".join(matches) or "UNKNOWN",
            flush=True
        )

        raise subprocess.CalledProcessError(
            result.returncode, "rclone"
        )

    return result.stdout


def probe():
    remote = "mega:Streamclipper/_probe_" + uuid.uuid4().hex
    uploaded = False

    try:
        version = run("version").splitlines()[0]

        print("MEGA: RCLONE VERSION", version, flush=True)

        for key in (
            "RCLONE_CONFIG_MEGA_TYPE",
            "RCLONE_CONFIG_MEGA_USER",
            "RCLONE_CONFIG_MEGA_PASS"
        ):
            print(
                "MEGA: CONFIG", key,
                "PRESENT" if os.getenv(key) else "MISSING",
                flush=True
            )

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
