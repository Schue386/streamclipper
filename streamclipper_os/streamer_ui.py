"""Streamer management dashboard components."""
from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo

def local_time(value):
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return "-"
        return dt.astimezone(
            ZoneInfo("Europe/Berlin")
        ).strftime("%d.%m.%Y %H:%M:%S")
    except (ValueError, TypeError, KeyError):
        return "-"

def render_streamers(items):
    def e(value):
        return escape(str(value or ""), quote=True)

    form = """
    <form method="post" action="/streamers">
      <select name="platform">
        <option value="twitch">Twitch</option>
        <option value="kick">Kick</option>
        <option value="youtube">YouTube</option>
      </select>
      <input name="channel" placeholder="Kanalname oder ID"
             maxlength="200" required>
      <button name="action" value="add">Hinzufügen</button>
    </form>
    """

    states = {
        "live": ("LIVE", "#22c55e"),
        "offline": ("OFFLINE", "#94a3b8"),
        "pending": ("PRÜFUNG AUSSTEHEND", "#eab308"),
        "error": ("API-FEHLER", "#ef4444"),
        "stale": ("VERALTET", "#f97316"),
        "disabled": ("DEAKTIVIERT", "#94a3b8"),
        "last_live": ("ZULETZT LIVE", "#94a3b8"),
        "last_offline": ("ZULETZT OFFLINE", "#94a3b8"),
        "last_unknown": ("NOCH NICHT ERKANNT", "#eab308")
    }

    rows = []
    for item in items:
        platform = e(item["platform"])
        channel = e(item["channel"])
        active = item["enabled"]

        label, color = states.get(
            item["status"], ("UNBEKANNT", "#94a3b8")
        )

        action = "disable" if active else "enable"
        button = "Deaktivieren" if active else "Aktivieren"

        rows.append(f"""
        <article>
          <small>{platform.upper()}</small>
          <h3>{channel}</h3>
          <p style="color:{color};font-weight:bold">
            ● {label}
          </p>
          <p>{e(item.get("title"))}</p>
          <small>Letzter Streamstart:
          {e(local_time(item.get("started_at")))}</small><br>
          <small>Letzte Live-Erkennung:
          {e(local_time(item.get("last_seen_at")))}</small><br>
          <small>Letzte Statusprüfung:
          {e(local_time(item.get("checked_at")))}</small><br>
          <small>{e(item.get("error"))}</small>
          <form method="post" action="/streamers">
            <input type="hidden" name="platform" value="{platform}">
            <input type="hidden" name="channel" value="{channel}">
            <button name="action" value="{action}">{button}</button>
            <button name="action" value="remove">Entfernen</button>
          </form>
        </article>
        """)

    return form + (
        "".join(rows) or "<p>Keine Streamer eingetragen</p>"
    )
