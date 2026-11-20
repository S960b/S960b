#!/usr/bin/env python3
"""NEWS v2.6 deterministic RSS candidate filter (dry-run, no model calls).

Fail closed on ambiguous asset/scope. An archive row is NOT a model admission.
Uses first_observed_ms rather than last_observed_ms to avoid rejuvenation.
Conservative rules are intentionally auditable; shadow-mode first.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}
ASSET_PATTERNS = {
    "BTC": re.compile(r"\b(?:bitcoin|btc|bitcoincore)\b", re.I),
    "ETH": re.compile(r"\b(?:ethereum|ether|eth|erc-20)\b", re.I),
    "SOL": re.compile(r"\b(?:solana|sol)\b", re.I),
}
# Positive, substantive events only. Not an indication of trading direction.
EVENT = re.compile(
    r"\b(?:hack(?:ed|ing)?|exploit(?:ed)?|breach|stolen|theft|attack|"
    r"outage|halt(?:ed)?|suspend(?:ed|sion)?|freeze|frozen|"
    r"depeg(?:ged)?|insolven(?:t|cy)|liquidat(?:ion|ions)|"
    r"approval|approved|reject(?:ed|ion)|lawsuit|settlement|"
    r"etf|hard fork|security advisory|vulnerabilit(?:y|ies)|"
    r"cve-\d{4}-\d+|emergency|restor(?:ed|ation)|"
    r"fomc|federal reserve|interest rate|rate cut|rate hike|"
    r"fraud|cluster instability|degrad(?:ed|ation)|offline|elevated error rate)\b", re.I)
NOISE = re.compile(
    r"\b(?:giveaway|contest|sweepstakes|sponsored|advertorial|"
    r"price prediction|technical analysis|death cross|"
    r"top \d+|what is|how to buy|beginner(?:'s|s)? guide)\b", re.I)
SCHEDULED = re.compile(
    r"\b(?:scheduled|upcoming|planned|routine|maintenance window|"
    r"testnet reset|tentative schedule)\b", re.I)
# The source of a globally relevant event is not enough: non-crypto
# government releases must have a clear market-moving subject.
MACRO = re.compile(
    r"\b(?:fomc statement|fomc minutes|federal funds rate|"
    r"minutes of the federal open market committee|"
    r"interest rate decision|emergency rate cut|bitcoin etf|"
    r"ethereum etf|crypto(?:currency)? regulation|stablecoin|usdc)\b", re.I)
CRYPTO_ENFORCEMENT = re.compile(r"\b(?:crypto(?:currency)?|digital assets?|tokens?|trading platforms?)\b", re.I)
SEC_ENFORCEMENT = re.compile(r"\b(?:fraud|steal|stolen|enforcement action|lawsuit|settlement|charged?|indict(?:ed|ment))\b", re.I)
STATUS = frozenset(("solana_status", "coinbase_exchange_status",
                    "kraken_incidents", "circle_status"))
GLOBAL_OFFICIAL = frozenset(("fed_press_monetary", "sec_press",
                             "whitehouse_actions"))
MAX_AGE_MS = 600_000  # provisional: must be reviewed against protocol before activation

@dataclass(frozen=True)
class Decision:
    accepted: bool
    symbol: str | None
    reason: str
    rule_version: str = "rss_noise_v2_shadow"

def decide(row, *, now_ms, max_age_ms=MAX_AGE_MS):
    """Accept sidecar-style row; no external I/O or nondeterministic clock."""
    try:
        source = row["source_id"]
        scope = row["asset_scope"]
        pub = row["published_ms"]
        first = row["first_observed_ms"]
        title = row["title"]
        summary = row.get("summary", "")
    except (KeyError, AttributeError):
        return Decision(False, None, "invalid_metadata")
    if (not isinstance(source, str) or not isinstance(scope, str) or
        type(pub) is not int or type(first) is not int or
        type(now_ms) is not int or type(max_age_ms) is not int or
        not isinstance(title, str) or not isinstance(summary, str) or
        pub <= 0 or first < pub or now_ms < first or max_age_ms < 0):
        return Decision(False, None, "invalid_timestamps_or_types")
    # Source event freshness is checked both at first observation and now.
    if first - pub > max_age_ms or now_ms - pub > max_age_ms:
        return Decision(False, None, "stale")
    if not title.strip():
        return Decision(False, None, "empty_title")
    headline = title[:400]
    text = (headline + " " + summary[:600]).strip()
    if NOISE.search(headline):
        return Decision(False, None, "editorial_noise")
    if SCHEDULED.search(headline) and not re.search(r"\b(?:emergency|unscheduled)\b", headline, re.I):
        return Decision(False, None, "scheduled")
    if source in STATUS and re.search(r"\b(?:testnet|test network)\b", text, re.I):
        return Decision(False, None, "testnet")
    # Status-page outages are not automatically chain/network outages.
    # Exclude peripheral-only disruptions even when "offline"/"degraded" occurs.
    if source in STATUS:
        peripheral = re.search(
            r"\b(?:explorer|docs?|documentation|website|web site|"
            r"dashboard|user interface|\bui\b|support portal|status page)\b",
            headline, re.I)
        network = re.search(
            r"\b(?:mainnet|block production|validators?|consensus|"
            r"finality|chain halted|cluster instability|network outage|"
            r"rpc (?:down|unavailable|outage)|transactions? (?:halted|stalled))\b",
            headline, re.I)
        if peripheral and not network:
            return Decision(False, None, "peripheral_only")
        # "degraded" alone is insufficient evidence of a market-moving outage.
        weak = re.search(r"\b(?:degraded|elevated error rate|offline)\b", headline, re.I)
        strong = re.search(
            r"\b(?:outage|halted|stalled|cluster instability|"
            r"network unavailable|withdrawals? suspended|"
            r"deposits? suspended|transactions? delayed)\b", headline, re.I)
        if weak and not strong and not network:
            return Decision(False, None, "impact_unconfirmed")
    mentioned = {a for a, p in ASSET_PATTERNS.items() if p.search(text)}
    if scope in SYMBOLS:
        if mentioned and scope not in mentioned:
            return Decision(False, None, "scope_conflict")
        asset = scope
    elif scope in ("GLOBAL", "MULTI"):
        if len(mentioned) == 1:
            asset = next(iter(mentioned))
        elif len(mentioned) > 1:
            return Decision(False, None, "multiple_assets")
        elif source in GLOBAL_OFFICIAL and (MACRO.search(text) or (source == "sec_press" and CRYPTO_ENFORCEMENT.search(text) and SEC_ENFORCEMENT.search(text))):
            asset = "BTC"  # predefined benchmark for global macro
        elif source in STATUS and re.search(r"\b(?:exchange|trading platform|all trading|all withdrawals|usdc|stablecoin)\b", text, re.I):
            asset = "BTC"  # global risk benchmark; no automatic 3x calls
        else:
            return Decision(False, None, "no_target_asset")
    else:
        return Decision(False, None, "unknown_scope")
    if not EVENT.search(text) and not (source in GLOBAL_OFFICIAL and (MACRO.search(text) or (source == "sec_press" and CRYPTO_ENFORCEMENT.search(text) and SEC_ENFORCEMENT.search(text)))):
        return Decision(False, None, "no_material_event")
    return Decision(True, SYMBOLS[asset], "candidate")

def selftest():
    base = dict(source_id="cryptoslate", asset_scope="MULTI",
                published_ms=100000, first_observed_ms=101000, summary="")
    def check(title, scope, source, accepted, symbol=None, reason=None, **changes):
        d = decide(dict(base, title=title, asset_scope=scope, source_id=source, **changes),
                   now_ms=102000)
        assert (d.accepted, d.symbol) == (accepted, symbol), (title, d)
        if reason:
            assert d.reason == reason, (title, d)
    check("Bitcoin ETF approved", "MULTI", "coindesk", True, "BTCUSDT")
    check("Ethereum suffers major exploit", "MULTI", "decrypt", True, "ETHUSDT")
    check("Solana mainnet halted", "SOL", "solana_status", True, "SOLUSDT")
    check("Bitcoin price prediction 2027", "MULTI", "coindesk", False)
    check("Upcoming Trading Suspension: CFG-USD", "GLOBAL", "coinbase_exchange_status", False)
    check("Bitcoin BTC Funding Maintenance", "GLOBAL", "kraken_incidents", False)
    check("SEC Proposes Executive Compensation Disclosure Reforms", "GLOBAL", "sec_press", False)
    check("Bitcoin and Ethereum ETF approved", "MULTI", "coindesk", False, reason="multiple_assets")
    old = dict(base, title="Solana mainnet halted", source_id="solana_status", asset_scope="SOL", published_ms=1000)
    assert decide(old, now_ms=102000, max_age_ms=10000).reason == "stale"
    check("Delayed USDC transactions on Pharos testnet", "GLOBAL", "circle_status", False)
    check("FOMC statement: federal funds rate cut", "GLOBAL", "fed_press_monetary", True, "BTCUSDT")
    check("CVE-2025-62859 Bitcoin vulnerability", "BTC", "bitcoin_core", True, "BTCUSDT")
    check("Minutes of the Federal Open Market Committee, October 27-28, 2026", "GLOBAL", "fed_press_monetary", True, "BTCUSDT")
    check("SEC: Eight Entities Used Fake Crypto Asset Trading Platforms to Steal More Than $37 Million", "GLOBAL", "sec_press", True, "BTCUSDT")
    check("SEC Announces Enforcement Annual Results for Fiscal Year 2026", "GLOBAL", "sec_press", False)
    check("Cluster Instability", "SOL", "solana_status", True, "SOLUSDT")
    check("Public Endpoints and Explorer offline", "SOL", "solana_status", False, reason="peripheral_only")
    check("Explorer UI degraded — RPC unaffected", "SOL", "solana_status", False, reason="peripheral_only")
    check("Docs site offline for maintenance", "SOL", "solana_status", False, reason="scheduled")
    check("Mainnet Beta outage — block production halted", "SOL", "solana_status", True, "SOLUSDT")
    check("Solana mainnet halted, validators offline", "SOL", "solana_status", True, "SOLUSDT")
    check("Bitcoin (BTC) Funding Maintenance", "GLOBAL", "kraken_incidents", False)
    print("RSS NOISE FILTER SELFTEST OK (22 cases)")

if __name__ == "__main__":
    selftest()
