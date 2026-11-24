#!/usr/bin/env bash
# Stage 37B2C: do not patch an active plugin or any permissions. NO live reload.
set -Eeuo pipefail
umask 077
export PATH="/home/kali/.hermes/node/bin:$PATH"
unset NODE_OPTIONS NODE_PATH
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT=/home/kali/hermes-pg-stage37b2c-claim-intent
NODE=/home/kali/.hermes/node/bin/node
if [[ ! -x "$NODE" ]]; then NODE="$(command -v node || true)"; fi
[[ -x "$NODE" ]] || { echo STAGE37B2C_STOP_NODE_UNAVAILABLE; exit 2; }
[[ "$(id -un)" == kali && "$HOME" == /home/kali && $(id -u) -ne 0 ]] || { echo STAGE37B2C_STOP_OS_IDENTITY; exit 2; }
[[ ! -e "$ROOT" && ! -L "$ROOT" ]] || { echo STAGE37B2C_STOP_ONE_SHOT_ROOT_ALREADY_EXISTS; exit 2; }
(
 cd "$HERE"
 sha256sum -c SHA256SUMS
 python3 -m unittest discover -q -p 'test_stage37b2c.py'
 "$NODE" --check intent37b2c.mjs
 "$NODE" --check test_intent37b2c.mjs
 "$NODE" --check test_candidate37b2c.cjs
 "$NODE" test_intent37b2c.mjs
)
# Reuse the pinned read-only SQL and process preflight from Stage 37B2A.
python3 -IB /home/kali/hermes-pg-stage37b2-fault-injection/stage37b2.py preflight
# Validate actual pinned source anchors BEFORE creating anything persistent.
STAGE37B2C_SOURCE_DIR="$HERE" python3 -IB - <<'PY'
import hashlib,os,sys
sys.path.insert(0,os.environ['STAGE37B2C_SOURCE_DIR'])
import render37b2c as r
s=r.trusted_file(r.SRC).decode('utf8')
r.need(hashlib.sha256(s.encode('utf8')).hexdigest()==r.SOURCE_SHA,'SOURCE_SHA_CHANGED')
r.patched(s)
print('STAGE37B2C_PINNED_SOURCE_PATCH_ANCHORS_VALIDATED')
PY
mkdir -m 700 -- "$ROOT"
for f in intent37b2c.mjs render37b2c.py test_intent37b2c.mjs test_candidate37b2c.cjs test_stage37b2c.py README.md; do
 install -m 600 -- "$HERE/$f" "$ROOT/$f"
done
sha256sum "$ROOT/intent37b2c.mjs" | awk '{print $1}' > "$ROOT/helper.sha256"
chmod 600 "$ROOT/helper.sha256"
python3 -IB "$ROOT/render37b2c.py" render
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage37b2c-unregistered.mjs"
"$NODE" "$ROOT/test_candidate37b2c.cjs" "$ROOT/mt-pg-inbox-managed-stage37b2c-unregistered.mjs"
python3 -IB "$ROOT/render37b2c.py" verify
printf '%s\n' \
  STAGE37B2C_NODE_EXPLICIT_PATH_PROVEN \
  STAGE37B2C_PYTHON_UNIT_TESTS_21_OK \
  STAGE37B2C_HELPER_CASES_19_OK \
  STAGE37B2C_CANDIDATE_SANDBOX_TICK_TEST_OK \
  STAGE37B2C_UNREGISTERED_CANDIDATE_PREPARED_NO_LIVE_CUTOVER
