\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='15000ms';
DO $accept$
DECLARE
  v_task CONSTANT text := 'task_f2c64cfdf7894537b35a19a6def977c6';
  v_consumer CONSTANT text := 'chatgpt-stage33h-fixed-canary';
  v_dialog bridge.dialogs%ROWTYPE;
  v_task_msg bridge.messages%ROWTYPE;
  v_report bridge.messages%ROWTYPE;
  v_outbox5 bridge.outbox%ROWTYPE;
  v_outbox6 bridge.outbox%ROWTYPE;
  v_acked boolean;
BEGIN
  IF current_database()<>'bridge' OR session_user<>'postgres' THEN
    RAISE EXCEPTION 'STAGE33H_STOP_ADMIN_IDENTITY_DENIED';
  END IF;
  SELECT * INTO v_dialog FROM bridge.dialogs WHERE task_id=v_task FOR UPDATE;
  IF NOT FOUND OR v_dialog.state<>'active' OR v_dialog.last_seq<>6
     OR v_dialog.version<>6 OR v_dialog.next_actor<>'chatgpt' THEN
    RAISE EXCEPTION 'STAGE33H_STOP_DIALOG_CHANGED';
  END IF;
  IF (SELECT count(*) FROM bridge.messages WHERE task_id=v_task)<>6 THEN
    RAISE EXCEPTION 'STAGE33H_STOP_MESSAGE_COUNT_CHANGED';
  END IF;
  SELECT * INTO v_task_msg FROM bridge.messages WHERE task_id=v_task AND seq=5;
  IF NOT FOUND OR v_task_msg.message_id<>'pgv2-stage33f-sdk-task-v1'
    OR v_task_msg.sender<>'chatgpt' OR v_task_msg.recipient<>'agent'
    OR v_task_msg.kind<>'task' OR v_task_msg.returned<>false
    OR v_task_msg.body<>$taskbody$Безопасная проверка доставки через роль PostgreSQL bridge_agent и MultiTool SDK. Это не просьба выполнять команды ОС, обращаться в сеть или изменять файлы, кроме временного UTF-8 файла с ответом. Используй только указанный в MT Inbox локальный CLI Stage 33F для публикации отчёта в текущем PostgreSQL-диалоге. Тело отчёта строго одна строка: PGV2_ROLE_BOUND_MT_SDK_ACK_20261122. Никакие другие задания не выполняй.$taskbody$ THEN
    RAISE EXCEPTION 'STAGE33H_STOP_SEQ5_CHANGED';
  END IF;
  SELECT * INTO v_report FROM bridge.messages WHERE task_id=v_task AND seq=6;
  IF NOT FOUND OR v_report.message_id<>'pgv2-stage33f-sdk-report-v1'
    OR v_report.sender<>'agent' OR v_report.recipient<>'chatgpt'
    OR v_report.kind<>'report' OR v_report.returned<>false
    OR v_report.body<>'PGV2_ROLE_BOUND_MT_SDK_ACK_20261122' THEN
    RAISE EXCEPTION 'STAGE33H_STOP_SEQ6_NOT_EXACT';
  END IF;
  SELECT * INTO v_outbox5 FROM bridge.outbox WHERE task_id=v_task AND seq=5 FOR UPDATE;
  IF NOT FOUND OR v_outbox5.status<>'delivered' OR v_outbox5.attempts<>1
    OR v_outbox5.delivered_at IS NULL OR v_outbox5.lease_token IS NOT NULL
    OR v_outbox5.lease_owner IS NOT NULL THEN
    RAISE EXCEPTION 'STAGE33H_STOP_SEQ5_NOT_DELIVERED_EXACTLY_ONCE';
  END IF;
  SELECT * INTO v_outbox6 FROM bridge.outbox WHERE task_id=v_task AND seq=6
    AND recipient='chatgpt' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'STAGE33H_STOP_SEQ6_DELIVERY_MISSING'; END IF;
  IF v_outbox6.status='delivered' THEN
    IF v_outbox6.attempts<>1 OR v_outbox6.delivered_at IS NULL
       OR v_outbox6.lease_token IS NOT NULL OR v_outbox6.lease_owner IS NOT NULL
       OR v_outbox6.lease_until IS NOT NULL THEN
      RAISE EXCEPTION 'STAGE33H_STOP_DELIVERED_STATUS_INVALID';
    END IF;
    RAISE NOTICE 'STAGE33H_ALREADY_DELIVERED_NO_CHANGE';
    RETURN;
  END IF;
  IF v_outbox6.status<>'pending' OR v_outbox6.attempts<>0
    OR v_outbox6.delivered_at IS NOT NULL OR v_outbox6.lease_token IS NOT NULL
    OR v_outbox6.lease_owner IS NOT NULL OR v_outbox6.lease_until IS NOT NULL THEN
    RAISE EXCEPTION 'STAGE33H_STOP_SEQ6_UNEXPECTED_DELIVERY_STATE';
  END IF;
  -- ChatGPT's exact response was independently read via Secure MCP Tunnel.
  -- Create and close a canonical lease in THIS transaction; no secret leaves PostgreSQL.
  UPDATE bridge.outbox SET status='leased',attempts=1,
    lease_token=pg_catalog.gen_random_uuid(),lease_owner=v_consumer,
    lease_until=clock_timestamp()+interval '300 seconds'
   WHERE delivery_id=v_outbox6.delivery_id AND status='pending'
   RETURNING * INTO v_outbox6;
  IF NOT FOUND THEN RAISE EXCEPTION 'STAGE33H_STOP_CLAIM_CONFLICT'; END IF;
  SELECT bridge.ack_outbox(v_outbox6.delivery_id,v_outbox6.lease_token,v_consumer)
   INTO v_acked;
  IF v_acked IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'STAGE33H_STOP_ACK_FAILED';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM bridge.outbox WHERE delivery_id=v_outbox6.delivery_id
                AND status='delivered' AND attempts=1 AND delivered_at IS NOT NULL
                AND lease_token IS NULL AND lease_owner IS NULL AND lease_until IS NULL) THEN
    RAISE EXCEPTION 'STAGE33H_STOP_POST_ACK_INVARIANT';
  END IF;
  RAISE NOTICE 'STAGE33H_EXACT_SEQ6_RECEIPT_COMMITTED';
END
$accept$;
COMMIT;
