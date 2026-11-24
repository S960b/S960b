#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35n-key-canonical
exec python3 stage35n.py verify
