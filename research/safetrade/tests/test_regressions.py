"""Financial invariants and known-answer fixtures, not merely parser smoke tests."""
import asyncio
import copy
import gzip
import json
import math
import time
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from adapters.events import make_event
from analysis.book import OrderBook, load_parquet_all
from analysis.oracle import prepare_mid_column, premium_series, build_oracle_series, oracle_asof
from analysis.leadlag import lead_lag, coverage_stats
from analysis.reach import test_A_reach as reach_A, test_B_executable as reach_B
from simulator.paper import Signal, PaperSimulator, OrderBookReplay

BASE = Path(__file__).resolve().parents[1]
NANO = 1_000_000_000


def cfg():
    return yaml.safe_load((BASE/'config/config.yaml').read_text())


def book(t, price=100, exchange='safetrade', symbol='BTCUSDT', qty='10', flags=None, ready=True, run='r'):
    return make_event(exchange, symbol, symbol.lower(), 'book_snapshot', recv_mono=int(t*NANO),
                      recv_utc=1_700_000_000_000_000_000+int(t*NANO),
                      bids=[[str(price-.01), qty]], asks=[[str(price+.01), qty]], run_id=run,
                      quality_flags=flags, payload={'synchronized': ready, 'fixture': True})


def sig(t=10, target=102):
    return Signal(int(t*NANO), target, target, 'up', 3, [], 0.0, 5, signal_utc_ns=1_700_000_000_000_000_000+int(t*NANO))


def test_books_are_isolated_across_exchange_symbol_and_run():
    events = [book(1, 100, exchange='a'), book(2, 1000, exchange='b'),
              book(3, 200, exchange='a', symbol='ETHUSDT'), book(4, 500, exchange='a', run='other'),
              make_event('a', 'BTCUSDT', 'x', 'book_delta', recv_mono=5*NANO, recv_utc=5*NANO,
                         bids=[['100.1','2']], run_id='r')]
    result = prepare_mid_column(pd.DataFrame(events))
    # The last delta affects only the first book; all other books remain separate.
    # Remove old ask to avoid a crossed book, then compare the stored state.
    events[-1]['bids'] = [['99.99', '3']]
    result = prepare_mid_column(pd.DataFrame(events))
    assert result.iloc[-1]._mid == pytest.approx(100)
    assert list(result._mid.iloc[:4]) == pytest.approx([100, 1000, 200, 500])


def test_unsynchronised_safe_delta_does_not_modify_rest():
    snapshot = book(1, flags=['rest_provisional'], ready=False)
    delta = make_event('safetrade', 'BTCUSDT', 'btcusdt', 'book_delta', recv_mono=2*NANO,
                       bids=[['500', '2']], asks=[['501','2']], run_id='r')
    frame = prepare_mid_column(pd.DataFrame([snapshot, delta]))
    assert frame._selected.tolist() == [True, False]
    replay = OrderBookReplay([snapshot, delta])
    assert replay.asof_ns(2*NANO) is None
    assert float(replay.asof_ns(2*NANO, require_executable=False).mid()) == 100


def test_external_bbo_is_not_overwritten_by_book_channel():
    bbo = make_event('a', 'BTCUSDT', 'x', 'bbo', recv_mono=NANO,
                     payload={'bid_price':'99', 'ask_price':'101'}, run_id='r')
    s = book(2, price=1000, exchange='a')
    frame = prepare_mid_column(pd.DataFrame([bbo, s]))
    assert frame._selected.tolist() == [True, False]


def test_invalid_quote_is_a_barrier_not_dropped():
    evs = []
    for ex in ['a', 'b', 'c']:
        evs.append(make_event(ex,'BTCUSDT','x','bbo',recv_mono=NANO,
                             payload={'bid_price':'99','ask_price':'101'}))
    evs.append(make_event('a','BTCUSDT','x','health',recv_mono=2*NANO,
                         quality_flags=['connection_reset']))
    rs = build_oracle_series(prepare_mid_column(pd.DataFrame(evs)), ['a','b','c'])
    assert oracle_asof(rs, NANO) == 100
    assert oracle_asof(rs, 2*NANO) is None


