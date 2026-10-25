#!/usr/bin/env bash
set -euo pipefail
BASE=$(cat "$(dirname "$0")/origin.txt")
ROUTE=$(cat "$(dirname "$0")/route.txt")
SIG=$(cat "$(dirname "$0")/signature.txt")
URL="${BASE}${ROUTE}?sig=${SIG}"
echo "Downloading CTF release archive..."
curl --fail --location --retry 3 --connect-timeout 10 --max-time 60 "$URL" -o release.tar.gz
file release.tar.gz
printf '\nArchive members:\n'
tar -tzvf release.tar.gz
printf '\nSHA256:\n'
sha256sum release.tar.gz
printf '\nSend release.tar.gz back to ChatGPT for reverse engineering.\n'
