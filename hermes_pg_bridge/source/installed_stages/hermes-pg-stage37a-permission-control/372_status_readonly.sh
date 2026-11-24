#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
/usr/bin/python3 -IB /home/kali/hermes-pg-stage37a-permission-control/stage37a.py status
systemctl --user is-active hermes-pg-stage37a-permit-watchdog.timer || true