def test_oracle_expiry_is_earliest_source_expiry():
    evs = [make_event(ex,'BTCUSDT','x','bbo',recv_mono=int(t*NANO),
                      payload={'bid_price':'99','ask_price':'101'})
           for ex,t in [('a',1),('b',2.5),('c',2.8)]]
    rs = build_oracle_series(prepare_mid_column(pd.DataFrame(evs)), ['a','b','c'], 2)
    assert oracle_asof(rs, int(2.9*NANO), 2) == 100
    assert oracle_asof(rs, int(3.1*NANO), 2) is None


def test_premium_zero_when_contemporaneous_prices_equal_and_rising():
    times = np.arange(1, 61)*NANO
    prices = np.linspace(100, 130, 60)
    r = np.array(list(zip(times, prices, [3]*60)), dtype=object)
    t,b,n = premium_series(list(zip(times,prices)), r, min_points=10)
    assert len(b) == 50
    assert np.max(np.abs(b)) < 1e-12
    changed = list(zip(times,prices))
    changed[20] = (times[20], 10000)
    _, b2, _ = premium_series(changed, r, min_points=10)
    assert b2[10] == b[10]  # current observation cannot change its own premium


def test_known_ten_second_delay_is_positive_ten():
    rng = np.random.default_rng(73)
    prices = 100*np.exp(np.cumsum(rng.normal(0, .001, 450)))
    t = np.arange(450, dtype=np.int64)*NANO
    ext = pd.DataFrame({'mono_ns':t, 'mid':prices})
    safe = pd.DataFrame({'mono_ns':t+10*NANO, 'mid':prices})
    report = lead_lag({'a':ext},safe,5,max_lag_s=20,max_age_s=2)
    values = report['correlations']['a']
    best = max(values, key=lambda lag: values[lag]['corr'])
    assert best == 10
    assert values[10]['corr'] == pytest.approx(1)
    assert all(0 <= v['coverage'] <= 1 for v in coverage_stats({'a':ext}).values())


def test_reach_internal_gap_is_unknown_and_targets_are_independent():
    source = {'mid_ts':[(0,100), (2*NANO,100), (50*NANO,100)], 'max_age_s':5,
              'record_end_ns':60*NANO}
    r = reach_A(source, 102, None, NANO, [30], 1)
    assert r[30]['status_raw']=='unknown'
    assert r[30]['status_adj']=='unavailable'
    source = {'mid_ts':[(0,102), (2*NANO,104)], 'mid_at_t0':102}
    r = reach_A(source, 101, 103, NANO, [5], 1)
    assert r[5]['status_raw']=='already_at_target'
    assert r[5]['status_adj']=='reached'
    down = reach_A(source, 103, 101, NANO, [5], 1, 'down')
    assert down[5]['status_raw']=='already_at_target'
    assert down[5]['status_adj']=='unknown'
    assert reach_B({'bids_vwap_ts':[]},Decimal('.1'),Decimal('100'),0,[5],10,10)[5]['status_exec']=='unknown'


def test_paper_budget_fees_and_one_position_per_scenario():
    c = cfg()
    replay = OrderBookReplay([book(t, price=100 if t<12 else 101) for t in range(10,31)], 5)
    sim = PaperSimulator(c,['BTCUSDT'])
    result = sim.run_hypothesis([sig(10),sig(11),sig(13)], replay, 'A',5,0,2,[0],[25],'BTCUSDT')
    scenario = result['stats']['scenarios']['L0_Q25']
    assert scenario['n_entries']==2 and scenario['n_closed']==2
    assert scenario['rejections']['position_busy']==1
    assert scenario['cash_usdt'] == pytest.approx(100+scenario['realized_pnl_usdt'])
    for tr in result['trades']:
        cost = tr.entry_vwap*tr.qty+tr.fee_buy
        assert cost <= Decimal('25')
        assert tr.pnl == tr.qty*tr.exit_vwap-tr.fee_sell-cost
        assert tr.signal_utc_ns > 1_000_000_000_000_000_000


