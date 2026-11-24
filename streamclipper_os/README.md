# StreamClipper OS – Baustein 1: Streamüberwachung und Datenbank

Eigenständiger, ergänzender Prototyp für `Schue386/streamclipper`. Keine bestehenden Dateien werden ersetzt. Benötigt Python 3.10+; ausschließlich Standardbibliothek.

## Bereits umgesetzt
- SQLite-Datenbank mit `streamers`, `streams`, `clips`, `publications`, `metrics`.
- Twitch-Helix-Abfrage von konfigurierten Kanälen (max. 100 je Anfrage).
- Erkennung **neuer** Stream-IDs ohne doppelte Ereignisse.
- Testmodus mit lokalen JSON-Daten; keine Zugangsdaten erforderlich.
- Keine Downloads, Videoerstellung oder Veröffentlichungen in diesem Baustein.

## Lokal testen

```bash
python -m unittest discover -s tests -v
python stream_monitor.py add zarbex
python stream_monitor.py list
python stream_monitor.py check --demo-file example_live.json
python stream_monitor.py check --demo-file example_live.json
```

Der zweite Aufruf von `check` muss `new_streams: []` liefern.

## Echte Twitch-Abfrage

Eine Twitch-Entwickleranwendung mit Client-ID und gültigem OAuth-App-Access-Token ist nötig. Token sicher als Umgebungsvariable bereitstellen, **nicht in GitHub einchecken**.

```bash
export TWITCH_CLIENT_ID='...'
export TWITCH_ACCESS_TOKEN='...'
python stream_monitor.py check
python stream_monitor.py run --interval 120
```

`run` erfordert eine laufende Python-Umgebung. Google Colab bietet keinen garantierten 24/7-Betrieb. Tokens laufen ab und müssen durch eine spätere sichere Tokenverwaltung erneuert werden.

## Nächste Integration

Bei einem neuen `stream_id` kann ein separater, idempotenter Job an den bestehenden `streamclipper live`-Prozess übergeben werden. Dazu sind zunächst dessen CLI-Aufruf und Lebenszyklus zu prüfen. Der Monitor startet **absichtlich noch keine Aufzeichnung**. Später werden Export-Gate-Ergebnisse als `clips` und veröffentlichte IDs als `publications` eingetragen; `metrics` speichert zeitgestempelte Plattformstatistiken. Ein tatsächlicher Upload benötigt offizielle API-Berechtigungen und nachgewiesene Nutzungsrechte.

## Datenschutz und Sicherheit

Keine Zugangsdaten in Dateien oder Logs speichern. Für fremde Streams vor Clipping/Veröffentlichung erforderliche Rechte klären. Die Datenbank ist ein lokaler Prototyp, keine öffentliche Web-API.


## Erweiterung v2: Live-Clipping-Auftragswarteschlange

`clip_jobs.py` ergänzt eine idempotente Job-Warteschlange. Sie verwendet die
bereits registrierten Streams und erzeugt maximal einen Auftrag pro Stream-ID.

```bash
python stream_monitor.py add zarbex
python stream_monitor.py check --demo-file example_live.json
python clip_jobs.py enqueue
python clip_jobs.py run-next
```

`run-next` zeigt **standardmäßig nur eine Vorschau** des Aufrufs.
Erst `python clip_jobs.py run-next --execute` startet den bereits installierten
`streamclipper live`-Befehl tatsächlich. Dies setzt eine laufende Python-Umgebung,
installiertes StreamClipper und FFmpeg sowie berechtigten Zugriff auf die Quelle voraus.

**Grenzen:** Noch keine kontinuierliche Jobsteuerung, automatische Neustarts,
Token-Erneuerung, Synchronisierung der gerenderten MP4-Dateien mit `clips`,
Upload-Schnittstellen oder Cloud-Deployment. Ein beendeter Live-Prozess wird als
`completed` markiert, ohne bereits die Qualität oder Anzahl seiner Clips zu prüfen.
Die bestehende StreamClipper-Codebasis wird nicht überschrieben.


## Erweiterung v3: Clip-Katalog und sichere Upload-Warteschlange

`clip_catalog.py` erfasst MP4-Dateien anhand ihres SHA-256-Inhalts, prüft mit
`ffprobe` Länge (61–90 Sekunden) und vertikales Format und speichert den Status
in SQLite. Eine fehlende Prüfung ist **kein** Qualitätsnachweis. Eine erfolgreiche
technische Prüfung beweist **keine** inhaltliche Clipqualität oder Rechtefreigabe.

