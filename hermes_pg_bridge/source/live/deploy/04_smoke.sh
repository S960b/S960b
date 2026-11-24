#!/usr/bin/env bash
# Run only after stages 01-03. Creates a small smoke dialogue.
set -Eeuo pipefail
if [[ ${EUID} -ne 0 ]]; then echo 'Run with sudo' >&2; exit 1; fi
[[ -e /opt/bridge-pg-v2/.venv/bin/python ]] || { echo 'Stage 02 missing' >&2; exit 1; }
runuser -u bridgepg -- env \
  PGPASSFILE=/var/lib/bridge-pg-v2/.pgpass \
  PYTHONPATH=/opt/bridge-pg-v2 \
  /opt/bridge-pg-v2/.venv/bin/python -m bridge_pg.smoke
