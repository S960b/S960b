#!/usr/bin/env bash
# Separate v2 code & venv on Kali; does not create services or install plugins.
set -Eeuo pipefail
umask 077
EXPECTED='/home/kali/bridge-pg-v2'
if [[ "$(id -un)" != 'kali' ]]; then echo 'RUN AS kali, no sudo' >&2; exit 2; fi
if [[ "$(pwd -P)" != "$EXPECTED" ]]; then echo "RUN FROM $EXPECTED" >&2; exit 2; fi
if ss -ltn '( sport = :8789 or sport = :8766 or sport = :15432 )' | tail -n +2 | grep -q .; then
  echo 'NEW v2 ports are not all free; stop before install' >&2; exit 2
fi
if [[ -e .venv ]]; then echo 'Existing .venv; not overwriting'; exit 2; fi
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m compileall -q bridge_pg
.venv/bin/python -m bridge_pg.cli --help >/dev/null
printf '%s\n' 'V2_KALI_PREPARE_OK; no services started'