Beispiel, nachdem ein bekannter Stream und eine echte MP4 vorliegen:

```bash
python clip_catalog.py register --stream-id STREAM_ID --file /pfad/clip.mp4
python clip_catalog.py approve-rights --clip-id CLIP_ID --note "Schriftliche Erlaubnis des Streamers liegt vor"
python clip_catalog.py queue --clip-id CLIP_ID --platform youtube
python clip_catalog.py summary
```

Upload-Queue `ready` bedeutet ausschließlich **intern vorbereitet**. Es gibt
noch **keinen** API-Uploader und **keine** automatische Veröffentlichung.
`tests/test_clip_catalog.py` verwendet künstliche Videodaten und injizierte
Metadaten, um die Entscheidungslogik zu prüfen; es ersetzt keinen FFmpeg-Test
mit einem echten gerenderten Video. Die tatsächliche StreamClipper-Engine ist
noch nicht mit der MP4-Erfassung verbunden. Vor produktivem Einsatz müssen
unter anderem der 59,5-Sekunden-Render-Limit, Berechtigungsnachweise,
mehrfache Veröffentlichungsversuche und ein echter API-Upload getestet werden.


## v4: Import von gerenderten MP4-Clips

`python clip_importer.py` scannt die Ausgabeordner **abgeschlossener**
Clipping-Jobs und registriert MP4-Dateien automatisch im vorhandenen Katalog.
Mit `python clip_importer.py --job-id 1` kann gezielt ein Job verarbeitet werden.
Die echte Videoprüfung erfolgt ausschließlich über ffprobe; bei fehlender
Prüfung bleibt der Clip `unverified` und kann nicht für Uploads freigegeben
werden. Mehrfaches Scannen erzeugt keine doppelten Clips.

Wichtig: Dies ist ein **aufrufbarer Importer**, noch kein dauerhaft laufender
Dienst. Es gab keinen Live-Stream-Test und keinen Plattform-Upload. Die
Clip-Länge des ursprünglichen StreamClipper ist noch nicht angepasst.


## v5: Zentraler Autopilot (lokaler Prototyp)

`autopilot.py` führt Twitch-Abfrage, ereignisbasierte Auftragserstellung,
optionale Clipping-Ausführung und Import abgeschlossener Aufträge in einem
Zyklus zusammen. **Standardmäßig nur Vorschau, keine Aufzeichnung.**

```bash
python stream_monitor.py add zarbex
python autopilot.py --demo-file example_live.json
python autopilot.py --demo-file example_live.json
python autopilot.py --loop --interval 120   # echte Twitch-Zugangsdaten nötig
```

`--execute` ist eine ausdrückliche Freigabe zum Start des vorhandenen
`streamclipper live`-CLI. Der Prozess läuft synchron bis zu zwei Stunden
(über `--timeout` änderbar); erst danach wird erneut abgefragt. **Das ist
noch kein parallel laufender 24/7-Autopilot.** Währenddessen neu startende
Streams können verpasst werden. Keine automatischen Veröffentlichungen.

**Einschränkungen:** Nur neue Twitch-Stream-IDs aus dem jeweiligen Poll werden
neu eingereiht. Bereits vor v5 entdeckte Streams werden nicht nachträglich
eingereiht. Abgebrochene Prozesse können Jobs im Zustand `running` lassen;
ein Recovery-/Lease-Mechanismus ist noch offen. Import abgeschlossener Jobs
wird erneut geprüft, bleibt durch Inhalts-Hash jedoch duplikatfrei.
Die Qualitätsgrenze 61–90 s im Katalog weicht weiterhin von der bestehenden
59,5-s-Rendergrenze der Original-Engine ab. Kein echter Live-End-to-End-Test.


## v6: Prozess-Supervisor (lokaler Prototyp)

`supervisor.py` startet bis zu `--workers` Live-Aufzeichnungen parallel und
überwacht ihren Prozessstatus. Er läuft **nur solange der Host aktiv ist**.

```bash
python supervisor.py                    # sichere Vorschau, keine Aufnahme
python supervisor.py --loop --execute --workers 2  # echte Aufnahmen
python supervisor.py --recover          # nur nach Prüfung alter Host-Prozesse
```

