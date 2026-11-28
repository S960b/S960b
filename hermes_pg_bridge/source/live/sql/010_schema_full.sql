-- Hermes PostgreSQL Bridge: full schema skeleton (no data, no secrets).
-- Restore on a fresh database:
--   1. create an empty database (owner: postgres or a DBA role);
--   2. run this file as a superuser: psql -v ON_ERROR_STOP=1 -f 010_schema_full.sql;
--   3. set passwords for bridge_app / bridge_chatgpt / bridge_agent out of band;
--   4. verify with deploy/08_restore_check.sh.
-- Roles carry no passwords here. Function owners and final grants are preserved.

DO $roles$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bridge_owner') THEN
    CREATE ROLE bridge_owner NOLOGIN INHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bridge_app') THEN
    CREATE ROLE bridge_app LOGIN INHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bridge_chatgpt') THEN
    CREATE ROLE bridge_chatgpt LOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bridge_agent') THEN
    CREATE ROLE bridge_agent LOGIN NOINHERIT;
  END IF;
END
$roles$;

--
-- PostgreSQL database dump
--


-- Dumped from database version 17.12 (Ubuntu 17.12-1.pgdg24.04+1)
-- Dumped by pg_dump version 17.12 (Ubuntu 17.12-1.pgdg24.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: bridge; Type: SCHEMA; Schema: -; Owner: bridge_owner
--

CREATE SCHEMA bridge;


ALTER SCHEMA bridge OWNER TO bridge_owner;

--
-- Name: ack_outbox(bigint, uuid, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 UPDATE bridge.outbox SET status='delivered', delivered_at=clock_timestamp(),
   lease_until=NULL, lease_token=NULL, lease_owner=NULL
 WHERE delivery_id=p_delivery_id AND status='leased' AND lease_token=p_lease_token
   AND lease_owner=p_consumer;
 RETURN FOUND;
END
$$;


ALTER FUNCTION bridge.ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text) OWNER TO bridge_owner;

--
-- Name: ag_ack_managed_message(text, bigint, bigint, uuid, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_ack_managed_message(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
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
END $$;


ALTER FUNCTION bridge.ag_ack_managed_message(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) OWNER TO bridge_owner;

--
-- Name: ag_ack_outbox(bigint, uuid, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS (SELECT 1 FROM bridge.outbox WHERE delivery_id=p_delivery_id AND recipient='agent') THEN RETURN false; END IF;
 RETURN bridge.ack_outbox(p_delivery_id,p_lease_token,p_consumer);
END $$;


ALTER FUNCTION bridge.ag_ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text) OWNER TO bridge_owner;

--
-- Name: ag_claim_managed_message(text, bigint, text, text, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_claim_managed_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer DEFAULT 3600) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE result jsonb;
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
 WHERE r.task_id=p_task_id AND r.dispatch_enabled AND d.state='active'
 AND d.next_actor='agent' FOR SHARE OF r;
 IF NOT FOUND THEN RAISE EXCEPTION 'managed_dialog_not_enabled' USING ERRCODE='42501'; END IF;
 result:=bridge.ag_claim_message(p_task_id,p_seq,p_message_id,p_consumer,p_lease_seconds);
 RETURN result;
END $$;


ALTER FUNCTION bridge.ag_claim_managed_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) OWNER TO bridge_owner;

--
-- Name: ag_claim_message(text, bigint, text, text, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_claim_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer DEFAULT 3600) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.claim_message(p_task_id,p_seq,p_message_id,p_consumer,p_lease_seconds);
END $$;


ALTER FUNCTION bridge.ag_claim_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) OWNER TO bridge_owner;

--
-- Name: ag_claim_outbox(text, integer, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_claim_outbox(p_consumer text, p_limit integer DEFAULT 10, p_lease_seconds integer DEFAULT 60) RETURNS TABLE(delivery_id bigint, task_id text, seq bigint, message_id text, sender text, kind text, body text, lease_token uuid, attempts integer)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT q.delivery_id,q.task_id,q.seq,q.message_id,q.sender,q.kind,q.body,q.lease_token,q.attempts
 FROM bridge.claim_outbox('agent',p_consumer,p_limit,p_lease_seconds) AS q;
END $$;


ALTER FUNCTION bridge.ag_claim_outbox(p_consumer text, p_limit integer, p_lease_seconds integer) OWNER TO bridge_owner;

--
-- Name: ag_dispatch_list(integer, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_dispatch_list(p_offset integer DEFAULT 0, p_limit integer DEFAULT 20) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
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
END $$;


ALTER FUNCTION bridge.ag_dispatch_list(p_offset integer, p_limit integer) OWNER TO bridge_owner;

--
-- Name: ag_managed_delivery_status(text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_managed_delivery_status(p_task_id text, p_seq bigint) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_seq IS NULL OR p_seq<1 OR NOT EXISTS(
  SELECT 1 FROM bridge.managed_dialogs r JOIN bridge.messages m ON m.task_id=r.task_id
  JOIN bridge.outbox o ON o.task_id=m.task_id AND o.seq=m.seq
  WHERE r.task_id=p_task_id AND r.activated_at IS NOT NULL AND m.seq=p_seq
   AND m.sender='chatgpt' AND m.recipient='agent' AND m.kind='task' AND o.recipient='agent') THEN
  RAISE EXCEPTION 'not_managed_task_delivery' USING ERRCODE='42501'; END IF;
 RETURN bridge.delivery_status(p_task_id,p_seq);
END $$;


ALTER FUNCTION bridge.ag_managed_delivery_status(p_task_id text, p_seq bigint) OWNER TO bridge_owner;

--
-- Name: ag_managed_report_status(text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_managed_report_status(p_task_id text, p_message_id text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
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
END $$;


ALTER FUNCTION bridge.ag_managed_report_status(p_task_id text, p_message_id text) OWNER TO bridge_owner;

--
-- Name: ag_read_managed_message(text, bigint, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_read_managed_message(p_task_id text, p_seq bigint, p_message_id text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
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
END $$;


ALTER FUNCTION bridge.ag_read_managed_message(p_task_id text, p_seq bigint, p_message_id text) OWNER TO bridge_owner;

--
-- Name: ag_release_outbox(bigint, uuid, text, integer, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text, p_retry_seconds integer DEFAULT 10, p_error text DEFAULT NULL::text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS (SELECT 1 FROM bridge.outbox WHERE delivery_id=p_delivery_id AND recipient='agent') THEN RETURN false; END IF;
 RETURN bridge.release_outbox(p_delivery_id,p_lease_token,p_consumer,p_retry_seconds,p_error);
END $$;


ALTER FUNCTION bridge.ag_release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text, p_retry_seconds integer, p_error text) OWNER TO bridge_owner;

--
-- Name: ag_send_bound_managed_report(text, text, text, bigint, bigint, bigint, uuid, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_send_bound_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint, p_task_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $_$
DECLARE d bridge.dialogs%ROWTYPE;
 t bridge.messages%ROWTYPE;
 m bridge.messages%ROWTYPE;
 o bridge.outbox%ROWTYPE;
 b bridge.managed_report_binding%ROWTYPE;
 n bigint; suffix text:=''; want_id text; digest bytea; result jsonb;
BEGIN
 IF session_user::text<>'bridge_agent' THEN
   RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_task_id IS NULL OR p_task_id !~ '^task_[0-9a-f]{32}$'
 OR p_task_seq IS NULL OR p_task_seq NOT BETWEEN 1 AND 9007199254740990
 OR p_delivery_id IS NULL OR p_delivery_id<1 OR p_lease_token IS NULL
 OR p_expected_version IS NULL OR p_expected_version<0
 OR p_consumer IS NULL OR length(p_consumer) NOT BETWEEN 1 AND 128
 OR p_body IS NULL OR octet_length(p_body)>65536 THEN
   RAISE EXCEPTION 'invalid_bound_report' USING ERRCODE='P0001'; END IF;
 n:=p_task_seq;
 WHILE n>0 LOOP
   suffix:=substr('0123456789abcdefghijklmnopqrstuvwxyz',(n%36)::integer+1,1)||suffix;
   n:=n/36;
 END LOOP;
 want_id:='pgv2-mg-'||substr(p_task_id,6)||'-'||suffix||'-report';
 IF p_message_id IS DISTINCT FROM want_id THEN
   RAISE EXCEPTION 'bound_report_id_mismatch' USING ERRCODE='P0001'; END IF;
 digest:=sha256(convert_to(p_lease_token::text,'UTF8'));
 SELECT * INTO d FROM bridge.dialogs WHERE task_id=p_task_id FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM bridge.managed_dialogs WHERE task_id=p_task_id) THEN
   RAISE EXCEPTION 'unregistered_dialog' USING ERRCODE='42501'; END IF;
 SELECT * INTO t FROM bridge.messages WHERE task_id=p_task_id AND seq=p_task_seq;
 IF NOT FOUND OR t.sender<>'chatgpt' OR t.recipient<>'agent' OR t.kind<>'task' OR t.returned IS DISTINCT FROM false THEN
   RAISE EXCEPTION 'bound_task_mismatch' USING ERRCODE='P0001'; END IF;
 SELECT * INTO o FROM bridge.outbox WHERE task_id=p_task_id AND seq=p_task_seq
   AND recipient='agent' AND delivery_id=p_delivery_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'bound_delivery_mismatch' USING ERRCODE='P0001'; END IF;
 SELECT * INTO m FROM bridge.messages WHERE task_id=p_task_id AND message_id=p_message_id;
 IF FOUND THEN
   SELECT * INTO b FROM bridge.managed_report_binding WHERE task_id=p_task_id AND task_seq=p_task_seq;
   IF NOT FOUND OR b.report_seq<>m.seq OR b.delivery_id<>o.delivery_id OR b.attempt<>o.attempts
      OR b.lease_digest IS DISTINCT FROM digest OR b.consumer IS DISTINCT FROM p_consumer
      OR b.expected_version<>p_expected_version OR m.sender<>'agent' OR m.recipient<>'chatgpt'
      OR m.kind<>'report' OR m.returned IS DISTINCT FROM false OR m.body IS DISTINCT FROM p_body
      OR o.status NOT IN ('leased','delivered') THEN
     RAISE EXCEPTION 'bound_report_replay_mismatch' USING ERRCODE='P0001'; END IF;
   RETURN bridge.ag_send_message(p_task_id,p_message_id,'report',p_body,p_expected_version);
 END IF;
 IF d.state<>'active' OR d.next_actor<>'agent' OR d.last_seq<>p_task_seq
 OR d.version<>p_expected_version THEN
   RAISE EXCEPTION 'bound_report_stale_turn' USING ERRCODE='P0001'; END IF;
 IF o.status<>'leased' OR o.attempts<1 OR o.lease_token IS DISTINCT FROM p_lease_token
 OR o.lease_owner IS DISTINCT FROM p_consumer OR o.lease_until IS NULL
 OR o.lease_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'bound_report_claim_required' USING ERRCODE='P0001'; END IF;
 result:=bridge.ag_send_message(p_task_id,p_message_id,'report',p_body,p_expected_version);
 IF result->>'created' IS DISTINCT FROM 'true' OR (result->>'seq')::bigint IS DISTINCT FROM p_task_seq+1 THEN
   RAISE EXCEPTION 'bound_report_insert_mismatch' USING ERRCODE='P0001'; END IF;
 INSERT INTO bridge.managed_report_binding(task_id,task_seq,report_seq,delivery_id,attempt,lease_digest,consumer,expected_version)
 VALUES(p_task_id,p_task_seq,p_task_seq+1,p_delivery_id,o.attempts,digest,p_consumer,p_expected_version);
 RETURN result;
END $_$;


ALTER FUNCTION bridge.ag_send_bound_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint, p_task_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) OWNER TO bridge_owner;

--
-- Name: ag_send_managed_report(text, text, text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_send_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint DEFAULT NULL::bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM bridge.managed_dialogs r WHERE r.task_id=p_task_id) THEN
  RAISE EXCEPTION 'unregistered_dialog' USING ERRCODE='42501'; END IF;
 RETURN bridge.ag_send_message(p_task_id,p_message_id,'report',p_body,p_expected_version);
END $$;


ALTER FUNCTION bridge.ag_send_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint) OWNER TO bridge_owner;

--
-- Name: ag_send_message(text, text, text, text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_send_message(p_task_id text, p_message_id text, p_kind text, p_body text, p_expected_version bigint DEFAULT NULL::bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.send_message(p_task_id,p_message_id,'agent',p_kind,p_body,p_expected_version);
END $$;


ALTER FUNCTION bridge.ag_send_message(p_task_id text, p_message_id text, p_kind text, p_body text, p_expected_version bigint) OWNER TO bridge_owner;

--
-- Name: ag_view_managed_dialog(text, integer, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.ag_view_managed_dialog(p_task_id text, p_offset integer DEFAULT 0, p_limit integer DEFAULT 30) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text<>'bridge_agent' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_offset IS NULL OR p_offset<0 OR p_offset>100000 OR p_limit IS NULL OR p_limit<1 OR p_limit>50 THEN
  RAISE EXCEPTION 'invalid_page' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS(
  SELECT 1 FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
  WHERE r.task_id=p_task_id AND r.activated_at IS NOT NULL AND d.state='active') THEN
  RAISE EXCEPTION 'managed_dialog_not_activated' USING ERRCODE='42501'; END IF;
 RETURN bridge.view_dialog(p_task_id,p_offset,p_limit);
END $$;


ALTER FUNCTION bridge.ag_view_managed_dialog(p_task_id text, p_offset integer, p_limit integer) OWNER TO bridge_owner;

--
-- Name: cg_ack_managed_report(text, bigint, bigint, uuid, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_ack_managed_report(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
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
END $$;


ALTER FUNCTION bridge.cg_ack_managed_report(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) OWNER TO bridge_owner;

--
-- Name: cg_claim_managed_report(text, bigint, text, text, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_claim_managed_report(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer DEFAULT 3600) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
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
END $$;


ALTER FUNCTION bridge.cg_claim_managed_report(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) OWNER TO bridge_owner;

--
-- Name: cg_create_dialog(text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_create_dialog(p_request_id text, p_title text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.create_dialog(p_request_id,p_title,'chatgpt');
END $$;


ALTER FUNCTION bridge.cg_create_dialog(p_request_id text, p_title text) OWNER TO bridge_owner;

--
-- Name: cg_create_managed_dialog(text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_create_managed_dialog(p_request_id text, p_title text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $_$
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
END $_$;


ALTER FUNCTION bridge.cg_create_managed_dialog(p_request_id text, p_title text) OWNER TO bridge_owner;

--
-- Name: cg_list_managed_dialogs(integer, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_list_managed_dialogs(p_offset integer DEFAULT 0, p_limit integer DEFAULT 30) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
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
END $$;


ALTER FUNCTION bridge.cg_list_managed_dialogs(p_offset integer, p_limit integer) OWNER TO bridge_owner;

--
-- Name: cg_managed_creation_status(text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_managed_creation_status(p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE result jsonb;
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN
  RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501';
 END IF;
 result:=bridge.creation_status(p_request_id);
 IF result IS NULL OR NOT EXISTS(SELECT 1 FROM bridge.managed_dialogs r WHERE r.task_id=result->>'task_id') THEN
  RETURN jsonb_build_object('found',false);
 END IF;
 RETURN result;
END $$;


ALTER FUNCTION bridge.cg_managed_creation_status(p_request_id text) OWNER TO bridge_owner;

--
-- Name: cg_managed_delivery_status(text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_managed_delivery_status(p_task_id text, p_seq bigint) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN
  RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM bridge.managed_dialogs r WHERE r.task_id=p_task_id) THEN
  RAISE EXCEPTION 'unregistered_dialog' USING ERRCODE='42501';
 END IF;
 RETURN bridge.delivery_status(p_task_id,p_seq);
END $$;


ALTER FUNCTION bridge.cg_managed_delivery_status(p_task_id text, p_seq bigint) OWNER TO bridge_owner;

--
-- Name: cg_managed_detail(text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_managed_detail(p_task_id text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE result jsonb;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('task_id',d.task_id,'registered',true,'dispatch_enabled',r.dispatch_enabled,
 'policy_version',r.policy_version,'state',d.state,'next_actor',d.next_actor,
 'version',d.version,'last_seq',d.last_seq,'title',d.title)
 INTO result FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
 WHERE r.task_id=p_task_id;
 RETURN COALESCE(result,jsonb_build_object('task_id',p_task_id,'registered',false));
END $$;


ALTER FUNCTION bridge.cg_managed_detail(p_task_id text) OWNER TO bridge_owner;

--
-- Name: cg_managed_send_status(text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_managed_send_status(p_task_id text, p_message_id text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN
  RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM bridge.managed_dialogs r WHERE r.task_id=p_task_id) THEN
  RAISE EXCEPTION 'unregistered_dialog' USING ERRCODE='42501';
 END IF;
 RETURN bridge.send_status(p_task_id,p_message_id);
END $$;


ALTER FUNCTION bridge.cg_managed_send_status(p_task_id text, p_message_id text) OWNER TO bridge_owner;

--
-- Name: cg_managed_status(text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_managed_status(p_task_id text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE result jsonb;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('task_id',d.task_id,'registered',true,
     'dispatch_enabled',r.dispatch_enabled,'state',d.state,
     'next_actor',d.next_actor,'version',d.version,'last_seq',d.last_seq)
 INTO result FROM bridge.managed_dialogs r JOIN bridge.dialogs d ON d.task_id=r.task_id
 WHERE d.task_id=p_task_id;
 RETURN COALESCE(result,jsonb_build_object('task_id',p_task_id,'registered',false));
END $$;


ALTER FUNCTION bridge.cg_managed_status(p_task_id text) OWNER TO bridge_owner;

--
-- Name: cg_return_turn(text, text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_return_turn(p_task_id text, p_message_id text, p_expected_version bigint DEFAULT NULL::bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.return_turn(p_task_id,p_message_id,'chatgpt',p_expected_version);
END $$;


ALTER FUNCTION bridge.cg_return_turn(p_task_id text, p_message_id text, p_expected_version bigint) OWNER TO bridge_owner;

--
-- Name: cg_send_managed_task(text, text, text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_send_managed_task(p_task_id text, p_message_id text, p_body text, p_expected_version bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE allowed boolean;
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_expected_version IS NULL OR p_expected_version<0 THEN
  RAISE EXCEPTION 'expected_version_required' USING ERRCODE='P0001'; END IF;
 SELECT dispatch_enabled INTO allowed FROM bridge.managed_dialogs WHERE task_id=p_task_id FOR SHARE;
 IF NOT FOUND OR allowed IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'managed_dispatch_disabled' USING ERRCODE='42501'; END IF;
 RETURN bridge.cg_send_message(p_task_id,p_message_id,'task',p_body,p_expected_version);
END $$;


ALTER FUNCTION bridge.cg_send_managed_task(p_task_id text, p_message_id text, p_body text, p_expected_version bigint) OWNER TO bridge_owner;

--
-- Name: cg_send_message(text, text, text, text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_send_message(p_task_id text, p_message_id text, p_kind text, p_body text, p_expected_version bigint DEFAULT NULL::bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.send_message(p_task_id,p_message_id,'chatgpt',p_kind,p_body,p_expected_version);
END $$;


ALTER FUNCTION bridge.cg_send_message(p_task_id text, p_message_id text, p_kind text, p_body text, p_expected_version bigint) OWNER TO bridge_owner;

--
-- Name: cg_set_managed_dispatch(text, boolean, bigint, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_set_managed_dispatch(p_task_id text, p_enabled boolean, p_expected_policy_version bigint, p_expected_dialog_version bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
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
END $$;


ALTER FUNCTION bridge.cg_set_managed_dispatch(p_task_id text, p_enabled boolean, p_expected_policy_version bigint, p_expected_dialog_version bigint) OWNER TO bridge_owner;

--
-- Name: cg_tombstone_dialog(text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_tombstone_dialog(p_task_id text, p_expected_version bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text <> 'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 RETURN bridge.tombstone_dialog(p_task_id,p_expected_version);
END $$;


ALTER FUNCTION bridge.cg_tombstone_dialog(p_task_id text, p_expected_version bigint) OWNER TO bridge_owner;

--
-- Name: cg_view_managed_dialog(text, integer, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.cg_view_managed_dialog(p_task_id text, p_offset integer DEFAULT 0, p_limit integer DEFAULT 30) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF session_user::text<>'bridge_chatgpt' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM bridge.managed_dialogs WHERE task_id=p_task_id) THEN
  RAISE EXCEPTION 'not_managed' USING ERRCODE='42501'; END IF;
 RETURN bridge.view_dialog(p_task_id,p_offset,p_limit);
END $$;


ALTER FUNCTION bridge.cg_view_managed_dialog(p_task_id text, p_offset integer, p_limit integer) OWNER TO bridge_owner;

--
-- Name: claim_message(text, bigint, text, text, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.claim_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer DEFAULT 3600) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE o bridge.outbox%ROWTYPE;
BEGIN
 IF p_task_id IS NULL OR p_message_id IS NULL OR p_consumer IS NULL
    OR p_seq IS NULL OR p_lease_seconds IS NULL
    OR length(p_consumer) NOT BETWEEN 1 AND 128 OR p_seq < 1
    OR p_lease_seconds NOT BETWEEN 30 AND 7200 THEN
   RAISE EXCEPTION 'invalid_request' USING ERRCODE='P0001'; END IF;
 SELECT x.* INTO o FROM bridge.outbox x JOIN bridge.messages m
   ON (m.task_id=x.task_id AND m.seq=x.seq) JOIN bridge.dialogs d ON d.task_id=x.task_id
 WHERE x.task_id=p_task_id AND x.seq=p_seq AND x.recipient='agent'
   AND m.message_id=p_message_id AND m.returned=false AND d.state='active'
   AND d.next_actor='agent' AND x.status='pending' AND x.available_at <= clock_timestamp()
 FOR UPDATE OF x;
 IF NOT FOUND THEN RETURN jsonb_build_object('claimed',false); END IF;
 UPDATE bridge.outbox SET status='leased', attempts=attempts+1,
   lease_token=pg_catalog.gen_random_uuid(), lease_owner=p_consumer,
   lease_until=clock_timestamp() + make_interval(secs=>p_lease_seconds)
 WHERE delivery_id=o.delivery_id RETURNING * INTO o;
 RETURN jsonb_build_object('claimed',true,'delivery_id',o.delivery_id,
  'lease_token',o.lease_token,'lease_owner',o.lease_owner);
END
$$;


ALTER FUNCTION bridge.claim_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) OWNER TO bridge_owner;

--
-- Name: claim_outbox(text, text, integer, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.claim_outbox(p_recipient text, p_consumer text, p_limit integer DEFAULT 10, p_lease_seconds integer DEFAULT 60) RETURNS TABLE(delivery_id bigint, task_id text, seq bigint, message_id text, sender text, kind text, body text, lease_token uuid, attempts integer)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF p_recipient NOT IN ('chatgpt','agent') OR p_recipient IS NULL
    OR p_consumer IS NULL OR length(p_consumer) NOT BETWEEN 1 AND 128
    OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50
    OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 10 AND 3600 THEN
   RAISE EXCEPTION 'invalid_request' USING ERRCODE = 'P0001';
 END IF;
 RETURN QUERY WITH picked AS (
   SELECT o.delivery_id FROM bridge.outbox o
   WHERE o.recipient = p_recipient
     AND o.available_at <= clock_timestamp()
     AND (o.status = 'pending' OR (o.status = 'leased' AND o.lease_until < clock_timestamp()))
   ORDER BY o.delivery_id LIMIT p_limit FOR UPDATE SKIP LOCKED
 ), leased AS (
   UPDATE bridge.outbox o SET status = 'leased', attempts = o.attempts + 1,
     lease_token = pg_catalog.gen_random_uuid(), lease_owner = p_consumer,
     lease_until = clock_timestamp() + make_interval(secs => p_lease_seconds)
   FROM picked p WHERE o.delivery_id = p.delivery_id
   RETURNING o.*
 )
 SELECT l.delivery_id, l.task_id, l.seq, m.message_id, m.sender,
        m.kind, m.body, l.lease_token, l.attempts
 FROM leased l JOIN bridge.messages m ON (m.task_id = l.task_id AND m.seq = l.seq)
 ORDER BY l.delivery_id;
END
$$;


ALTER FUNCTION bridge.claim_outbox(p_recipient text, p_consumer text, p_limit integer, p_lease_seconds integer) OWNER TO bridge_owner;

--
-- Name: create_dialog(text, text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.create_dialog(p_request_id text, p_title text, p_initiator text DEFAULT 'chatgpt'::text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE d bridge.dialogs%ROWTYPE; inserted boolean := false;
BEGIN
  IF length(p_request_id) NOT BETWEEN 1 AND 160 OR length(p_title) NOT BETWEEN 1 AND 4096
     OR p_initiator NOT IN ('chatgpt','agent') OR p_request_id IS NULL OR p_title IS NULL OR p_initiator IS NULL THEN
    RAISE EXCEPTION 'invalid_request' USING ERRCODE = 'P0001';
  END IF;
  INSERT INTO bridge.dialogs(task_id, request_id, title, initiator, next_actor)
  VALUES ('task_' || replace(pg_catalog.gen_random_uuid()::text, '-', ''), p_request_id, p_title, p_initiator, p_initiator)
  ON CONFLICT (request_id) DO NOTHING RETURNING * INTO d;
  inserted := FOUND;
  IF NOT inserted THEN
    SELECT * INTO d FROM bridge.dialogs WHERE request_id = p_request_id;
    IF d.title IS DISTINCT FROM p_title OR d.initiator IS DISTINCT FROM p_initiator THEN
      RAISE EXCEPTION 'request_id_conflict' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN jsonb_build_object('task_id', d.task_id, 'request_id', d.request_id,
    'title', d.title, 'initiator', d.initiator, 'next_actor', d.next_actor,
    'version', d.version, 'created', inserted, 'state', d.state);
END
$$;


ALTER FUNCTION bridge.create_dialog(p_request_id text, p_title text, p_initiator text) OWNER TO bridge_owner;

--
-- Name: creation_status(text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.creation_status(p_request_id text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
 SELECT (SELECT jsonb_build_object('request_id', request_id, 'task_id', task_id,
   'title', title, 'initiator', initiator, 'next_actor', next_actor,
   'version', version, 'state', state) FROM bridge.dialogs WHERE request_id = p_request_id);
$$;


ALTER FUNCTION bridge.creation_status(p_request_id text) OWNER TO bridge_owner;

--
-- Name: delivery_status(text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.delivery_status(p_task_id text, p_seq bigint) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
 SELECT (SELECT jsonb_build_object('delivery_id',delivery_id,'status',status,
   'attempts',attempts,'delivered_at',delivered_at)
 FROM bridge.outbox WHERE task_id=p_task_id AND seq=p_seq);
$$;


ALTER FUNCTION bridge.delivery_status(p_task_id text, p_seq bigint) OWNER TO bridge_owner;

--
-- Name: dialog_metadata(text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.dialog_metadata(p_task_id text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
 SELECT (SELECT jsonb_build_object('task_id', task_id, 'title', title,
   'initiator', initiator, 'next_actor', next_actor, 'state', state,
   'last_seq', last_seq, 'version', version,
   'created_at', created_at, 'updated_at', updated_at)
 FROM bridge.dialogs WHERE task_id = p_task_id);
$$;


ALTER FUNCTION bridge.dialog_metadata(p_task_id text) OWNER TO bridge_owner;

--
-- Name: enroll_dialog_v64(text, text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.enroll_dialog_v64(p_initiator text, p_request_id text, p_title text) RETURNS jsonb
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $_$
DECLARE k text; v jsonb; tid text; enabled boolean;
BEGIN
 IF current_user::text <> 'bridge_owner' THEN
   RAISE EXCEPTION 'enrollment_owner_required' USING ERRCODE='42501';
 END IF;
 IF p_initiator IS NULL OR p_initiator NOT IN ('chatgpt','agent')
    OR p_request_id IS NULL OR p_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
    OR p_title IS NULL OR length(btrim(p_title))=0 OR length(p_title)>160
    OR octet_length(p_title)>640 OR p_title ~ '[[:cntrl:]]'
 THEN RAISE EXCEPTION 'invalid_enrollment' USING ERRCODE='P0001'; END IF;
 -- Delimiter cannot occur in old managed API request ids.
 k := 'v64:' || p_initiator || ':' || p_request_id;
 v := bridge.create_dialog(k,p_title,p_initiator);
 tid := v->>'task_id';
 IF v->>'request_id' IS DISTINCT FROM k OR v->>'title' IS DISTINCT FROM p_title
    OR v->>'initiator' IS DISTINCT FROM p_initiator
    OR tid IS NULL OR tid !~ '^task_[0-9a-f]{32}$'
 THEN RAISE EXCEPTION 'enrollment_binding_conflict' USING ERRCODE='P0001'; END IF;
 IF v->>'state' IS DISTINCT FROM 'active' THEN
    RAISE EXCEPTION 'enrollment_retired' USING ERRCODE='P0001';
 END IF;
 IF v->>'created'='true' THEN
    INSERT INTO bridge.managed_dialogs(task_id,request_id,dispatch_enabled)
      VALUES(tid,k,false);
 END IF;
 SELECT r.dispatch_enabled INTO enabled FROM bridge.managed_dialogs r
   WHERE r.task_id=tid AND r.request_id=k;
 IF NOT FOUND THEN RAISE EXCEPTION 'enrollment_unmanaged_collision' USING ERRCODE='P0001'; END IF;
 RETURN v || jsonb_build_object('request_id',p_request_id,'managed',true,'dispatch_enabled',enabled);
END $_$;


ALTER FUNCTION bridge.enroll_dialog_v64(p_initiator text, p_request_id text, p_title text) OWNER TO bridge_owner;

--
-- Name: enrollment_opening_note_v64(text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.enrollment_opening_note_v64(p_task_id text, p_note text) RETURNS jsonb
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $_$
DECLARE r bridge.managed_dialogs%ROWTYPE; d bridge.dialogs%ROWTYPE;
 n bridge.enrollment_note_v64%ROWTYPE;
BEGIN
 IF current_user::text <> 'bridge_owner' THEN
  RAISE EXCEPTION 'enrollment_owner_required' USING ERRCODE='42501';
 END IF;
 IF p_task_id IS NULL OR p_task_id !~ '^task_[0-9a-f]{32}$'
 OR p_note IS NULL OR length(btrim(p_note))=0 OR octet_length(p_note)>4096
 THEN RAISE EXCEPTION 'invalid_opening_note' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM bridge.managed_dialogs WHERE task_id=p_task_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'opening_not_registered' USING ERRCODE='P0001'; END IF;
 SELECT * INTO d FROM bridge.dialogs WHERE task_id=p_task_id FOR UPDATE;
 IF NOT FOUND OR d.state<>'active' OR d.initiator<>'agent'
 OR left(d.request_id,10)<>'v64:agent:'
 OR r.request_id IS DISTINCT FROM d.request_id
 THEN RAISE EXCEPTION 'opening_not_eligible' USING ERRCODE='P0001'; END IF;
 SELECT * INTO n FROM bridge.enrollment_note_v64 WHERE task_id=p_task_id;
 IF FOUND THEN
  IF n.note IS DISTINCT FROM p_note THEN
    RAISE EXCEPTION 'opening_note_conflict' USING ERRCODE='P0001';
  END IF;
  RETURN jsonb_build_object('task_id',p_task_id,'created',false,'handed_off_version',n.handed_off_version);
 END IF;
 IF r.dispatch_enabled OR r.policy_version<>0 OR d.version<>0 OR d.last_seq<>0
 OR d.next_actor<>'agent'
 OR EXISTS(SELECT 1 FROM bridge.messages WHERE task_id=p_task_id)
 OR EXISTS(SELECT 1 FROM bridge.outbox WHERE task_id=p_task_id)
 THEN RAISE EXCEPTION 'opening_not_pristine' USING ERRCODE='P0001'; END IF;
 INSERT INTO bridge.enrollment_note_v64(task_id,note,handed_off_version)
 VALUES(p_task_id,p_note,1);
 UPDATE bridge.dialogs SET next_actor='chatgpt',version=1,updated_at=clock_timestamp()
 WHERE task_id=p_task_id AND state='active' AND version=0 AND last_seq=0 AND next_actor='agent';
 IF NOT FOUND THEN RAISE EXCEPTION 'opening_stale' USING ERRCODE='P0001'; END IF;
 RETURN jsonb_build_object('task_id',p_task_id,'created',true,'handed_off_version',1);
END $_$;


ALTER FUNCTION bridge.enrollment_opening_note_v64(p_task_id text, p_note text) OWNER TO bridge_owner;

--
-- Name: enrollment_status_v64(text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.enrollment_status_v64(p_initiator text, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $_$
DECLARE k text; v jsonb; enabled boolean;
BEGIN
 IF current_user::text <> 'bridge_owner' THEN
   RAISE EXCEPTION 'enrollment_owner_required' USING ERRCODE='42501';
 END IF;
 IF p_initiator IS NULL OR p_initiator NOT IN ('chatgpt','agent')
    OR p_request_id IS NULL OR p_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
 THEN RAISE EXCEPTION 'invalid_enrollment' USING ERRCODE='P0001'; END IF;
 k := 'v64:' || p_initiator || ':' || p_request_id;
 v := bridge.creation_status(k);
 IF v IS NULL THEN RETURN jsonb_build_object('found',false); END IF;
 IF v->>'initiator' IS DISTINCT FROM p_initiator OR v->>'request_id' IS DISTINCT FROM k THEN
   RAISE EXCEPTION 'enrollment_binding_conflict' USING ERRCODE='P0001';
 END IF;
 SELECT r.dispatch_enabled INTO enabled FROM bridge.managed_dialogs r
  WHERE r.task_id=v->>'task_id' AND r.request_id=k;
 IF NOT FOUND THEN RETURN jsonb_build_object('found',false); END IF;
 RETURN v || jsonb_build_object('request_id',p_request_id,'found',true,'dispatch_enabled',enabled);
END $_$;


ALTER FUNCTION bridge.enrollment_status_v64(p_initiator text, p_request_id text) OWNER TO bridge_owner;

--
-- Name: get_message(text, bigint, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.get_message(p_task_id text, p_seq bigint, p_message_id text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
 SELECT (SELECT to_jsonb(m) FROM (SELECT task_id,seq,message_id,sender,recipient,
 kind,body,returned,created_at FROM bridge.messages WHERE task_id=p_task_id AND
 seq=p_seq AND message_id=p_message_id) m);
$$;


ALTER FUNCTION bridge.get_message(p_task_id text, p_seq bigint, p_message_id text) OWNER TO bridge_owner;

--
-- Name: hand_back_turn(text, bigint); Type: FUNCTION; Schema: bridge; Owner: postgres
--

CREATE FUNCTION bridge.hand_back_turn(p_task_id text, p_expected_version bigint DEFAULT NULL::bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE d bridge.dialogs%ROWTYPE; m bridge.messages%ROWTYPE;
BEGIN
  IF p_task_id IS NULL THEN
    RAISE EXCEPTION 'invalid_request' USING ERRCODE = 'P0001';
  END IF;
  SELECT * INTO d FROM bridge.dialogs WHERE task_id = p_task_id FOR UPDATE;
  IF NOT FOUND OR d.state <> 'active' THEN
    RAISE EXCEPTION 'dialog_not_found' USING ERRCODE = 'P0001';
  END IF;
  IF p_expected_version IS NOT NULL AND d.version <> p_expected_version THEN
    RAISE EXCEPTION 'version_conflict' USING ERRCODE = 'P0001';
  END IF;
  IF d.next_actor = 'agent' THEN
    RETURN jsonb_build_object('task_id', p_task_id, 'version', d.version,
                              'next_actor', 'agent', 'changed', false);
  END IF;
  SELECT * INTO m FROM bridge.messages WHERE task_id = p_task_id AND seq = d.last_seq;
  IF NOT FOUND OR m.sender <> 'agent' OR m.returned THEN
    RAISE EXCEPTION 'hand_back_not_allowed' USING ERRCODE = 'P0001';
  END IF;
  UPDATE bridge.dialogs SET next_actor = 'agent', version = version + 1,
    updated_at = clock_timestamp() WHERE task_id = p_task_id;
  RETURN jsonb_build_object('task_id', p_task_id, 'version', d.version + 1,
                            'next_actor', 'agent', 'changed', true);
END
$$;


ALTER FUNCTION bridge.hand_back_turn(p_task_id text, p_expected_version bigint) OWNER TO postgres;

--
-- Name: health(); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.health() RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
 SELECT CASE WHEN session_user::text IN ('bridge_chatgpt','bridge_agent') THEN
   jsonb_build_object('ok',true,'db',current_database(),
     'dialogs',(SELECT count(*) FROM bridge.dialogs d JOIN bridge.managed_dialogs r USING(task_id)
                 WHERE d.state='active' AND (session_user::text='bridge_chatgpt' OR r.dispatch_enabled)),
     'messages',(SELECT count(*) FROM bridge.messages m JOIN bridge.managed_dialogs r USING(task_id)
                  WHERE session_user::text='bridge_chatgpt' OR r.dispatch_enabled),
     'pending',(SELECT count(*) FROM bridge.outbox o JOIN bridge.managed_dialogs r USING(task_id)
                 WHERE o.status='pending' AND (session_user::text='bridge_chatgpt' OR r.dispatch_enabled)),
     'leased',(SELECT count(*) FROM bridge.outbox o JOIN bridge.managed_dialogs r USING(task_id)
                 WHERE o.status='leased' AND (session_user::text='bridge_chatgpt' OR r.dispatch_enabled)))
 ELSE
   jsonb_build_object('ok',true,'db',current_database(),
     'dialogs',(SELECT count(*) FROM bridge.dialogs WHERE state='active'),
     'messages',(SELECT count(*) FROM bridge.messages),
     'pending',(SELECT count(*) FROM bridge.outbox WHERE status='pending'),
     'leased',(SELECT count(*) FROM bridge.outbox WHERE status='leased'))
 END;
 $$;


ALTER FUNCTION bridge.health() OWNER TO bridge_owner;

--
-- Name: list_dialogs(integer, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.list_dialogs(p_offset integer DEFAULT 0, p_limit integer DEFAULT 30) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
SELECT COALESCE(jsonb_agg(to_jsonb(d) - 'request_id' ORDER BY d.updated_at DESC, d.task_id), '[]'::jsonb)
FROM (SELECT task_id, title, initiator, next_actor, state, last_seq, version, created_at, updated_at
      FROM bridge.dialogs WHERE state = 'active'
      ORDER BY updated_at DESC, task_id LIMIT LEAST(GREATEST(p_limit, 1), 200)
      OFFSET LEAST(GREATEST(p_offset,0), 1000000)) d;
$$;


ALTER FUNCTION bridge.list_dialogs(p_offset integer, p_limit integer) OWNER TO bridge_owner;

--
-- Name: metadata_policy_allowed_v65m(text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.metadata_policy_allowed_v65m(p_operation text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 PERFORM bridge.require_metadata_policy_v65k(p_operation);
 RETURN jsonb_build_object('allowed',true);
EXCEPTION WHEN insufficient_privilege THEN
 RETURN jsonb_build_object('allowed',false);
END $$;


ALTER FUNCTION bridge.metadata_policy_allowed_v65m(p_operation text) OWNER TO bridge_owner;

--
-- Name: owner_enrollment_v64(text, text, text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.owner_enrollment_v64(p_initiator text, p_request_id text, p_title text, p_note text) RETURNS jsonb
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE v jsonb; n jsonb; tid text;
BEGIN
 IF current_user::text <> 'bridge_owner' THEN
  RAISE EXCEPTION 'enrollment_owner_required' USING ERRCODE='42501';
 END IF;
 IF p_initiator IS NULL OR p_initiator NOT IN ('chatgpt','agent')
 OR (p_initiator='chatgpt' AND p_note IS NOT NULL)
 OR (p_initiator='agent' AND (p_note IS NULL OR length(btrim(p_note))=0 OR octet_length(p_note)>4096))
 THEN RAISE EXCEPTION 'invalid_enrollment_note' USING ERRCODE='P0001'; END IF;
 v:=bridge.enroll_dialog_v64(p_initiator,p_request_id,p_title);
 tid:=v->>'task_id';
 IF p_initiator='agent' THEN
  n:=bridge.enrollment_opening_note_v64(tid,p_note);
 END IF;
 RETURN bridge.enrollment_status_v64(p_initiator,p_request_id)
  || jsonb_build_object('created',v->>'created'='true','opening_note_present',p_initiator='agent');
END $$;


ALTER FUNCTION bridge.owner_enrollment_v64(p_initiator text, p_request_id text, p_title text, p_note text) OWNER TO bridge_owner;

--
-- Name: owner_enrollment_view_v64(text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.owner_enrollment_view_v64(p_initiator text, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE v jsonb; n text;
BEGIN
 IF current_user::text <> 'bridge_owner' THEN
  RAISE EXCEPTION 'enrollment_owner_required' USING ERRCODE='42501';
 END IF;
 v:=bridge.enrollment_status_v64(p_initiator,p_request_id);
 IF v->>'found' IS DISTINCT FROM 'true' THEN RETURN v; END IF;
 SELECT note INTO n FROM bridge.enrollment_note_v64 WHERE task_id=v->>'task_id';
 RETURN v || jsonb_build_object('opening_note',n);
END $$;


ALTER FUNCTION bridge.owner_enrollment_view_v64(p_initiator text, p_request_id text) OWNER TO bridge_owner;

--
-- Name: register_metadata_v65b(text, text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.register_metadata_v65b(p_request_id text, p_title text, p_note text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $_$
DECLARE origin text; v jsonb;
BEGIN
 CASE session_user::text
 WHEN 'bridge_chatgpt' THEN origin:='chatgpt';
 WHEN 'bridge_agent' THEN origin:='agent';
 ELSE RAISE EXCEPTION 'registration_client_required' USING ERRCODE='42501';
 END CASE;
 PERFORM bridge.require_metadata_policy_v65k('create');
 IF p_request_id IS NULL OR length(p_request_id) NOT BETWEEN 1 AND 64
 OR p_request_id !~ '^[A-Za-z0-9]' OR p_request_id ~ '[^A-Za-z0-9._-]'
 OR p_title IS NULL OR length(p_title) NOT BETWEEN 1 AND 160
 OR octet_length(p_title)>640 OR p_title ~ '[[:cntrl:]]'
 OR p_title !~ '[^[:space:]]'
 OR (origin='chatgpt' AND p_note IS NOT NULL)
 OR (origin='agent' AND (p_note IS NULL OR octet_length(p_note) NOT BETWEEN 1 AND 4096
     OR p_note !~ '[^[:space:]]'))
 THEN RAISE EXCEPTION 'invalid_registration' USING ERRCODE='P0001'; END IF;
 v:=bridge.owner_enrollment_v64(origin,p_request_id,p_title,p_note);
 IF v->>'found' IS DISTINCT FROM 'true'
 OR v->>'request_id' IS DISTINCT FROM p_request_id
 OR v->>'initiator' IS DISTINCT FROM origin
 OR v->>'title' IS DISTINCT FROM p_title
 OR v->>'state' IS DISTINCT FROM 'active'
 OR v->>'task_id' IS NULL OR v->>'task_id' !~ '^task_[0-9a-f]{32}$'
 OR jsonb_typeof(v->'dispatch_enabled') IS DISTINCT FROM 'boolean'
 OR jsonb_typeof(v->'created') IS DISTINCT FROM 'boolean'
 OR (v->>'created'='true' AND v->>'dispatch_enabled' IS DISTINCT FROM 'false')
 THEN RAISE EXCEPTION 'registration_receipt_invalid' USING ERRCODE='P0001'; END IF;
 RETURN jsonb_build_object('request_id',p_request_id,'initiator',origin,
 'task_id',v->>'task_id','title',p_title,'created',v->'created',
 'dispatch_enabled',v->'dispatch_enabled','state',v->>'state',
 'opening_note_present',v->'opening_note_present');
END $_$;


ALTER FUNCTION bridge.register_metadata_v65b(p_request_id text, p_title text, p_note text) OWNER TO bridge_owner;

--
-- Name: registration_metadata_status_v65b(text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.registration_metadata_status_v65b(p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $_$
DECLARE origin text; v jsonb;
BEGIN
 CASE session_user::text
 WHEN 'bridge_chatgpt' THEN origin:='chatgpt';
 WHEN 'bridge_agent' THEN origin:='agent';
 ELSE RAISE EXCEPTION 'registration_client_required' USING ERRCODE='42501';
 END CASE;
 PERFORM bridge.require_metadata_policy_v65k('status');
 IF p_request_id IS NULL OR length(p_request_id) NOT BETWEEN 1 AND 64
 OR p_request_id !~ '^[A-Za-z0-9]' OR p_request_id ~ '[^A-Za-z0-9._-]'
 THEN RAISE EXCEPTION 'invalid_registration' USING ERRCODE='P0001'; END IF;
 v:=bridge.enrollment_status_v64(origin,p_request_id);
 IF v->>'found' IS DISTINCT FROM 'true' THEN
   RETURN jsonb_build_object('found',false);
 END IF;
 IF v->>'initiator' IS DISTINCT FROM origin
 OR v->>'request_id' IS DISTINCT FROM p_request_id
 OR v->>'task_id' IS NULL OR v->>'task_id' !~ '^task_[0-9a-f]{32}$'
 OR jsonb_typeof(v->'dispatch_enabled') IS DISTINCT FROM 'boolean'
 THEN RAISE EXCEPTION 'registration_receipt_invalid' USING ERRCODE='P0001'; END IF;
 RETURN jsonb_build_object('found',true,'request_id',p_request_id,
 'initiator',origin,'task_id',v->>'task_id','title',v->>'title',
 'state',v->>'state','dispatch_enabled',v->'dispatch_enabled');
END $_$;


ALTER FUNCTION bridge.registration_metadata_status_v65b(p_request_id text) OWNER TO bridge_owner;

--
-- Name: release_outbox(bigint, uuid, text, integer, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text, p_retry_seconds integer DEFAULT 10, p_error text DEFAULT NULL::text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
BEGIN
 IF p_retry_seconds IS NULL OR p_retry_seconds NOT BETWEEN 1 AND 3600 THEN
   RAISE EXCEPTION 'invalid_request' USING ERRCODE='P0001';
 END IF;
 UPDATE bridge.outbox SET status='pending', available_at=clock_timestamp() + make_interval(secs => p_retry_seconds),
   lease_until=NULL, lease_token=NULL, lease_owner=NULL, last_error=left(p_error,512)
 WHERE delivery_id=p_delivery_id AND status='leased' AND lease_token=p_lease_token
   AND lease_owner=p_consumer;
 RETURN FOUND;
END
$$;


ALTER FUNCTION bridge.release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text, p_retry_seconds integer, p_error text) OWNER TO bridge_owner;

--
-- Name: require_metadata_policy_v65k(text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.require_metadata_policy_v65k(p_operation text) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE who text; now_s numeric;
BEGIN
 IF current_user::text <> 'bridge_owner'
 OR current_setting('transaction_isolation') <> 'read committed'
 THEN RAISE EXCEPTION 'registration_not_authorized' USING ERRCODE='42501'; END IF;
 CASE session_user::text
 WHEN 'bridge_chatgpt' THEN who:='chatgpt';
 WHEN 'bridge_agent' THEN who:='agent';
 ELSE RAISE EXCEPTION 'registration_not_authorized' USING ERRCODE='42501';
 END CASE;
 now_s:=extract(epoch from clock_timestamp());
 IF p_operation IS NULL OR p_operation NOT IN ('create','status')
 OR NOT EXISTS (
 SELECT 1 FROM bridge.metadata_policy_v65k p
 WHERE p.origin=who AND p.issued_at<=now_s
 AND (p.expires_at IS NULL OR p.expires_at>now_s)
 AND CASE p_operation WHEN 'create' THEN p.allow_create
                      WHEN 'status' THEN p.allow_status ELSE false END
 )
 THEN RAISE EXCEPTION 'registration_not_authorized' USING ERRCODE='42501'; END IF;
END $$;


ALTER FUNCTION bridge.require_metadata_policy_v65k(p_operation text) OWNER TO bridge_owner;

--
-- Name: return_turn(text, text, text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.return_turn(p_task_id text, p_message_id text, p_sender text, p_expected_version bigint DEFAULT NULL::bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE d bridge.dialogs%ROWTYPE; m bridge.messages%ROWTYPE; o bridge.outbox%ROWTYPE;
BEGIN
 SELECT * INTO d FROM bridge.dialogs WHERE task_id=p_task_id FOR UPDATE;
 IF NOT FOUND OR d.state <> 'active' THEN RAISE EXCEPTION 'dialog_not_found' USING ERRCODE='P0001'; END IF;
 IF p_expected_version IS NOT NULL AND d.version <> p_expected_version THEN
   RAISE EXCEPTION 'version_conflict' USING ERRCODE='P0001'; END IF;
 SELECT * INTO m FROM bridge.messages WHERE task_id=p_task_id AND message_id=p_message_id FOR UPDATE;
 IF NOT FOUND OR m.seq <> d.last_seq OR m.sender IS DISTINCT FROM p_sender OR m.returned
   OR d.next_actor = p_sender THEN
   RAISE EXCEPTION 'return_not_allowed' USING ERRCODE='P0001';
 END IF;
 SELECT * INTO o FROM bridge.outbox WHERE task_id=p_task_id AND seq=m.seq FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'outbox_not_found' USING ERRCODE='P0001'; END IF;
 IF o.status <> 'pending' THEN RAISE EXCEPTION 'delivery_already_started' USING ERRCODE='P0001'; END IF;
 UPDATE bridge.messages SET returned=true WHERE task_id=p_task_id AND seq=m.seq;
 UPDATE bridge.outbox SET status='cancelled' WHERE delivery_id=o.delivery_id;
 UPDATE bridge.dialogs SET next_actor=p_sender, version=version+1,
   updated_at=clock_timestamp() WHERE task_id=p_task_id;
 RETURN jsonb_build_object('task_id',p_task_id,'message_id',p_message_id,
  'returned',true,'next_actor',p_sender,'version',d.version+1);
END
$$;


ALTER FUNCTION bridge.return_turn(p_task_id text, p_message_id text, p_sender text, p_expected_version bigint) OWNER TO bridge_owner;

--
-- Name: send_message(text, text, text, text, text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.send_message(p_task_id text, p_message_id text, p_sender text, p_kind text, p_body text, p_expected_version bigint DEFAULT NULL::bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE d bridge.dialogs%ROWTYPE; m bridge.messages%ROWTYPE;
        next_a text; new_seq bigint;
BEGIN
  IF p_task_id IS NULL OR p_message_id IS NULL OR p_sender IS NULL OR p_kind IS NULL OR p_body IS NULL
    OR length(p_message_id) NOT BETWEEN 1 AND 160 OR length(p_kind) NOT BETWEEN 1 AND 64
    OR octet_length(p_body) > 65536 OR p_sender NOT IN ('chatgpt', 'agent') THEN
    RAISE EXCEPTION 'invalid_request' USING ERRCODE = 'P0001';
  END IF;
  SELECT * INTO d FROM bridge.dialogs WHERE task_id = p_task_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'dialog_not_found' USING ERRCODE = 'P0001'; END IF;
  SELECT * INTO m FROM bridge.messages WHERE task_id = p_task_id AND message_id = p_message_id;
  IF FOUND THEN
    IF m.sender IS DISTINCT FROM p_sender OR m.kind IS DISTINCT FROM p_kind OR m.body IS DISTINCT FROM p_body THEN
      RAISE EXCEPTION 'message_id_conflict' USING ERRCODE = 'P0001';
    END IF;
    RETURN jsonb_build_object('task_id', m.task_id, 'message_id', m.message_id,
      'seq', m.seq, 'sender', m.sender, 'recipient', m.recipient,
      'version', d.version, 'created', false, 'returned', m.returned);
  END IF;
  IF d.state <> 'active' THEN RAISE EXCEPTION 'dialog_not_active' USING ERRCODE = 'P0001'; END IF;
  IF p_expected_version IS NOT NULL AND d.version <> p_expected_version THEN
    RAISE EXCEPTION 'version_conflict' USING ERRCODE = 'P0001';
  END IF;
  IF d.next_actor <> p_sender THEN
    RAISE EXCEPTION 'wrong_turn' USING ERRCODE = 'P0001';
  END IF;
  next_a := CASE WHEN p_sender = 'chatgpt' THEN 'agent' ELSE 'chatgpt' END;
  new_seq := d.last_seq + 1;
  INSERT INTO bridge.messages(task_id, seq, message_id, sender, recipient, kind, body)
  VALUES (p_task_id, new_seq, p_message_id, p_sender, next_a, p_kind, p_body);
  UPDATE bridge.dialogs SET last_seq = new_seq, version = version + 1,
    next_actor = next_a, updated_at = clock_timestamp() WHERE task_id = p_task_id;
  INSERT INTO bridge.outbox(task_id, seq, recipient) VALUES (p_task_id, new_seq, next_a);
  RETURN jsonb_build_object('task_id', p_task_id, 'message_id', p_message_id,
    'seq', new_seq, 'sender', p_sender, 'recipient', next_a,
    'version', d.version + 1, 'created', true, 'returned', false);
END
$$;


ALTER FUNCTION bridge.send_message(p_task_id text, p_message_id text, p_sender text, p_kind text, p_body text, p_expected_version bigint) OWNER TO bridge_owner;

--
-- Name: send_status(text, text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.send_status(p_task_id text, p_message_id text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
SELECT COALESCE((SELECT jsonb_build_object('found', true, 'task_id', task_id, 'seq', seq,
                  'message_id', message_id, 'sender', sender, 'kind', kind, 'returned', returned)
                 FROM bridge.messages WHERE task_id = p_task_id AND message_id = p_message_id),
                jsonb_build_object('found',false));
$$;


ALTER FUNCTION bridge.send_status(p_task_id text, p_message_id text) OWNER TO bridge_owner;

--
-- Name: tb_enrollment_event_v64(text); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.tb_enrollment_event_v64(p_task_id text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $_$
DECLARE v jsonb;
BEGIN
 IF session_user::text <> 'bridge_app' THEN
  RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501';
 END IF;
 IF p_task_id IS NULL OR p_task_id !~ '^task_[0-9a-f]{32}$' THEN
  RAISE EXCEPTION 'invalid_task_id' USING ERRCODE='P0001';
 END IF;
 SELECT jsonb_build_object('task_id',n.task_id,'kind','agent_opening_note','note',n.note)
 INTO v FROM bridge.enrollment_note_v64 n
 JOIN bridge.dialogs d ON d.task_id=n.task_id
 JOIN bridge.managed_dialogs r ON r.task_id=n.task_id
 WHERE n.task_id=p_task_id AND d.state='active' AND d.initiator='agent'
 AND d.request_id=r.request_id AND left(d.request_id,10)='v64:agent:';
 RETURN v;
END $_$;


ALTER FUNCTION bridge.tb_enrollment_event_v64(p_task_id text) OWNER TO bridge_owner;

--
-- Name: tb_list_rows(integer, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.tb_list_rows(p_offset integer, p_limit integer) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE result jsonb;
BEGIN
 IF session_user::text <> 'bridge_app' THEN RAISE EXCEPTION 'actor_denied' USING ERRCODE='42501'; END IF;
 IF p_offset IS NULL OR p_offset NOT BETWEEN 0 AND 100000 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
 THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='P0001'; END IF;
WITH page AS MATERIALIZED (
 SELECT task_id,title,initiator,next_actor,last_seq,updated_at
 FROM bridge.dialogs
 WHERE state='active'
 ORDER BY updated_at DESC,task_id
 LIMIT p_limit+1 OFFSET p_offset
)
SELECT COALESCE(jsonb_agg(jsonb_build_object(
 'task_id',d.task_id,'title',d.title,'initiator',d.initiator,
 'next_actor',d.next_actor,'last_seq',d.last_seq,
 'latest',CASE WHEN m.seq IS NULL THEN NULL ELSE jsonb_build_object(
 'message_id',m.message_id,'seq',m.seq,'kind',m.kind,'sender',m.sender,
 'recipient',m.recipient,'returned',m.returned,'created_at',m.created_at) END,
 'delivery',o.status) ORDER BY d.updated_at DESC,d.task_id),'[]'::jsonb)
INTO result
FROM page d
LEFT JOIN bridge.messages m ON m.task_id=d.task_id AND m.seq=d.last_seq
LEFT JOIN bridge.outbox o ON o.task_id=m.task_id AND o.seq=m.seq AND o.recipient=m.recipient;
 RETURN result;
END $$;


ALTER FUNCTION bridge.tb_list_rows(p_offset integer, p_limit integer) OWNER TO bridge_owner;

--
-- Name: tombstone_dialog(text, bigint); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.tombstone_dialog(p_task_id text, p_version bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE d bridge.dialogs%ROWTYPE;
BEGIN
 IF p_task_id IS NULL OR p_version IS NULL THEN
   RAISE EXCEPTION 'invalid_request' USING ERRCODE='P0001';
 END IF;
 SELECT * INTO d FROM bridge.dialogs WHERE task_id = p_task_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'dialog_not_found' USING ERRCODE='P0001'; END IF;
 IF d.version <> p_version THEN RAISE EXCEPTION 'version_conflict' USING ERRCODE='P0001'; END IF;
 IF d.state = 'tombstoned' THEN
   RETURN jsonb_build_object('task_id',p_task_id,'deleted',true,'version',d.version);
 END IF;
 -- Serialize with claim paths that lock the outbox rather than the dialogue.
 PERFORM 1 FROM bridge.outbox WHERE task_id=p_task_id ORDER BY delivery_id FOR UPDATE;
 IF EXISTS (SELECT 1 FROM bridge.outbox WHERE task_id=p_task_id AND status='leased') THEN
   RAISE EXCEPTION 'dialog_has_active_delivery' USING ERRCODE='P0001';
 END IF;
 UPDATE bridge.dialogs SET state='tombstoned',version=version+1,
   updated_at=clock_timestamp() WHERE task_id=p_task_id;
 UPDATE bridge.outbox SET status='cancelled', lease_token=NULL,lease_owner=NULL,lease_until=NULL
 WHERE task_id=p_task_id AND status='pending';
 RETURN jsonb_build_object('task_id',p_task_id,'deleted',true,'version',d.version+1);
END
$$;


ALTER FUNCTION bridge.tombstone_dialog(p_task_id text, p_version bigint) OWNER TO bridge_owner;

--
-- Name: view_dialog(text, integer, integer); Type: FUNCTION; Schema: bridge; Owner: bridge_owner
--

CREATE FUNCTION bridge.view_dialog(p_task_id text, p_offset integer DEFAULT 0, p_limit integer DEFAULT 30) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bridge', 'pg_temp'
    AS $$
DECLARE d bridge.dialogs%ROWTYPE; entries jsonb;
BEGIN
  SELECT * INTO d FROM bridge.dialogs WHERE task_id = p_task_id;
  IF NOT FOUND OR d.state <> 'active' THEN RAISE EXCEPTION 'dialog_not_found' USING ERRCODE = 'P0001'; END IF;
  SELECT COALESCE(jsonb_agg(to_jsonb(m) ORDER BY m.seq DESC), '[]'::jsonb) INTO entries
  FROM (SELECT seq, message_id, sender, recipient, kind, body, returned, created_at
        FROM bridge.messages WHERE task_id = p_task_id ORDER BY seq DESC
        LIMIT LEAST(GREATEST(p_limit, 1), 200) OFFSET LEAST(GREATEST(p_offset,0),1000000)) m;
  RETURN jsonb_build_object('task_id', d.task_id, 'title', d.title, 'state', d.state,
    'next_actor', d.next_actor, 'last_seq', d.last_seq, 'version', d.version, 'messages', entries);
END
$$;


ALTER FUNCTION bridge.view_dialog(p_task_id text, p_offset integer, p_limit integer) OWNER TO bridge_owner;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: dialogs; Type: TABLE; Schema: bridge; Owner: bridge_owner
--

CREATE TABLE bridge.dialogs (
    task_id text NOT NULL,
    request_id text NOT NULL,
    title text NOT NULL,
    initiator text NOT NULL,
    next_actor text NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    last_seq bigint DEFAULT 0 NOT NULL,
    version bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT dialogs_initiator_check CHECK ((initiator = ANY (ARRAY['chatgpt'::text, 'agent'::text]))),
    CONSTRAINT dialogs_last_seq_check CHECK ((last_seq >= 0)),
    CONSTRAINT dialogs_next_actor_check CHECK ((next_actor = ANY (ARRAY['chatgpt'::text, 'agent'::text]))),
    CONSTRAINT dialogs_request_id_check CHECK (((length(request_id) >= 1) AND (length(request_id) <= 160))),
    CONSTRAINT dialogs_state_check CHECK ((state = ANY (ARRAY['active'::text, 'tombstoned'::text]))),
    CONSTRAINT dialogs_task_id_check CHECK ((task_id ~ '^task_[0-9a-f]{32}$'::text)),
    CONSTRAINT dialogs_title_check CHECK (((length(title) >= 1) AND (length(title) <= 4096))),
    CONSTRAINT dialogs_version_check CHECK ((version >= 0))
);


ALTER TABLE bridge.dialogs OWNER TO bridge_owner;

--
-- Name: enrollment_note_v64; Type: TABLE; Schema: bridge; Owner: bridge_owner
--

CREATE TABLE bridge.enrollment_note_v64 (
    task_id text NOT NULL,
    note text NOT NULL,
    handed_off_version bigint NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT enrollment_note_v64_handed_off_version_check CHECK ((handed_off_version = 1)),
    CONSTRAINT enrollment_note_v64_note_check CHECK (((octet_length(note) >= 1) AND (octet_length(note) <= 4096)))
);


ALTER TABLE bridge.enrollment_note_v64 OWNER TO bridge_owner;

--
-- Name: managed_dialogs; Type: TABLE; Schema: bridge; Owner: bridge_owner
--

CREATE TABLE bridge.managed_dialogs (
    task_id text NOT NULL,
    request_id text NOT NULL,
    dispatch_enabled boolean DEFAULT false NOT NULL,
    registered_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    activated_at timestamp with time zone,
    policy_version bigint DEFAULT 0 NOT NULL,
    CONSTRAINT managed_dialogs_check CHECK (((NOT dispatch_enabled) OR (activated_at IS NOT NULL))),
    CONSTRAINT managed_dialogs_policy_version_check CHECK ((policy_version >= 0)),
    CONSTRAINT managed_dialogs_request_id_check CHECK (((length(request_id) >= 1) AND (length(request_id) <= 160)))
);


ALTER TABLE bridge.managed_dialogs OWNER TO bridge_owner;

--
-- Name: managed_report_binding; Type: TABLE; Schema: bridge; Owner: bridge_owner
--

CREATE TABLE bridge.managed_report_binding (
    task_id text NOT NULL,
    task_seq bigint NOT NULL,
    report_seq bigint NOT NULL,
    delivery_id bigint NOT NULL,
    attempt integer NOT NULL,
    lease_digest bytea NOT NULL,
    consumer text NOT NULL,
    expected_version bigint NOT NULL,
    CONSTRAINT managed_report_binding_attempt_check CHECK ((attempt > 0)),
    CONSTRAINT managed_report_binding_check CHECK ((report_seq = (task_seq + 1))),
    CONSTRAINT managed_report_binding_lease_digest_check CHECK ((octet_length(lease_digest) = 32))
);


ALTER TABLE bridge.managed_report_binding OWNER TO bridge_owner;

--
-- Name: messages; Type: TABLE; Schema: bridge; Owner: bridge_owner
--

CREATE TABLE bridge.messages (
    task_id text NOT NULL,
    seq bigint NOT NULL,
    message_id text NOT NULL,
    sender text NOT NULL,
    recipient text NOT NULL,
    kind text NOT NULL,
    body text NOT NULL,
    returned boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT messages_body_check CHECK ((octet_length(body) <= 65536)),
    CONSTRAINT messages_check CHECK (((recipient = ANY (ARRAY['chatgpt'::text, 'agent'::text])) AND (recipient <> sender))),
    CONSTRAINT messages_kind_check CHECK (((length(kind) >= 1) AND (length(kind) <= 64))),
    CONSTRAINT messages_message_id_check CHECK (((length(message_id) >= 1) AND (length(message_id) <= 160))),
    CONSTRAINT messages_sender_check CHECK ((sender = ANY (ARRAY['chatgpt'::text, 'agent'::text]))),
    CONSTRAINT messages_seq_check CHECK ((seq >= 1))
);


ALTER TABLE bridge.messages OWNER TO bridge_owner;

--
-- Name: metadata_policy_v65k; Type: TABLE; Schema: bridge; Owner: bridge_owner
--

CREATE TABLE bridge.metadata_policy_v65k (
    origin text NOT NULL,
    issued_at bigint NOT NULL,
    expires_at bigint,
    allow_create boolean NOT NULL,
    allow_status boolean NOT NULL,
    CONSTRAINT metadata_policy_v65k_check CHECK (((expires_at > issued_at) AND (((expires_at)::numeric - (issued_at)::numeric) <= (86400)::numeric))),
    CONSTRAINT metadata_policy_v65k_check1 CHECK ((allow_create OR allow_status)),
    CONSTRAINT metadata_policy_v65k_issued_at_check CHECK ((issued_at >= 0)),
    CONSTRAINT metadata_policy_v65k_origin_check CHECK ((origin = ANY (ARRAY['chatgpt'::text, 'agent'::text])))
);


ALTER TABLE bridge.metadata_policy_v65k OWNER TO bridge_owner;

--
-- Name: COLUMN metadata_policy_v65k.expires_at; Type: COMMENT; Schema: bridge; Owner: bridge_owner
--

COMMENT ON COLUMN bridge.metadata_policy_v65k.expires_at IS 'NULL means no time expiry for metadata registration only; owner can revoke by deleting the policy.';


--
-- Name: outbox; Type: TABLE; Schema: bridge; Owner: bridge_owner
--

CREATE TABLE bridge.outbox (
    delivery_id bigint NOT NULL,
    task_id text NOT NULL,
    seq bigint NOT NULL,
    recipient text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    lease_until timestamp with time zone,
    lease_token uuid,
    lease_owner text,
    last_error text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    delivered_at timestamp with time zone,
    CONSTRAINT outbox_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT outbox_check CHECK (((status <> 'leased'::text) OR ((lease_until IS NOT NULL) AND (lease_token IS NOT NULL) AND (lease_owner IS NOT NULL)))),
    CONSTRAINT outbox_recipient_check CHECK ((recipient = ANY (ARRAY['chatgpt'::text, 'agent'::text]))),
    CONSTRAINT outbox_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'leased'::text, 'delivered'::text, 'cancelled'::text])))
);


ALTER TABLE bridge.outbox OWNER TO bridge_owner;

--
-- Name: outbox_delivery_id_seq; Type: SEQUENCE; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE bridge.outbox ALTER COLUMN delivery_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME bridge.outbox_delivery_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dialogs dialogs_pkey; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.dialogs
    ADD CONSTRAINT dialogs_pkey PRIMARY KEY (task_id);


--
-- Name: dialogs dialogs_request_id_key; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.dialogs
    ADD CONSTRAINT dialogs_request_id_key UNIQUE (request_id);


--
-- Name: enrollment_note_v64 enrollment_note_v64_pkey; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.enrollment_note_v64
    ADD CONSTRAINT enrollment_note_v64_pkey PRIMARY KEY (task_id);


--
-- Name: managed_dialogs managed_dialogs_pkey; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.managed_dialogs
    ADD CONSTRAINT managed_dialogs_pkey PRIMARY KEY (task_id);


--
-- Name: managed_dialogs managed_dialogs_request_id_key; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.managed_dialogs
    ADD CONSTRAINT managed_dialogs_request_id_key UNIQUE (request_id);


--
-- Name: managed_report_binding managed_report_binding_delivery_id_attempt_key; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.managed_report_binding
    ADD CONSTRAINT managed_report_binding_delivery_id_attempt_key UNIQUE (delivery_id, attempt);


--
-- Name: managed_report_binding managed_report_binding_pkey; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.managed_report_binding
    ADD CONSTRAINT managed_report_binding_pkey PRIMARY KEY (task_id, task_seq);


--
-- Name: managed_report_binding managed_report_binding_task_id_report_seq_key; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.managed_report_binding
    ADD CONSTRAINT managed_report_binding_task_id_report_seq_key UNIQUE (task_id, report_seq);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (task_id, seq);


--
-- Name: messages messages_task_id_message_id_key; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.messages
    ADD CONSTRAINT messages_task_id_message_id_key UNIQUE (task_id, message_id);


--
-- Name: metadata_policy_v65k metadata_policy_v65k_pkey; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.metadata_policy_v65k
    ADD CONSTRAINT metadata_policy_v65k_pkey PRIMARY KEY (origin);


--
-- Name: outbox outbox_pkey; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.outbox
    ADD CONSTRAINT outbox_pkey PRIMARY KEY (delivery_id);


--
-- Name: outbox outbox_task_id_seq_recipient_key; Type: CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.outbox
    ADD CONSTRAINT outbox_task_id_seq_recipient_key UNIQUE (task_id, seq, recipient);


--
-- Name: dialogs_updated_idx; Type: INDEX; Schema: bridge; Owner: bridge_owner
--

CREATE INDEX dialogs_updated_idx ON bridge.dialogs USING btree (updated_at DESC, task_id);


--
-- Name: messages_created_idx; Type: INDEX; Schema: bridge; Owner: bridge_owner
--

CREATE INDEX messages_created_idx ON bridge.messages USING btree (created_at DESC);


--
-- Name: outbox_ready_idx; Type: INDEX; Schema: bridge; Owner: bridge_owner
--

CREATE INDEX outbox_ready_idx ON bridge.outbox USING btree (recipient, available_at, delivery_id) WHERE (status = ANY (ARRAY['pending'::text, 'leased'::text]));


--
-- Name: enrollment_note_v64 enrollment_note_v64_task_id_fkey; Type: FK CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.enrollment_note_v64
    ADD CONSTRAINT enrollment_note_v64_task_id_fkey FOREIGN KEY (task_id) REFERENCES bridge.managed_dialogs(task_id);


--
-- Name: managed_dialogs managed_dialogs_task_id_fkey; Type: FK CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.managed_dialogs
    ADD CONSTRAINT managed_dialogs_task_id_fkey FOREIGN KEY (task_id) REFERENCES bridge.dialogs(task_id) ON DELETE RESTRICT;


--
-- Name: managed_report_binding managed_report_binding_delivery_id_fkey; Type: FK CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.managed_report_binding
    ADD CONSTRAINT managed_report_binding_delivery_id_fkey FOREIGN KEY (delivery_id) REFERENCES bridge.outbox(delivery_id) ON DELETE RESTRICT;


--
-- Name: managed_report_binding managed_report_binding_task_id_report_seq_fkey; Type: FK CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.managed_report_binding
    ADD CONSTRAINT managed_report_binding_task_id_report_seq_fkey FOREIGN KEY (task_id, report_seq) REFERENCES bridge.messages(task_id, seq) ON DELETE RESTRICT;


--
-- Name: managed_report_binding managed_report_binding_task_id_task_seq_fkey; Type: FK CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.managed_report_binding
    ADD CONSTRAINT managed_report_binding_task_id_task_seq_fkey FOREIGN KEY (task_id, task_seq) REFERENCES bridge.messages(task_id, seq) ON DELETE RESTRICT;


--
-- Name: messages messages_task_id_fkey; Type: FK CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.messages
    ADD CONSTRAINT messages_task_id_fkey FOREIGN KEY (task_id) REFERENCES bridge.dialogs(task_id) ON DELETE RESTRICT;


--
-- Name: outbox outbox_task_id_seq_fkey; Type: FK CONSTRAINT; Schema: bridge; Owner: bridge_owner
--

ALTER TABLE ONLY bridge.outbox
    ADD CONSTRAINT outbox_task_id_seq_fkey FOREIGN KEY (task_id, seq) REFERENCES bridge.messages(task_id, seq) ON DELETE RESTRICT;


--
-- Name: SCHEMA bridge; Type: ACL; Schema: -; Owner: bridge_owner
--

GRANT USAGE ON SCHEMA bridge TO bridge_app;
GRANT USAGE ON SCHEMA bridge TO bridge_chatgpt;
GRANT USAGE ON SCHEMA bridge TO bridge_agent;


--
-- Name: FUNCTION ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text) TO bridge_app;


--
-- Name: FUNCTION ag_ack_managed_message(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_ack_managed_message(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ag_ack_managed_message(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) TO bridge_agent;


--
-- Name: FUNCTION ag_ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text) FROM PUBLIC;


--
-- Name: FUNCTION ag_claim_managed_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_claim_managed_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ag_claim_managed_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) TO bridge_agent;


--
-- Name: FUNCTION ag_claim_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_claim_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) FROM PUBLIC;


--
-- Name: FUNCTION ag_claim_outbox(p_consumer text, p_limit integer, p_lease_seconds integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_claim_outbox(p_consumer text, p_limit integer, p_lease_seconds integer) FROM PUBLIC;


--
-- Name: FUNCTION ag_dispatch_list(p_offset integer, p_limit integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_dispatch_list(p_offset integer, p_limit integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ag_dispatch_list(p_offset integer, p_limit integer) TO bridge_agent;


--
-- Name: FUNCTION ag_managed_delivery_status(p_task_id text, p_seq bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_managed_delivery_status(p_task_id text, p_seq bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ag_managed_delivery_status(p_task_id text, p_seq bigint) TO bridge_agent;


--
-- Name: FUNCTION ag_managed_report_status(p_task_id text, p_message_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_managed_report_status(p_task_id text, p_message_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ag_managed_report_status(p_task_id text, p_message_id text) TO bridge_agent;


--
-- Name: FUNCTION ag_read_managed_message(p_task_id text, p_seq bigint, p_message_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_read_managed_message(p_task_id text, p_seq bigint, p_message_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ag_read_managed_message(p_task_id text, p_seq bigint, p_message_id text) TO bridge_agent;


--
-- Name: FUNCTION ag_release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text, p_retry_seconds integer, p_error text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text, p_retry_seconds integer, p_error text) FROM PUBLIC;


--
-- Name: FUNCTION ag_send_bound_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint, p_task_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_send_bound_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint, p_task_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ag_send_bound_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint, p_task_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) TO bridge_agent;


--
-- Name: FUNCTION ag_send_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_send_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ag_send_managed_report(p_task_id text, p_message_id text, p_body text, p_expected_version bigint) TO bridge_agent;


--
-- Name: FUNCTION ag_send_message(p_task_id text, p_message_id text, p_kind text, p_body text, p_expected_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_send_message(p_task_id text, p_message_id text, p_kind text, p_body text, p_expected_version bigint) FROM PUBLIC;


--
-- Name: FUNCTION ag_view_managed_dialog(p_task_id text, p_offset integer, p_limit integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.ag_view_managed_dialog(p_task_id text, p_offset integer, p_limit integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.ag_view_managed_dialog(p_task_id text, p_offset integer, p_limit integer) TO bridge_agent;


--
-- Name: FUNCTION cg_ack_managed_report(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_ack_managed_report(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_ack_managed_report(p_task_id text, p_seq bigint, p_delivery_id bigint, p_lease_token uuid, p_consumer text) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_claim_managed_report(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_claim_managed_report(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_claim_managed_report(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_create_dialog(p_request_id text, p_title text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_create_dialog(p_request_id text, p_title text) FROM PUBLIC;


--
-- Name: FUNCTION cg_create_managed_dialog(p_request_id text, p_title text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_create_managed_dialog(p_request_id text, p_title text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_create_managed_dialog(p_request_id text, p_title text) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_list_managed_dialogs(p_offset integer, p_limit integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_list_managed_dialogs(p_offset integer, p_limit integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_list_managed_dialogs(p_offset integer, p_limit integer) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_managed_creation_status(p_request_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_managed_creation_status(p_request_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_managed_creation_status(p_request_id text) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_managed_delivery_status(p_task_id text, p_seq bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_managed_delivery_status(p_task_id text, p_seq bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_managed_delivery_status(p_task_id text, p_seq bigint) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_managed_detail(p_task_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_managed_detail(p_task_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_managed_detail(p_task_id text) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_managed_send_status(p_task_id text, p_message_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_managed_send_status(p_task_id text, p_message_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_managed_send_status(p_task_id text, p_message_id text) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_managed_status(p_task_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_managed_status(p_task_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_managed_status(p_task_id text) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_return_turn(p_task_id text, p_message_id text, p_expected_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_return_turn(p_task_id text, p_message_id text, p_expected_version bigint) FROM PUBLIC;


--
-- Name: FUNCTION cg_send_managed_task(p_task_id text, p_message_id text, p_body text, p_expected_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_send_managed_task(p_task_id text, p_message_id text, p_body text, p_expected_version bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_send_managed_task(p_task_id text, p_message_id text, p_body text, p_expected_version bigint) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_send_message(p_task_id text, p_message_id text, p_kind text, p_body text, p_expected_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_send_message(p_task_id text, p_message_id text, p_kind text, p_body text, p_expected_version bigint) FROM PUBLIC;


--
-- Name: FUNCTION cg_set_managed_dispatch(p_task_id text, p_enabled boolean, p_expected_policy_version bigint, p_expected_dialog_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_set_managed_dispatch(p_task_id text, p_enabled boolean, p_expected_policy_version bigint, p_expected_dialog_version bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_set_managed_dispatch(p_task_id text, p_enabled boolean, p_expected_policy_version bigint, p_expected_dialog_version bigint) TO bridge_chatgpt;


--
-- Name: FUNCTION cg_tombstone_dialog(p_task_id text, p_expected_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_tombstone_dialog(p_task_id text, p_expected_version bigint) FROM PUBLIC;


--
-- Name: FUNCTION cg_view_managed_dialog(p_task_id text, p_offset integer, p_limit integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.cg_view_managed_dialog(p_task_id text, p_offset integer, p_limit integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.cg_view_managed_dialog(p_task_id text, p_offset integer, p_limit integer) TO bridge_chatgpt;


--
-- Name: FUNCTION claim_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.claim_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.claim_message(p_task_id text, p_seq bigint, p_message_id text, p_consumer text, p_lease_seconds integer) TO bridge_app;


--
-- Name: FUNCTION claim_outbox(p_recipient text, p_consumer text, p_limit integer, p_lease_seconds integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.claim_outbox(p_recipient text, p_consumer text, p_limit integer, p_lease_seconds integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.claim_outbox(p_recipient text, p_consumer text, p_limit integer, p_lease_seconds integer) TO bridge_app;


--
-- Name: FUNCTION create_dialog(p_request_id text, p_title text, p_initiator text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.create_dialog(p_request_id text, p_title text, p_initiator text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.create_dialog(p_request_id text, p_title text, p_initiator text) TO bridge_app;


--
-- Name: FUNCTION creation_status(p_request_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.creation_status(p_request_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.creation_status(p_request_id text) TO bridge_app;


--
-- Name: FUNCTION delivery_status(p_task_id text, p_seq bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.delivery_status(p_task_id text, p_seq bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.delivery_status(p_task_id text, p_seq bigint) TO bridge_app;


--
-- Name: FUNCTION dialog_metadata(p_task_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.dialog_metadata(p_task_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.dialog_metadata(p_task_id text) TO bridge_app;


--
-- Name: FUNCTION enroll_dialog_v64(p_initiator text, p_request_id text, p_title text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.enroll_dialog_v64(p_initiator text, p_request_id text, p_title text) FROM PUBLIC;


--
-- Name: FUNCTION enrollment_opening_note_v64(p_task_id text, p_note text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.enrollment_opening_note_v64(p_task_id text, p_note text) FROM PUBLIC;


--
-- Name: FUNCTION enrollment_status_v64(p_initiator text, p_request_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.enrollment_status_v64(p_initiator text, p_request_id text) FROM PUBLIC;


--
-- Name: FUNCTION get_message(p_task_id text, p_seq bigint, p_message_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.get_message(p_task_id text, p_seq bigint, p_message_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.get_message(p_task_id text, p_seq bigint, p_message_id text) TO bridge_app;


--
-- Name: FUNCTION hand_back_turn(p_task_id text, p_expected_version bigint); Type: ACL; Schema: bridge; Owner: postgres
--

REVOKE ALL ON FUNCTION bridge.hand_back_turn(p_task_id text, p_expected_version bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.hand_back_turn(p_task_id text, p_expected_version bigint) TO bridge_app;


--
-- Name: FUNCTION health(); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.health() FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.health() TO bridge_app;
GRANT ALL ON FUNCTION bridge.health() TO bridge_chatgpt;
GRANT ALL ON FUNCTION bridge.health() TO bridge_agent;


--
-- Name: FUNCTION list_dialogs(p_offset integer, p_limit integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.list_dialogs(p_offset integer, p_limit integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.list_dialogs(p_offset integer, p_limit integer) TO bridge_app;


--
-- Name: FUNCTION metadata_policy_allowed_v65m(p_operation text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.metadata_policy_allowed_v65m(p_operation text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.metadata_policy_allowed_v65m(p_operation text) TO bridge_agent;
GRANT ALL ON FUNCTION bridge.metadata_policy_allowed_v65m(p_operation text) TO bridge_chatgpt;


--
-- Name: FUNCTION owner_enrollment_v64(p_initiator text, p_request_id text, p_title text, p_note text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.owner_enrollment_v64(p_initiator text, p_request_id text, p_title text, p_note text) FROM PUBLIC;


--
-- Name: FUNCTION owner_enrollment_view_v64(p_initiator text, p_request_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.owner_enrollment_view_v64(p_initiator text, p_request_id text) FROM PUBLIC;


--
-- Name: FUNCTION register_metadata_v65b(p_request_id text, p_title text, p_note text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.register_metadata_v65b(p_request_id text, p_title text, p_note text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.register_metadata_v65b(p_request_id text, p_title text, p_note text) TO bridge_agent;
GRANT ALL ON FUNCTION bridge.register_metadata_v65b(p_request_id text, p_title text, p_note text) TO bridge_chatgpt;


--
-- Name: FUNCTION registration_metadata_status_v65b(p_request_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.registration_metadata_status_v65b(p_request_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.registration_metadata_status_v65b(p_request_id text) TO bridge_agent;
GRANT ALL ON FUNCTION bridge.registration_metadata_status_v65b(p_request_id text) TO bridge_chatgpt;


--
-- Name: FUNCTION release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text, p_retry_seconds integer, p_error text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text, p_retry_seconds integer, p_error text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text, p_retry_seconds integer, p_error text) TO bridge_app;


--
-- Name: FUNCTION require_metadata_policy_v65k(p_operation text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.require_metadata_policy_v65k(p_operation text) FROM PUBLIC;


--
-- Name: FUNCTION return_turn(p_task_id text, p_message_id text, p_sender text, p_expected_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.return_turn(p_task_id text, p_message_id text, p_sender text, p_expected_version bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.return_turn(p_task_id text, p_message_id text, p_sender text, p_expected_version bigint) TO bridge_app;


--
-- Name: FUNCTION send_message(p_task_id text, p_message_id text, p_sender text, p_kind text, p_body text, p_expected_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.send_message(p_task_id text, p_message_id text, p_sender text, p_kind text, p_body text, p_expected_version bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.send_message(p_task_id text, p_message_id text, p_sender text, p_kind text, p_body text, p_expected_version bigint) TO bridge_app;


--
-- Name: FUNCTION send_status(p_task_id text, p_message_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.send_status(p_task_id text, p_message_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.send_status(p_task_id text, p_message_id text) TO bridge_app;


--
-- Name: FUNCTION tb_enrollment_event_v64(p_task_id text); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.tb_enrollment_event_v64(p_task_id text) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.tb_enrollment_event_v64(p_task_id text) TO bridge_app;


--
-- Name: FUNCTION tb_list_rows(p_offset integer, p_limit integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.tb_list_rows(p_offset integer, p_limit integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.tb_list_rows(p_offset integer, p_limit integer) TO bridge_app;


--
-- Name: FUNCTION tombstone_dialog(p_task_id text, p_version bigint); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.tombstone_dialog(p_task_id text, p_version bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.tombstone_dialog(p_task_id text, p_version bigint) TO bridge_app;


--
-- Name: FUNCTION view_dialog(p_task_id text, p_offset integer, p_limit integer); Type: ACL; Schema: bridge; Owner: bridge_owner
--

REVOKE ALL ON FUNCTION bridge.view_dialog(p_task_id text, p_offset integer, p_limit integer) FROM PUBLIC;
GRANT ALL ON FUNCTION bridge.view_dialog(p_task_id text, p_offset integer, p_limit integer) TO bridge_app;


--
-- PostgreSQL database dump complete
--
