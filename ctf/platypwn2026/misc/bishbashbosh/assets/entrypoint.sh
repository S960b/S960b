#!/usr/bin/env bash
set -e

if [ ! -f /flag/flag.txt ]; then
    echo "FATAL: /flag is missing, refusing to start; If you are using podman-compose (fedora users likely are), see the compose file for a possible fix." >&2
    exit 1
fi

exec socat "$@"