**Wichtig:** `supervisor.py` verarbeitet bereits angelegte Jobs. Neue Streams
werden weiterhin durch `autopilot.py` erkannt und eingereiht. Eine vollständig
integrierte 24/7-Startsteuerung ist noch nicht vorhanden. `--recover` setzt
unterbrochene Jobs auf *failed*, startet sie nicht automatisch erneut. Vor
`--recover` müssen eventuell noch laufende Prozesse am Host überprüft werden.
Der Supervisor ist für **einen Host und einen Supervisor-Prozess** ausgelegt,
nicht für verteilte Worker. Es gibt noch keinen sicheren verteilten Lease,
keine automatische Wiederaufnahme und keinen Cloud-Betrieb.

Tests: `python -m unittest discover -s tests -v`


## v7: lokale Start-/Stopp-Steuerung

```bash
python control.py status
python control.py start
python control.py stop
python supervisor.py --execute --loop
```

**Sicherheit:** Standardzustand ist AUS. `start` erlaubt nur dem bereits mit
`--execute` laufenden Supervisor, neue Clipping-Prozesse zu starten. `stop`
verhindert neue Starts, **beendet aber laufende Aufzeichnungen nicht**.
Ein Supervisor-Neustart erfordert Prüfung eventuell verbliebener Prozesse.
Keine HTTP-API, kein Android-Dashboard und keine Authentifizierung vorhanden.
Die Steuerung ist ausschließlich lokal auf dem ausführenden Rechner nutzbar.
Keine Veröffentlichung wird ausgelöst.


## v8: Lokales, mobilfreundliches Dashboard

```bash
python dashboard.py
```

Auf **demselben Gerät** im Browser `http://127.0.0.1:8765/` öffnen.
Das Dashboard zeigt Kennzahlen, Streamer und letzte Aufträge und ermöglicht
die Freigabe/Sperre weiterer Aufzeichnungen. Der Supervisor muss separat
mit `--execute` laufen, sonst startet auch nach Freigabe keine Aufnahme.

**Sicherheit und Grenzen:** Der HTTP-Server bindet ausschließlich an
`127.0.0.1`, nicht an öffentliche IP-Adressen. Browser-Steuerbefehle
werden durch Origin-Prüfung gegen fremde Websites abgesichert. Das ist
**keine** öffentliche Weboberfläche und keine Android-Fernsteuerung.
Für Fernzugriff braucht es später Authentifizierung, HTTPS und eine
geschützte Serveranbindung. Keine automatischen Uploads.


## v9: Ein zentraler Host-Dienst

`service.py` vereint die bisher getrennte Twitch-Abfrage und den parallelen
Supervisor in einem einzigen **lokalen Prozess**. Er prüft Streams in
konfigurierbaren Intervallen, reiht neue Aufträge ein und kontrolliert laufende
Clipping-Prozesse auch dann, wenn die Twitch-Abfrage fehlschlägt.

```bash
python -m unittest discover -s tests -v
python stream_monitor.py add zarbex
python control.py start
python service.py --demo-file example_live.json --once
# Echte Aufzeichnungen erst auf einem geeigneten Host, nach Rechteprüfung:
python service.py --execute --workers 2
# Separat auf demselben Host, nur lokal erreichbar:
python dashboard.py
```

**Sicherheitsregeln:** Der Dienst startet standardmäßig keine Aufzeichnung.
Dafür müssen sowohl `control.py start` als auch `service.py --execute` gesetzt
sein. Es gibt **keinen Upload** und keine öffentlich erreichbare Weboberfläche.
Ein gestoppter Autopilot verhindert neue Aufträge und Starts, beendet laufende
Aufzeichnungen aber nicht sofort. Bei einem unerwarteten Neustart mit `running`
Jobs verweigert der Dienst den Start, bis die Prozesse manuell geprüft sind.

**Grenzen:** Ein laufender Linux-Host ist erforderlich; keine automatische
Serverbereitstellung, kein systemd/Docker-Setup, keine sicheren Remote-Zugänge,
kein 24/7-Praxistest. Die vorhandene Engine-Längenbegrenzung und die echte
End-to-End-Integration sind noch offen. Das Dashboard ist weiterhin loopback-only.


## v10: Linux-Servervorlagen und SSH-geschützter Zugriff

