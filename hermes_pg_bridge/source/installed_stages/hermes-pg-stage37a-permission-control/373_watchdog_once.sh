#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
exec /usr/bin/python3 -IB /home/kali/hermes-pg-stage37a-permission-control/stage37a.py watchdog
