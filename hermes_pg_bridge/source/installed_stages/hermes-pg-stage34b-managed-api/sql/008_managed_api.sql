\set ON_ERROR_STOP on
-- Stage34B: additive actor-bound managed-dialog API. No dialog is enrolled or enabled.
-- One-time migration, run against the dedicated bridge DB as postgres.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
DO $pre$
DECLARE n integer;
BEGIN
 IF current_database()<>'bridge' OR (SELECT nspowner FROM pg_namespace WHERE nspname='bridge')
 IS DISTINCT FROM (SELECT oid FROM pg_roles WHERE rolname='bridge_owner') THEN
  RAISE EXCEPTION 'STAGE34B_DB_OR_OWNER_MISMATCH'; END IF;
 SELECT count(*) INTO n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge';
 IF n<>30 THEN RAISE EXCEPTION 'STAGE34B_EXPECT_30_FUNCTIONS'; END IF;
 SELECT count(*) INTO n FROM pg_class c JOIN pg_namespace s ON s.oid=c.relnamespace
 WHERE s.nspname='bridge' AND c.relkind IN ('r','p');
 IF n<>4 THEN RAISE EXCEPTION 'STAGE34B_EXPECT_4_TABLES'; END IF;
 IF (SELECT count(*) FROM bridge.managed_dialogs)<>0 OR
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='bridge'
      AND table_name='managed_dialogs' AND column_name='policy_version') THEN
  RAISE EXCEPTION 'STAGE34B_NOT_FRESH'; END IF;
 IF (SELECT last_seq FROM bridge.dialogs WHERE task_id='task_f2c64cfdf7894537b35a19a6def977c6')<>6 OR
 (SELECT status FROM bridge.outbox WHERE task_id='task_f2c64cfdf7894537b35a19a6def977c6' AND seq=5)<>'delivered' OR
 (SELECT status FROM bridge.outbox WHERE task_id='task_f2c64cfdf7894537b35a19a6def977c6' AND seq=6)<>'delivered' THEN
  RAISE EXCEPTION 'STAGE34B_CANARY_CHANGED'; END IF;
END $pre$;

SET ROLE bridge_owner;
ALTER TABLE bridge.managed_dialogs ADD COLUMN policy_version bigint NOT NULL DEFAULT 0
 CHECK (policy_version>=0);

CREATE FUNCTION bridge.cg_list_managed_dialogs(p_offset integer DEFAULT 0,p_limit integer DEFAULT 30)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE result jsonb;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_offset IS NULL OR p_offset<0 OR p_offset>100000 OR p_limit IS NULL OR p_limit<1 OR p_limit>50 THEN
  RAISE EXCEPTION 'invalid_page' USING ERRCODE='P0001'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY q.updated_at DESC,q.task_id),'[]'::jsonb)
 INTO result FROM (
  SELECT d.task_id,d.title,d.state,d.next_actor,d.last_seq,d.version,d.updated_at,
    r.dispatch_enabled,r.policy_version
  FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
  ORDER BY d.updated_at DESC,d.task_id LIMIT p_limit OFFSET p_offset
 ) q;
 RETURN result;
END $fn$;

