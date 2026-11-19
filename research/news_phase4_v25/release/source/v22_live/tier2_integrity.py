#!/usr/bin/env python3
"""Tier-2 evidence bindings: full arm/attestation/raw/frozen seal and verifier.

No inference or network; read-only verification may be invoked before writing
a +4h outcome. A hash is tamper-evident, not an authenticated external signature:
someone who can rewrite *every* record and recompute hashes can still falsify
a local dataset. The original raw bytes and independent capture remain critical.
"""
import hashlib
import json
from pathlib import Path

import tier2_raw_store as RAW


class IntegrityError(ValueError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS tier2_bindings(
 event_id TEXT PRIMARY KEY,
 trial_id TEXT NOT NULL,
 mode TEXT NOT NULL,
 bundle_json TEXT NOT NULL,
 bundle_sha256 TEXT NOT NULL);
"""

TRIPLE_COLS = (
    "event_id", "protocol_id", "tier", "prompts_version", "prompts_sha256",
    "protocol_file_sha256", "attestation_record_sha256", "attestation_chain_pos",
    "model_observed", "request_sha256", "response_sha256", "cohort",
    "published_ms", "cutoff_ms", "entry_price", "entry_observed_ms",
    "last_arm_ms", "horizon_ms", "created_ms",
)
ARM_COLS = ("event_id", "arm", "action", "p_up", "expected_return_bps",
            "uncertainty", "text_sha256", "tool_evidence")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def _load_chain(attestation_path):
    import tier2 as T2
    return T2.load_chain(attestation_path)


def _message_content(raw):
    try:
        msg = json.loads(raw.decode("utf-8", "strict"))["choices"][0]["message"]
        content = msg["content"]
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError):
        raise IntegrityError("raw_response_content_unreadable") from None
    if not isinstance(content, str) or not content.strip():
        raise IntegrityError("raw_response_content_missing")
    return content


def _verify_live_arm(*, arm, trial_id, attestation_path, record,
                     frozen_sha256, triple):
    """Check *actual* response content and exact request against all evidence."""
    if record.get("extra") != {"arm": arm["arm"], "trial_id": trial_id}:
        raise IntegrityError("attestation_arm_or_trial_mismatch:" + arm["arm"])
    req_sha = arm["request_sha256"]
    resp_sha = arm["response_sha256"]
    if record["ingress"].get("sha256") != req_sha:
        raise IntegrityError("attestation_request_digest_mismatch:" + arm["arm"])
    if record["response"].get("sha256") != resp_sha:
        raise IntegrityError("attestation_response_digest_mismatch:" + arm["arm"])
    try:
        wire = RAW.verify_wire(attestation_path=attestation_path,
                               trial_id=trial_id, arm=arm["arm"],
                               request_sha256=req_sha,
                               response_sha256=resp_sha)
    except (RAW.RawEvidenceError, FileNotFoundError, ValueError) as exc:
        raise IntegrityError("raw_evidence_invalid:" + str(exc)) from None
    content = _message_content(wire["response"])
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != arm["text_sha256"]:
        raise IntegrityError("response_text_not_raw_bound:" + arm["arm"])

    # F02: hashes of a locally rewritten DB+bundle do NOT prove that the
    # numeric decision originated from the raw model response. Reparse the
    # original response with the same strict Tier-2 parser used at seal time.
    import v21_paper_prep as P
    import tier2 as T2
    try:
        parsed = P.parse_arm_response(
            content, max_abs_bps=max(abs(T2.BPS_RANGE[0]),
                                     abs(T2.BPS_RANGE[1])))
        T2.validate_arm_payload(parsed, arm=arm["arm"])
    except (P.PrepError, T2.Tier2Error, ValueError, TypeError) as exc:
        raise IntegrityError("raw_forecast_invalid:" + arm["arm"] + ":"
                             + str(exc)) from None
    for field in ("p_up", "expected_return_bps", "uncertainty"):
        if float(parsed[field]) != float(arm[field]):
            raise IntegrityError("raw_forecast_numeric_mismatch:"
                                 + arm["arm"] + ":" + field)
    if T2.decision_label(parsed) != arm["action"]:
        raise IntegrityError("raw_forecast_action_mismatch:" + arm["arm"])

    freeze = Path(attestation_path).parent / ("frozen-" + trial_id + ".json")
    if freeze.is_symlink() or not freeze.is_file():
        raise IntegrityError("frozen_input_missing")
    raw_freeze = freeze.read_bytes()
    if hashlib.sha256(raw_freeze).hexdigest() != frozen_sha256:
        raise IntegrityError("frozen_input_digest_mismatch")
    try:
        frozen = json.loads(raw_freeze)
        request = json.loads(wire["request"].decode("utf-8", "strict"))
        messages = request["messages"]
    except (ValueError, UnicodeError, KeyError, TypeError):
        raise IntegrityError("frozen_request_invalid") from None
    if (frozen.get("trial_id") != trial_id
            or messages != [
                {"role": "system", "content": frozen.get("system_prompt")},
                {"role": "user", "content": frozen["prompts"][arm["arm"]]},
            ]):
        raise IntegrityError("request_does_not_match_frozen_input:" + arm["arm"])
    if request.get("model") != arm["model_observed"]:
        raise IntegrityError("request_model_mismatch:" + arm["arm"])

    # The real immutable input includes the precise candidate. A genuine
    # triple must be tied to THAT candidate, not merely to the same prompts
    # and raw response files. This check is required at seal and at read time.
    event = frozen.get("event")
    if not isinstance(event, dict) or not isinstance(triple, dict):
        raise IntegrityError("frozen_event_or_triple_missing")
    if (triple.get("event_id") != event.get("event_id")
            or triple.get("cohort") not in (str(event.get("cohort")), str(event.get("cohort")) + "|tier2")
            or triple.get("published_ms") != event.get("published_ms")
            or triple.get("cutoff_ms")
               != int(event.get("cutoff_ms") or event["published_ms"])):
        raise IntegrityError("frozen_event_triple_mismatch")
    if (triple.get("prompts_sha256") != T2.canonical_sha(frozen["prompts"])
            or triple.get("prompts_version") != frozen.get("prompt_version")
            or triple.get("protocol_file_sha256") != frozen.get("protocol_sha256")):
        raise IntegrityError("frozen_protocol_or_prompts_mismatch")
    return True


def prepare_binding(*, event_id, record, parsed_arms, attestation_path,
                    trial_id=None, frozen_sha256=None):
    """Validate three facts; return immutable canonical binding, no DB writes.

    Real records must possess raw bytes, per-arm attestation context and the
    exact frozen-input digest. Historical mock tests are explicitly identified
    by their existing '_fixture_basis' marker, never upgraded to live evidence.
    """
    chain = _load_chain(attestation_path)
    ordered = sorted(parsed_arms, key=lambda item: item[0]["arm"])
    if len(ordered) != 3:
        raise IntegrityError("binding_not_three_arms")
    modes = set()
    arm_docs = []
    for a, parsed in ordered:
        ref = a["attestation_record_sha256"]
        if ref not in chain:
            raise IntegrityError("binding_attestation_missing")
        pos, att = chain[ref]
        fixture = att.get("_fixture_basis") == "raw_bytes"
        modes.add("offline_fixture" if fixture else "live_raw")
        text_hash = hashlib.sha256(a["text"].encode("utf-8")).hexdigest()
        arm_doc = {
            "arm": a["arm"],
            "action": _decision_label(parsed),
            "p_up": float(parsed["p_up"]),
            "expected_return_bps": float(parsed["expected_return_bps"]),
            "uncertainty": float(parsed["uncertainty"]),
            "text_sha256": text_hash,
            "tool_evidence": a["tool_evidence"],
            "request_sha256": a.get("request_sha256"),
            "response_sha256": a.get("response_sha256"),
            "attestation_record_sha256": ref,
            "attestation_chain_pos": pos,
            "model_observed": a["model_observed"],
            "completed_ms": int(a["completed_ms"]),
        }
        arm_docs.append(arm_doc)
    if len(modes) != 1:
        raise IntegrityError("mixed_live_and_fixture_evidence")
    mode = modes.pop()
    if mode == "live_raw":
        if not trial_id or not frozen_sha256:
            raise IntegrityError("live_binding_requires_trial_and_freeze")
        for arm_doc in arm_docs:
            _verify_live_arm(arm=arm_doc, trial_id=trial_id,
                             attestation_path=attestation_path,
                             record=chain[arm_doc["attestation_record_sha256"]][1],
                             frozen_sha256=frozen_sha256, triple=record)
    else:
        # Fake/mock evidence has no genuine wire data. It may exercise the seal
        # logic offline but cannot be interpreted as a prospective observation.
        trial_id = trial_id or "offline-fixture"
        frozen_sha256 = None
    bundle = {
        "version": 1,
        "mode": mode,
        "trial_id": trial_id,
        "frozen_sha256": frozen_sha256,
        "attestation_path": str(Path(attestation_path).resolve()),
        "triple": dict(record),
        "arms": arm_docs,
    }
    return bundle


def _decision_label(parsed):
    import tier2 as T2
    return T2.decision_label(parsed)


def persist_binding(con, bundle):
    # The tier2 schema is created by open_tier2 before starting a transaction.
    # Avoid executescript() here: it implicitly commits an in-flight transaction.
    con.execute(
        "INSERT INTO tier2_bindings(event_id,trial_id,mode,bundle_json,"
        "bundle_sha256) VALUES(?,?,?,?,?)",
        (bundle["triple"]["event_id"], bundle["trial_id"], bundle["mode"],
         canonical(bundle), sha(bundle)))


def verify_binding(con, event_id, *, attestation_path=None, require_live=False):
    """Read-only verification; refuses altered arms, missing chain and raw data.

    Must be called by label worker AND Tier-2 analyzer. It creates no schema.
    """
    from tier2 import load_chain
    try:
        row = con.execute(
            "SELECT trial_id, mode, bundle_json, bundle_sha256 "
            "FROM tier2_bindings WHERE event_id=?", (event_id,)).fetchone()
    except Exception as exc:
        raise IntegrityError("binding_table_missing:" + type(exc).__name__) from None
    if row is None:
        raise IntegrityError("binding_missing:" + event_id)
    trial, mode, raw, expected = row
    try:
        bundle = json.loads(raw)
    except ValueError:
        raise IntegrityError("binding_json_invalid") from None
    if (sha(bundle) != expected or bundle.get("mode") != mode
            or bundle.get("trial_id") != trial or bundle.get("version") != 1):
        raise IntegrityError("binding_digest_or_mode_mismatch")
    triple_row = con.execute("SELECT * FROM tier2_triples WHERE event_id=?",
                             (event_id,)).fetchone()
    if triple_row is None or len(triple_row) != 20:
        raise IntegrityError("triple_missing_or_schema_mismatch")
    stored_seal = triple_row[18]
    actual_record = dict(zip(TRIPLE_COLS, triple_row[:18] + triple_row[19:]))
    if (actual_record != bundle.get("triple")
            or stored_seal != expected):
        raise IntegrityError("sealed_triple_or_digest_changed")
    db_arms = con.execute(
        "SELECT * FROM tier2_arms WHERE event_id=? ORDER BY arm",
        (event_id,)).fetchall()
    if len(db_arms) != 3:
        raise IntegrityError("sealed_arm_count_invalid")
    bundled_arms = bundle.get("arms")
    if not isinstance(bundled_arms, list) or len(bundled_arms) != 3:
        raise IntegrityError("binding_arms_invalid")
    for db_row, doc in zip(db_arms, bundled_arms):
        expected_row = (
            event_id, doc["arm"], doc["action"], doc["p_up"],
            doc["expected_return_bps"], doc["uncertainty"],
            doc["text_sha256"], doc["tool_evidence"],
        )
        if tuple(db_row) != expected_row:
            raise IntegrityError("sealed_arm_changed:" + str(db_row[1]))
    if require_live and mode != "live_raw":
        raise IntegrityError("offline_fixture_not_a_live_observation")
    attestation_path = attestation_path or bundle.get("attestation_path")
    if attestation_path is not None:
        chain = load_chain(attestation_path)
        for doc in bundled_arms:
            ref = doc["attestation_record_sha256"]
            if ref not in chain:
                raise IntegrityError("sealed_attestation_missing:" + doc["arm"])
            pos, att = chain[ref]
            if pos != doc["attestation_chain_pos"]:
                raise IntegrityError("attestation_position_changed:" + doc["arm"])
            if att["response"].get("sha256") != doc["response_sha256"]:
                raise IntegrityError("attestation_response_changed:" + doc["arm"])
            if mode == "live_raw":
                _verify_live_arm(arm=doc, trial_id=trial,
                                 attestation_path=attestation_path,
                                 record=att, frozen_sha256=bundle["frozen_sha256"],
                                 triple=bundle["triple"])
    elif mode == "live_raw":
        raise IntegrityError("live_binding_requires_attestation_path")
    return bundle