def test_unknown_exit_keeps_cash_and_open_position():
    c = cfg()
    replay = OrderBookReplay([book(10),book(11)])
    result = PaperSimulator(c,[]).run_hypothesis([sig(10),sig(12)], replay,'A',5,0,30,[0],[25],'BTCUSDT')
    scenario = result['stats']['scenarios']['L0_Q25']
    assert scenario['n_entries']==1 and scenario['n_open_unknown']==1
    assert scenario['cash_usdt'] < 100
    assert scenario['realized_pnl_usdt']==0
    assert result['trades'][0].status=='open_unknown'
    assert result['trades'][0].pnl is None


def test_insufficient_depth_and_provisional_book_are_rejected():
    for events in [[book(10, qty='.001'), book(12)],
                   [book(10,flags=['rest_provisional']),book(12)]]:
        result = PaperSimulator(cfg(),[]).run_hypothesis([sig()],OrderBookReplay(events),'A',5,0,2,[0],[25],'BTCUSDT')
        assert result['stats']['scenarios']['L0_Q25']['n_entries']==0


def test_future_favorable_entry_is_not_used_for_decision():
    # No edge at decision; future cheap quotes cannot retrospectively approve an order.
    events = [book(10,105),book(11,100),book(12,103)]
    result = PaperSimulator(cfg(),[]).run_hypothesis([sig()],OrderBookReplay(events),'A',5,0,1,[1000],[25],'BTCUSDT')
    assert result['stats']['scenarios']['L1000_Q25']['n_entries']==0
    # With a good decision book and then adverse price, the fixed cap prevents fill.
    events = [book(10,100),book(11,105),book(12,103)]
    result = PaperSimulator(cfg(),[]).run_hypothesis([sig()],OrderBookReplay(events),'A',5,0,1,[1000],[25],'BTCUSDT')
    assert result['stats']['scenarios']['L1000_Q25']['rejections']['entry_unfilled']==1


def test_latest_run_uses_metadata_and_never_mixes_monotonic_clocks(tmp_path):
    from storage import ParquetWriter
    for run, utc, mono in [('z_old',1_700_000_000_000_000_000,999*NANO),('a_new',1_800_000_000_000_000_000,NANO)]:
        writer = ParquetWriter(str(tmp_path),run)
        ev = book(1,run=run); ev['recv_utc_ns']=utc; ev['recv_monotonic_ns']=mono
        writer.add(ev); writer.close()
    df = load_parquet_all(str(tmp_path))
    assert df.run_id.unique().tolist()==['a_new']
    assert load_parquet_all(str(tmp_path),run_id='z_old').run_id.unique().tolist()==['z_old']
    with pytest.raises(ValueError):
        load_parquet_all(str(tmp_path),max_runs=2)


def test_raw_restart_offsets_and_idle_flush(tmp_path):
    from storage import JsonlRotator, ParquetWriter
    refs = []
    for i in range(2):
        writer = JsonlRotator(str(tmp_path),'same')
        writer.write({'текст':'монета'})
        refs.append(writer.write({'i':i}))
        writer.close()
    assert len(list(tmp_path.glob('*.jsonl.gz')))==2
    for ref in refs:
        name,offset = ref.split('#')
        data = gzip.open(tmp_path/(name+'.gz'),'rb').read()
        assert json.loads(data[int(offset):].splitlines()[0])['i'] in (0,1)
    writer = ParquetWriter(str(tmp_path/'pq'),'r',flush_interval_s=.01)
    writer.add(book(1))
    time.sleep(.02)
    writer.flush_due()
    assert writer.rows_flushed==1


def test_writer_error_preserves_buffer_for_retry(tmp_path,monkeypatch):
    from storage import ParquetWriter
    import storage.storage as storage
    writer = ParquetWriter(str(tmp_path),'r')
    writer.add(book(1))
    real = storage.pq.write_table
    def fail(*args,**kwargs):
        raise OSError('disk fault')
    monkeypatch.setattr(storage.pq,'write_table',fail)
    with pytest.raises(OSError):
        writer.close()
    assert sum(len(b) for b in writer._buffers.values())==1
    monkeypatch.setattr(storage.pq,'write_table',real)
    writer.close()
    assert len(load_parquet_all(str(tmp_path)))==1


