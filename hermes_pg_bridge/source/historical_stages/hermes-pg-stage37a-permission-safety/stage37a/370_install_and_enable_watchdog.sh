#!/usr/bin/env bash
# Install only a fail-closed, periodically running write-permit watchdog.
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$(id -u)" != 0 && "$HOME" == /home/kali ]] || { echo STAGE37A_STOP_WRONG_OWNER >&2; exit 2; }
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd "$SRC"
sha256sum -c SHA256SUMS >/dev/null
python3 -m unittest discover -q -p 'test_stage37a.py'
python3 -m py_compile stage37a.py test_stage37a.py
for f in ./*.sh; do bash -n "$f"; done
[[ -d "$HOME/.config/bridge-pg-v2" && ! -L "$HOME/.config/bridge-pg-v2" ]] || { echo STAGE37A_STOP_BRIDGE_DIR_UNSAFE >&2; exit 2; }
[[ -f "$HOME/.config/gigatool/opencode.json" && ! -L "$HOME/.config/gigatool/opencode.json" ]] || { echo STAGE37A_STOP_CONFIG_UNSAFE >&2; exit 2; }
# Never activate watchdog automatically when a test is currently armed.
S=$(python3 -IB "$SRC/stage37a.py" status)
printf '%s\n' "$S"
[[ "$S" == *'STAGE37A_PERMIT_PRESENT False'* && "$S" == *'STAGE37A_CLI_GATE_PRESENT False'* ]] || { echo STAGE37A_STOP_ACTIVE_WRITE_TRIAL_NOT_AUTO_INSTALL >&2; exit 2; }
# Validate current 8-plugin runtime shape without pinning one SHA: drift must not block revocation.
python3 - <<'PY'
import json,os,pathlib
p=pathlib.Path('/home/kali/.config/gigatool/opencode.json')
a=json.loads(p.read_text('utf8'))
items=a.get('plugin') if isinstance(a,dict) else None
if not isinstance(items,list) or len(items)!=8: raise SystemExit('STAGE37A_STOP_NOT_EIGHT_PLUGIN_BASELINE')
paths=[]
for i in items:
    if isinstance(i,str):paths.append(i)
    elif isinstance(i,list) and len(i)==2 and isinstance(i[0],str):paths.append(i[0])
    else:raise SystemExit('STAGE37A_STOP_UNKNOWN_PLUGIN_SHAPE')
if not any('stage34eb-pilot-unregistered.mjs' in x for x in paths) or not any('stage33i.mjs' in x for x in paths):
    raise SystemExit('STAGE37A_STOP_MANAGED_OR_CANARY_PLUGIN_NOT_REGISTERED')
print('STAGE37A_EIGHT_PLUGIN_BASELINE_READONLY_VERIFIED')
PY
DEST="$HOME/hermes-pg-stage37a-permission-control"
UDIR="$HOME/.config/systemd/user"
[[ -d "$UDIR" && ! -L "$UDIR" && "$(stat -c %u "$UDIR")" == "$(id -u)" ]] || { echo STAGE37A_STOP_SYSTEMD_USER_DIR_UNSAFE >&2; exit 2; }
if [[ ! -e "$DEST" && ! -L "$DEST" ]]; then
  mkdir -m 700 -- "$DEST"
fi
[[ -d "$DEST" && ! -L "$DEST" && "$(stat -c %u "$DEST")" == "$(id -u)" && "$(stat -c %a "$DEST")" == 700 ]] || { echo STAGE37A_STOP_INSTALL_DIR_DRIFT >&2; exit 2; }
for name in stage37a.py 371_emergency_revoke.sh 372_status_readonly.sh 373_watchdog_once.sh 374_disable_watchdog_timer.sh; do
  [[ ! -L "$DEST/$name" ]] || { echo STAGE37A_STOP_INSTALLED_SYMLINK >&2; exit 2; }
  if [[ -e "$DEST/$name" ]]; then
    cmp -s -- "$SRC/$name" "$DEST/$name" || { echo STAGE37A_STOP_INSTALLED_FILE_DRIFT >&2; exit 2; }
  else
    install -m 600 -- "$SRC/$name" "$DEST/$name"
  fi
done
SERVICE="$UDIR/hermes-pg-stage37a-permit-watchdog.service"
TIMER="$UDIR/hermes-pg-stage37a-permit-watchdog.timer"
for name in hermes-pg-stage37a-permit-watchdog.service hermes-pg-stage37a-permit-watchdog.timer; do
  t="$UDIR/$name"
  [[ ! -L "$t" ]] || { echo STAGE37A_STOP_UNIT_SYMLINK >&2; exit 2; }
  if [[ -e "$t" ]]; then
    cmp -s -- "$SRC/$name" "$t" || { echo STAGE37A_STOP_UNIT_DRIFT >&2; exit 2; }
  else
    install -m 600 -- "$SRC/$name" "$t"
  fi
done
systemctl --user daemon-reload
systemctl --user start hermes-pg-stage37a-permit-watchdog.service
systemctl --user enable --now hermes-pg-stage37a-permit-watchdog.timer >/dev/null
systemctl --user is-active --quiet hermes-pg-stage37a-permit-watchdog.timer || { echo STAGE37A_STOP_TIMER_NOT_ACTIVE >&2; exit 2; }
# One synchronous, safe verification with no active capabilities.
python3 -IB "$DEST/stage37a.py" watchdog
python3 -IB "$DEST/stage37a.py" status
printf '%s\n' STAGE37A_WATCHDOG_INSTALLED_2MIN_20MIN_GATE_LIMIT STAGE37A_NO_CONFIG_DB_TUNNEL_OR_MCP_MUTATION STAGE37A_READY_FOR_OPERATOR_AUDIT
