"""Automatic Twitch application authentication."""
import os
import json
import time
import urllib.parse
import urllib.request
import urllib.error
import threading

_lock = threading.Lock()
_token = ""
_expires = 0.0

def get_token(force=False):
    global _token, _expires
    with _lock:
        client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
        secret = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
        if not client_id or not secret:
            raise ValueError("Twitch credentials missing")

        if not force and _token and time.monotonic() < _expires:
            return _token

        data = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": secret,
            "grant_type": "client_credentials"
        }).encode()

        request = urllib.request.Request(
            "https://id.twitch.tv/oauth2/token",
            data=data,
            method="POST"
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                message = str(json.loads(
                    exc.read()
                ).get("message","Unknown error"))

                for value in (client_id,secret):
                    if value:
                        message = message.replace(
                            value,"[REDACTED]"
                        )

            except Exception:
                message = "No details available"

            print(
                "TWITCH AUTH ERROR:",
                exc.code,message[:200],
                flush=True
            )
            raise

        token = payload["access_token"]
        lifetime = int(payload["expires_in"])

        if not token or lifetime <= 0:
            raise ValueError("Invalid Twitch token response")

        _token = token
        _expires = time.monotonic() + max(0, lifetime - 120)

        print("Twitch authentication successful", flush=True)
        return _token
