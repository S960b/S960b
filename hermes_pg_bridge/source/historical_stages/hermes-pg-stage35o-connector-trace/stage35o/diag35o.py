#!/usr/bin/env python3
"""Read-only forensic snapshot of a failed ChatGPT tunnel plugin enrollment.
Never exposes raw journal lines, tokens, messages, IDs, headers, or response bodies.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

TUN = 'hermes-pg-tunnel-stage35jk.service'
MCP = 'hermes-pg-stage35c-static-shadow.service'
LEGACY = 'hermes-pg-tunnel-stage33fd.service'
BASE = 'http://127.0.0.1:18778'
MAX = 8 * 1024 * 1024


def command(args, limit=MAX):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=12, check=False)
        if len(p.stdout) > limit:
            return None
        return p.stdout.decode('utf-8', 'replace') if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def http(path):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(BASE + path, timeout=4) as r:
            data = r.read(200000)
            return r.status, data
    except urllib.error.HTTPError as e:
        e.close()
        return e.code, b''
    except (OSError, TimeoutError, ValueError):
        return None, b''


def metric_value(raw, name):
    # Only exact, innocuous metrics; do not expose arbitrary labels or raw metric data.
    for line in raw.splitlines():
        if line.startswith(name + ' '):
            try:
                return float(line.split()[1])
            except (ValueError, IndexError):
                return None
    return None


CATEGORIES = {
    'upstream_error': re.compile(r'mcp upstream error|upstream.*(?:failed|error)', re.I),
    'unsupported_channel': re.compile(r'unsupported channel|unsupported_channel', re.I),
    'poll_error': re.compile(r'(?:poll|control.plane).{0,65}(?:failed|error|unauthorized|forbidden|401|403)', re.I),
    'response_error': re.compile(r'(?:post|deliver|response).{0,65}(?:failed|error|unauthorized|forbidden|401|403)', re.I),
    'http_401': re.compile(r'\b401\b|unauthorized', re.I),
    'http_403': re.compile(r'\b403\b|forbidden', re.I),
    'http_404': re.compile(r'\b404\b', re.I),
    'http_424': re.compile(r'\b424\b', re.I),
    'http_5xx': re.compile(r'\b5\d{2}\b', re.I),
    'rpc_initialize': re.compile(r'\binitialize\b', re.I),
    'rpc_tools_list': re.compile(r'tools/list', re.I),
    'harpoon': re.compile(r'\bharpoon\b', re.I),
    'oauth': re.compile(r'\boauth\b', re.I),
}


def summarize_logs(raw):
    total = 0
    recent = 0
    totals = Counter()
    last5 = Counter()
    now_us = int(time.time() * 1_000_000)
    for line in (raw or '').splitlines():
        try:
            obj = json.loads(line)
            msg = obj.get('MESSAGE', '')
            if not isinstance(msg, str):
                continue
            timestamp = int(obj.get('__REALTIME_TIMESTAMP', 0))
        except (ValueError, TypeError):
            continue
        total += 1
        is_recent = 0 <= now_us - timestamp <= 5 * 60 * 1_000_000
        if is_recent:
            recent += 1
        for cat, pat in CATEGORIES.items():
            if pat.search(msg):
                totals[cat] += 1
                if is_recent:
                    last5[cat] += 1
    return total, recent, totals, last5


def status(unit):
    out = command(['systemctl', '--user', 'show', unit,
                   '--property=ActiveState,SubState,Result', '--no-pager'], 10000)
    if out is None:
        return 'unavailable'
    fields = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    def keep(key):
        value = fields.get(key, '?')
        return value if re.fullmatch(r'[a-z-]{1,32}', value) else '?'
    return '/'.join(keep(x) for x in ('ActiveState', 'SubState', 'Result'))


def main():
    if os.geteuid() == 0 or os.environ.get('HOME') != '/home/kali' or command(['id', '-un'], 128) != 'kali\n':
        raise SystemExit('STAGE35O_STOP_RUN_AS_KALI_ONLY')
    print('STAGE35O_READONLY_CONNECTOR_RECREATE_DIAGNOSTIC')
    for unit, label in [(TUN, 'new_tunnel'), (MCP, 'managed_mcp'), (LEGACY, 'old_tunnel')]:
        print('STAGE35O_SERVICE_' + label.upper(), status(unit))
    st, _ = http('/readyz')
    print('STAGE35O_READYZ_HTTP', st if st is not None else 'unreachable')
    st, raw = http('/health/oauth')
    oauth = 'unknown'
    if st == 200:
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                for k in ('state', 'status'):
                    if d.get(k) in ('disabled', 'enabled'):
                        oauth = d[k]
        except (ValueError, TypeError):
            pass
    print('STAGE35O_OAUTH', str(st or 'unreachable'), oauth)
    st, raw = http('/api/status')
    if st == 200:
        try:
            data = json.loads(raw)
            # Only the presence of the main / harpoon channel; no IDs or URLs.
            channels = data.get('channels', {}) if isinstance(data, dict) else {}
            if isinstance(channels, list):
                names = [str(c.get('name','')) for c in channels if isinstance(c,dict)]
            elif isinstance(channels, dict):
                names = list(channels)
            else:
                names = []
            print('STAGE35O_CHANNELS main=%s harpoon=%s' % ('main' in names, 'harpoon' in names))
        except (ValueError, TypeError):
            print('STAGE35O_CHANNELS unknown')
    else:
        print('STAGE35O_STATUS_HTTP', st if st is not None else 'unreachable')
    st, raw = http('/metrics')
    print('STAGE35O_METRICS_HTTP', st if st is not None else 'unreachable')
    if st == 200:
        metrics = raw.decode('utf-8', 'replace')
        v = metric_value(metrics, 'commands_poll_last_successful_timestamp_seconds')
        if v is None:
            print('STAGE35O_POLL_METRIC_UNAVAILABLE')
        else:
            ago = time.time() - v
            print('STAGE35O_CONTROL_PLANE_POLL_SUCCESS_OBSERVED', bool(v > 0))
            print('STAGE35O_CONTROL_PLANE_POLL_RECENT_5MIN', bool(v > 0 and -15 <= ago <= 300))
    for unit, label in [(TUN, 'TUNNEL'), (MCP, 'MCP')]:
        out = command(['journalctl', '--user', '-u', unit, '--since', '20 minutes ago',
                       '--no-pager', '-o', 'json'])
        if out is None:
            print('STAGE35O_' + label + '_JOURNAL_UNAVAILABLE_OR_TOO_LARGE')
            continue
        total, recent, sums, recent_sums = summarize_logs(out)
        print('STAGE35O_%s_JOURNAL_LINES_20MIN=%s_LAST5MIN=%s' % (label,total,recent))
        for cat in CATEGORIES:
            print('STAGE35O_%s_%s_20MIN=%s_LAST5MIN=%s' % (
                label, cat.upper(), sums[cat], recent_sums[cat]))
    print('STAGE35O_DONE_READONLY_NO_SERVICE_OR_DB_CHANGES')


if __name__ == '__main__':
    main()
