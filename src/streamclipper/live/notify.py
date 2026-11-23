"""Live clip alerts for Discord, Telegram and generic JSON webhooks (standard library only, never fatal)."""

import json
import os
import urllib.error
import urllib.request

from streamclipper.audio import _mmss


def _post(url: str, payload: dict, timeout: float = 10.0) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "StreamClipper/5.3"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout):
        return


def build_alert_request(webhook: str, entry: dict, channel: str = "", where: str = "") -> tuple[str, dict]:
    """
    Build (url, json_payload) for Discord, Telegram or a generic webhook.
    Telegram shorthand: 'telegram:<BOT_TOKEN>:<CHAT_ID>' (or full api.telegram.org URL with TELEGRAM_CHAT_ID env).
    """
    hook = str(entry.get("hook_headline") or "")
    title = str(entry.get("title") or hook or f"Clip #{entry.get('rank', 1)}")
    score = entry.get("score", "-")
    cat = str(entry.get("category") or "clip")
    peak = _mmss(float(entry.get("peak") or 0.0))
    rank = entry.get("rank", 1)
    summary = f"CLIP #{rank:02d} ({cat} · score {score}) at {peak}"
    if channel:
        summary = f"[{channel}] {summary}"

    raw = str(webhook or "").strip()
    if raw.startswith("telegram:"):
        parts = raw.split(":", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            token, chat_id = parts[1], parts[2]
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            lines = [summary, title]
            if hook and hook != title:
                lines.append(f"Hook: {hook}")
            if where:
                lines.append(f"File: {where}")
            return url, {"chat_id": chat_id, "text": "\n".join(lines)}

    low = raw.lower()
    if "discord.com/api/webhooks/" in low or "discordapp.com/api/webhooks/" in low:
        fields = [
            {"name": "Score", "value": str(score), "inline": True},
            {"name": "Category", "value": cat, "inline": True},
            {"name": "Stream Time", "value": peak, "inline": True},
        ]
        if hook:
            fields.append({"name": "Hook", "value": hook[:256], "inline": False})
        if where:
            fields.append({"name": "Output", "value": f"`{where}`"[:256], "inline": False})
        embed_title = f"{f'[{channel}] ' if channel else ''}{title}"[:256]
        return raw, {
            "username": "StreamClipper",
            "embeds": [{"title": embed_title, "color": 0x8B5CF6, "fields": fields}],
        }

    if "api.telegram.org/bot" in low:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        lines = [summary, title] + ([f"File: {where}"] if where else [])
        return raw, {"chat_id": chat_id, "text": "\n".join(lines)}

    return raw, {
        "event": "clip_ready",
        "channel": channel,
        "rank": rank,
        "score": score,
        "category": cat,
        "peak_s": entry.get("peak"),
        "title": title,
        "hook": hook,
        "output": where,
    }


def send_clip_alert(webhook: str, entry: dict, channel: str = "", where: str = "", post_fn=None) -> bool:
    """Send a clip-ready notification. Never raises (never prints the webhook URL/token)."""
    if not webhook:
        return False
    try:
        url, payload = build_alert_request(webhook, entry, channel=channel, where=where)
        (post_fn or _post)(url, payload)
        return True
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
        print(f"Live: webhook alert failed ({type(e).__name__})")
        return False
