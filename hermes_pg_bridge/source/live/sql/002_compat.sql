-- PG Bridge v2 compatibility primitives, installed after 001_init.sql.
-- Fresh installation only; run as PostgreSQL administrator, not bridge_app.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET ROLE bridge_owner;

CREATE FUNCTION bridge.creation_status(p_request_id text)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, bridge, pg_temp AS $fn$
 SELECT (SELECT jsonb_build_object('request_id', request_id, 'task_id', task_id,
   'title', title, 'initiator', initiator, 'next_actor', next_actor,
   'version', version, 'state', state) FROM bridge.dialogs WHERE request_id = p_request_id);
$fn$;

CREATE FUNCTION bridge.dialog_metadata(p_task_id text)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, bridge, pg_temp AS $fn$
 SELECT (SELECT jsonb_build_object('task_id', task_id, 'title', title,
   'initiator', initiator, 'next_actor', next_actor, 'state', state,
   'last_seq', last_seq, 'version', version,
   'created_at', created_at, 'updated_at', updated_at)
 FROM bridge.dialogs WHERE task_id = p_task_id);
$fn$;

CREATE FUNCTION bridge.tombstone_dialog(p_task_id text, p_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, bridge, pg_temp AS $fn$
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
 UPDATE bridge.dialogs SET state='tombstoned',version=version+1,
   updated_at=clock_timestamp() WHERE task_id=p_task_id;
 UPDATE bridge.outbox SET status='cancelled', lease_token=NULL,lease_owner=NULL,lease_until=NULL
 WHERE task_id=p_task_id AND status='pending';
 RETURN jsonb_build_object('task_id',p_task_id,'deleted',true,'version',d.version+1);
END
$fn$;

-- Claim one specific target before beginning any external SDK operation.
-- Only an active, unreturned, currently MT-addressed message may be claimed.
CREATE FUNCTION bridge.claim_message(p_task_id text, p_seq bigint,
                                      p_message_id text, p_consumer text,
                                      p_lease_seconds integer DEFAULT 3600)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, bridge, pg_temp AS $fn$
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
$fn$;

CREATE FUNCTION bridge.delivery_status(p_task_id text, p_seq bigint)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, bridge, pg_temp AS $fn$
 SELECT (SELECT jsonb_build_object('delivery_id',delivery_id,'status',status,
   'attempts',attempts,'delivered_at',delivered_at)
 FROM bridge.outbox WHERE task_id=p_task_id AND seq=p_seq);
$fn$;

CREATE FUNCTION bridge.get_message(p_task_id text, p_seq bigint, p_message_id text)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, bridge, pg_temp AS $fn$
 SELECT (SELECT to_jsonb(m) FROM (SELECT task_id,seq,message_id,sender,recipient,
 kind,body,returned,created_at FROM bridge.messages WHERE task_id=p_task_id AND
 seq=p_seq AND message_id=p_message_id) m);
$fn$;

RESET ROLE;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA bridge FROM PUBLIC, bridge_app;
GRANT EXECUTE ON FUNCTION bridge.creation_status(text),bridge.dialog_metadata(text),
 bridge.tombstone_dialog(text,bigint),bridge.claim_message(text,bigint,text,text,integer),
 bridge.delivery_status(text,bigint),bridge.get_message(text,bigint,text) TO bridge_app;
COMMIT;
