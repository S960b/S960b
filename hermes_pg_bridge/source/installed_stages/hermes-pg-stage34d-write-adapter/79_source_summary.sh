#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34d-write-adapter
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/home/kali/hermes-pg-stage34d-write-adapter/plugin_contract34d.json')
r=json.loads(p.read_text())
for f in r['segments']:
    print('\n=== '+f['function']+' L'+str(f['start_line'])+'-'+str(f['end_line'])+' ===')
    # Exact source snippets for later patch design, not logs or user messages.
    print(f['source'])
print('STAGE34D_PINNED_PLUGIN_SOURCE_EXCERPTS_COMPLETE')
PY
