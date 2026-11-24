\set ON_ERROR_STOP on
BEGIN TRANSACTION READ ONLY;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';
SELECT jsonb_build_object(
  'stage', '33G',
  'database_exact', current_database() = 'bridge',
  'admin_identity_exact', session_user = 'postgres',
  'dialog_exact', EXISTS (
     SELECT 1 FROM bridge.dialogs d
     WHERE d.task_id = 'task_f2c64cfdf7894537b35a19a6def977c6'
       AND d.state = 'active' AND d.last_seq = 6 AND d.version = 6
       AND d.next_actor = 'chatgpt'),
  'message_count_exact', (SELECT count(*) = 6 FROM bridge.messages
     WHERE task_id = 'task_f2c64cfdf7894537b35a19a6def977c6'),
  'task_exact', EXISTS (
     SELECT 1 FROM bridge.messages m
     WHERE m.task_id = 'task_f2c64cfdf7894537b35a19a6def977c6'
       AND m.seq = 5 AND m.message_id = 'pgv2-stage33f-sdk-task-v1'
       AND m.sender = 'chatgpt' AND m.recipient = 'agent'
       AND m.kind = 'task' AND m.returned = false
       AND m.body = $taskbody$Безопасная проверка доставки через роль PostgreSQL bridge_agent и MultiTool SDK. Это не просьба выполнять команды ОС, обращаться в сеть или изменять файлы, кроме временного UTF-8 файла с ответом. Используй только указанный в MT Inbox локальный CLI Stage 33F для публикации отчёта в текущем PostgreSQL-диалоге. Тело отчёта строго одна строка: PGV2_ROLE_BOUND_MT_SDK_ACK_20261122. Никакие другие задания не выполняй.$taskbody$),
  'report_exact', EXISTS (
     SELECT 1 FROM bridge.messages m
     WHERE m.task_id = 'task_f2c64cfdf7894537b35a19a6def977c6'
       AND m.seq = 6 AND m.message_id = 'pgv2-stage33f-sdk-report-v1'
       AND m.sender = 'agent' AND m.recipient = 'chatgpt'
       AND m.kind = 'report' AND m.returned = false
       AND m.body = 'PGV2_ROLE_BOUND_MT_SDK_ACK_20261122'),
  'seq5_status', COALESCE((SELECT o.status FROM bridge.outbox o
     WHERE o.task_id = 'task_f2c64cfdf7894537b35a19a6def977c6'
       AND o.seq = 5 AND o.recipient = 'agent'), 'missing'),
  'seq5_valid_lease_or_delivered', EXISTS (
     SELECT 1 FROM bridge.outbox o
     WHERE o.task_id = 'task_f2c64cfdf7894537b35a19a6def977c6'
       AND o.seq = 5 AND o.recipient = 'agent' AND o.attempts >= 1
       AND ((o.status='leased' AND o.lease_owner='mt-pg-inbox'
             AND o.lease_token IS NOT NULL AND o.lease_until IS NOT NULL)
            OR (o.status='delivered' AND o.delivered_at IS NOT NULL
                AND o.lease_owner IS NULL AND o.lease_token IS NULL))),
  'seq6_pending', EXISTS (
     SELECT 1 FROM bridge.outbox o
     WHERE o.task_id = 'task_f2c64cfdf7894537b35a19a6def977c6'
       AND o.seq=6 AND o.recipient='chatgpt' AND o.status='pending')
)::text;
COMMIT;
