\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';
DO $ack$
DECLARE
  v_task CONSTANT text := 'task_f2c64cfdf7894537b35a19a6def977c6';
  v_dialog bridge.dialogs%ROWTYPE;
  v_task_msg bridge.messages%ROWTYPE;
  v_report bridge.messages%ROWTYPE;
  v_outbox bridge.outbox%ROWTYPE;
  v_seq6 bridge.outbox%ROWTYPE;
  v_count bigint;
  v_acked boolean;
BEGIN
  IF current_database() <> 'bridge' OR session_user <> 'postgres' THEN
    RAISE EXCEPTION 'STAGE33G_STOP_ADMIN_DATABASE_IDENTITY_MISMATCH';
  END IF;
  SELECT * INTO v_dialog FROM bridge.dialogs WHERE task_id=v_task FOR UPDATE;
  IF NOT FOUND OR v_dialog.state <> 'active' OR v_dialog.last_seq <> 6
     OR v_dialog.version <> 6 OR v_dialog.next_actor <> 'chatgpt' THEN
    RAISE EXCEPTION 'STAGE33G_STOP_DIALOG_MISMATCH';
  END IF;
  SELECT count(*) INTO v_count FROM bridge.messages WHERE task_id=v_task;
  IF v_count <> 6 THEN RAISE EXCEPTION 'STAGE33G_STOP_MESSAGE_COUNT_MISMATCH'; END IF;
  SELECT * INTO v_task_msg FROM bridge.messages WHERE task_id=v_task AND seq=5;
  IF NOT FOUND OR v_task_msg.message_id <> 'pgv2-stage33f-sdk-task-v1'
     OR v_task_msg.sender <> 'chatgpt' OR v_task_msg.recipient <> 'agent'
     OR v_task_msg.kind <> 'task' OR v_task_msg.returned <> false
     OR v_task_msg.body <> $taskbody$Безопасная проверка доставки через роль PostgreSQL bridge_agent и MultiTool SDK. Это не просьба выполнять команды ОС, обращаться в сеть или изменять файлы, кроме временного UTF-8 файла с ответом. Используй только указанный в MT Inbox локальный CLI Stage 33F для публикации отчёта в текущем PostgreSQL-диалоге. Тело отчёта строго одна строка: PGV2_ROLE_BOUND_MT_SDK_ACK_20261122. Никакие другие задания не выполняй.$taskbody$ THEN
    RAISE EXCEPTION 'STAGE33G_STOP_TASK_NOT_EXACT';
  END IF;
  SELECT * INTO v_report FROM bridge.messages WHERE task_id=v_task AND seq=6;
  IF NOT FOUND OR v_report.message_id <> 'pgv2-stage33f-sdk-report-v1'
     OR v_report.sender <> 'agent' OR v_report.recipient <> 'chatgpt'
     OR v_report.kind <> 'report' OR v_report.returned <> false
     OR v_report.body <> 'PGV2_ROLE_BOUND_MT_SDK_ACK_20261122' THEN
    RAISE EXCEPTION 'STAGE33G_STOP_REPORT_NOT_EXACT';
  END IF;
  SELECT * INTO v_seq6 FROM bridge.outbox WHERE task_id=v_task AND seq=6;
  IF NOT FOUND OR v_seq6.recipient <> 'chatgpt' OR v_seq6.status <> 'pending' THEN
    RAISE EXCEPTION 'STAGE33G_STOP_CHATGPT_OUTBOX_CHANGED';
  END IF;
  SELECT * INTO v_outbox FROM bridge.outbox
  WHERE task_id=v_task AND seq=5 AND recipient='agent' FOR UPDATE;
  IF NOT FOUND OR v_outbox.attempts < 1 THEN
    RAISE EXCEPTION 'STAGE33G_STOP_DELIVERY_MISSING';
  END IF;
  IF v_outbox.status = 'delivered' THEN
    IF v_outbox.delivered_at IS NULL OR v_outbox.lease_owner IS NOT NULL
       OR v_outbox.lease_token IS NOT NULL THEN
      RAISE EXCEPTION 'STAGE33G_STOP_ALREADY_DELIVERED_INCONSISTENT';
    END IF;
    RAISE NOTICE 'STAGE33G_ALREADY_DELIVERED_NO_CHANGE';
    RETURN;
  END IF;
  IF v_outbox.status <> 'leased' OR v_outbox.lease_owner <> 'mt-pg-inbox'
     OR v_outbox.lease_token IS NULL OR v_outbox.lease_until IS NULL THEN
    RAISE EXCEPTION 'STAGE33G_STOP_LEASE_NOT_OWNED';
  END IF;
  -- Use the EXISTING guarded PostgreSQL function, inside this single transaction.
  -- The lease token is read and consumed INSIDE PostgreSQL; never printed or sent to Kali.
  SELECT bridge.ack_outbox(v_outbox.delivery_id, v_outbox.lease_token,
                           'mt-pg-inbox') INTO v_acked;
  IF v_acked IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'STAGE33G_STOP_LEASE_ACK_FAILED';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM bridge.outbox
    WHERE delivery_id=v_outbox.delivery_id AND status='delivered'
      AND delivered_at IS NOT NULL AND lease_token IS NULL AND lease_owner IS NULL
  ) THEN
    RAISE EXCEPTION 'STAGE33G_STOP_POST_ACK_INVARIANT';
  END IF;
  RAISE NOTICE 'STAGE33G_EXACT_SEQ5_ACK_COMMITTED';
END
$ack$;
COMMIT;