def test_collector_drains_links_raw_and_stops_on_writer_failure(tmp_path,monkeypatch):
    import collector.collector as module
    class FakeAdapter:
        def __init__(self,ex,raw_q): self.exchange,self.raw_q=ex,raw_q
        def health(self): return {'connected':True}
        async def stream(self,symbol,sink,run_id,boot_id):
            for i in range(8):
                ev = book(i+1,exchange=self.exchange,run=run_id); ev['boot_id']=boot_id
                self.raw_q.put_nowait({'exchange':self.exchange,'symbol':symbol,'recv_mono_ns':ev['recv_monotonic_ns'],'raw':'{}'})
                await sink.put(ev)
            await asyncio.Event().wait()
    monkeypatch.setattr(module,'get_adapter',lambda ex,**kw: FakeAdapter(ex,kw['raw_q']))
    c = cfg(); c['storage']['disk_limit_bytes']=0; c['storage']['disk_warn_bytes']=0
    collector = module.Collector(c,str(tmp_path))
    asyncio.run(collector.run(duration_s=.05))
    assert collector.c['events_enqueued']==collector.c['events_written']==collector.c['events_flushed']==32
    assert collector.c['raw_unlinked']==0
    df = load_parquet_all(str(tmp_path/'data/parquet'))
    assert len(df)==32 and df.raw_ref.str.len().min()>0
    failing = module.Collector(c,str(tmp_path/'fail'))
    def fail(_): raise OSError('forced write failure')
    monkeypatch.setattr(failing.pq_writer,'add',fail)
    with pytest.raises(RuntimeError,match='writer_failure'):
        asyncio.run(asyncio.wait_for(failing.run(),timeout=3))


def test_ws_silence_does_not_reconnect_and_bybit_empty_is_safe():
    from adapters.binance import BinanceAdapter
    from adapters.bybit import BybitAdapter
    class SlowSocket:
        async def recv(self):
            await asyncio.sleep(.02); return '{}'
    asyncio.run(BinanceAdapter()._ws_recv_msg(SlowSocket()))
    event = BybitAdapter()._parse('BTC/USDT','BTCUSDT',{'topic':'orderbook.1.BTCUSDT','ts':123,'data':{'b':[],'a':[]}},1,1,'r','b')
    assert event['exchange_event_ts']==123
    assert prepare_mid_column(pd.DataFrame([event]))._mid.isna().all()


def test_rest_observation_continues_during_ws_failures(monkeypatch):
    from adapters.safetrade import SafeTradeAdapter
    ad = SafeTradeAdapter(raw_q=asyncio.Queue(),rest_snapshot_s=.01)
    async def depth(*args):
        return '{}',{'bids':[['99','1']],'asks':[['101','1']]},time.time_ns(),time.monotonic_ns(),time.time_ns(),time.monotonic_ns()
    async def no_ws(*args,**kwargs): raise OSError('WS blocked')
    monkeypatch.setattr(ad,'rest_depth',depth); monkeypatch.setattr(ad,'connect',no_ws)
    async def run():
        q = asyncio.Queue(); task=asyncio.create_task(ad.stream('BTC/USDT',q,'r','b'))
        await asyncio.sleep(.15); task.cancel(); await asyncio.gather(task,return_exceptions=True)
        return list(q._queue)
    events = asyncio.run(run())
    assert sum(e['event_type']=='book_snapshot' for e in events)>=2
    assert ad.health()['connected'] is False


def test_dashboard_uses_safe_quote_and_three_external_only():
    from dashboard.model import quote_table
    now = 1_700_000_001_000_000_000
    evs = [make_event(ex,'BTCUSDT','x','bbo',recv_utc=now,recv_mono=NANO,
                      payload={'bid_price':str(price-1),'ask_price':str(price+1)})
           for ex,price in [('binance',100),('okx',101),('bybit',102)]]
    evs.append(book(1,price=900,ready=False,flags=['rest_provisional']))
    table,r,n = quote_table(pd.DataFrame(evs),cfg(),now)
    assert r==101 and n==3
    assert table[table['Биржа']=='safetrade'].iloc[0]['Mid']==900
    _,stale,n = quote_table(pd.DataFrame(evs),cfg(),now+3*NANO)
    assert stale is None and n==0


