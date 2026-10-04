#!/usr/bin/env python3
"""Frozen-snapshot recalculation of side semantics (BBO fix, step 7 acceptance).

Читает ТОЛЬКО замороженные JSONL снапшота (depth + trades); live-файлы не
читает, collector не трогает. Применяет cutoff (UTC ISO) к таймстампам данных
(depth: t; trades: created_at), прогоняет analyze() из исправленного
side_semantics_check.py и пишет воспроизводимый JSON-отчёт:
SHA256 обоих входов, параметры tick/tolerance/max_age, per-pair счётчики и
доли, проверки инвариантов с явной схемой полей.

Пример:
  python3 scripts/side_semantics_frozen_run.py \
      --snapshot-dir /home/kali/side_semantics_snapshot_20261004T1916 \
      --run-id mk_18db19212772b000 \
      --cutoff 2026-10-04T16:15:37Z \
      --out research/safetrade/reports/side_semantics_mk_18db19212772b000_cutoff_20261004T161537Z.json
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # scripts/ -> side_semantics_check
# noqa: E402 — импорт после настройки sys.path
from side_semantics_check import (  # noqa: E402
    DEFAULT_TICK, MAX_AGE_S, TICK, analyze, parse_iso_utc,
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path):
    rows = []
    with open(path) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    return rows


def apply_cutoff(depth_rows, trade_rows, cutoff_epoch):
    """Возвращает (depth_kept, depth_dropped, trades_kept_polls, trades_total,
    trades_kept, trades_dropped)."""
    depth_kept, depth_dropped = [], 0
    for r in depth_rows:
        if not isinstance(r, dict):
            continue
        if (r.get('t') or 0) / 1e9 <= cutoff_epoch + 1e-9:
            depth_kept.append(r)
        else:
            depth_dropped += 1
    trades_total, trades_kept, trades_dropped = 0, 0, 0
    polls = []
    for poll in trade_rows:
        if not isinstance(poll, dict):
            continue
        kept = []
        for t in poll.get('trades', []):
            if not isinstance(t, dict):
                continue
            ts = parse_iso_utc(t.get('created_at'))
            if ts is None:
                continue
            trades_total += 1
            if ts <= cutoff_epoch + 1e-9:
                kept.append(t)
                trades_kept += 1
            else:
                trades_dropped += 1
        p2 = dict(poll)
        p2['trades'] = kept
        polls.append(p2)
    return depth_kept, depth_dropped, polls, trades_total, trades_kept, trades_dropped


def f4(x):
    return None if x is None else round(float(x), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--snapshot-dir', required=True)
    ap.add_argument('--run-id', default='mk_18db19212772b000')
    ap.add_argument('--cutoff', required=True, help='UTC ISO, напр. 2026-10-04T16:15:37Z')
    ap.add_argument('--out', required=True)
    ap.add_argument('--max-age', type=float, default=MAX_AGE_S)
    ap.add_argument('--tick-fallback', default=str(DEFAULT_TICK))
    args = ap.parse_args()

    cutoff_dt = datetime.fromisoformat(args.cutoff.replace('Z', '+00:00'))
    cutoff_epoch = cutoff_dt.timestamp()

    depth_path = os.path.join(args.snapshot_dir, f'{args.run_id}_depth.jsonl')
    trades_path = os.path.join(args.snapshot_dir, f'{args.run_id}_trades.jsonl')

    depth_rows = load_jsonl(depth_path)
    trade_rows = load_jsonl(trades_path)
    depth_kept, depth_dropped, polls, trades_total, trades_kept, trades_dropped = \
        apply_cutoff(depth_rows, trade_rows, cutoff_epoch)

    agg = analyze(depth_kept, polls, ticks=dict(TICK),
                  max_age_s=args.max_age,
                  fallback_tick=Decimal(args.tick_fallback))

    now = datetime.now(timezone.utc).isoformat()
    scripts_dir = HERE
    check_sha = sha256_file(os.path.join(scripts_dir, 'side_semantics_check.py'))
    runner_sha = sha256_file(os.path.abspath(__file__))

    pairs = {}
    inv = {'total_breakdown': {}, 'unique_simplified': {}, 'conservation': {},
           'classified_breakdown': {}}
    for pair in sorted(agg):
        a = agg[pair]
        i1 = a['total'] == a['unique'] + a['duplicates'] + a['missing_id']
        i2 = a['unique'] == a['classified'] + a['ambiguous'] + a['indet']
        i3 = (a['unique'] + a['missing_id'] ==
              a['classified'] + a['ambiguous'] + a['indet'] +
              a['no_snap'] + a['unverified'])
        i4 = a['classified'] == a['aggr_ok'] + a['passive_ok']
        pairs[pair] = {
            'total_rows': a['total'],
            'unique': a['unique'],
            'duplicates': a['duplicates'],
            'missing_id': a['missing_id'],
            'classified': a['classified'],
            'aggressor_ok': a['aggr_ok'],
            'passive_ok': a['passive_ok'],
            'ambiguous': a['ambiguous'],
            'indet': a['indet'],
            'no_snap': a['no_snap'],
            'unverified': a['unverified'],
            'aggressor_share_pct': f4(a['aggr_pct']),
            'passive_share_pct': f4(a['passive_pct']),
            'classified_share_of_total_pct': f4(
                a['classified'] / a['total'] * 100 if a['total'] else None),
            'verdict': a['verdict'],
            'examples': a['examples'],
            'inv_total_breakdown': i1,
            'inv_unique_simplified': i2,
            'inv_conservation': i3,
            'inv_classified_breakdown': i4,
        }
        inv['total_breakdown'][pair] = i1
        inv['unique_simplified'][pair] = i2
        inv['conservation'][pair] = i3
        inv['classified_breakdown'][pair] = i4

    report = {
        'schema': 'safetrade.side_semantics.frozen_run/v1',
        'generated_at_utc': now,
        'run_id': args.run_id,
        'cutoff_utc': args.cutoff,
        'cutoff_epoch': cutoff_epoch,
        'invocation': 'python3 scripts/side_semantics_frozen_run.py '
                      f'--snapshot-dir {args.snapshot_dir} --run-id {args.run_id} '
                      f'--cutoff {args.cutoff} --out {args.out}',
        'inputs': {
            'depth': {
                'path': depth_path, 'sha256': sha256_file(depth_path),
                'rows_total': len(depth_rows), 'rows_in_window': len(depth_kept),
                'rows_dropped_by_cutoff': depth_dropped},
            'trades': {
                'path': trades_path, 'sha256': sha256_file(trades_path),
                'poll_rows_total': len(trade_rows),
                'poll_rows_kept': len(polls),
                'trades_total': trades_total,
                'trades_in_window': trades_kept,
                'trades_dropped_by_cutoff': trades_dropped},
        },
        'parameters': {
            'tick_per_pair': {k: str(v) for k, v in TICK.items()},
            'tick_fallback': args.tick_fallback,
            'max_snapshot_age_s': args.max_age,
            'boundary': 'BBO-only: best_bid=max(bids), best_ask=min(asks); '
                        'deep levels are not spread boundaries',
            'dedup_key': '(pair, id); rows without id -> missing_id '
                         '(not deduped, still classified); duplicates skip '
                         'classification',
            'percent_denominator': 'classified = aggressor_ok + passive_ok',
            'side_unverified': 'preserved as unverified, not classified',
            'scripts': {
                'side_semantics_check.py': {'path': 'research/safetrade/scripts/side_semantics_check.py',
                                            'sha256': check_sha},
                'runner': {'path': os.path.relpath(os.path.abspath(__file__)),
                           'sha256': runner_sha},
            },
        },
        'invariants': {
            'scheme': ('поле-схема: total_rows = все строки сделок с валидным '
                       'created_at+price; unique = первые вхождения (pair,id); '
                       'duplicates = повторные (pair,id) (не классифицируются); '
                       'missing_id = строки без id (не дедуплицируются, но '
                       'классифицируются); classified = aggressor_ok + passive_ok; '
                       'каждая строка (unique или missing_id) попадает ровно в '
                       'один исход: classified | ambiguous | indet | no_snap | '
                       'unverified. Точные тождества: total_rows = unique + '
                       'duplicates + missing_id; unique + missing_id = classified '
                       '+ ambiguous + indet + no_snap + unverified. '
                       'Упрощённый вид из ТЗ "unique = classified + ambiguous + '
                       'indet" выполняется строго только при missing_id=0 '
                       '(не входит в него) и no_snap=0, unverified=0.'),
            'checks': {
                'total_breakdown': {
                    'formula': 'total_rows == unique + duplicates + missing_id',
                    'per_pair': inv['total_breakdown'],
                    'all_hold': all(inv['total_breakdown'].values())},
                'unique_simplified': {
                    'formula': 'unique == classified + ambiguous + indet '
                               '(строгий вид ТЗ; верен iff no_snap==0 and '
                               'unverified==0)',
                    'per_pair': inv['unique_simplified'],
                    'all_hold': all(inv['unique_simplified'].values())},
                'conservation': {
                    'formula': 'unique + missing_id == classified + ambiguous + '
                               'indet + no_snap + unverified',
                    'per_pair': inv['conservation'],
                    'all_hold': all(inv['conservation'].values())},
                'classified_breakdown': {
                    'formula': 'classified == aggressor_ok + passive_ok',
                    'per_pair': inv['classified_breakdown'],
                    'all_hold': all(inv['classified_breakdown'].values())},
            },
        },
        'pairs': pairs,
    }

    with open(args.out, 'w') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write('\n')

    print(f'run={args.run_id} cutoff={args.cutoff} max_age_s={args.max_age} '
          f'| frozen snapshot, BBO-фикс:')
    print(f'{"pair":<14} {"total":>6} {"uniq":>5} {"dup":>4} {"miss":>5} '
          f'{"aggr":>5} {"pass":>5} {"amb":>4} {"indet":>5} {"no_snap":>7} '
          f'{"unver":>6}  {"доли от classified":>24}')
    for pair, p in pairs.items():
        pct = (f'aggr={p["aggressor_share_pct"]:.2f}% pass={p["passive_share_pct"]:.2f}%'
               if p['classified'] else 'classified=0')
        print(f'{pair:<14} {p["total_rows"]:>6} {p["unique"]:>5} '
              f'{p["duplicates"]:>4} {p["missing_id"]:>5} {p["aggressor_ok"]:>5} '
              f'{p["passive_ok"]:>5} {p["ambiguous"]:>4} {p["indet"]:>5} '
              f'{p["no_snap"]:>7} {p["unverified"]:>6}  {pct}')
        print(f'   {pair}: {p["verdict"]} '
              f'(aggr {p["aggressor_ok"]}/{p["classified"]}) '
              f'| inv: total={p["inv_total_breakdown"]} '
              f'uniq_simpl={p["inv_unique_simplified"]} '
              f'consv={p["inv_conservation"]} cls={p["inv_classified_breakdown"]}')
    print(f'JSON: {os.path.abspath(args.out)}')


if __name__ == '__main__':
    main()