#!/usr/bin/env bash
# Read-only preflight. Designed to show no passwords or private data.
set -Eeuo pipefail
printf 'utc=%s\n' "$(date -u +%FT%TZ)"
printf 'python=%s\n' "$(python3 --version 2>&1)"
printf 'postgres_ready='; pg_isready -h 127.0.0.1 -p 5432 || true
printf 'postgres_service='; systemctl is-active postgresql || true
printf 'port_8765_8766_8787_8788='; (ss -lnt '( sport = :8765 or sport = :8766 or sport = :8787 or sport = :8788 )' || true)
printf 'disk='; df -h / | tail -1
printf 'memory='; free -m | head -2 | tail -1
printf 'schema_exists='; runuser -u postgres -- psql -X -A -t -d bridge -c "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'bridge');"
printf 'bridge_app_exists='; runuser -u postgres -- psql -X -A -t -d bridge -c "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bridge_app');"
