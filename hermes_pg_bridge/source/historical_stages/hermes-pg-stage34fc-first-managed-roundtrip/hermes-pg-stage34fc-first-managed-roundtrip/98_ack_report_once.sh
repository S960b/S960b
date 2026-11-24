#!/usr/bin/env bash
# Run only AFTER the exact report and the agent task ACK were independently
# verified. This claims and ACKs ChatGPT-directed report seq2 in one transaction.
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage34fc-first-managed-roundtrip
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" stage34fc.py ack-report-once
echo STAGE34FC_CHATGPT_REPORT_RECEIPT_SCRIPT_COMPLETE
