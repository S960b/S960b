\set ON_ERROR_STOP on
-- Hermes PG v2 Stage 34A: additive managed-dispatch registry. NO existing dialog is enrolled.
-- Intentional, one-shot DDL as postgres, in the dedicated bridge DB.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
DO $pre$
DECLARE n integer;
BEGIN
 IF current_database()<>'bridge' THEN RAISE EXCEPTION 'STAGE34A_WRONG_DATABASE'; END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='bridge' AND nspowner=(SELECT oid FROM pg_roles WHERE rolname='bridge_owner')) THEN
  RAISE EXCEPTION 'STAGE34A_SCHEMA_OWNER_MISMATCH'; END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_chatgpt' AND rolcanlogin AND NOT rolsuper AND NOT rolcreaterole)
    OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_agent' AND rolcanlogin AND NOT rolsuper AND NOT rolcreaterole)
    OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_owner' AND NOT rolcanlogin) THEN
  RAISE EXCEPTION 'STAGE34A_ROLES_NOT_READY'; END IF;
 SELECT count(*) INTO n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge';
 IF n<>25 THEN RAISE EXCEPTION 'STAGE34A_EXPECTED_25_FUNCTIONS'; END IF;
 SELECT count(*) INTO n FROM pg_class c JOIN pg_namespace s ON s.oid=c.relnamespace
 WHERE s.nspname='bridge' AND c.relkind IN ('r','p');
 IF n<>3 OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace s ON s.oid=c.relnamespace
   WHERE s.nspname='bridge' AND c.relname='managed_dialogs') THEN
  RAISE EXCEPTION 'STAGE34A_NOT_A_FRESH_SHADOW_MIGRATION'; END IF;
 IF (SELECT last_seq FROM bridge.dialogs WHERE task_id='task_f2c64cfdf7894537b35a19a6def977c6')<>6
    OR (SELECT status FROM bridge.outbox WHERE task_id='task_f2c64cfdf7894537b35a19a6def977c6' AND seq=5)<>'delivered'
    OR (SELECT status FROM bridge.outbox WHERE task_id='task_f2c64cfdf7894537b35a19a6def977c6' AND seq=6)<>'delivered' THEN
  RAISE EXCEPTION 'STAGE34A_CANARY_RECEIPT_NOT_FINAL'; END IF;
 IF has_table_privilege('bridge_agent','bridge.messages','SELECT') OR
    has_table_privilege('bridge_chatgpt','bridge.messages','SELECT') OR
    has_function_privilege('bridge_agent','bridge.cg_create_dialog(text,text)','EXECUTE') OR
    has_function_privilege('bridge_chatgpt','bridge.ag_claim_message(text,bigint,text,text,integer)','EXECUTE') THEN
  RAISE EXCEPTION 'STAGE34A_ROLE_ISOLATION_FAILED'; END IF;
END $pre$;

SET ROLE bridge_owner;
CREATE TABLE bridge.managed_dialogs (
 task_id text PRIMARY KEY REFERENCES bridge.dialogs(task_id) ON DELETE RESTRICT,
 request_id text NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 160),
 dispatch_enabled boolean NOT NULL DEFAULT false,
 registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 activated_at timestamptz,
 CHECK (NOT dispatch_enabled OR activated_at IS NOT NULL)
);
REVOKE ALL ON bridge.managed_dialogs FROM PUBLIC,bridge_app,bridge_chatgpt,bridge_agent;

-- Create and register atomically; an existing unmanaged request_id is NEVER adoptable.
-- Dispatch remains disabled even after a successful create.
CREATE FUNCTION bridge.cg_create_managed_dialog(p_request_id text,p_title text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE v jsonb; chosen text; current_enabled boolean;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_request_id IS NULL OR p_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
   OR p_title IS NULL OR length(btrim(p_title))=0 OR length(p_title)>4096 THEN
  RAISE EXCEPTION 'invalid_request' USING ERRCODE='P0001'; END IF;
 v:=bridge.cg_create_dialog(p_request_id,p_title);
 chosen:=v->>'task_id';
 IF v->>'created'='true' THEN
  INSERT INTO bridge.managed_dialogs(task_id,request_id) VALUES(chosen,p_request_id);
 ELSE
  SELECT dispatch_enabled INTO current_enabled FROM bridge.managed_dialogs
   WHERE task_id=chosen AND request_id=p_request_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'unmanaged_request_id_collision' USING ERRCODE='P0001'; END IF;
 END IF;
 SELECT dispatch_enabled INTO current_enabled FROM bridge.managed_dialogs WHERE task_id=chosen;
 RETURN v || jsonb_build_object('managed',true,'dispatch_enabled',current_enabled);
END $fn$;

CREATE FUNCTION bridge.cg_managed_status(p_task_id text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE result jsonb;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('task_id',d.task_id,'registered',true,
     'dispatch_enabled',r.dispatch_enabled,'state',d.state,
     'next_actor',d.next_actor,'version',d.version,'last_seq',d.last_seq)
 INTO result FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
 WHERE d.task_id=p_task_id;
 RETURN COALESCE(result,jsonb_build_object('task_id',p_task_id,'registered',false));
END $fn$;

-- Agent's only future discovery API. Old dialogs and not-yet-approved new dialogs are invisible.
CREATE FUNCTION bridge.ag_dispatch_list(p_offset integer DEFAULT 0,p_limit integer DEFAULT 20)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE result jsonb;
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_offset IS NULL OR p_offset<0 OR p_offset>100000 OR
    p_limit IS NULL OR p_limit<1 OR p_limit>50 THEN
  RAISE EXCEPTION 'invalid_page' USING ERRCODE='P0001'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY q.updated_at DESC,q.task_id),'[]'::jsonb)
 INTO result FROM (
  SELECT d.task_id,d.title,d.next_actor,d.last_seq,d.version,d.updated_at
  FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
  WHERE r.dispatch_enabled AND d.state='active' AND d.next_actor='agent'
  ORDER BY d.updated_at DESC,d.task_id
  LIMIT p_limit OFFSET p_offset
 ) q;
 RETURN result;
