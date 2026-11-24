-- PostgreSQL Bridge v2 / fresh, standalone database schema.
-- Run only on the dedicated EMPTY database `bridge` as PostgreSQL administrator.
-- Deliberately NOT idempotent: rerunning must fail instead of changing production.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $role$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'bridge_app') THEN
    RAISE EXCEPTION 'missing pre-created bridge_app login role';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'bridge_owner') THEN
    CREATE ROLE bridge_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='bridge_app'
              AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
    RAISE EXCEPTION 'bridge_app role has privileged role attributes; refusing';
  END IF;
  IF pg_catalog.pg_has_role('bridge_app', 'bridge_owner', 'member') THEN
    RAISE EXCEPTION 'bridge_app must not be a member of bridge_owner';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = 'bridge') THEN
    RAISE EXCEPTION 'schema bridge already exists; refusing non-fresh install';
  END IF;
END
$role$;

-- Previously bridge_app owned database bridge. Database owner can create schemas
-- regardless of table grants: move ownership to isolated NOLOGIN role first.
ALTER DATABASE bridge OWNER TO bridge_owner;
REVOKE ALL ON DATABASE bridge FROM PUBLIC, bridge_app;
GRANT CONNECT ON DATABASE bridge TO bridge_app;
CREATE SCHEMA bridge AUTHORIZATION bridge_owner;
REVOKE ALL ON SCHEMA bridge FROM PUBLIC;
GRANT USAGE ON SCHEMA bridge TO bridge_app;
SET ROLE bridge_owner;
-- Global defaults for bridge_owner: prevent PUBLIC EXECUTE on future functions too.
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE bridge.dialogs (
  task_id text PRIMARY KEY CHECK (task_id ~ '^task_[0-9a-f]{32}$'),
  request_id text NOT NULL UNIQUE CHECK (length(request_id) BETWEEN 1 AND 160),
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 4096),
  initiator text NOT NULL CHECK (initiator IN ('chatgpt','agent')),
  next_actor text NOT NULL CHECK (next_actor IN ('chatgpt','agent')),
  state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','tombstoned')),
  last_seq bigint NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
  version bigint NOT NULL DEFAULT 0 CHECK (version >= 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX dialogs_updated_idx ON bridge.dialogs(updated_at DESC, task_id);

CREATE TABLE bridge.messages (
  task_id text NOT NULL REFERENCES bridge.dialogs(task_id) ON DELETE RESTRICT,
  seq bigint NOT NULL CHECK (seq >= 1),
  message_id text NOT NULL CHECK (length(message_id) BETWEEN 1 AND 160),
  sender text NOT NULL CHECK (sender IN ('chatgpt','agent')),
  recipient text NOT NULL CHECK (recipient IN ('chatgpt','agent') AND recipient <> sender),
  kind text NOT NULL CHECK (length(kind) BETWEEN 1 AND 64),
  body text NOT NULL CHECK (octet_length(body) <= 65536),
  returned boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (task_id, seq),
  UNIQUE (task_id, message_id)
);
CREATE INDEX messages_created_idx ON bridge.messages(created_at DESC);

CREATE TABLE bridge.outbox (
  delivery_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  task_id text NOT NULL,
  seq bigint NOT NULL,
  recipient text NOT NULL CHECK (recipient IN ('chatgpt','agent')),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','leased','delivered','cancelled')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  lease_until timestamptz,
  lease_token uuid,
  lease_owner text,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  delivered_at timestamptz,
  FOREIGN KEY (task_id, seq) REFERENCES bridge.messages(task_id, seq) ON DELETE RESTRICT,
  UNIQUE (task_id, seq, recipient),
  CHECK (status <> 'leased' OR (lease_until IS NOT NULL AND lease_token IS NOT NULL AND lease_owner IS NOT NULL))
);
CREATE INDEX outbox_ready_idx ON bridge.outbox (recipient, available_at, delivery_id)
  WHERE status IN ('pending', 'leased');

-- A retry with identical request_id is read back, not duplicated.
CREATE FUNCTION bridge.create_dialog(p_request_id text, p_title text, p_initiator text DEFAULT 'chatgpt')
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
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
$fn$;

-- Serialize per-dialog writes and make message+turn+outbox one transaction.
-- EXACTLY SAME message_id+payload returns the original row even after the turn advanced.
CREATE FUNCTION bridge.send_message(p_task_id text, p_message_id text, p_sender text,
                                    p_kind text, p_body text, p_expected_version bigint DEFAULT NULL)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
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
$fn$;

CREATE FUNCTION bridge.send_status(p_task_id text, p_message_id text)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
SELECT COALESCE((SELECT jsonb_build_object('found', true, 'task_id', task_id, 'seq', seq,
                  'message_id', message_id, 'sender', sender, 'kind', kind, 'returned', returned)
                 FROM bridge.messages WHERE task_id = p_task_id AND message_id = p_message_id),
                jsonb_build_object('found',false));
$fn$;

CREATE FUNCTION bridge.list_dialogs(p_offset integer DEFAULT 0, p_limit integer DEFAULT 30)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
SELECT COALESCE(jsonb_agg(to_jsonb(d) - 'request_id' ORDER BY d.updated_at DESC, d.task_id), '[]'::jsonb)
FROM (SELECT task_id, title, initiator, next_actor, state, last_seq, version, created_at, updated_at
      FROM bridge.dialogs WHERE state = 'active'
      ORDER BY updated_at DESC, task_id LIMIT LEAST(GREATEST(p_limit, 1), 200)
      OFFSET LEAST(GREATEST(p_offset,0), 1000000)) d;
$fn$;

CREATE FUNCTION bridge.view_dialog(p_task_id text, p_offset integer DEFAULT 0, p_limit integer DEFAULT 30)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
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
$fn$;

-- Deliveries are at-least-once; recipient must acknowledge only after local durable acceptance.
-- SKIP LOCKED distributes work safely across workers; lease expiry recovers crashed workers.
CREATE FUNCTION bridge.claim_outbox(p_recipient text, p_consumer text,
                                    p_limit integer DEFAULT 10, p_lease_seconds integer DEFAULT 60)
RETURNS TABLE(delivery_id bigint, task_id text, seq bigint, message_id text, sender text,
              kind text, body text, lease_token uuid, attempts integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
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
$fn$;

CREATE FUNCTION bridge.ack_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
BEGIN
 UPDATE bridge.outbox SET status='delivered', delivered_at=clock_timestamp(),
   lease_until=NULL, lease_token=NULL, lease_owner=NULL
 WHERE delivery_id=p_delivery_id AND status='leased' AND lease_token=p_lease_token
   AND lease_owner=p_consumer;
 RETURN FOUND;
END
$fn$;

CREATE FUNCTION bridge.release_outbox(p_delivery_id bigint, p_lease_token uuid, p_consumer text,
                                      p_retry_seconds integer DEFAULT 10, p_error text DEFAULT NULL)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
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
$fn$;

-- Do not allow a client to retract an already-delivered message.
CREATE FUNCTION bridge.return_turn(p_task_id text, p_message_id text, p_sender text,
                                   p_expected_version bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
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
$fn$;

CREATE FUNCTION bridge.health()
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
SELECT jsonb_build_object(
  'ok',true,'db',current_database(),
  'dialogs',(SELECT count(*) FROM bridge.dialogs WHERE state='active'),
  'messages',(SELECT count(*) FROM bridge.messages),
  'pending',(SELECT count(*) FROM bridge.outbox WHERE status='pending'),
  'leased',(SELECT count(*) FROM bridge.outbox WHERE status='leased')
);
$fn$;

RESET ROLE;
REVOKE ALL ON ALL TABLES IN SCHEMA bridge FROM PUBLIC, bridge_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA bridge FROM PUBLIC, bridge_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA bridge FROM PUBLIC, bridge_app;
GRANT EXECUTE ON FUNCTION bridge.create_dialog(text,text,text),
  bridge.send_message(text,text,text,text,text,bigint),
  bridge.send_status(text,text),bridge.list_dialogs(integer,integer),
  bridge.view_dialog(text,integer,integer),bridge.claim_outbox(text,text,integer,integer),
  bridge.ack_outbox(bigint,uuid,text),bridge.release_outbox(bigint,uuid,text,integer,text),
  bridge.return_turn(text,text,text,bigint),bridge.health() TO bridge_app;
COMMIT;
