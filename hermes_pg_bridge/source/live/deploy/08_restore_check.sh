#!/bin/sh
# Verify that sql/010_schema_full.sql recreates the complete bridge contract
# in an empty database. Run on the PostgreSQL host as a superuser (postgres).
# The check never touches the live "bridge" database.
#
#   sh 08_restore_check.sh [scratch_db] [schema_file]
set -eu

DB=${1:-bridge_restore_check}
SCHEMA=${2:-$(dirname "$0")/../sql/010_schema_full.sql}

test -f "$SCHEMA" || { echo "schema file not found: $SCHEMA" >&2; exit 2; }
case "$DB" in bridge) echo "refusing to use the live database" >&2; exit 2;; esac

dropdb --if-exists "$DB"
createdb "$DB"
trap 'dropdb --if-exists "$DB" >/dev/null 2>&1 || true' EXIT INT TERM
psql -X -q -v ON_ERROR_STOP=1 -d "$DB" -f "$SCHEMA" >/dev/null

psql -X -qAt -v ON_ERROR_STOP=1 -d "$DB" <<'SQL'
WITH expected(name) AS (
  VALUES ('create_dialog'),('send_message'),('hand_back_turn'),
         ('cg_create_managed_dialog'),('cg_send_managed_task'),('cg_set_managed_dispatch'),
         ('ag_claim_managed_message'),('ag_send_bound_managed_report'),
         ('ag_send_managed_report'),('tb_list_rows'))
SELECT 'missing_functions=' || COALESCE(string_agg(e.name, ','), 'none')
FROM expected e
WHERE NOT EXISTS (
  SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname = 'bridge' AND p.proname = e.name);

SELECT 'functions=' || count(*) FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'bridge';

SELECT 'tables=' || count(*) FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'bridge' AND c.relkind = 'r';

SELECT 'rights=' || (
  has_function_privilege('bridge_agent','bridge.ag_claim_managed_message(text,bigint,text,text,integer)','EXECUTE')
  AND has_function_privilege('bridge_agent','bridge.ag_send_bound_managed_report(text,text,text,bigint,bigint,bigint,uuid,text)','EXECUTE')
  AND has_function_privilege('bridge_chatgpt','bridge.cg_create_managed_dialog(text,text)','EXECUTE')
  AND has_function_privilege('bridge_app','bridge.tb_list_rows(integer,integer)','EXECUTE')
  AND has_function_privilege('bridge_app','bridge.hand_back_turn(text,bigint)','EXECUTE'));

SELECT 'owners=' || COALESCE(string_agg(rolname || ':' || n, ',' ORDER BY rolname), 'none')
FROM (
  SELECT r.rolname, count(*) AS n
  FROM pg_proc p
  JOIN pg_namespace ns ON ns.oid = p.pronamespace
  JOIN pg_roles r ON r.oid = p.proowner
  WHERE ns.nspname = 'bridge' GROUP BY 1
) o;
SQL

echo "RESTORE_CHECK_OK $DB"
