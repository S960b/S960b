\set ON_ERROR_STOP on
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='5000ms';
SELECT jsonb_build_object(
 'database', current_database(),
 'function_count',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge'),
 'role_chatgpt_no_login',(SELECT NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolbypassrls FROM pg_roles WHERE rolname='bridge_chatgpt'),
 'role_agent_no_login',(SELECT NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolbypassrls FROM pg_roles WHERE rolname='bridge_agent'),
 'legacy_execute',has_function_privilege('bridge_app','bridge.send_message(text,text,text,text,text,bigint)','EXECUTE'),
 'chatgpt_send',has_function_privilege('bridge_chatgpt','bridge.cg_send_message(text,text,text,text,bigint)','EXECUTE'),
 'agent_send',has_function_privilege('bridge_agent','bridge.ag_send_message(text,text,text,text,bigint)','EXECUTE'),
 'agent_claim',has_function_privilege('bridge_agent','bridge.ag_claim_message(text,bigint,text,text,integer)','EXECUTE'),
 'chatgpt_raw_send',has_function_privilege('bridge_chatgpt','bridge.send_message(text,text,text,text,text,bigint)','EXECUTE'),
 'agent_raw_send',has_function_privilege('bridge_agent','bridge.send_message(text,text,text,text,text,bigint)','EXECUTE'),
 'agent_as_chatgpt',has_function_privilege('bridge_agent','bridge.cg_send_message(text,text,text,text,bigint)','EXECUTE'),
 'chatgpt_as_agent',has_function_privilege('bridge_chatgpt','bridge.ag_send_message(text,text,text,text,bigint)','EXECUTE'),
 'chatgpt_table_select',has_table_privilege('bridge_chatgpt','bridge.messages','SELECT'),
 'agent_table_select',has_table_privilege('bridge_agent','bridge.messages','SELECT'),
 'chatgpt_table_insert',has_table_privilege('bridge_chatgpt','bridge.messages','INSERT'),
 'agent_table_insert',has_table_privilege('bridge_agent','bridge.messages','INSERT'),
 'chatgpt_db_create',has_database_privilege('bridge_chatgpt','bridge','CREATE'),
 'agent_db_create',has_database_privilege('bridge_agent','bridge','CREATE'),
 'chatgpt_schema_create',has_schema_privilege('bridge_chatgpt','bridge','CREATE'),
 'agent_schema_create',has_schema_privilege('bridge_agent','bridge','CREATE'),
 'public_exec_count',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge' AND EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE')),
 'wrong_wrapper_owner_count',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge' AND n.nspowner <> p.proowner),
 'unprotected_wrapper_count',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge' AND (p.proname LIKE 'cg_%' OR p.proname LIKE 'ag_%') AND (NOT p.prosecdef OR position('session_user' in lower(pg_get_functiondef(p.oid)))=0)),
 'dialog_count',(SELECT (bridge.health()->>'dialogs')::integer),
 'message_count',(SELECT (bridge.health()->>'messages')::integer)
)::text;
COMMIT;
