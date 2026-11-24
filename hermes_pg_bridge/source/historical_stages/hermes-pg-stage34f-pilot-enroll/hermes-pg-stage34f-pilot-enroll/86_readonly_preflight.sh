#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage34f-pilot-enroll
exec env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python pilot34f.py preflight
