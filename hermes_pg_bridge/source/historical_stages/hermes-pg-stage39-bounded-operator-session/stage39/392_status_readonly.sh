#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/home/kali/hermes-pg-stage39-operator-session
/usr/bin/python3 -I -B "$ROOT/stage39.py" status
systemctl --user is-active hermes-pg-stage39-keeper.timer || true
