#!/usr/bin/env bash
# Emergency close; no config hash/database/runtime precondition.
set -Eeuo pipefail
umask 077
exec /usr/bin/python3 -IB /home/kali/hermes-pg-stage37a-permission-control/stage37a.py revoke
