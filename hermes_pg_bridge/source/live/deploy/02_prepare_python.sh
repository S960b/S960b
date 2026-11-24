#!/usr/bin/env bash
# Stage 2 dependency preparation only (does not run or expose a service).
# A future MCP/taskboard install stage will be authored after source inventory.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ${EUID} -ne 0 ]]; then echo 'Run with sudo' >&2; exit 1; fi
if ! id bridgepg >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/bridge-pg-v2 --create-home --shell /usr/sbin/nologin bridgepg
fi
install -d -o bridgepg -g bridgepg -m 0700 /var/lib/bridge-pg-v2
install -d -o root -g root -m 0755 /opt/bridge-pg-v2
python3 -m venv /opt/bridge-pg-v2/.venv
/opt/bridge-pg-v2/.venv/bin/python -m pip install --disable-pip-version-check -r "$ROOT/requirements.txt"
install -d -o root -g root -m 0755 /opt/bridge-pg-v2/bridge_pg
install -m 0644 "$ROOT/bridge_pg/"*.py /opt/bridge-pg-v2/bridge_pg/
echo 'PYTHON_RUNTIME_PREPARED; no service started'
