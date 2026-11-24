#!/usr/bin/env bash
# Use the independent Stage37A revoker; quarantines gate and MCP permit.
set -Eeuo pipefail
bash /home/kali/hermes-pg-stage37a-permission-control/371_emergency_revoke.sh
