import os
import json
import urllib.request
import urllib.error
import urllib.parse
import uuid

def probe():
    url = os.getenv("TURSO_DATABASE_URL", "").strip()
    token = os.getenv("TURSO_AUTH_TOKEN", "").strip()

    print("TURSO: URL LENGTH", len(url), flush=True)
    print("TURSO: TOKEN PRESENT", bool(token), flush=True)
    print("TURSO: URL MATCH", url.strip() in (
        "libsql://streamclipper-schue386.aws-eu-central-1.turso.io",
        "https://streamclipper-schue386.aws-eu-central-1.turso.io"
    ), flush=True)

    if not url or not token:
        print("TURSO: CONFIG MISSING", flush=True)
        return False

    try:
        url = url.replace("libsql://", "https://", 1)
        parsed = urllib.parse.urlsplit(url)

        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Invalid database URL")

        marker = uuid.uuid4().hex

        statements = [
            "CREATE TABLE IF NOT EXISTS connection_probe "
            "(id TEXT PRIMARY KEY)",
            f"INSERT INTO connection_probe VALUES ('{marker}')",
            f"SELECT id FROM connection_probe WHERE id='{marker}'",
            f"DELETE FROM connection_probe WHERE id='{marker}'"
        ]

        requests = [
            {"type": "execute", "stmt": {"sql": sql, "args": [], "named_args": [], "want_rows": True}}
            for sql in statements
        ]
        requests.append({"type": "close"})

        req = urllib.request.Request(
            url.rstrip("/") + "/v2/pipeline",
            data=json.dumps({"baton": None, "requests": requests}).encode(),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.load(response)

        results = result["results"]

        if len(results) != 5:
            raise RuntimeError("Incomplete response")

        if any(r.get("type") != "ok" for r in results):
            raise RuntimeError("SQL execution failed")

        rows = results[2]["response"]["result"]["rows"]

        if not rows or rows[0][0]["value"] != marker:
            raise RuntimeError("Read verification failed")

        print("TURSO: CONNECTION OK", flush=True)
        print("TURSO: WRITE OK", flush=True)
        print("TURSO: READ OK", flush=True)
        print("TURSO: CLEANUP OK", flush=True)
        return True

    except urllib.error.HTTPError as exc:
        print("TURSO: HTTP STATUS", exc.code, flush=True)
        detail = exc.read(4096).decode("utf-8", errors="replace")
        for secret in (token, url):
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
        print("TURSO: HTTP DETAIL", detail[:300], flush=True)
        return False

    except Exception as exc:
        print("TURSO: FAILED " + type(exc).__name__, flush=True)
        return False

if __name__ == "__main__":
    probe()
