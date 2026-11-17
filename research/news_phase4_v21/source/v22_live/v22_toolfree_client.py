#!/usr/bin/env python3
"""NEWS v2.2 — minimal tool-free inference client.

The point is structural, not declarative: this client builds the request body
itself and there is **no code path that can add tools**. The gateway bridge on
this host only forwards `tools` when the caller sends them, and this caller
never does. That makes "no tools were advertised" a property of the request we
can show, rather than a promise in a config file.

Contract enforced here:
  * request body carries `model` and `messages` only — no tools, no tool_choice,
    no functions, no agent, no session orchestration;
  * a tool-call appearing in the RESPONSE is a hard failure;
  * identity (model id) must be present, otherwise refuse;
  * request and response digests are recorded, plus HTTP status and any
    provider-request-id header the gateway returns;
  * no retries on ambiguity — one attempt, then report.
"""
import hashlib
import json
import urllib.error
import urllib.request

ENDPOINT = "http://127.0.0.1:8090/v1/chat/completions"
TIMEOUT_S = 120
MAX_TOKENS = 400

# Any of these keys in the outbound body would mean the request advertises
# capabilities. The builder below cannot produce them; the check exists so a
# future edit fails loudly instead of quietly re-adding a tool surface.
FORBIDDEN_REQUEST_KEYS = ("tools", "tool_choice", "functions", "function_call",
                          "agent", "session", "mcp")


class IsolationError(ValueError):
    pass


def build_request(model, system, user):
    """The ONLY place a request body is constructed."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    leaked = [k for k in FORBIDDEN_REQUEST_KEYS if k in body]
    if leaked:
        raise IsolationError("request_advertises_capabilities:" + ",".join(leaked))
    return body


def digest(obj):
    payload = obj if isinstance(obj, (bytes, str)) else json.dumps(
        obj, sort_keys=True, ensure_ascii=True)
    if isinstance(payload, str):
        payload = payload.encode()
    return hashlib.sha256(payload).hexdigest()


def call(*, model, system, user, opener=None):
    """One bounded generation call. Returns evidence, or raises IsolationError."""
    body = build_request(model, system, user)
    raw = json.dumps(body).encode()
    req = urllib.request.Request(ENDPOINT, data=raw, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        resp = (opener or urllib.request.urlopen)(req, timeout=TIMEOUT_S)
        status = getattr(resp, "status", 200)
        payload = resp.read().decode("utf-8", "replace")
        request_id = (resp.headers.get("x-request-id")
                      or resp.headers.get("x-gw-request-id"))
    except urllib.error.HTTPError as exc:
        raise IsolationError("http_" + str(exc.code)) from None
    except urllib.error.URLError as exc:
        raise IsolationError("transport:" + type(exc.reason).__name__) from None

    try:
        data = json.loads(payload)
    except ValueError:
        raise IsolationError("response_not_json") from None

    choices = data.get("choices") or []
    if not choices:
        raise IsolationError("response_empty_choices")
    message = (choices[0] or {}).get("message") or {}

    # Structural refusals, in the order that matters most.
    if message.get("tool_calls"):
        raise IsolationError("tool_call_present_in_response")
    tool_items = [it for it in (data.get("output") or [])
                  if isinstance(it, dict) and it.get("type") in
                  ("function_call", "tool_call", "computer_call")]
    if tool_items:
        raise IsolationError("tool_item_present_in_response")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise IsolationError("response_text_missing")

    model_id = data.get("model") or ""
    if not isinstance(model_id, str) or not model_id.strip():
        raise IsolationError("model_identity_missing")

    if (choices[0] or {}).get("finish_reason") not in ("stop", "length"):
        raise IsolationError("non_terminal_finish_reason:" +
                             str((choices[0] or {}).get("finish_reason")))

    return {
        "status": status,
        "model": model_id,
        "provider_request_id": request_id,
        "request_sha256": digest(raw),
        "response_sha256": digest(payload),
        "request_keys": sorted(body),
        "tools_advertised": False,
        "finish_reason": choices[0].get("finish_reason"),
        "content": content,
        "usage": data.get("usage"),
    }


if __name__ == "__main__":
    import sys
    out = call(model=sys.argv[1] if len(sys.argv) > 1 else "",
               system="You answer with exactly one word.",
               user="Reply with the single word: ok")
    print(json.dumps({k: v for k, v in out.items() if k != "content"},
                     indent=1, sort_keys=True))
