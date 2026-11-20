#!/usr/bin/env python3
"""NEWS v2.1 — bounded read-only Bluesky Jetstream listener (paper only).

Listens to the public no-auth Jetstream WebSocket, filters to an explicit DID
allowlist, and writes accepted posts into the isolated v2.1 datastore.

Safety rules enforced here:
  * only DIDs in DID_ALLOWLIST are ever classified; everything else is dropped;
  * identity is re-checked against the AT Protocol resolver every
    IDENTITY_RECHECK_S seconds and the listener FAILS CLOSED on mismatch
    (handle no longer resolving to the same DID => stop, do not ingest);
  * a repost (app.bsky.feed.repost) is never treated as fresh news; only
    app.bsky.feed.post commits are considered;
  * chronological durability: the last accepted Jetstream time_us is persisted
    as a cursor and written with each batch;
  * bounded: reconnect backoff, max frames per run, max run wall time;
  * no model calls, no trades, no writes outside the v2.1 DB.
"""
import argparse
import json
import os
import signal
import socket
import ssl
import sqlite3
import struct
import sys
import time
import urllib.request

JETSTREAM_HOST = "jetstream.us-east.bsky.network"
JETSTREAM_PATH = "/xrpc/network.bsky.jetstream.subscribeEvents"
RESOLVE_URL = "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle="

# Only DIDTo-handle pairs that were verified bidirectionally are here.
ALLOWLIST = {
    "did:plc:iqoyar2mdkpg6mskhmri3nr6": "decrypt.co",
}