In `deploy/` liegen zwei systemd-Service-Vorlagen und eine ausführliche
Bereitstellungsanleitung. Der Clipping-Dienst bleibt im Standardmodus
ohne `--execute`; das Dashboard bindet weiterhin ausschließlich an
`127.0.0.1`. Android-Fernzugriff erfolgt **nur über einen eingerichteten
SSH-Tunnel**. Ein öffentlich erreichbarer Port 8765 ist nicht zulässig.

SQLite verwendet nun ein 30-Sekunden-Busy-Timeout gegen kurzzeitige
Schreibkonflikte. Die bisherige Verbindungsverwaltung muss vor dem
Dauerbetrieb weiter bereinigt werden.

Die Vorlagen wurden nicht auf einem echten Server installiert. Ein
SSH-Tunnel, der Original-StreamClipper-Befehl und der vollständige
Livebetrieb sind noch nicht praktisch verifiziert.

## v11 – integrierte lokale Quellversion
Die Original-Engine 5.3.0 liegt jetzt im selben Projekt. Installation auf Linux aus dem Projektstamm: `python -m venv .venv` und `.venv/bin/pip install -e '.[asr]'`. OS-Befehle aus `streamclipper_os/` mit der Python-Umgebung des Projektstamms ausführen. Die systemd-Vorlagen verwenden diesen Projektaufbau unter `/opt/streamclipper-os`.
Neu: `--long-clips` setzt die Live-Kandidatendauer auf 61–90 s (Ziel 75 s) und die Timeline-Obergrenze auf 90 s. Schnitte können weiterhin kürzer ausfallen; FFprobe bleibt maßgeblich. Standardverhalten ohne Flag unverändert. Server-Aufträge verwenden `--no-draft` und `--long-clips`. Import berücksichtigt nur MP4-Dateien direkt im Ausgabeordner, nicht interne Fenster. SQLite-Verbindungen werden an den Aufrufgrenzen geschlossen. Test mit synthetischem 70-s-MP4 prüft den echten FFprobe-Import, nicht die KI-Clipping-Engine. Kein Live- oder Upload-Nachweis. GitHub unverändert.

## v12: VOD-Import
Im Ordner streamclipper_os: `python vod_import.py https://youtu.be/OABmyAGqMCQ` zeigt den geplanten Import (15 Minuten, 720p). Nur mit `--execute --authorized` und vorhandener Nutzungsberechtigung wird der Import gestartet. Installierte Engine und FFmpeg erforderlich. Noch keine Dashboard- oder Datenbankanbindung für VODs; Längenvorgaben sind Auswahlparameter, keine Garantie.

## v13 – Long context
Long-mode story windows target 75 seconds, preserve original context when pause removal would shorten below minimum, and reject out-of-range timelines. VOD and live share the duration guard. No actual YouTube retrieval or full rendering validated in this release.

## v14
Automatic split/uncut-stack layouts fall back to full for portrait inputs and when face detection is unavailable. Explicit camera crops remain supported. 42 OS tests passed; no live validation.

## v17 YouTube queue adapter
Run from streamclipper_os with the original engine installed: `python youtube_worker.py --db streamclipper_os.sqlite3` previews one eligible job. Add `--execute` to attempt a private upload using the existing saved YouTube OAuth token. Rights and technical approval are required. An uncertain attempt is never automatically retried; reconcile it with YouTube manually. This is a standalone one-job worker, not yet scheduled by service.py. Metadata is template-based. Real API uploads and statistics retrieval remain untested. The youtube_attempts table records the video ID and attempt state.

## v18 – YouTube im Dienstzyklus
`service.py --youtube-upload` erlaubt private Uploads bereits freigegebener und eingereihter Clips, nur bei eingeschaltetem Autopiloten. Standardmäßig aus. Erfolgreiche Uploads werden auch in `publications` registriert, als Grundlage für spätere Metriken. Uploads laufen synchron und können den Supervisor verzögern; vor 24/7-Freigabe ist ein separater Worker erforderlich. Kein echter API-Upload getestet. Keine automatische Rechtefreigabe oder Statistikabfrage.

## v19 – Independent YouTube worker
Run `python youtube_daemon.py --db PATH --once` for a disabled smoke test.
The separate systemd template is disabled for real uploads by default; add
`--execute` only after configuration and approval. The coordinator no longer
accepts `--youtube-upload`. Stopping the autopilot prevents new uploads at the
next check, not uploads already underway. Metrics collection remains pending.

