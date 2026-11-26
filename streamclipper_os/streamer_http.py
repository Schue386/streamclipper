"""Authenticated streamer management requests."""
import os
import sqlite3
from contextlib import closing
from urllib.parse import parse_qs
from stream_monitor import connect
from streamer_manager import manage

def handle_streamers(handler, db_path):
    hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    origin = handler.headers.get("Origin")

    if not hostname or origin != "https://" + hostname:
        return handler.respond(403, b"Invalid origin")

    if handler.headers.get("Transfer-Encoding"):
        return handler.respond(400, b"Invalid request")

    try:
        size = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        return handler.respond(400, b"Invalid length")

    content_type = handler.headers.get("Content-Type", "")
    if not 1 <= size <= 2048:
        return handler.respond(400, b"Invalid size")

    if content_type.split(";")[0].strip() != "application/x-www-form-urlencoded":
        return handler.respond(415, b"Invalid content type")

    try:
        handler.connection.settimeout(15)
        fields = parse_qs(
            handler.rfile.read(size).decode("utf-8"),
            max_num_fields=3,
            strict_parsing=True
        )

        for key in ("platform", "channel", "action"):
            if len(fields.get(key, [])) != 1:
                raise ValueError("Invalid fields")

        with closing(connect(db_path)) as db:
            manage(
                db,
                fields["platform"][0],
                fields["channel"][0],
                fields["action"][0]
            )

    except (ValueError, UnicodeError):
        return handler.respond(400, b"Invalid streamer request")
    except sqlite3.Error:
        return handler.respond(503, b"Database unavailable")
    except OSError:
        return handler.respond(400, b"Request failed")

    handler.send_response(303)
    handler.send_header("Location", "/")
    handler.send_header("Content-Length", "0")
    handler.end_headers()
