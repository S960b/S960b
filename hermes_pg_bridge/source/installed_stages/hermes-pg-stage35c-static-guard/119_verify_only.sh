#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35c-static-guard
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage35c.py verify
