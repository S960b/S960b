#!/usr/bin/env python3
"""NEWS Tier-2 — observational paper-forward protocol (offline only in stage 19).

Tier-2 is a SEPARATE, WEAKER protocol. It does not upgrade anything: the strict
v2.2 lane keeps demanding `known_zero` with server-auditable evidence, and this
lane records only what our own process observed:

    client   no tool definitions in the outbound body
    gateway  the forwarded body carried no tool keys (digests compared)
    response the complete response contained no tool-call artifact
    upstream UNKNOWN — there is no server-side tool history

The only overall label this module can produce is `observational_tier2`. It
cannot write `known_zero`: `SEALABLE_TOOL_EVIDENCE` here contains only the
observational label, and a test asserts the strict constant is never used.

Everything in this module works offline. The model gate for Tier-2 does not
exist yet, so `require_gate()` always refuses; wiring a live executor is a
deliberate later step that needs an owner decision.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import v22_arms as A

PROTOCOL_ID = "prospective-v2.5-tier2"
PROMPTS_VERSION = "v2.5-tier2-arms3"
TIER = "observational_tier2"

DATA = Path.home() / ".local/share/news-phase4-v25"
DEFAULT_DB = DATA / "tier2.sqlite"
DEFAULT_ATTESTATION = DATA / "attestation.jsonl"
GATE_FILE = DATA / "TIER2_ACTIVE"          # deliberately absent
PROTOCOL_FILE = Path(__file__).resolve().parent / "TIER2_PROTOCOL_PREREGISTRATION.md"

HORIZON_MS = 4 * 3_600_000
MAX_DRIFT_MS = 60_000
FRESHNESS_BUDGET_S = {"crypto": 120, "geopolitics": 600, "regulatory": 900,
                      "monetary_policy": 300, "macro": 300, "sanctions": 900,
                      "energy": 900}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tier2_triples(
 event_id TEXT PRIMARY KEY,
 protocol_id TEXT NOT NULL,
 tier TEXT NOT NULL CHECK(tier='observational_tier2'),
 prompts_version TEXT NOT NULL,
 prompts_sha256 TEXT NOT NULL,
 protocol_file_sha256 TEXT NOT NULL,
 attestation_record_sha256 TEXT NOT NULL,
 attestation_chain_pos INTEGER NOT NULL,
 model_observed TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 response_sha256 TEXT NOT NULL,
 cohort TEXT NOT NULL,
 published_ms INTEGER NOT NULL,
 cutoff_ms INTEGER NOT NULL,
 entry_price REAL NOT NULL,
 entry_observed_ms INTEGER NOT NULL,
 last_arm_ms INTEGER NOT NULL,
 horizon_ms INTEGER NOT NULL,
 seal_sha256 TEXT NOT NULL,
 created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tier2_arms(
 event_id TEXT NOT NULL, arm TEXT NOT NULL, action TEXT NOT NULL,
 p_up REAL NOT NULL, expected_return_bps REAL NOT NULL,
 uncertainty REAL NOT NULL, text_sha256 TEXT NOT NULL,
 tool_evidence TEXT NOT NULL,
 PRIMARY KEY(event_id, arm));
CREATE TABLE IF NOT EXISTS tier2_outcomes(
 event_id TEXT PRIMARY KEY, due_ms INTEGER NOT NULL, price REAL NOT NULL,
 observed_ms INTEGER NOT NULL, source TEXT NOT NULL, drift_ms INTEGER NOT NULL,
 label_state TEXT NOT NULL);
"""


class Tier2Error(RuntimeError):
    pass