## v20 – YouTube-Statistiken
`python youtube_metrics.py --db PATH` zeigt gespeicherte Kennzahlen ohne Netzwerkzugriff.
`python youtube_metrics.py --db PATH --execute --interval 3600` ruft Statistiken stündlich ab; `--once` beendet nach einer Abfrage. Erfordert ein gültiges `YOUTUBE_READ_ACCESS_TOKEN` mit YouTube-Leseberechtigung. Keine automatische Token-Erneuerung; abgelaufene Tokens müssen ersetzt werden. Keine Zugangsdaten ins Repository schreiben. Der Dienst lädt nichts hoch. Fehlende Werte bleiben NULL; Summen umfassen nur vorhandene Werte, die Interaktionsrate nur Videos mit vollständigen Kennzahlen und positiven Gesamtaufrufen. Historische Stichproben bleiben gespeichert. Dashboard zeigt jeweils die letzte Stichprobe pro Veröffentlichung. Keine echte API-Abfrage getestet; 76 OS-Tests erfolgreich. Keine automatische Strategieoptimierung implementiert.

## v21 – Erfolgsanalyse
`python performance.py --db <Pfad>` liefert eine lesende Auswertung. Nur als `published` registrierte YouTube-Clips mit Messalter 24–168 Stunden und höchstens 48 Stunden alten Messungen werden berücksichtigt. Aufrufe pro Tag sind eine einfache Altersnormalisierung, keine Prognose. Der Median pro Streamer reduziert Ausreißereinflüsse; ab fünf Clips wird eine manuelle Prioritätsprüfung empfohlen. Fehlende Interaktionsdaten bleiben unbekannt. Das Dashboard zeigt die Rangfolge; keine automatische Änderung von Aufträgen oder Streamern. Private Testuploads werden ausgeschlossen. Öffentliche Freigabe und echte Statistikdaten sind noch nicht praktisch validiert.

## v22: Adaptive Auftragspriorisierung
`adaptive.py` nutzt die altersnormalisierten Median-Aufrufe aus `performance.py` ab fünf vergleichbaren Clips. Jeder dritte Auftragsstart nach kumulierter Versuchszahl sowie Wartezeiten ab 15 Minuten bevorzugen FIFO. Nur aktivierte, als live registrierte Kanäle sind auswählbar. Keine Änderung an Veröffentlichungs- oder Qualitätsfreigaben. Die Logik wurde mit simulierten Daten getestet; tatsächliche Erfolgssteigerungen sind nicht nachgewiesen. 80 OS-Testausführungen bestanden.

## v23 TikTok inbox adapter (experimental)
`tiktok_worker.run_next(db)` previews a ready clip. Explicit approval of its queue ID and a user-authorized video.upload token are required to call the inbox upload adapter. No direct public posting, automatic daemon, OAuth UI or status polling yet. Inbox delivery is not publication; the creator must complete posting in TikTok. Uncertain attempts are not retried automatically. Single-chunk files up to 64 MiB only. API contract and live authorization still require verification. Never store tokens in the repository.

## v24: TikTok-Status
`TIKTOK_ACCESS_TOKEN` serverseitig setzen und `python tiktok_status.py --db /pfad/state.sqlite3` ausführen. Nur Statusabfrage, kein Upload. Dashboard zeigt die letzten 30 Übertragungszustände. PUBLISH_COMPLETE ist ein API-Status, kein Nachweis öffentlicher Sichtbarkeit. OAuth-Erneuerung und automatische Zeitplanung noch offen. Keine echten API-Aufrufe getestet.

## v25 OAuth refresh (Linux)
Optional for YouTube metrics and TikTok status CLI only. Set YOUTUBE_READ_TOKEN_FILE or TIKTOK_TOKEN_FILE and the corresponding _CLIENT_ID and _CLIENT_SECRET variables. The credential file must be in a private directory (0700), file mode 0600, with JSON keys platform, access_token, refresh_token, expires_at (Unix seconds). Obtain initial authorization through the provider's official consent flow. No credentials are bundled. Refresh is serialized and rotated refresh tokens are saved atomically. Without a token file the existing environment-token behavior remains. Upload authorization and initial OAuth consent are not changed. Real provider requests are untested.

