Stage38A agent instructions:
1. Execute 510_arm_gate_only.sh from verified archive. Only exact existing permit allowed; no config changes.
2. Success marker: STAGE38A_NOW_AGENT_CAN_RESUME_EXACT_EXISTING_REPORT_SEQ5.
3. Check managed send status for message_id pgv2-mg-b6c26aa18220412bafdbf32393f16568-5-report.
4. If not committed and no uncertain state, submit exactly ONE report from previously completed read-only diagnostics, same message_id, using managed Inbox workflow:
STAGE38_CORRECT_TASKBOARD_PORT_8789
ENDPOINT / HTTP=401
ENDPOINT /health HTTP=401
ENDPOINT /api/tasks HTTP=401
ENDPOINT /api/taskboard HTTP=401
STAGE38_CORRECT_TASKBOARD_READONLY_DONE
5. Report status and wait for ChatGPT ACK. No manual DB modifications, no taskboard bypass, no rerunning SDK tasks.
6. If STOP or permit expired, no retries and no direct editing of files.
