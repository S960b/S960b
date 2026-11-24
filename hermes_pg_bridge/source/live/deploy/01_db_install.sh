#!/usr/bin/env bash
# Stage 1 only. Creates a fresh schema in the existing `bridge` PostgreSQL DB.
# NO systemd changes, NO Git changes, NO network exposure, NO old-service changes.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ${EUID} -ne 0 ]]; then echo 'Run as root: sudo bash deploy/01_db_install.sh' >&2; exit 1; fi
command -v runuser >/dev/null || { echo 'runuser missing' >&2; exit 1; }
command -v psql >/dev/null || { echo 'psql missing' >&2; exit 1; }
command -v pg_isready >/dev/null || { echo 'pg_isready missing' >&2; exit 1; }
if ! pg_isready -q -h 127.0.0.1 -p 5432; then echo 'PostgreSQL not ready on loopback:5432' >&2; exit 1; fi
DB_EXISTS="$(runuser -u postgres -- psql -X -A -t -d postgres -c "SELECT 1 FROM pg_database WHERE datname='bridge'")"
[[ "$DB_EXISTS" == '1' ]] || { echo 'Database bridge missing; refusing to create it' >&2; exit 1; }
ROLE_EXISTS="$(runuser -u postgres -- psql -X -A -t -d postgres -c "SELECT 1 FROM pg_roles WHERE rolname='bridge_app'")"
[[ "$ROLE_EXISTS" == '1' ]] || { echo 'Role bridge_app missing; refusing to create login credentials' >&2; exit 1; }
SCHEMA_EXISTS="$(runuser -u postgres -- psql -X -A -t -d bridge -c "SELECT 1 FROM pg_namespace WHERE nspname='bridge'")"
[[ "$SCHEMA_EXISTS" != '1' ]] || { echo 'bridge schema already exists; refusing overwrite' >&2; exit 1; }
read -r -p 'Type CREATE-FRESH-BRIDGE-SCHEMA to authorize stage 1: ' answer
[[ "$answer" == 'CREATE-FRESH-BRIDGE-SCHEMA' ]] || { echo 'Cancelled'; exit 2; }
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d bridge -f "$ROOT/sql/001_init.sql"
echo 'POSTGRES_SCHEMA_CREATED; old Git services untouched'
