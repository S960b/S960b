#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$(pwd -P)" == /home/kali/hermes-pg-stage33h-cg-receipt ]] || exit 2
# No runtime state / logs / credentials or environment are read by this helper.
env -u APPDIR -u APPIMAGE python3 source_inventory33h.py
