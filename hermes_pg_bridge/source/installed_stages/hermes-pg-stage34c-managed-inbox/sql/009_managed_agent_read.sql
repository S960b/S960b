\set ON_ERROR_STOP on
-- Stage34C: ADDITIVE managed-only read endpoints. No dialogs or dispatch changed.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
DO $pre$
DECLARE n int;
BEGIN
 IF current_database()<>'bridge' OR NOT EXISTS(
  SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
  WHERE n.nspname='bridge' AND r.rolname='bridge_owner') THEN
  RAISE EXCEPTION 'STAGE34C_DATABASE_OR_OWNER_MISMATCH'; END IF;
 SELECT count(*) INTO n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge';
 IF n<>39 THEN RAISE EXCEPTION 'STAGE34C_EXPECTED_39_FUNCTIONS'; END IF;
 IF (SELECT count(*) FROM bridge.managed_dialogs)<>0 OR
    (SELECT count(*) FROM bridge.dialogs)<>3 THEN
  RAISE EXCEPTION 'STAGE34C_MANAGED_OR_LEGACY_STATE_CHANGED'; END IF;
 IF (SELECT last_seq FROM bridge.dialogs WHERE task_id='task_f2c64cfdf7894537b35a19a6def977c6')<>6 OR
    (SELECT status FROM bridge.outbox WHERE task_id='task_f2c64cfdf7894537b35a19a6def977c6' AND seq=5)<>'delivered' OR
    (SELECT status FROM bridge.outbox WHERE task_id='task_f2c64cfdf7894537b35a19a6def977c6' AND seq=6)<>'delivered' THEN
  RAISE EXCEPTION 'STAGE34C_CANARY_CHANGED'; END IF;
END $pre$;
SET ROLE bridge_owner;
-- Agent can see an activated managed dialogue even if dispatch is later disabled,
-- to reconcile a previously started SDK turn. Never grants access to unmanaged dialogs.
CREATE FUNCTION bridge.ag_view_managed_dialog(p_task_id text,p_offset integer DEFAULT 0,p_limit integer DEFAULT 30)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_offset IS NULL OR p_offset<0 OR p_offset>100000 OR p_limit IS NULL OR p_limit<1 OR p_limit>50 THEN
  RAISE EXCEPTION 'invalid_page' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS(
  SELECT 1 FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
  WHERE r.task_id=p_task_id AND r.activated_at IS NOT NULL AND d.state='active') THEN
  RAISE EXCEPTION 'managed_dialog_not_activated' USING ERRCODE='42501'; END IF;
 RETURN bridge.view_dialog(p_task_id,p_offset,p_limit);
END $fn$;

CREATE FUNCTION bridge.ag_managed_delivery_status(p_task_id text,p_seq bigint)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_seq IS NULL OR p_seq<1 OR NOT EXISTS(
  SELECT 1 FROM bridge.managed_dialogs r JOIN bridge.messages m ON m.task_id=r.task_id
  JOIN bridge.outbox o ON o.task_id=m.task_id AND o.seq=m.seq
  WHERE r.task_id=p_task_id AND r.activated_at IS NOT NULL AND m.seq=p_seq
   AND m.sender='chatgpt' AND m.recipient='agent' AND m.kind='task' AND o.recipient='agent') THEN
  RAISE EXCEPTION 'not_managed_task_delivery' USING ERRCODE='42501'; END IF;
 RETURN bridge.delivery_status(p_task_id,p_seq);
END $fn$;

CREATE FUNCTION bridge.ag_managed_report_status(p_task_id text,p_message_id text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE m bridge.messages%ROWTYPE;
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_message_id IS NULL OR length(p_message_id)>64 OR NOT EXISTS(
  SELECT 1 FROM bridge.managed_dialogs r WHERE r.task_id=p_task_id AND r.activated_at IS NOT NULL) THEN
  RAISE EXCEPTION 'not_managed_report' USING ERRCODE='42501'; END IF;
 SELECT * INTO m FROM bridge.messages WHERE task_id=p_task_id AND message_id=p_message_id;
 IF FOUND AND (m.sender<>'agent' OR m.recipient<>'chatgpt' OR m.kind<>'report') THEN
  RAISE EXCEPTION 'not_managed_report' USING ERRCODE='42501'; END IF;
 RETURN bridge.send_status(p_task_id,p_message_id);
END $fn$;
RESET ROLE;
REVOKE ALL ON FUNCTION bridge.ag_view_managed_dialog(text,integer,integer),
 bridge.ag_managed_delivery_status(text,bigint),bridge.ag_managed_report_status(text,text)
 FROM PUBLIC,bridge_app,bridge_agent,bridge_chatgpt;
GRANT EXECUTE ON FUNCTION bridge.ag_view_managed_dialog(text,integer,integer),
 bridge.ag_managed_delivery_status(text,bigint),bridge.ag_managed_report_status(text,text)
 TO bridge_agent;
DO $post$
DECLARE n integer;
BEGIN
 SELECT count(*) INTO n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge';
 IF n<>42 OR (SELECT count(*) FROM bridge.managed_dialogs)<>0 THEN
  RAISE EXCEPTION 'STAGE34C_SCHEMA_OR_REGISTRY_CHANGED'; END IF;
 IF has_function_privilege('bridge_chatgpt','bridge.ag_view_managed_dialog(text,integer,integer)','EXECUTE') OR
    has_function_privilege('bridge_app','bridge.ag_managed_delivery_status(text,bigint)','EXECUTE') OR
    has_table_privilege('bridge_agent','bridge.managed_dialogs','SELECT') THEN
  RAISE EXCEPTION 'STAGE34C_ACL_LEAK'; END IF;
END $post$;
COMMIT;
SELECT 'STAGE34C_MANAGED_AGENT_READ_SQL_COMMITTED' AS marker;
