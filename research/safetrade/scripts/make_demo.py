#!/usr/bin/env python3
"""Generate reproducible synthetic data with a ten-second target delay. No network."""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import yaml

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
from adapters.events import make_event, new_run_id
from storage import ParquetWriter, JsonlRotator, StateStore


def make_demo(out, duration_s=600):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load((CODE/'config/config.yaml').read_text())
    config['oracle'].update(window_min=2, premium_min_span_s=30, premium_min_points=10)
    config['storage'].update(disk_warn_bytes=0, disk_limit_bytes=0)
    (out/'config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    run, boot = new_run_id('demo_lag10'), 'synthetic_boot'
    rng = np.random.default_rng(73)
    returns = rng.normal(0, .001, duration_s)
    returns[:90] = 0
    prices = 100*np.exp(np.cumsum(returns))
    mono_anchor = 10_000_000_000_000_000
    utc_anchor = time.time_ns()-duration_s*1_000_000_000
    writer = ParquetWriter(str(out/'data/parquet'), run)
    raw = JsonlRotator(str(out/'data/raw'),run)
    latest = []
    for second in range(duration_s):
        batch = []
        mono, utc = mono_anchor+second*1_000_000_000, utc_anchor+second*1_000_000_000
        for ex in config['sources']:
            p = prices[second]
            ev = make_event(ex,'BTCUSDT','BTCUSDT','bbo',recv_mono=mono,recv_utc=utc,run_id=run,boot_id=boot,
                            quality_flags=['synthetic'],
                            payload={'bid_price':str(p-.01),'ask_price':str(p+.01),'fixture':True})
            batch.append(ev)
        p = prices[max(0,second-10)]
        batch.append(make_event('safetrade','BTCUSDT','btcusdt','book_snapshot',recv_mono=mono,recv_utc=utc,
                               run_id=run,boot_id=boot,quality_flags=['synthetic'],
                               bids=[[str(p-.01),'10']],asks=[[str(p+.01),'10']],
                               payload={'synchronized':True,'fixture':True}))
        for ev in batch:
            ev['raw_ref'] = raw.write({'exchange':ev['exchange'],'symbol':'BTCUSDT',
                                      'recv_utc_ns':utc,'recv_mono_ns':mono,'raw':json.dumps(ev),'synthetic':True})
            writer.add(ev)
        latest = batch
    writer.close(); raw.close()
    db = StateStore(str(out/'data/state.db'))
    db.start_run(run,boot,config['sources']+['safetrade'],['BTC/USDT'])
    db.set_ui('latest_events',latest)
    db.set_ui('counters',{'run_id':run,'events_flushed':duration_s*4,'mode':'synthetic'})
    db.stop_run(run,'demo'); db.close()
    return out


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True)
    args = p.parse_args()
    print(make_demo(args.out))
