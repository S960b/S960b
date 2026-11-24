#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage36b-managed-inbox-handoff
python3 -IB stage36b.py status
