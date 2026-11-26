"""Persistent Turso data access."""
import os
import math
import threading

_write_lock = threading.RLock()
import json
import urllib.request
import urllib.parse


class StoreError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def execute(statements):
    url = os.getenv("TURSO_DATABASE_URL", "").strip()
    token = os.getenv("TURSO_AUTH_TOKEN", "").strip()

    url = url.replace("libsql://", "https://", 1)
    parsed = urllib.parse.urlsplit(url)

    if (
        not token
        or parsed.scheme != "https"
        or parsed.netloc !=
        "streamclipper-schue386.aws-eu-central-1.turso.io"
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise StoreError("Invalid Turso configuration")

    requests = []

    for sql, params in statements:
        args = []

        for value in params:
            if value is None:
                args.append({"type": "null"})
            elif isinstance(value, int):
                args.append({
                    "type": "integer",
                    "value": str(value)
                })
            elif isinstance(value, float):
                if not math.isfinite(value):
                    raise StoreError("Invalid float parameter")
                args.append({
                    "type": "float",
                    "value": value
                })
            elif isinstance(value, str):
                args.append({
                    "type": "text",
                    "value": value
                })
            else:
                raise StoreError("Unsupported parameter")

        requests.append({
            "type": "execute",
            "stmt": {
                "sql": sql,
                "args": args,
                "named_args": [],
                "want_rows": True
            }
        })

    requests.append({"type": "close"})

    req = urllib.request.Request(
        url.rstrip("/") + "/v2/pipeline",
        data=json.dumps({
            "baton": None,
            "requests": requests
        }).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(req, timeout=15) as response:
            payload = json.load(response)

        results = payload["results"]

        if len(results) != len(requests):
            raise StoreError("Incomplete response")

        if any(r.get("type") != "ok" for r in results):
            raise StoreError("SQL execution failed")

        return [
            r["response"]["result"]
            for r in results[:-1]
        ]

    except StoreError:
        raise
    except Exception as exc:
        raise StoreError(type(exc).__name__) from None


def prepare():
    execute([
        ("""CREATE TABLE IF NOT EXISTS streamers (
            platform TEXT NOT NULL,
            channel TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
                CHECK(enabled IN (0,1)),
            PRIMARY KEY(platform, channel)
        )""", ())
    ])


def load_streamers():
    result = execute([
        ("SELECT platform, channel, enabled "
         "FROM streamers ORDER BY platform, channel", ())
    ])[0]

    return [
        (
            row[0]["value"],
            row[1]["value"],
            int(row[2]["value"])
        )
        for row in result["rows"]
    ]


def save_streamer(platform, channel, enabled):
    execute([
        ("""INSERT INTO streamers
            (platform, channel, enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(platform, channel)
            DO UPDATE SET enabled=excluded.enabled""",
         (platform, channel, int(enabled)))
    ])


def remove_streamer(platform, channel):
    execute([
        ("DELETE FROM streamers "
         "WHERE platform=? AND channel=?",
         (platform, channel))
    ])


def restore_streamers(db):
    from streamer_status import prepare as prepare_status

    prepare()
    rows = load_streamers()

    for platform, channel, enabled in rows:
        if (
            platform not in ("twitch", "kick", "youtube")
            or not channel
            or enabled not in (0, 1)
        ):
            raise StoreError("Invalid stored streamer")

    prepare_status(db)

    remote = {(p, c): e for p, c, e in rows}

    with db:
        db.execute("BEGIN IMMEDIATE")

        local = {
            (p, c): e for p, c, e in db.execute(
                "SELECT platform, channel, enabled FROM streamers"
            )
        }

        for p, c in local.keys() - remote.keys():
            db.execute(
                "DELETE FROM streamers WHERE platform=? AND channel=?",
                (p, c)
            )

        for (p, c), enabled in remote.items():
            if local.get((p, c)) != enabled:
                db.execute(
                    "INSERT INTO streamers VALUES (?, ?, ?) "
                    "ON CONFLICT(platform, channel) "
                    "DO UPDATE SET enabled=excluded.enabled",
                    (p, c, enabled)
                )
                db.execute(
                    "DELETE FROM streamer_status "
                    "WHERE platform=? AND channel=?",
                    (p, c)
                )

    print("TURSO: STREAMERS RESTORED", len(rows), flush=True)
    return len(rows)


def initialize_streamers(db):
    return restore_streamers(db)


def _manage_persistent(db, platform, channel, action):
    import sqlite3
    from contextlib import closing
    from stream_monitor import connect
    from streamer_manager import manage

    platform = platform.strip().lower()
    channel = channel.strip()

    with closing(connect(":memory:")) as staging:
        staging.executemany(
            "INSERT INTO streamers "
            "(platform, channel, enabled) VALUES (?, ?, ?)",
            load_streamers()
        )
        staging.commit()

        before = {
            (p, c): e for p, c, e in staging.execute(
                "SELECT platform, channel, enabled FROM streamers"
            )
        }

        manage(staging, platform, channel, action)

        after = {
            (p, c): e for p, c, e in staging.execute(
                "SELECT platform, channel, enabled FROM streamers"
            )
        }

        for p, c in before.keys() - after.keys():
            remove_streamer(p, c)

        for (p, c), enabled in after.items():
            if before.get((p, c)) != enabled:
                save_streamer(p, c, enabled)

    try:
        restore_streamers(db)
    except sqlite3.Error:
        raise StoreError("Local synchronization failed") from None

    print("TURSO: STREAMER CHANGE SAVED", flush=True)


def manage_persistent(db, platform, channel, action):
    with _write_lock:
        return _manage_persistent(
            db, platform, channel, action
        )
