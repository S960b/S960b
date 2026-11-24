\set ON_ERROR_STOP on
-- DISASTER BACKOUT ONLY, manually approved. Never run automatically.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
DO $check$
BEGIN
 IF current_database()<>'bridge' THEN RAISE EXCEPTION 'STAGE34A_ROLLBACK_WRONG_DATABASE'; END IF;
 IF (SELECT count(*) FROM bridge.managed_dialogs)<>0 THEN
   RAISE EXCEPTION 'STAGE34A_ROLLBACK_REFUSED_MANAGED_DIALOGS_EXIST'; END IF;
 IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge')<>30 THEN
   RAISE EXCEPTION 'STAGE34A_ROLLBACK_REFUSED_UNKNOWN_SCHEMA'; END IF;
END $check$;
SET ROLE bridge_owner;
DROP FUNCTION bridge.ag_send_managed_report(text,text,text,bigint);
DROP FUNCTION bridge.ag_claim_managed_message(text,bigint,text,text,integer);
DROP FUNCTION bridge.ag_dispatch_list(integer,integer);
DROP FUNCTION bridge.cg_managed_status(text);
DROP FUNCTION bridge.cg_create_managed_dialog(text,text);
DROP TABLE bridge.managed_dialogs;
RESET ROLE;
COMMIT;
