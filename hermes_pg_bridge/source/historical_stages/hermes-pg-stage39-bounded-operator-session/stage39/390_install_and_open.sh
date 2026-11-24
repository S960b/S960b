#!/usr/bin/env bash
# Run in a CHILD shell; never source this file into an interactive terminal.
set -Eeuo pipefail
umask 077
fail(){ echo "STAGE39_STOP_${1}" >&2; exit 2; }
[[ "$(id -un)" == kali && "$(id -u)" -ne 0 && "${HOME:-}" == /home/kali ]] || fail WRONG_OWNER
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd "$SOURCE"
sha256sum -c SHA256SUMS >/dev/null || fail PACKAGE_INTEGRITY
/usr/bin/python3 -I -B "$SOURCE/test_stage39.py" >/dev/null 2>&1 || fail OFFLINE_TESTS
for f in "$SOURCE"/*.sh; do bash -n "$f" || fail BASH_SYNTAX; done
for p in /home/kali/.config/bridge-pg-v2/managed-mcp-write-permit35c.json /home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed; do
  [[ ! -e "$p" && ! -L "$p" ]] || fail EXISTING_WRITE_CAPABILITIES
 done
TARGET=/home/kali/hermes-pg-stage39-operator-session
if [[ -e "$TARGET" ]]; then
  [[ -d "$TARGET" && ! -L "$TARGET" ]] || fail INSTALL_LOCATION_UNTRUSTED
else
  mkdir -m 700 -- "$TARGET" || fail INSTALL_DIRECTORY
fi
[[ "$(stat -c '%U:%a' "$TARGET")" == 'kali:700' ]] || fail INSTALL_DIRECTORY_MODE
for f in stage39.py test_stage39.py README_RU.md SHA256SUMS 390_install_and_open.sh 391_start_session.sh 392_status_readonly.sh 393_close_session.sh stage39-keeper.service stage39-keeper.timer; do
  [[ -f "$SOURCE/$f" && ! -L "$SOURCE/$f" ]] || fail SOURCE_MISSING
  if [[ -e "$TARGET/$f" || -L "$TARGET/$f" ]]; then
    [[ -f "$TARGET/$f" && ! -L "$TARGET/$f" ]] || fail INSTALL_FILE_UNTRUSTED
    cmp -s -- "$SOURCE/$f" "$TARGET/$f" || fail INSTALLED_FILE_DRIFT
  else
    install -m 600 -- "$SOURCE/$f" "$TARGET/$f" || fail INSTALL_COPY
  fi
  case "$f" in *.sh) chmod 700 -- "$TARGET/$f";; *) chmod 600 -- "$TARGET/$f";; esac
done
(
  cd "$TARGET"
  sha256sum -c SHA256SUMS >/dev/null
) || fail INSTALLED_FILE_HASHES
UNITDIR=/home/kali/.config/systemd/user
mkdir -p -- "$UNITDIR"
[[ -d "$UNITDIR" && ! -L "$UNITDIR" ]] || fail UNIT_DIRECTORY
for name in stage39-keeper.service stage39-keeper.timer; do
  src="$TARGET/$name"
  dest="$UNITDIR/hermes-pg-$name"
  if [[ -e "$dest" || -L "$dest" ]]; then
    [[ -f "$dest" && ! -L "$dest" ]] || fail UNIT_FILE_UNTRUSTED
    cmp -s -- "$src" "$dest" || fail UNIT_FILE_DRIFT
  else
    install -m 600 -- "$src" "$dest" || fail UNIT_FILE_INSTALL
  fi
 done
# The unit name is hermes-pg-stage39-keeper.service/timer; content uses same Unit=.
systemctl --user daemon-reload || fail DAEMON_RELOAD
systemctl --user enable --now hermes-pg-stage39-keeper.timer >/dev/null || fail TIMER_ENABLE
systemctl --user is-active --quiet hermes-pg-stage39-keeper.timer || fail TIMER_NOT_ACTIVE
printf '%s\n' 'STAGE39_PACKAGE_CHECKSUM_AND_37_OFFLINE_TESTS_OK' 'STAGE39_KEEPER_TIMER_INSTALLED_ACTIVE_60_SECONDS'
# Start only after the timer is confirmed active; no new plugin or config mutation.
bash "$TARGET/391_start_session.sh"
