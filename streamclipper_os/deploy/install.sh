#!/usr/bin/env bash
set -euo pipefail
# Fresh Debian/Ubuntu installation only. Never starts or enables services.
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ ${1:-} == --plan ]]; then
  printf '%s\n' 'Install FFmpeg, Python venv, engine and ASR dependencies; create dedicated user and state directory; install preview-only units. No service start.'
  exit 0
fi
[[ $# == 1 && $1 == --install ]] || { echo 'Use --plan or --install'; exit 2; }
[[ $EUID == 0 ]] || { echo 'Run as root via sudo'; exit 1; }
[[ -f /etc/debian_version && -d /run/systemd/system ]] || { echo 'Debian/Ubuntu with systemd required'; exit 1; }
for p in /opt/streamclipper-os /var/lib/streamclipper-os /etc/streamclipper-os.env /etc/systemd/system/streamclipper-{os,dashboard,youtube}.service; do
  [[ ! -e $p && ! -L $p ]] || { echo "Existing installation: $p; refusing overwrite"; exit 1; }
done
[[ -f "$root/pyproject.toml" ]] || exit 1
apt-get update
apt-get install -y python3 python3-venv ffmpeg ca-certificates
python3 -c 'import sys; assert sys.version_info >= (3,10)'
getent passwd streamclipper >/dev/null && { echo 'Existing service account; refusing reuse'; exit 1; }
useradd --system --user-group --home-dir /var/lib/streamclipper-os --shell /usr/sbin/nologin streamclipper
install -d -m 0755 /opt/streamclipper-os
cp -r "$root/src" "$root/streamclipper_os" "$root/pyproject.toml" "$root/README.md" "$root/LICENSE" /opt/streamclipper-os/
chown -R root:root /opt/streamclipper-os
chmod -R go-w /opt/streamclipper-os
python3 -m venv /opt/streamclipper-os/.venv
/opt/streamclipper-os/.venv/bin/pip install '/opt/streamclipper-os[asr]'
install -d -o streamclipper -g streamclipper -m 0700 /var/lib/streamclipper-os
install -m 0600 /dev/null /etc/streamclipper-os.env
for unit in streamclipper-os streamclipper-dashboard streamclipper-youtube; do
  install -m 0644 "$root/streamclipper_os/deploy/$unit.service" /etc/systemd/system/
done
systemctl daemon-reload
runuser -u streamclipper -- env PATH=/opt/streamclipper-os/.venv/bin:/usr/bin:/bin /opt/streamclipper-os/.venv/bin/python /opt/streamclipper-os/streamclipper_os/preflight.py --state /var/lib/streamclipper-os
printf '%s\n' 'Installed. Services not started. Configure credentials and validate before enabling.'