## v26 – TikTok-Metriken
`python tiktok_metrics.py --db PATH` zeigt lokale Kennzahlen; `--execute` fragt die Video Query API mit einem autorisierten Token (video.list) ab. Nur explizit als veröffentlicht registrierte Videos mit echter Video-ID werden abgefragt. Publish-IDs aus Inbox-Uploads sind keine Video-IDs. Die Zuordnung nach Veröffentlichung ist noch nicht automatisiert. Rankings werden je Zielplattform getrennt berechnet; die adaptive Steuerung bleibt YouTube-basiert. Kein echter API-Test und kein neuer Dauerbetrieb.

### v27
Read-only platform-specific length and UTC publication-time recommendations. At least five eligible clips in each of two buckets and >20% median advantage required. Observational, not causal; no automatic scheduling changes. Game and edit-style metadata not yet available.

## v28 Kick discovery
Register with `python stream_monitor.py add CHANNEL --platform kick`. Set KICK_ACCESS_TOKEN in the service environment. Discovery is separate from Twitch; Kick recording is deliberately not enabled until real API and media tests pass. API schema tested with fixtures only.

### v29 YouTube live discovery (experimental)
Register a UC channel ID using `python stream_monitor.py add UC... --platform youtube`.
Set `YOUTUBE_DATA_API_KEY` in the service environment. The service searches at most once per channel per hour per process; restarts reset this throttle. Search quota is shared across channels; start with one channel and monitor quota. Search results are not proof of recording availability. No YouTube or Kick recording jobs are enabled by this change. Live detection is tested with mocked responses only. An unsuccessful search does not clear previous live records; last_seen_at must be considered when interpreting status. The standalone Twitch check command remains Twitch-only.

### v30: Offline integration validation
Run `PYTHONPATH=../src:. python -m unittest discover -s tests -q` inside streamclipper_os. The integration test uses a synthetic 75-second MP4, simulated stream discovery and upload/statistics responses, and a temporary database. Recording and highlight selection are not exercised by this test. No external publishing occurs.

## v34
Oracle Free Tier deployment profile: see deploy/oracle-free.md. Single worker, CPU/memory limits, launch disk-space gate. ARM64 and real throughput not yet verified. No automatic deletion.

## v35 Render pilot
Deploy repository as Docker Web Service, Free plan, health path `/health`. Set DASHBOARD_PASSWORD to a random secret of at least 24 characters (Blueprint generates one). Use HTTPS, username admin. UptimeRobot may monitor the public `/health` endpoint without credentials, subject to provider terms. `/resources` requires authentication and reports cgroup memory when available. This pilot is read-only: recording, upload and control are intentionally disabled. SQLite in /tmp is disposable; no real credentials or irreplaceable data should be stored. D1 integration, actual 512 MB video benchmark and remote deployment remain pending. Docker image has not been built in the local test environment. Existing Linux installation remains available.

## v36 offline resource pilot
Run `python streamclipper_os/render_benchmark.py VIDEO.mp4` on Linux. The 512 MiB virtual address space ceiling is not a cgroup RSS limit. See benchmark-v36.json for local results. ASR and Render deployment remain untested. The read-only Render service is unchanged.

### v37: Render ASR pilot
Optional Docker build argument `ENABLE_ASR=1` installs faster-whisper. Default read-only service remains unchanged. For an isolated test with an explicitly provisioned local converted multilingual Whisper Tiny model: `python streamclipper_os/asr_benchmark.py input.mp4 /path/to/tiny --seconds 30`. CPU INT8, one thread, no model download, no upload. Measure total container memory separately; process RSS does not establish compliance with 512 MB. No real inference validated in development environment (dependency/model absent). Do not enable autonomous recording based on this test alone.

## v38 – Render deployment pilot
Deploy this repository as a Docker Web Service (Free), using the root Dockerfile and /health. Set DASHBOARD_PASSWORD (24+ random characters). Open the HTTPS service URL as admin. The dashboard offers a synthetic 75-second FFmpeg test; /pilot reports its result and /resources reports current cgroup memory. The browser start requires the Render external hostname and same-origin HTTPS. No external media, recording, ASR or publishing is performed. The result is ephemeral; save it before restart. Default Docker build excludes ASR. Do not add social account credentials yet. UptimeRobot may monitor /health subject to Render terms. No cloud deployment or container memory limit verified locally.