def canonical_sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def protocol_sha(path=PROTOCOL_FILE):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def open_tier2(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    con.commit()
    return con


def require_gate(gate=GATE_FILE):
    """Tier-2 model calls stay disabled. There is no activation file, and this
    function has no way to create one - activation is an owner act."""
    if not Path(gate).exists():
        raise Tier2Error("tier2_not_activated")
    return True


# ---------------------------------------------------------------- attestation

def load_chain(path=DEFAULT_ATTESTATION):
    """Read the SHA-linked attestation log. Returns {record_sha256: (pos, rec)}."""
    path = Path(path)
    out = {}
    if not path.exists():
        return out
    prev = "genesis"
    pos = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        stored = rec.get("record_sha256")
        body = {k: v for k, v in rec.items() if k != "record_sha256"}
        if canonical_sha(body) != stored:
            raise Tier2Error("attestation_record_corrupt")
        if rec.get("prev_sha256") != prev:
            raise Tier2Error("attestation_chain_broken")
        pos += 1
        out[stored] = (pos, rec)
        prev = stored
    return out


# ------------------------------------------------------------------- refusal

def validate_arm_set(arm_results):
    """Tier-2 arm contract. Refuses on anything it cannot vouch for."""
    if not isinstance(arm_results, list) or len(arm_results) != 3:
        raise Tier2Error("incomplete_triple")
    names = [a.get("arm") for a in arm_results]
    if sorted(names) != sorted(A.ARM_NAMES):
        raise Tier2Error("unexpected_arm_names")
    for a in arm_results:
        if a.get("tool_evidence") != TIER:
            raise Tier2Error("tool_evidence_not_observational:" + str(a.get("arm")))
        if a.get("tool_evidence") == "known_zero":
            raise Tier2Error("strict_label_forbidden_in_tier2")
        if not a.get("attestation_record_sha256"):
            raise Tier2Error("attestation_reference_missing:" + str(a.get("arm")))
        if a.get("model_observed") != a.get("model_expected"):
            raise Tier2Error("model_identity_mismatch:" + str(a.get("arm")))
        if not a.get("terminal_response"):
            raise Tier2Error("terminal_response_missing:" + str(a.get("arm")))
        if a.get("retried_ambiguously"):
            raise Tier2Error("ambiguous_retry:" + str(a.get("arm")))
        if a.get("tool_artifacts"):
            raise Tier2Error("tool_artifact_in_response:" + str(a.get("arm")))
    return True


def validate_event(event, now_ms):
    if not isinstance(event, dict):
        raise Tier2Error("event_missing")
    for key in ("event_id", "cohort", "published_ms"):
        if key not in event:
            raise Tier2Error("event_field_missing:" + key)
    if event.get("synthesized") or event.get("backdated"):
        raise Tier2Error("synthesized_or_backdated_event")
    budget = FRESHNESS_BUDGET_S.get(event["cohort"])
    if budget is None:
        raise Tier2Error("cohort_without_budget:" + str(event["cohort"]))
    age_s = (int(now_ms) - int(event["published_ms"])) // 1000
    if age_s > budget:
        raise Tier2Error("event_too_old:" + str(age_s) + "s")
    return True


def validate_quote(quote, last_arm_ms, publish_ms=None):
    if not isinstance(quote, dict):
        raise Tier2Error("quote_missing")
    observed = int(quote.get("observed_ms") or 0)
    if observed <= int(last_arm_ms):
        raise Tier2Error("entry_not_after_last_arm")
    if publish_ms is not None and observed < int(publish_ms):
        raise Tier2Error("entry_before_publication")
    return True


def validate_candle(candle, due_ms):
    """Exit candle must be CLOSED and its drift inside [0, 60 s)."""
    if not isinstance(candle, dict):
        raise Tier2Error("candle_missing")
    close_ms = int(candle.get("close_ms") or 0)
    if not candle.get("closed"):
        raise Tier2Error("candle_not_closed")
    drift = close_ms - int(due_ms)
    if drift < 0 or drift >= MAX_DRIFT_MS:
        raise Tier2Error("candle_drift_out_of_range:" + str(drift))
    return True


# --------------------------------------------------------------------- seal

def seal_triple(*, con, event, arm_results, quote, attestation_path, now_ms,
                prompts_sha256, cohort_extra="|tier2"):
    """Seal one Tier-2 triple. Refuses rather than repairs."""
    validate_event(event, now_ms)
    validate_arm_set(arm_results)
    last_arm_ms = max(int(a["completed_ms"]) for a in arm_results)
    validate_quote(quote, last_arm_ms, event["published_ms"])

    # An empty chain means nothing can be vouched for: refusing outright stops a
    # caller from "re-attesting" a triple whose evidence was deleted. The seal
    # consumes evidence, it never manufactures it.
    chain = load_chain(attestation_path)
    if not chain:
        raise Tier2Error("attestation_chain_empty")
    for a in arm_results:
        ref = a["attestation_record_sha256"]
        if ref not in chain:
            raise Tier2Error("attestation_not_in_chain:" + str(a["arm"]))
        pos, rec = chain[ref]
        if rec["response"].get("sha256") != a.get("response_sha256"):
            raise Tier2Error("attestation_response_mismatch:" + str(a["arm"]))
        if rec["response"].get("model") != a.get("model_observed"):
            raise Tier2Error("attestation_model_mismatch:" + str(a["arm"]))
        if rec["verdict"].get("upstream") != "upstream_unverified":
            raise Tier2Error("attestation_claims_upstream_proof")
        if rec["verdict"].get("client") != "client_no_tools_advertised":
            raise Tier2Error("client_advertised_tools")
        if rec["verdict"].get("gateway") != "gateway_response_no_tool_calls":
            raise Tier2Error("gateway_reported_tool_calls")
        a["_chain_pos"] = pos

    parsed = []
    for a in arm_results:
        row = json.loads(a["text"])
        for key in ("p_up", "expected_return_bps", "uncertainty"):
            if key not in row:
                raise Tier2Error("arm_payload_incomplete:" + str(a["arm"]))
        parsed.append((a, row))

    # Arms carry their own attestation; a triple whose arms disagree about which
    # record proves them is not one observation and cannot be sealed.
    refs = {a["attestation_record_sha256"] for a in arm_results}
    if len(refs) != 3:
        raise Tier2Error("arms_reference_different_attestations:" + str(len(refs)))
    if len({a.get("response_sha256") for a in arm_results}) == 1:
        raise Tier2Error("identical_response_sha_across_arms")
    if len({a.get("model_observed") for a in arm_results}) != 1:
        raise Tier2Error("model_identity_differs_across_arms")

    record = {
        "event_id": event["event_id"],
        "protocol_id": PROTOCOL_ID,
        "tier": TIER,
        "prompts_version": PROMPTS_VERSION,
        "prompts_sha256": prompts_sha256,
        "protocol_file_sha256": protocol_sha(),
        "attestation_record_sha256": arm_results[0]["attestation_record_sha256"],
        "attestation_chain_pos": arm_results[0]["_chain_pos"],
        "model_observed": arm_results[0]["model_observed"],
        "request_sha256": arm_results[0].get("request_sha256") or "",
        "response_sha256": arm_results[0].get("response_sha256") or "",
        "cohort": str(event["cohort"]) + cohort_extra,
        "published_ms": int(event["published_ms"]),
        "cutoff_ms": int(event.get("cutoff_ms") or event["published_ms"]),
        "entry_price": float(quote["price"]),
        "entry_observed_ms": int(quote["observed_ms"]),
        "last_arm_ms": last_arm_ms,
        "horizon_ms": HORIZON_MS,
        "created_ms": int(now_ms),
    }
    seal = canonical_sha(record)

    if con.execute("SELECT 1 FROM tier2_triples WHERE event_id=?",
                   (record["event_id"],)).fetchone():
        return {"status": "already_sealed", "event_id": record["event_id"]}

    con.execute(
        "INSERT INTO tier2_triples VALUES(" + ",".join("?" * 20) + ")",
        (record["event_id"], record["protocol_id"], record["tier"],
         record["prompts_version"], record["prompts_sha256"],
         record["protocol_file_sha256"], record["attestation_record_sha256"],
         record["attestation_chain_pos"], record["model_observed"],
         record["request_sha256"], record["response_sha256"], record["cohort"],
         record["published_ms"], record["cutoff_ms"], record["entry_price"],
         record["entry_observed_ms"], record["last_arm_ms"],
         record["horizon_ms"], seal, record["created_ms"]))
    for a, row in parsed:
        con.execute("INSERT INTO tier2_arms VALUES(?,?,?,?,?,?,?,?)",
                    (record["event_id"], a["arm"],
                     decision_label(row), float(row["p_up"]),
                     float(row["expected_return_bps"]),
                     float(row["uncertainty"]),
                     hashlib.sha256(a["text"].encode()).hexdigest(), TIER))
    con.commit()
    return {"status": "sealed", "tier": TIER, "event_id": record["event_id"],
            "seal": seal, "cohort": record["cohort"]}


def decision_label(row):
    p = float(row["p_up"])
    bps = float(row["expected_return_bps"])
    if p > 0.5 and bps > 0:
        return "LONG"
    if p < 0.5 and bps < 0:
        return "SHORT"
    return "FLAT"


def counters(con):
    """Tier-2 counters, deliberately separate from any strict cohort query."""
    triples = con.execute("SELECT COUNT(*) FROM tier2_triples").fetchone()[0]
    labelled = con.execute(
        "SELECT COUNT(*) FROM tier2_outcomes WHERE label_state='labelled'"
    ).fetchone()[0]
    return {"tier": TIER, "protocol_id": PROTOCOL_ID,
            "triples": triples, "labelled": labelled,
            "note": "never merge with strict v2.2 counters"}


# ------------------------------------------------------- offline mock pipeline

def _write_attestation(path, *, response_sha, model, latency_ms=100,
                       gateway="gateway_response_no_tool_calls",
                       client="client_no_tools_advertised", upstream=None):
    """Build one chained attestation entry. Offline fixtures only; a live path
    would be v22_attestation.attest with real digests."""
    import v22_attestation as AT
    rec = {
        "at_ms": int(time.time() * 1000),
        "latency_ms": latency_ms,
        "ingress": {"sha256": "0" * 64, "tool_keys": [],
                    "keys": ["max_tokens", "messages", "model", "stream",
                             "temperature"]},
        "egress": {"sha256": "1" * 64, "tool_keys": [], "keys": ["input",
                                                                "max_output_tokens",
                                                                "model"],
                   "model_expected": model},
        "response": {"parsed": True, "sha256": response_sha, "model": model,
                     "response_id": "resp_mock", "status": "completed",
                     "output_item_types": ["message"], "tool_items": [],
                     "tool_calls_in_items": [], "has_top_level_tools": False},
        "verdict": {"client": client, "gateway": gateway,
                    "upstream": upstream or "upstream_unverified"},
    }
    return AT.append(rec, path)


def mock_triple(*, con, event, now_ms, attestation_path, quote_price=64_000.0,
                actions=None, model="deepseek-v4-flash", responses=None,
                tool_artifact=False, retried=False, terminal=True,
                wrong_model_arm=None):
    """Exercise the whole Tier-2 seal offline. Never calls a model."""
    actions = actions or {"news": (0.55, 20.0, 0.4),
                          "news_market": (0.56, 24.0, 0.35),
                          "market": (0.50, 0.0, 0.5)}
    arms = []
    for i, arm in enumerate(A.ARM_NAMES):
        text = responses.get(arm) if responses else None
        if text is None:
            p, bps, unc = actions[arm]
            text = json.dumps({"p_up": p, "expected_return_bps": bps,
                               "uncertainty": unc, "rationale": "tier2_mock"},
                              sort_keys=True)
        resp_sha = hashlib.sha256((arm + text).encode()).hexdigest()
        observed_model = (wrong_model_arm if arm == wrong_model_arm else model)
        rec = _write_attestation(attestation_path, response_sha=resp_sha,
                                 model=observed_model,
                                 gateway=("gateway_response_has_tool_calls"
                                          if tool_artifact
                                          else "gateway_response_no_tool_calls"))
        arms.append({
            "arm": arm,
            "text": text,
            "completed_ms": int(now_ms) + 1_000 + i * 100,
            "tool_evidence": TIER,
            "attestation_record_sha256": rec["record_sha256"],
            "model_observed": observed_model,
            "model_expected": model,
            "response_sha256": resp_sha,
            "request_sha256": hashlib.sha256(arm.encode()).hexdigest(),
            "terminal_response": terminal,
            "retried_ambiguously": retried,
            "tool_artifacts": ["function_call"] if tool_artifact else [],
        })
    last = max(a["completed_ms"] for a in arms)
    quote = {"price": quote_price, "observed_ms": last + 1_000,
             "source": "tier2_mock"}
    prompts_sha = hashlib.sha256(
        ("|".join(a["text"] for a in arms)).encode()).hexdigest()
    return seal_triple(con=con, event=event, arm_results=arms, quote=quote,
                      attestation_path=attestation_path, now_ms=now_ms,
                      prompts_sha256=prompts_sha)
