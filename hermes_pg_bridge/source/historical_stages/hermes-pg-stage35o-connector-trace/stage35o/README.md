# Stage35O — one-shot read-only connector failure snapshot

Requires kali user, no sudo. Execute immediately after a failed ChatGPT custom-plugin create attempt. Reads local service status, localhost tunnel health, aggregates journal messages for previous 20 minutes without disclosing log lines, paths, tokens, IDs, request payloads, or other values. No writes to service files, DB, or plugin settings. The collected journal may lack the ChatGPT-side error; do not infer a cause from missing events. It does not change or restart any service. The package may be rerun because it is read-only.
