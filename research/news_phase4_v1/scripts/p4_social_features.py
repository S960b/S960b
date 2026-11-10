"""Pure point-in-time Mastodon #bitcoin social-snapshot validator.

Accepts one already-fetched public hashtag-timeline response. No HTTP, SQLite,
filesystem, model calls, authentication, or implicit backfill. The output is a
candidate NMS input only; a successful API status is not a source-quality gate.
"""
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import re

SOURCE = 'mastodon.social#bitcoin'
VERSION = 'p4-mastodon-bitcoin-v1'
LOOKBACK_MS = 2 * 3_600_000
MAX_AGE_AFTER_FETCH_MS = 120_000
MAX_STATUSES = 40
MIN_ELIGIBLE = 5
POSITIVE = frozenset(('bullish', 'rally', 'surge', 'buy', 'uptrend', 'breakout'))
NEGATIVE = frozenset(('bearish', 'crash', 'dump', 'sell', 'downtrend', 'plunge'))
_WORD = re.compile(r"[a-z]+")
_LINK = re.compile(r'https?://\S+', re.I)
_MENTION = re.compile(r'(?<!\w)@[\w.-]+(?:@[\w.-]+)?')


class SocialFeatureError(ValueError):
    pass


def _clock(value):
    if not isinstance(value, str) or len(value) > 40:
        raise SocialFeatureError('invalid_post_time')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError('timezone_required')
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    except (ValueError, OverflowError, TypeError):
        raise SocialFeatureError('invalid_post_time') from None


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppress = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.suppress += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.suppress:
            self.suppress -= 1

    def handle_data(self, data):
        if not self.suppress:
            self.parts.append(data)


def _clean(html):
    if not isinstance(html, str) or len(html.encode('utf-8')) > 4096:
        raise SocialFeatureError('invalid_post_content')
    parser = _PlainText()
    parser.feed(html)
    plain = _LINK.sub(' ', ' '.join(parser.parts))
    plain = _MENTION.sub(' ', plain)
    plain = ' '.join(plain.split())
    return plain[:280]


def build_social_features(statuses, observed_ms, cutoff_ms, *, min_eligible=MIN_ELIGIBLE):
    """Validate a captured public Mastodon response and compute bounded evidence.

    Caller MUST ensure the HTTP request completed at observed_ms and must freeze
    cutoff_ms at or after this time. No post is treated as seen at its created_at.
    A full 40-item page is rejected conservatively because it may be truncated.
    """
    if (type(observed_ms) is not int or type(cutoff_ms) is not int or
            observed_ms <= 0 or not observed_ms <= cutoff_ms <= observed_ms + MAX_AGE_AFTER_FETCH_MS):
        raise SocialFeatureError('invalid_capture_cutoff')
    if type(min_eligible) is not int or not 1 <= min_eligible <= MAX_STATUSES:
        raise SocialFeatureError('invalid_minimum')
    if not isinstance(statuses, list) or len(statuses) > MAX_STATUSES:
        raise SocialFeatureError('invalid_page_size')
    if len(statuses) == MAX_STATUSES:
        raise SocialFeatureError('potentially_truncated_page')
    selected, seen = [], set()
    for status in statuses:
        if not isinstance(status, dict):
            raise SocialFeatureError('invalid_status')
        uri = status.get('uri')
        if not isinstance(uri, str) or not uri.startswith('https://') or len(uri) > 512:
            raise SocialFeatureError('invalid_status_uri')
        if uri in seen:
            continue
        seen.add(uri)
        created_ms = _clock(status.get('created_at'))
        edited_ms = _clock(status['edited_at']) if status.get('edited_at') is not None else None
        if created_ms > observed_ms or (edited_ms is not None and not created_ms <= edited_ms <= observed_ms):
            raise SocialFeatureError('future_or_inconsistent_post')
        if created_ms < observed_ms - LOOKBACK_MS:
            continue
        if (status.get('visibility') != 'public' or status.get('reblog') is not None or
                status.get('language') not in ('en', None)):
            continue
        account = status.get('account')
        if isinstance(account, dict) and account.get('bot') is True:
            continue
        text = _clean(status.get('content'))
        words = _WORD.findall(text.lower())
        if len(words) < 4:
            continue
        selected.append({'uri': uri, 'created_ms': created_ms,
                         'text': text, 'positive': sum(w in POSITIVE for w in words),
                         'negative': sum(w in NEGATIVE for w in words)})
    if len(selected) < min_eligible:
        raise SocialFeatureError('insufficient_fresh_public_posts')
    selected.sort(key=lambda r: (r['created_ms'], r['uri']))
    positive = sum(r['positive'] for r in selected)
    negative = sum(r['negative'] for r in selected)
    canonical = json.dumps({'version': VERSION, 'source': SOURCE, 'observed_ms': observed_ms,
                            'posts': selected}, sort_keys=True, ensure_ascii=True,
                           separators=(',', ':')).encode('utf-8')
    # Only bounded sanitized excerpts leave the pure function; no handles or URLs.
    return {'version': VERSION, 'source': SOURCE, 'observed_ms': observed_ms,
            'available_ms': observed_ms, 'cutoff_ms': cutoff_ms,
            'digest': sha256(canonical).hexdigest(), 'eligible_count': len(selected),
            'positive_hits': positive, 'negative_hits': negative,
            'lexicon_balance': (positive-negative)/(positive+negative) if positive+negative else None,
            'excerpts': [r['text'] for r in selected[:10]], 'evidence_bytes': len(canonical)}
