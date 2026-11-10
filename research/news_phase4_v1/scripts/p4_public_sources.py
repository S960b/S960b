"""Bounded, allowlisted public HTTP + RSS parsing for NEWS Phase 4.

No storage, model calls, private endpoints, credentials or trading.
A transport may be injected for offline tests. Live fetch must ONLY use
the constant public endpoints below and records response-end wall clocks.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import email.utils
import hashlib
import html
import json
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import xml.etree.ElementTree as ET

FEEDS = {
    'coindesk': 'https://www.coindesk.com/arc/outboundfeeds/rss',
    'decrypt': 'https://decrypt.co/feed',
    'cointelegraph': 'https://cointelegraph.com/rss',
    'theblock': 'https://www.theblock.co/rss.xml',
}
BINANCE_TIME = 'https://data-api.binance.vision/api/v3/time'
BINANCE_KLINES = 'https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=48'
BINANCE_TICKER = 'https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT'
ALLOWED_STATIC_URLS = frozenset((*FEEDS.values(), BINANCE_TIME, BINANCE_KLINES, BINANCE_TICKER))
ONE_MINUTE_KLINE = re.compile(r'https://data-api\.binance\.vision/api/v3/klines\?symbol=BTCUSDT&interval=1m&startTime=[0-9]{13}&limit=1\Z')
HOSTS = frozenset(urlsplit(u).hostname for u in (*FEEDS.values(), BINANCE_TIME))
KEYWORDS = re.compile(r'\b(bitcoin|btc|ethereum|eth|crypto|cryptocurrency|etf|fed|'
                      r'federal reserve|sec|inflation|cpi|tariff|stablecoin|liquidity)\b',re.I)
ATOM = '{http://www.w3.org/2005/Atom}'
DC = '{http://purl.org/dc/elements/1.1/}'
MAX_FEED_BYTES = 750_000
MAX_API_BYTES = 110_000
MAX_STORIES = 60
MAX_AGE_MS = 48 * 3600_000


class CaptureError(ValueError):
    pass


@dataclass(frozen=True)
class Captured:
    url: str
    body: bytes
    started_ms: int
    observed_ms: int
    status: int


def now_ms():
    return time.time_ns() // 1_000_000


def _safe_url(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in HOSTS or
            p.username or p.password or p.port not in (None,443) or p.fragment or
            (url not in ALLOWED_STATIC_URLS and not ONE_MINUTE_KLINE.fullmatch(url))):
        raise CaptureError('unapproved_endpoint')


class _NoCrossHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_url(newurl)
        if urlsplit(newurl).hostname != urlsplit(req.full_url).hostname:
            raise CaptureError('cross_host_redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch_public(url, *, max_bytes=MAX_FEED_BYTES, clock=now_ms):
    _safe_url(url)
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_FEED_BYTES:
        raise CaptureError('invalid_response_bound')
    start=clock()
    opener=build_opener(_NoCrossHostRedirect())
    req=Request(url,headers={'User-Agent':'NEWS-Phase4-Research/0.1','Accept':'application/xml,application/rss+xml,application/atom+xml,application/json'})
    try:
        with opener.open(req,timeout=12) as response:
            status=response.status
            actual=response.geturl()
            _safe_url(actual)
            if status != 200:
                raise CaptureError('http_not_ok')
            body=response.read(max_bytes+1)
    except (OSError,ValueError) as e:
        if isinstance(e,CaptureError):
            raise
        raise CaptureError('public_source_fetch_failed') from None
    end=clock()
    if (type(start) is not int or type(end) is not int or end < start or
            end-start > 20_000):
        raise CaptureError('untrusted_fetch_clock')
    if not 0 < len(body) <= max_bytes:
        raise CaptureError('response_size_invalid')
    return Captured(actual,body,start,end,status)


def _clean(value,limit):
    value=html.unescape(re.sub(r'<[^>]*>',' ',value or ''))
    return ' '.join(value.split())[:limit]


def _date_ms(raw):
    if not raw:
        return None
    try:
        stamp=email.utils.parsedate_to_datetime(raw)
        if stamp is None:
            stamp=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if stamp.tzinfo is None:
            return None
        return int(stamp.astimezone(timezone.utc).timestamp()*1000)
    except (TypeError,OverflowError,ValueError):
        return None


def parse_feed(capture,source):
    if (source not in FEEDS or not isinstance(capture,Captured) or capture.status!=200
            or not isinstance(capture.body,bytes)
            or len(capture.body)>MAX_FEED_BYTES):
        raise CaptureError('invalid_feed_capture')
    if b'<!DOCTYPE' in capture.body.upper() or b'<!ENTITY' in capture.body.upper():
        raise CaptureError('unsafe_xml')
    try:
        root=ET.fromstring(capture.body)
    except ET.ParseError:
        raise CaptureError('invalid_rss') from None
    items=root.findall('./channel/item') or root.findall(f'{ATOM}entry')
    out=[]
    for entry in items[:MAX_STORIES]:
        def val(*tags):
            for tag in tags:
                node=entry.find(tag)
                if node is None: continue
                if node.text and node.text.strip():
                    return node.text.strip()
                if tag==ATOM+'link' and node.attrib.get('href'):
                    return node.attrib['href'][:500]
            return ''
        title=_clean(val('title',ATOM+'title'),220)
        url=val('link',ATOM+'link')[:500]
        summary=_clean(val('description',ATOM+'summary',ATOM+'content'),400)
        published=_date_ms(val('pubDate',DC+'date',ATOM+'published',ATOM+'updated'))
        if (not title or not url or published is None or
                published>capture.observed_ms+300_000 or
                published<capture.observed_ms-MAX_AGE_MS or
                not KEYWORDS.search(title+' '+summary)):
            continue
        normalized=re.sub(r'\W+',' ',title.casefold()).strip()
        if len(normalized)<15:
            continue
        fingerprint=hashlib.sha256(normalized.encode()).hexdigest()
        news={'source':source,'title':title,'summary':summary,'url':url,
              'published_ms':published,'first_http_seen_ms':capture.observed_ms,
              'rss_body_sha256':hashlib.sha256(capture.body).hexdigest()}
        out.append((fingerprint,news))
    return out


def decode_json(capture,max_bytes=MAX_API_BYTES):
    if not isinstance(capture,Captured) or len(capture.body)>max_bytes:
        raise CaptureError('invalid_api_capture')
    try:
        return json.loads(capture.body.decode('utf-8'))
    except (UnicodeError,ValueError):
        raise CaptureError('invalid_api_json') from None


def check_binance_time(capture):
    if capture.url != BINANCE_TIME:
        raise CaptureError('wrong_clock_endpoint')
    obj=decode_json(capture,max_bytes=2048)
    server=obj.get('serverTime') if type(obj) is dict else None
    if type(server) is not int or server<1_000_000_000_000:
        raise CaptureError('invalid_server_clock')
    midpoint=(capture.started_ms+capture.observed_ms)//2
    skew=server-midpoint
    if abs(skew)>3000:
        raise CaptureError('exchange_clock_mismatch')
    return skew