CREATE FUNCTION bridge.cg_managed_detail(p_task_id text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE result jsonb;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('task_id',d.task_id,'registered',true,'dispatch_enabled',r.dispatch_enabled,
 'policy_version',r.policy_version,'state',d.state,'next_actor',d.next_actor,
 'version',d.version,'last_seq',d.last_seq,'title',d.title)
 INTO result FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
 WHERE r.task_id=p_task_id;
 RETURN COALESCE(result,jsonb_build_object('task_id',p_task_id,'registered',false));
END $fn$;

-- Explicit policy transition. Changing policy does NOT silently approve old dialogs;
-- the registry is exclusive to create_managed_dialog(). Stale policy versions fail.
CREATE FUNCTION bridge.cg_set_managed_dispatch(p_task_id text,p_enabled boolean,
 p_expected_policy_version bigint,p_expected_dialog_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE r bridge.managed_dialogs%ROWTYPE; d bridge.dialogs%ROWTYPE;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_enabled IS NULL OR p_expected_policy_version IS NULL OR p_expected_dialog_version IS NULL OR
   p_expected_policy_version<0 OR p_expected_dialog_version<0 THEN
  RAISE EXCEPTION 'invalid_policy_request' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM bridge.managed_dialogs WHERE task_id=p_task_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_managed' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM bridge.dialogs WHERE task_id=p_task_id FOR SHARE;
 IF NOT FOUND OR d.state<>'active' THEN RAISE EXCEPTION 'dialog_not_active' USING ERRCODE='P0001'; END IF;
 IF r.policy_version<>p_expected_policy_version OR d.version<>p_expected_dialog_version THEN
  RAISE EXCEPTION 'policy_version_conflict' USING ERRCODE='P0001'; END IF;
 IF p_enabled AND d.next_actor<>'chatgpt' THEN
  RAISE EXCEPTION 'cannot_enable_during_agent_turn' USING ERRCODE='P0001'; END IF;
 IF r.dispatch_enabled IS DISTINCT FROM p_enabled THEN
  UPDATE bridge.managed_dialogs SET dispatch_enabled=p_enabled,
   policy_version=policy_version+1,
   activated_at=CASE WHEN p_enabled THEN COALESCE(activated_at,clock_timestamp()) ELSE activated_at END
  WHERE task_id=p_task_id RETURNING * INTO r;
  RETURN jsonb_build_object('task_id',p_task_id,'dispatch_enabled',r.dispatch_enabled,
    'policy_version',r.policy_version,'changed',true);
 END IF;
 RETURN jsonb_build_object('task_id',p_task_id,'dispatch_enabled',r.dispatch_enabled,
   'policy_version',r.policy_version,'changed',false);
END $fn$;

CREATE FUNCTION bridge.cg_send_managed_task(p_task_id text,p_message_id text,
 p_body text,p_expected_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE allowed boolean;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_expected_version IS NULL OR p_expected_version<0 THEN
  RAISE EXCEPTION 'expected_version_required' USING ERRCODE='P0001'; END IF;
 SELECT dispatch_enabled INTO allowed FROM bridge.managed_dialogs WHERE task_id=p_task_id FOR SHARE;
 IF NOT FOUND OR allowed IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'managed_dispatch_disabled' USING ERRCODE='42501'; END IF;
 RETURN bridge.cg_send_message(p_task_id,p_message_id,'task',p_body,p_expected_version);
END $fn$;

CREATE FUNCTION bridge.cg_view_managed_dialog(p_task_id text,p_offset integer DEFAULT 0,p_limit integer DEFAULT 30)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM bridge.managed_dialogs WHERE task_id=p_task_id) THEN
  RAISE EXCEPTION 'not_managed' USING ERRCODE='42501'; END IF;
 RETURN bridge.view_dialog(p_task_id,p_offset,p_limit);
END $fn$;

-- Agent reads ONLY registered messages; if policy is disabled mid-SDK,
-- a leased/delivered message remains readable for reconciliation.
CREATE FUNCTION bridge.ag_read_managed_message(p_task_id text,p_seq bigint,p_message_id text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE result jsonb;
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 SELECT to_jsonb(q) INTO result FROM (
  SELECT m.task_id,m.seq,m.message_id,m.sender,m.recipient,m.kind,m.body,m.returned,m.created_at
  FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
  JOIN bridge.messages m ON m.task_id=d.task_id
  JOIN bridge.outbox o ON o.task_id=m.task_id AND o.seq=m.seq
  WHERE r.task_id=p_task_id AND m.seq=p_seq AND m.message_id=p_message_id
    AND d.state='active' AND m.sender='chatgpt' AND m.recipient='agent'
    AND m.kind='task' AND NOT m.returned
    AND (r.dispatch_enabled OR o.status IN ('leased','delivered'))
 ) q;
 RETURN result;
END $fn$;

CREATE FUNCTION bridge.ag_ack_managed_message(p_task_id text,p_seq bigint,p_delivery_id bigint,
 p_lease_token uuid,p_consumer text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE s text;
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 SELECT o.status INTO s FROM bridge.managed_dialogs r
 JOIN bridge.outbox o ON o.task_id=r.task_id
 JOIN bridge.messages m ON m.task_id=o.task_id AND m.seq=o.seq
 WHERE r.task_id=p_task_id AND o.seq=p_seq AND o.delivery_id=p_delivery_id
 AND o.recipient='agent' AND m.sender='chatgpt' AND m.kind='task';
 IF NOT FOUND THEN RETURN false; END IF;
 IF s='delivered' THEN RETURN true; END IF;
 RETURN bridge.ag_ack_outbox(p_delivery_id,p_lease_token,p_consumer);
END $fn$;

CREATE FUNCTION bridge.cg_claim_managed_report(p_task_id text,p_seq bigint,p_message_id text,
 p_consumer text,p_lease_seconds integer DEFAULT 3600)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE o bridge.outbox%ROWTYPE;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_seq IS NULL OR p_seq<1 OR p_consumer IS NULL OR length(p_consumer) NOT BETWEEN 1 AND 128
 OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 30 AND 7200 THEN
  RAISE EXCEPTION 'invalid_claim' USING ERRCODE='P0001'; END IF;
 SELECT x.* INTO o FROM bridge.outbox x
 JOIN bridge.messages m ON m.task_id=x.task_id AND m.seq=x.seq
 JOIN bridge.managed_dialogs r ON r.task_id=m.task_id
 JOIN bridge.dialogs d ON d.task_id=r.task_id
 WHERE x.task_id=p_task_id AND x.seq=p_seq AND m.message_id=p_message_id
 AND x.recipient='chatgpt' AND m.sender='agent' AND m.kind='report' AND NOT m.returned
 AND d.state='active' AND x.status='pending' AND x.available_at<=clock_timestamp()
 FOR UPDATE OF x;
 IF NOT FOUND THEN RETURN jsonb_build_object('claimed',false); END IF;
 UPDATE bridge.outbox SET status='leased',attempts=attempts+1,
 lease_token=pg_catalog.gen_random_uuid(),lease_owner=p_consumer,
 lease_until=clock_timestamp()+make_interval(secs=>p_lease_seconds)
 WHERE delivery_id=o.delivery_id RETURNING * INTO o;
 RETURN jsonb_build_object('claimed',true,'delivery_id',o.delivery_id,
 'lease_token',o.lease_token,'lease_owner',o.lease_owner);
END $fn$;

CREATE FUNCTION bridge.cg_ack_managed_report(p_task_id text,p_seq bigint,p_delivery_id bigint,
 p_lease_token uuid,p_consumer text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp AS $fn$
DECLARE s text;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 SELECT o.status INTO s FROM bridge.managed_dialogs r
 JOIN bridge.outbox o ON o.task_id=r.task_id
 JOIN bridge.messages m ON m.task_id=o.task_id AND m.seq=o.seq
 WHERE r.task_id=p_task_id AND o.seq=p_seq AND o.delivery_id=p_delivery_id
 AND o.recipient='chatgpt' AND m.sender='agent' AND m.kind='report';
 IF NOT FOUND THEN RETURN false; END IF;
 IF s='delivered' THEN RETURN true; END IF;
 RETURN bridge.ack_outbox(p_delivery_id,p_lease_token,p_consumer);
END $fn$;
RESET ROLE;

REVOKE ALL ON FUNCTION bridge.cg_list_managed_dialogs(integer,integer),
 bridge.cg_managed_detail(text),bridge.cg_set_managed_dispatch(text,boolean,bigint,bigint),
 bridge.cg_send_managed_task(text,text,text,bigint),bridge.cg_view_managed_dialog(text,integer,integer),
 bridge.ag_read_managed_message(text,bigint,text),bridge.ag_ack_managed_message(text,bigint,bigint,uuid,text),
 bridge.cg_claim_managed_report(text,bigint,text,text,integer),
 bridge.cg_ack_managed_report(text,bigint,bigint,uuid,text)
 FROM PUBLIC,bridge_app,bridge_chatgpt,bridge_agent;
GRANT EXECUTE ON FUNCTION bridge.cg_list_managed_dialogs(integer,integer),
 bridge.cg_managed_detail(text),bridge.cg_set_managed_dispatch(text,boolean,bigint,bigint),
 bridge.cg_send_managed_task(text,text,text,bigint),bridge.cg_view_managed_dialog(text,integer,integer),
 bridge.cg_claim_managed_report(text,bigint,text,text,integer),
 bridge.cg_ack_managed_report(text,bigint,bigint,uuid,text) TO bridge_chatgpt;
GRANT EXECUTE ON FUNCTION bridge.ag_read_managed_message(text,bigint,text),
 bridge.ag_ack_managed_message(text,bigint,bigint,uuid,text) TO bridge_agent;

DO $post$
DECLARE n integer;
BEGIN
 SELECT count(*) INTO n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge';
 IF n<>39 THEN RAISE EXCEPTION 'STAGE34B_EXPECT_39_FUNCTIONS'; END IF;
 IF (SELECT count(*) FROM bridge.managed_dialogs)<>0 OR
 (SELECT count(*) FROM information_schema.columns WHERE table_schema='bridge'
 AND table_name='managed_dialogs' AND column_name='policy_version')<>1 THEN
  RAISE EXCEPTION 'STAGE34B_REGISTRY_POSTCONDITION'; END IF;
 IF has_function_privilege('bridge_app','bridge.cg_send_managed_task(text,text,text,bigint)','EXECUTE') OR
 has_function_privilege('bridge_agent','bridge.cg_set_managed_dispatch(text,boolean,bigint,bigint)','EXECUTE') OR
 has_function_privilege('bridge_chatgpt','bridge.ag_read_managed_message(text,bigint,text)','EXECUTE') OR
 has_table_privilege('bridge_chatgpt','bridge.managed_dialogs','SELECT') OR
 has_table_privilege('bridge_agent','bridge.managed_dialogs','SELECT') THEN
  RAISE EXCEPTION 'STAGE34B_ACL_POSTCONDITION'; END IF;
END $post$;
COMMIT;
SELECT 'STAGE34B_MANAGED_API_SQL_COMMITTED' AS marker;
