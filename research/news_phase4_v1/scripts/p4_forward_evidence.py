"""Pure forward-only evidence envelopes for NEWS Intelligence Phase 4.

No HTTP, filesystem, SQLite or gateway calls. The caller must independently
attest response-end clocks and preserve exact raw evidence in an approved store.
Digest equality here is not proof that an external response really occurred.
"""
import hashlib
import json
import re

VERSION = 'p4-forward-evidence-v1'
ALLOWED_SOURCES = {
    'news': ('rss-allowlist-v1',),
    'market': ('binance-spot-btcusdt-1h-v1',),
    'social': ('mastodon-social-bitcoin-v1',),
}
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
MAX_SOURCE_BYTES = 750_000
MAX_FREEZE_DELAY_MS = 120_000
ARMS = {'news': ('news',),
        'news_market': ('news', 'market'),
        'news_market_social': ('news', 'market', 'social')}


class EvidenceError(ValueError):
    pass


def _fail(code):
    raise EvidenceError(code)


def _int(v):
    return type(v) is int and v >= 0


def _hash(v):
    return isinstance(v, str) and DIGEST.fullmatch(v) is not None


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf8')


def source_evidence(source, record, cutoff_ms):
    """Validate explicit provenance metadata of one ALREADY retrieved source."""
    if source not in ALLOWED_SOURCES or type(record) is not dict:
        _fail('unknown_source')
    required = {'source_id', 'available_ms', 'observed_ms', 'digest', 'size_bytes'}
    if set(record) != required or record['source_id'] not in ALLOWED_SOURCES[source]:
        _fail('source_identity_or_shape')
    a, o = record['available_ms'], record['observed_ms']
    if not (_int(a) and _int(o) and a > 0 and a <= o <= cutoff_ms):
        _fail('not_available_before_frozen_cutoff')
    if not _hash(record['digest']):
        _fail('invalid_source_digest')
    if not _int(record['size_bytes']) or not 0 < record['size_bytes'] <= MAX_SOURCE_BYTES:
        _fail('evidence_too_large')
    return dict(record)


def seal_evidence(*, news, market, social=None, news_first_seen_ms, cutoff_ms,
                  created_ms, social_missing_reason=None, social_checked_ms=None):
    """Build deterministic SQLite insertion envelope and hashes with no side effects.

    event_id is stable for a given publisher-news digest and first-seen time.
    An event is immutable; repeated observation must not create a second
    prediction event with an updated cutoff. Do not infer first-seen from
    publication date or reconstruct it from an old RSS cache.
    """
    if not (_int(cutoff_ms) and cutoff_ms > 0 and _int(created_ms) and
            cutoff_ms <= created_ms <= cutoff_ms + MAX_FREEZE_DELAY_MS):
        _fail('invalid_freeze_clock')
    if not _int(news_first_seen_ms) or news_first_seen_ms > cutoff_ms:
        _fail('invalid_news_first_seen')
    sources = {'news': source_evidence('news', news, cutoff_ms),
               'market': source_evidence('market', market, cutoff_ms)}
    if not news_first_seen_ms <= sources['news']['observed_ms']:
        _fail('first_seen_after_observation')
    if social is not None:
        if social_missing_reason is not None or social_checked_ms is not None:
            _fail('social_present_and_missing')
        sources['social'] = source_evidence('social', social, cutoff_ms)
    else:
        if social_missing_reason not in ('unavailable', 'denied', 'rate_limited',
                                         'no_data', 'clock_mismatch', 'incomplete'):
            _fail('social_missing_reason_required')
        if not _int(social_checked_ms) or not 0 < social_checked_ms <= cutoff_ms:
            _fail('invalid_social_check_clock')
    if created_ms < max(record['observed_ms'] for record in sources.values()):
        _fail('created_before_observation')
    event_id = hashlib.sha256(_canonical({
        'v': VERSION, 'news_digest': news['digest'],
        'first_seen_ms': news_first_seen_ms,
    })).hexdigest()
    features = [(event_id, name, r['available_ms'], r['observed_ms'], r['digest'])
                for name, r in sorted(sources.items())]
    missing = [] if social is not None else [
        (event_id, 'social', social_checked_ms, social_missing_reason)]
    frozen = {'version': VERSION, 'event_id': event_id, 'cutoff_ms': cutoff_ms,
              'news_first_seen_ms': news_first_seen_ms,
              'sources': sources, 'source_missing': missing}
    return {
        'event': (event_id, cutoff_ms, created_ms, news['digest'],
                  VERSION, VERSION),
        'features': features,
        'source_missing': missing,
        'frozen_digest': hashlib.sha256(_canonical(frozen)).hexdigest(),
        'available_arms': ('news', 'news_market') if social is None else
                          ('news', 'news_market', 'news_market_social'),
        'frozen': frozen,
    }


def arm_input_hash(envelope, arm):
    """Hash the EXACT frozen feature selection, not an LLM prompt.

    Actual adapter must separately hash canonical rendered prompt bytes before
    reserving and sending the model request; never confuse these hashes.
    """
    if arm not in ARMS or not isinstance(envelope, dict) or 'frozen' not in envelope:
        _fail('invalid_arm')
    f = envelope['frozen']
    sources = f['sources']
    if any(source not in sources for source in ARMS[arm]):
        _fail('arm_source_missing')
    return hashlib.sha256(_canonical({
        'version': VERSION, 'event_id': f['event_id'],
        'cutoff_ms': f['cutoff_ms'],
        'arm': arm, 'sources': {s: sources[s] for s in ARMS[arm]},
    })).hexdigest()
