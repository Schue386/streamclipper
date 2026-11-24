# StreamClipper OS v10 – Linux-Bereitstellung (Vorlage, nicht installiert)

Voraussetzungen: Linux-Server mit Python >=3.10, FFmpeg/FFprobe, ausreichendem
Speicherplatz, dem Original-StreamClipper (installiert in derselben virtuellen
Umgebung), einem eigenen unprivilegierten `streamclipper`-Benutzer sowie SSH-Zugriff.
Die Original-Engine und dieses OS-Paket sind derzeit **nicht im GitHub-Repository
zusammengeführt**. Ohne diese Integration ist ein Live-Test nicht freigegeben.

1. Projekt nach `/opt/streamclipper-os` kopieren und Python-Venv unter
   `/opt/streamclipper-os/.venv` einrichten. Schreibrechte für den Service-User
   auf `/var/lib/streamclipper-os` einrichten.
2. Twitch-API-Zugangsdaten, falls nötig, in `/etc/streamclipper-os.env`
   ablegen (`TWITCH_CLIENT_ID=...`, `TWITCH_ACCESS_TOKEN=...`), nur root-lesbar.
   Nicht ins Git-Repository oder in die ZIP schreiben.
3. Die zwei `.service`-Dateien nach `/etc/systemd/system/` kopieren;
   `systemctl daemon-reload`, dann beide Dienste aktivieren.
4. Dashboard ausschließlich per **SSH-Tunnel** öffnen, nicht per
   öffentlicher IP oder Portfreigabe:
   `ssh -N -L 8765:127.0.0.1:8765 user@server`
   und lokal im Browser `http://127.0.0.1:8765/` öffnen.
   Auf Android benötigt die SSH-App ausdrücklich Portweiterleitung.
   Ein normales SSH-Terminal allein macht das Dashboard nicht erreichbar.
5. Dienst-Logs: `journalctl -u streamclipper-os -f`.
   Der OS-Dienst startet standardmäßig **ohne `--execute`**:
   keine echten Clipping-Prozesse. `control.py start` allein genügt nicht.

**Sicherheit:** Niemals Port 8765 öffentlich freigeben. Das Dashboard hat
keine eigene Benutzeranmeldung und verlässt sich auf die SSH-Zugriffskontrolle.
Es gibt keinen Cloud-Zugang, keine automatische Installation und keinen
nachgewiesenen 24/7-Livebetrieb. `systemd` kann Dienste neu starten, aber
laufende Jobs werden nach ungeplantem Prozessabbruch absichtlich blockiert,
bis ein Betreiber die alten PIDs und Aufnahmen kontrolliert.

## v31: Vorabprüfung
Nach Installation der Engine in der Venv als Service-Benutzer ausführen:
`python preflight.py --state /var/lib/streamclipper-os`.
20 GiB freier Speicher sind eine vorläufige Mindestreserve, keine Kapazitätsgarantie.
Startkonfiguration zur Erprobung: 4 vCPU, 8 GiB RAM, 80 GiB SSD, ein Worker.
ASR/Rendering müssen auf dem Zielserver gemessen werden; erst danach skalieren.
Zugangsdaten werden nur auf Vorhandensein geprüft, nicht auf Gültigkeit.
Kein Live- oder Upload-Nachweis durch diese Prüfung. Keine öffentliche Portfreigabe.

## v32: Erstinstallation (ersetzt manuelle Schritte 1–3)
ZIP auf einen frischen Debian/Ubuntu-Server mit systemd übertragen und entpacken.
Im Projektordner `bash streamclipper_os/deploy/install.sh --plan` ausführen.
Erst nach bewusster Serverwahl: `sudo bash streamclipper_os/deploy/install.sh --install`.
Die Routine installiert auch ASR-Abhängigkeiten; Modell-Downloads erfolgen erst bei Nutzung.
Sie überschreibt keine bestehende Installation und startet keine Dienste. Bei einem
Teilabbruch muss der Administrator den Zustand prüfen; kein automatischer Rollback.
Nach erfolgreicher Vorabprüfung Zugangsdaten separat einrichten. Für die erste
Dashboard-Prüfung: `sudo systemctl start streamclipper-dashboard` und SSH-Tunnel
wie oben. Auf Android eine SSH-App mit lokaler Portweiterleitung verwenden.
Aufzeichnung und Upload bleiben ohne --execute gesperrt. Keine öffentliche Portfreigabe.