def _pipeline(tmp_path):
    from scripts.make_demo import make_demo
    from analysis.analyze_run import run_full_analysis, run_paper
    out = make_demo(tmp_path/'demo')
    c = yaml.safe_load((out/'config.yaml').read_text())
    df = load_parquet_all(str(out/'data/parquet'))
    assert len(df)==2400
    analysis = run_full_analysis(c,str(out),df,'BTCUSDT')
    assert analysis['synthetic'] is True
    assert analysis['premium']['warmup_ok']
    for values in analysis['lead_lag_W5']['correlations'].values():
        assert max(values,key=lambda lag:values[lag]['corr'])==10
    paper = run_paper(c,str(out),df,'BTCUSDT')
    assert len(paper['hypotheses']['A']['scenarios'])==12
    trades = pd.read_csv(out/'reports/paper_trades_BTCUSDT.csv')
    assert not trades.empty
    assert all(v['cash_usdt']>=0 for h in paper['hypotheses'].values() for v in h['scenarios'].values())
    json.loads((out/'reports/analysis_BTCUSDT.json').read_text())
    return out


def test_full_pipeline_and_saved_reports(tmp_path):
    _pipeline(tmp_path)


@pytest.mark.parametrize('with_data',[False, True])
def test_streamlit_empty_and_complete_data(tmp_path,monkeypatch,with_data):
    pytest.importorskip('streamlit')
    pytest.importorskip('plotly')
    from streamlit.testing.v1 import AppTest
    out = _pipeline(tmp_path) if with_data else tmp_path
    monkeypatch.setenv('SAFETRADE_BASE',str(out))
    monkeypatch.setenv('SAFETRADE_CONFIG',str(out/'config.yaml') if with_data else '')
    app = AppTest.from_file(str(BASE/'dashboard/app.py'),default_timeout=30).run()
    assert not app.exception
    assert len(app.tabs)==5
    if with_data:
        assert len(app.dataframe)>=3


def test_nan_crossed_and_invalid_books_do_not_produce_mid():
    events = [book(1)]
    crossed = book(2); crossed['bids']=[['500','1']]
    invalid = book(3,flags=['book_invalid'])
    nan = book(4); nan['asks']=[['NaN','1']]
    events.extend([crossed,invalid,nan])
    prepared = prepare_mid_column(pd.DataFrame(events))
    assert prepared._mid.iloc[1:].isna().all()


def test_nanosecond_timestamps_survive_and_signals_are_causal():
    c = cfg()
    from analysis.analyze_run import mono_to_utc
    anchor = 10_000_000_000_000_003
    utc = 1_800_000_000_000_000_013
    series = {ex:pd.DataFrame({'mono_ns':anchor+np.arange(60,dtype=np.int64)*NANO,
                              'utc_ns':utc+np.arange(60,dtype=np.int64)*NANO,
                              'mid':100+np.arange(60)*.1}) for ex in c['sources']}
    assert mono_to_utc(series,anchor+10*NANO)==utc+10*NANO
    full,_ = PaperSimulator(c,[]).detect_signals(series,anchor,anchor+60*NANO,5,0)
    past = {ex:s[s.mono_ns<=anchor+30*NANO].copy() for ex,s in series.items()}
    prefix,_ = PaperSimulator(c,[]).detect_signals(past,anchor,anchor+30*NANO,5,0)
    assert [s.t0_ns for s in prefix] == [s.t0_ns for s in full if s.t0_ns<=anchor+30*NANO]
    assert full[0].signal_utc_ns == utc+(full[0].t0_ns-anchor)


def test_unknown_arrival_reserves_budget_and_censors_account():
    c=cfg()
    # Decision at 10s is observed; by the 16s arrival its book is stale.
    replay=OrderBookReplay([book(10),book(25)],max_age_s=5)
    result=PaperSimulator(c,[]).run_hypothesis([sig(10),sig(20)],replay,'A',5,0,2,[6000],[25],'BTCUSDT')
    scenario=result['stats']['scenarios']['L6000_Q25']
    assert scenario['n_entries']==0 and scenario['n_unknown_entries']==1
    assert scenario['cash_usdt']==100 and scenario['available_cash_usdt']==75
    assert scenario['equity_usdt'] is None
    assert result['trades'][0].status=='entry_unknown'
    assert scenario['rejections']['position_busy']==1
