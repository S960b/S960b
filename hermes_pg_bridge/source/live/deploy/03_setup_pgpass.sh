#!/usr/bin/env bash
# Read password ONLY from pre-existing root-only file; never echo it or put it in argv.
# Prepares libpq credential file for the separate restricted bridgepg Unix user.
set -Eeuo pipefail
umask 077
if [[ ${EUID} -ne 0 ]]; then echo 'Run with sudo' >&2; exit 1; fi
password_path='/root/bridge-db-password.txt'
[[ -f "$password_path" && ! -L "$password_path" ]] || { echo 'Expected password file missing or symlink' >&2; exit 1; }
[[ "$(stat -c '%a' "$password_path")" == '600' ]] || { echo 'Password file must have mode 600' >&2; exit 1; }
id bridgepg >/dev/null || { echo 'First run 02_prepare_python.sh' >&2; exit 1; }
install -d -o bridgepg -g bridgepg -m 0700 /var/lib/bridge-pg-v2
python3 - "$password_path" <<'PY'
from pathlib import Path
import os, sys, tempfile
source = Path(sys.argv[1]); raw = source.read_text().rstrip('\r\n')
if not raw or '\n' in raw or '\r' in raw or '\0' in raw:
    raise SystemExit('Password has unexpected characters; no file written')
password = raw.replace('\\', '\\\\').replace(':', '\\:')
out = Path('/var/lib/bridge-pg-v2/.pgpass')
fd,tmp = tempfile.mkstemp(prefix='.pgpass-', dir=str(out.parent))
try:
    os.fchmod(fd,0o600)
    with os.fdopen(fd,'w') as h: h.write(f'127.0.0.1:5432:bridge:bridge_app:{password}\n')
    os.chown(tmp, __import__('pwd').getpwnam('bridgepg').pw_uid, __import__('grp').getgrnam('bridgepg').gr_gid)
    os.replace(tmp, out)
except BaseException:
    try: os.unlink(tmp)
    except FileNotFoundError: pass
    raise
PY
echo 'PGPASSFILE_PREPARED; password not printed'
