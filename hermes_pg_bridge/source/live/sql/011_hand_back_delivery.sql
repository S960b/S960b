-- Owner hands the turn back to the agent only after the last message was delivered.
-- The message and its outbox row are never retracted; only the turn flips.
-- Returns changed=false when the turn is already with the agent.
CREATE OR REPLACE FUNCTION bridge.hand_back_turn(p_task_id text, p_expected_version bigint DEFAULT NULL)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, bridge, pg_temp AS $fn$
DECLARE d bridge.dialogs%ROWTYPE; m bridge.messages%ROWTYPE; o bridge.outbox%ROWTYPE; st text;
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
    SELECT status INTO st FROM bridge.outbox WHERE task_id = p_task_id AND seq = d.last_seq;
    RETURN jsonb_build_object('task_id', p_task_id, 'version', d.version,
                              'next_actor', 'agent', 'changed', false,
                              'delivery_status', st);
  END IF;
  SELECT * INTO m FROM bridge.messages WHERE task_id = p_task_id AND seq = d.last_seq;
  IF NOT FOUND OR m.sender <> 'agent' OR m.returned THEN
    RAISE EXCEPTION 'hand_back_not_allowed' USING ERRCODE = 'P0001';
  END IF;
  SELECT * INTO o FROM bridge.outbox WHERE task_id = p_task_id AND seq = m.seq FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'outbox_not_found' USING ERRCODE = 'P0001';
  END IF;
  IF o.status <> 'delivered' THEN
    RAISE EXCEPTION 'hand_back_before_delivery' USING ERRCODE = 'P0001';
  END IF;
  UPDATE bridge.dialogs SET next_actor = 'agent', version = version + 1,
    updated_at = clock_timestamp() WHERE task_id = p_task_id;
  RETURN jsonb_build_object('task_id', p_task_id, 'version', d.version + 1,
                            'next_actor', 'agent', 'changed', true,
                            'delivery_status', o.status);
END
$fn$;

REVOKE ALL ON FUNCTION bridge.hand_back_turn(text, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bridge.hand_back_turn(text, bigint) TO bridge_app;