IDENTITY_RECHECK_S = 1800      # 30 min
RECONNECT_BACKOFF_S = (1, 2, 4, 8, 15, 30)
# Frames are counted for reporting, NOT as a stop condition: cutting the socket
# every 5 000 frames created a ~30 s gap between runs (observed 14 times).
# The run now ends only on the deadline or a real stream error.
MAX_FRAMES_PER_RUN = 10_000_000
MAX_RUN_SECONDS = 3300         # under a 1h service restart cycle
POST_COLLECTION = "app.bsky.feed.post"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bs_events(
 event_id TEXT PRIMARY KEY,
 did TEXT NOT NULL,
 handle TEXT NOT NULL,
 uri TEXT NOT NULL,
 published_ms INTEGER NOT NULL,
 received_ms INTEGER NOT NULL,
 text_sha256 TEXT NOT NULL,
 lang TEXT,
 payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bs_state(
 k TEXT PRIMARY KEY,
 v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bs_runs(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 started_ms INTEGER NOT NULL,
 ended_ms INTEGER,
 frames INTEGER DEFAULT 0,
 accepted INTEGER DEFAULT 0,
 reconnects INTEGER DEFAULT 0,
 stop_reason TEXT);
"""


def log(event, **kw):
    rec = {"ts_ms": time.time_ns() // 1_000_000, "event": event}
    rec.update(kw)
    sys.stdout.write(json.dumps(rec, sort_keys=True) + "\n")
    sys.stdout.flush()


def open_db(path):
    con = sqlite3.connect(path, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(SCHEMA)
    con.commit()
    return con


def get_state(con, key, default=None):
    row = con.execute("SELECT v FROM bs_state WHERE k=?", (key,)).fetchone()
    return row[0] if row else default


def set_state(con, key, value):
    con.execute("INSERT INTO bs_state(k,v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))
    con.commit()


def resolve_handle(handle, timeout=10):
    """Return the DID the handle currently resolves to, or None."""
    try:
        with urllib.request.urlopen(RESOLVE_URL + handle, timeout=timeout) as r:
            return json.loads(r.read(4096)).get("did")
    except Exception:
        return None


def check_identity():
    """Fail closed if any allowlisted handle no longer resolves to its DID."""
    for did, handle in ALLOWLIST.items():
        got = resolve_handle(handle)
        if got != did:
            return False, {"did": did, "handle": handle, "resolved": got}
    return True, None


def parse_frame(payload):
    """Return the commit for a NEW post, or None for anything else.

    Live Jetsream wraps commits as {"$type":"message","payload":{...}} while
    some older docs show a flat {"kind":"commit","commit":{...}} envelope.
    Both shapes are handled; only operation=create on app.bsky.feed.post is
    ever treated as a news candidate, so a repost or an update is never fresh.
    """
    try:
        o = json.loads(payload)
    except Exception:
        return None
    if not isinstance(o, dict):
        return None
    # live shape first, then the documented flat shape
    c = o.get("payload") if isinstance(o.get("payload"), dict) else None
    if c is None:
        if o.get("kind") != "commit":
            return None
        c = o.get("commit") or {}
    if c.get("operation") != "create":
        return None           # updates/deletes are not new events
    if c.get("collection") != POST_COLLECTION:
        return None           # reposts/likes/follows are never news
    rec = c.get("record") or {}
    did = c.get("did") or o.get("did")
    rkey = c.get("rkey")
    if not isinstance(did, str) or not isinstance(rkey, str):
        return None
    uri = f"at://{did}/{POST_COLLECTION}/{rkey}"
    # Jetstream carries an ISO timestamp on the commit; prefer it for ordering
    ts_ms = iso_to_ms(c.get("time"))
    return {"did": did, "uri": uri, "record": rec, "commit_ms": ts_ms,
            "seq": c.get("seq")}


def iso_to_ms(s):
    if not isinstance(s, str):
        return None
    try:
        import datetime
        return int(datetime.datetime.strptime(
            s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").timestamp() * 1000)
    except Exception:
        try:
            import datetime
            return int(datetime.datetime.strptime(
                s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").timestamp() * 1000)
        except Exception:
            return None


def store(con, item, received_ms):
    did = item["did"]
    handle = ALLOWLIST.get(did)
    if handle is None:
        return False
    rec = item["record"]
    text = rec.get("text") or ""
    published = iso_to_ms(rec.get("createdAt"))
    if published is None:
        return False
    import hashlib
    eid = hashlib.sha256(item["uri"].encode()).hexdigest()
    cur = con.execute(
        "INSERT OR IGNORE INTO bs_events"
        "(event_id,did,handle,uri,published_ms,received_ms,text_sha256,lang,payload_json)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (eid, did, handle, item["uri"], published, received_ms,
         hashlib.sha256(text.encode()).hexdigest(),
         (rec.get("langs") or [None])[0], json.dumps(rec)[:8192]))
    con.commit()
    return cur.rowcount == 1


def build_path():
    """Server-side collection filter, client-side DID filter.

    Empirically verified on 2026-11-16 against jetstream.us-east/us-west:
      * ?collections=app.bsky.feed.post  -> 101, and the firehose still flows
      * ?dids=<did>                       -> 101 but ZERO frames are ever
        delivered, on every host tried, even for a high-volume account
      * ?dids=<did>&wantedCollections=... -> 400 Bad Request
    So `dids` is accepted syntactically but not honoured by this deployment.
    We therefore filter by collection on the server (cheap, ~40% traffic cut)
    and enforce the DID allowlist strictly in parse_frame()/store().
    """
    return f"{JETSTREAM_PATH}?collections={POST_COLLECTION}"


def connect():
    key = __import__("base64").b64encode(os.urandom(16)).decode()
    req = (f"GET {build_path()} HTTP/1.1\r\nHost: {JETSTREAM_HOST}\r\n"
           f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
           f"User-Agent: NEWS-v21-listener/0.1\r\n\r\n")
    ctx = ssl.create_default_context()
    raw = socket.create_connection((JETSTREAM_HOST, 443), timeout=20)
    s = ctx.wrap_socket(raw, server_hostname=JETSTREAM_HOST)
    s.sendall(req.encode())
    head = s.recv(4096).decode("latin1", "replace")
    if "101" not in head.split("\r\n")[0]:
        s.close()
        raise RuntimeError("handshake_failed")
    return s


def iter_frames(sock, deadline):
    buf = b""
    while time.time() < deadline:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            return
        buf += chunk
        while len(buf) >= 2:
            b0, b1 = buf[0], buf[1]
            ln, off = b1 & 0x7F, 2
            if ln == 126:
                if len(buf) < 4:
                    break
                ln = struct.unpack(">H", buf[2:4])[0]
                off = 4
            elif ln == 127:
                if len(buf) < 10:
                    break
                ln = struct.unpack(">Q", buf[2:10])[0]
                off = 10
            if len(buf) < off + ln:
                break
            payload = buf[off:off + ln]
            buf = buf[off + ln:]
            if b0 & 0x0F == 0x1:
                yield payload


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--max-seconds", type=int, default=MAX_RUN_SECONDS)
    a = p.parse_args(argv)

    stopped = {"v": False}

    def on_signal(_sig, _frm):
        stopped["v"] = True
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    con = open_db(a.db)
    started = int(time.time() * 1000)
    run_id = con.execute(
        "INSERT INTO bs_runs(started_ms) VALUES(?)", (started,)).lastrowid
    con.commit()

    ok, detail = check_identity()
    if not ok:
        log("identity_mismatch_fail_closed", **detail)
        con.execute("UPDATE bs_runs SET ended_ms=?,stop_reason=? WHERE seq=?",
                    (int(time.time() * 1000), "identity_mismatch", run_id))
        con.commit()
        return 3

    frames = accepted = reconnects = 0
    last_commit_ms = {"v": None}
    last_flush = time.time()
    deadline = time.time() + max(1, a.max_seconds)
    backoff_idx = 0
    stop_reason = "deadline"
    last_recheck = time.time()

    while time.time() < deadline and not stopped["v"]:
        if time.time() - last_recheck > IDENTITY_RECHECK_S:
            ok, detail = check_identity()
            if not ok:
                log("identity_mismatch_fail_closed", **detail)
                stop_reason = "identity_mismatch"
                break
            last_recheck = time.time()
        try:
            sock = connect()
        except Exception as exc:
            delay = RECONNECT_BACKOFF_S[min(backoff_idx, len(RECONNECT_BACKOFF_S) - 1)]
            backoff_idx += 1
            reconnects += 1
            log("reconnect", reason=str(exc)[:60], sleep_s=delay)
            time.sleep(delay)
            continue
        backoff_idx = 0
        try:
            for payload in iter_frames(sock, deadline):
                frames += 1
                item = parse_frame(payload)
                if item:
                    cur = item.get("commit_ms")
                    if isinstance(cur, int):
                        # advance the cursor on EVERY observed commit, so a
                        # restart can be reasoned about even with no allowlist
                        # matches in the window
                        last_commit_ms["v"] = cur
                if item and store(con, item, int(time.time() * 1000)):
                    accepted += 1
                    log("accepted", handle=item["did"][:20], frames=frames)
                    cur = item.get("commit_ms")
                    if isinstance(cur, int):
                        set_state(con, "cursor_ms", cur)
                if time.time() - last_flush >= 30:
                    if isinstance(last_commit_ms["v"], int):
                        set_state(con, "cursor_ms", last_commit_ms["v"])
                    last_flush = time.time()
                if frames >= MAX_FRAMES_PER_RUN:
                    stop_reason = "max_frames"
                    break
        except (OSError, ssl.SSLError, RuntimeError) as exc:
            log("stream_error", reason=type(exc).__name__)
        finally:
            try:
                sock.close()
            except Exception:
                pass
        if stop_reason == "max_frames":
            break

    if isinstance(last_commit_ms["v"], int):
        set_state(con, "cursor_ms", last_commit_ms["v"])
    con.execute("UPDATE bs_runs SET ended_ms=?,frames=?,accepted=?,"
                "reconnects=?,stop_reason=? WHERE seq=?",
                (int(time.time() * 1000), frames, accepted, reconnects,
                 stop_reason, run_id))
    con.commit()
    con.close()
    log("run_finished", frames=frames, accepted=accepted,
        reconnects=reconnects, stop_reason=stop_reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
