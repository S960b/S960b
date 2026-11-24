-- Hermes PG v2 / Stage 33A: additive shadow roles + actor-bound SQL API.
-- Intended for the DEDICATED bridge DB, executed as postgres administrator.
-- New roles are NOLOGIN: this stage DOES NOT activate any new client.
-- bridge_app grants, tables, messages, services, and canary remain unchanged.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $preflight$
DECLARE fn_count integer;
BEGIN
  IF current_database() <> 'bridge' THEN
    RAISE EXCEPTION 'STAGE33_WRONG_DATABASE';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_owner' AND NOT rolcanlogin)
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_app' AND rolcanlogin)
     OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('bridge_chatgpt','bridge_agent')) THEN
    RAISE EXCEPTION 'STAGE33_ROLE_PREFLIGHT_FAILED';
  END IF;
  IF (SELECT nspowner FROM pg_namespace WHERE nspname='bridge')
     IS DISTINCT FROM (SELECT oid FROM pg_roles WHERE rolname='bridge_owner') THEN
    RAISE EXCEPTION 'STAGE33_SCHEMA_OWNER_MISMATCH';
  END IF;
  SELECT count(*) INTO fn_count FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname='bridge' AND p.prosecdef AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='bridge_owner');
  IF fn_count <> 16 OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge') <> 16 THEN
    RAISE EXCEPTION 'STAGE33_FUNCTION_PREFLIGHT_MISMATCH';
  END IF;
  IF has_table_privilege('bridge_app','bridge.messages','SELECT')
      OR has_table_privilege('bridge_app','bridge.messages','INSERT') THEN
    RAISE EXCEPTION 'STAGE33_LEGACY_RIGHTS_UNEXPECTED';
  END IF;
END $preflight$;

-- Only shadow roles. No password, no LOGIN, no membership in bridge_owner/bridge_app.
CREATE ROLE bridge_chatgpt NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE bridge_agent NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE bridge TO bridge_chatgpt, bridge_agent;
GRANT USAGE ON SCHEMA bridge TO bridge_chatgpt, bridge_agent;
SET ROLE bridge_owner;

