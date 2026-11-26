"""Streamer management dashboard components."""
from html import escape

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
      <button name="action" value="add">Hinzufuegen</button>
    </form>
    """

    rows = []
    for item in items:
        platform = e(item["platform"])
        channel = e(item["channel"])
        active = item["enabled"]
        state = item["status"]
        label = "Deaktiviert" if not active else {
            "live": "Zuletzt live",
            "offline": "Zuletzt offline",
            "unknown": "Noch nicht erkannt"
        }.get(state, "Unbekannt")

        action = "disable" if active else "enable"
        button = "Deaktivieren" if active else "Aktivieren"

        rows.append(f"""
        <article>
          <small>{platform}</small>
          <h3>{channel}</h3>
          <p>{label}</p>
          <p>{e(item.get("title"))}</p>
          <small>Start: {e(item.get("started_at"))}</small>
          <small>Letzte Live-Erkennung:
          {e(item.get("last_seen_at"))}</small>
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
