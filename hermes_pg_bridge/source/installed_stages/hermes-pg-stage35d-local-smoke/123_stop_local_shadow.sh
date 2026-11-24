#!/usr/bin/env bash
set -Eeuo pipefail
set +x
cd /home/kali/hermes-pg-stage35d-local-smoke
[[ "$(id -un)" == kali ]] || exit 2
# Optional explicit rollback; only Stage35C local shadow service is stopped.
systemctl --user stop hermes-pg-stage35c-static-shadow.service
systemctl --user is-active --quiet hermes-pg-tunnel-stage33fd.service
systemctl --user is-active --quiet hermes-pg-v2-stage33fb-cg.service
echo STAGE35D_LOCAL_SHADOW_STOPPED_OLD_TUNNEL_STILL_ACTIVE