END $fn$;

-- A future general MT plugin MUST claim through this function, not the legacy broad wrapper.
CREATE FUNCTION bridge.ag_claim_managed_message(p_task_id text,p_seq bigint,p_message_id text,
 p_consumer text,p_lease_seconds integer DEFAULT 3600)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE result jsonb;
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
 WHERE r.task_id=p_task_id AND r.dispatch_enabled AND d.state='active'
 AND d.next_actor='agent' FOR SHARE OF r;
 IF NOT FOUND THEN RAISE EXCEPTION 'managed_dialog_not_enabled' USING ERRCODE='42501'; END IF;
 result:=bridge.ag_claim_message(p_task_id,p_seq,p_message_id,p_consumer,p_lease_seconds);
 RETURN result;
END $fn$;

-- Allow an in-flight agent's final report even if administrative dispatch is disabled
-- while the SDK is already executing. Newly unregistered/legacy dialogs stay forbidden.
CREATE FUNCTION bridge.ag_send_managed_report(p_task_id text,p_message_id text,
 p_body text,p_expected_version bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM bridge.managed_dialogs r WHERE r.task_id=p_task_id) THEN
  RAISE EXCEPTION 'unregistered_dialog' USING ERRCODE='42501'; END IF;
 RETURN bridge.ag_send_message(p_task_id,p_message_id,'report',p_body,p_expected_version);
END $fn$;
RESET ROLE;

-- No implicit EXECUTE for PUBLIC; actor roles receive only their own wrappers.
REVOKE ALL ON FUNCTION bridge.cg_create_managed_dialog(text,text),
 bridge.cg_managed_status(text),bridge.ag_dispatch_list(integer,integer),
 bridge.ag_claim_managed_message(text,bigint,text,text,integer),
 bridge.ag_send_managed_report(text,text,text,bigint)
 FROM PUBLIC,bridge_app,bridge_chatgpt,bridge_agent;
GRANT EXECUTE ON FUNCTION bridge.cg_create_managed_dialog(text,text),
 bridge.cg_managed_status(text) TO bridge_chatgpt;
GRANT EXECUTE ON FUNCTION bridge.ag_dispatch_list(integer,integer),
 bridge.ag_claim_managed_message(text,bigint,text,text,integer),
 bridge.ag_send_managed_report(text,text,text,bigint) TO bridge_agent;

DO $post$
DECLARE n integer;
BEGIN
 SELECT count(*) INTO n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge';
 IF n<>30 THEN RAISE EXCEPTION 'STAGE34A_FUNCTION_COUNT_NOT_30'; END IF;
 IF has_table_privilege('bridge_agent','bridge.managed_dialogs','SELECT') OR
    has_table_privilege('bridge_chatgpt','bridge.managed_dialogs','SELECT') OR
    has_table_privilege('bridge_app','bridge.managed_dialogs','SELECT') THEN
  RAISE EXCEPTION 'STAGE34A_MANAGED_TABLE_ACL_LEAK'; END IF;
 IF has_function_privilege('bridge_chatgpt','bridge.ag_dispatch_list(integer,integer)','EXECUTE') OR
    has_function_privilege('bridge_agent','bridge.cg_create_managed_dialog(text,text)','EXECUTE') OR
    has_function_privilege('bridge_app','bridge.cg_create_managed_dialog(text,text)','EXECUTE') THEN
  RAISE EXCEPTION 'STAGE34A_ROLE_CROSSOVER'; END IF;
 IF (SELECT count(*) FROM bridge.managed_dialogs)<>0 THEN
  RAISE EXCEPTION 'STAGE34A_UNEXPECTED_MANAGED_ROWS'; END IF;
END $post$;
COMMIT;
SELECT 'STAGE34A_MANAGED_REGISTRY_ADDITIVE_SQL_COMMITTED' AS marker;