-- No caller-supplied sender/initiator fields. session_user remains original
-- authenticated login while current_user becomes bridge_owner under SECURITY DEFINER.
-- This guard also prevents bypass via SET ROLE or an accidental GRANT to a wrong role.
CREATE FUNCTION bridge.cg_create_dialog(p_request_id text, p_title text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.create_dialog(p_request_id,p_title,'chatgpt');
END $fn$;

CREATE FUNCTION bridge.cg_send_message(p_task_id text,p_message_id text,p_kind text,p_body text,p_expected_version bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.send_message(p_task_id,p_message_id,'chatgpt',p_kind,p_body,p_expected_version);
END $fn$;

CREATE FUNCTION bridge.ag_send_message(p_task_id text,p_message_id text,p_kind text,p_body text,p_expected_version bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.send_message(p_task_id,p_message_id,'agent',p_kind,p_body,p_expected_version);
END $fn$;

CREATE FUNCTION bridge.cg_return_turn(p_task_id text,p_message_id text,p_expected_version bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.return_turn(p_task_id,p_message_id,'chatgpt',p_expected_version);
END $fn$;

CREATE FUNCTION bridge.cg_tombstone_dialog(p_task_id text,p_expected_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.tombstone_dialog(p_task_id,p_expected_version);
END $fn$;

CREATE FUNCTION bridge.ag_claim_outbox(p_consumer text,p_limit integer DEFAULT 10,p_lease_seconds integer DEFAULT 60)
RETURNS TABLE(delivery_id bigint,task_id text,seq bigint,message_id text,sender text,
              kind text,body text,lease_token uuid,attempts integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT q.delivery_id,q.task_id,q.seq,q.message_id,q.sender,q.kind,q.body,q.lease_token,q.attempts
 FROM bridge.claim_outbox('agent',p_consumer,p_limit,p_lease_seconds) AS q;
END $fn$;

CREATE FUNCTION bridge.ag_claim_message(p_task_id text,p_seq bigint,p_message_id text,p_consumer text,p_lease_seconds integer DEFAULT 3600)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.claim_message(p_task_id,p_seq,p_message_id,p_consumer,p_lease_seconds);
END $fn$;

-- Defensive recipient check; no agent ACK/RELEASE of a ChatGPT-addressed outbox entry.
CREATE FUNCTION bridge.ag_ack_outbox(p_delivery_id bigint,p_lease_token uuid,p_consumer text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS (SELECT 1 FROM bridge.outbox WHERE delivery_id=p_delivery_id AND recipient='agent') THEN RETURN false; END IF;
 RETURN bridge.ack_outbox(p_delivery_id,p_lease_token,p_consumer);
END $fn$;

CREATE FUNCTION bridge.ag_release_outbox(p_delivery_id bigint,p_lease_token uuid,p_consumer text,p_retry_seconds integer DEFAULT 10,p_error text DEFAULT NULL)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS (SELECT 1 FROM bridge.outbox WHERE delivery_id=p_delivery_id AND recipient='agent') THEN RETURN false; END IF;
 RETURN bridge.release_outbox(p_delivery_id,p_lease_token,p_consumer,p_retry_seconds,p_error);
END $fn$;

RESET ROLE;
-- Explicitly remove any default function permissions, then grant narrowly.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA bridge FROM PUBLIC,bridge_chatgpt,bridge_agent;
GRANT EXECUTE ON FUNCTION bridge.cg_create_dialog(text,text),
  bridge.cg_send_message(text,text,text,text,bigint),
  bridge.cg_return_turn(text,text,bigint),
  bridge.cg_tombstone_dialog(text,bigint) TO bridge_chatgpt;
GRANT EXECUTE ON FUNCTION bridge.ag_send_message(text,text,text,text,bigint),
  bridge.ag_claim_outbox(text,integer,integer),
  bridge.ag_claim_message(text,bigint,text,text,integer),
  bridge.ag_ack_outbox(bigint,uuid,text),
  bridge.ag_release_outbox(bigint,uuid,text,integer,text) TO bridge_agent;
-- Shared read API. No direct table access.
GRANT EXECUTE ON FUNCTION bridge.health(),bridge.list_dialogs(integer,integer),
  bridge.view_dialog(text,integer,integer),bridge.dialog_metadata(text),
  bridge.send_status(text,text),bridge.get_message(text,bigint,text),
  bridge.delivery_status(text,bigint) TO bridge_chatgpt,bridge_agent;
GRANT EXECUTE ON FUNCTION bridge.creation_status(text) TO bridge_chatgpt;

DO $postflight$
DECLARE fn_count integer;
BEGIN
 SELECT count(*) INTO fn_count FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge';
 IF fn_count <> 25 THEN RAISE EXCEPTION 'STAGE33_WRAPPER_COUNT_MISMATCH'; END IF;
 IF EXISTS (
   SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname='bridge' AND EXISTS (
     SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
     WHERE a.grantee=0 AND a.privilege_type='EXECUTE'
   )
 ) THEN RAISE EXCEPTION 'STAGE33_PUBLIC_FUNCTION_EXECUTE'; END IF;
 IF has_table_privilege('bridge_chatgpt','bridge.messages','SELECT')
    OR has_table_privilege('bridge_agent','bridge.messages','SELECT')
    OR has_table_privilege('bridge_chatgpt','bridge.outbox','UPDATE')
    OR has_table_privilege('bridge_agent','bridge.outbox','UPDATE') THEN
   RAISE EXCEPTION 'STAGE33_TABLE_PRIVILEGE_LEAK'; END IF;
 IF has_function_privilege('bridge_chatgpt','bridge.send_message(text,text,text,text,text,bigint)','EXECUTE')
    OR has_function_privilege('bridge_agent','bridge.send_message(text,text,text,text,text,bigint)','EXECUTE')
    OR has_function_privilege('bridge_agent','bridge.cg_send_message(text,text,text,text,bigint)','EXECUTE')
    OR has_function_privilege('bridge_chatgpt','bridge.ag_send_message(text,text,text,text,bigint)','EXECUTE') THEN
   RAISE EXCEPTION 'STAGE33_CROSS_ACTOR_EXECUTE_LEAK'; END IF;
 IF NOT has_function_privilege('bridge_app','bridge.send_message(text,text,text,text,text,bigint)','EXECUTE') THEN
   RAISE EXCEPTION 'STAGE33_LEGACY_BRIDGE_APP_CHANGED'; END IF;
END $postflight$;
COMMIT;
SELECT 'STAGE33_SHADOW_ROLES_CREATED_NOLOGIN_OK' AS marker;
SELECT 'STAGE33_ACTOR_WRAPPERS_9_INSTALLED_OK' AS marker;
SELECT 'STAGE33_LEGACY_CLIENTS_UNCHANGED_OK' AS marker;
