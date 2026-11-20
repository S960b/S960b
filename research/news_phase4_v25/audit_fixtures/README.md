# Audit fixtures — synthetic only

Every file in this directory was **written by hand for the audit pack**. None of
them is a captured response from Binance, Decrypt, CryptoSlate or any other feed.
None contains a real session id, account, credential, or live database row.

| file | purpose | shape of the real object |
|---|---|---|
| `rss_decrypt_eth_exploit.xml` | filter should accept: Ethereum exploit, Solana halt | RSS 2.0 channel/item with `title`, `link`, `pubDate`, `description` |
| `rss_cryptoslate_noise.xml` | filter should reject: `no_target_asset`, `editorial_noise` | same |
| `binance_klines_1h_30.json` | market features path | `/api/v3/klines` rows `[open_ms, o, h, l, c, v, close_ms]` — 30 rows in production, 3 here |
| `binance_ticker_price_eth.json` | entry quote path | `/api/v3/ticker/price` object `{symbol, price}` |
| `binance_klines_1m_exit.json` | exit/label path | `/api/v3/klines` 1m row |

## What these fixtures deliberately do NOT prove

- They do not attest that any Binance payload belongs to a given ticker. Binance
  klines carry no symbol in the body; the binding is the request URL plus the
  locally hashed bytes (see `release/source/v22_live/v26_market_provenance.py`).
- They are not evidence of any delivery latency. Real latency measurements are
  described in the root README under "What has been measured".
- The RSS fixtures use `example.invalid` hosts and never resolve.

## Reproducing the offline tests

```bash
cd research/news_phase4_v25/release
PYTHONPATH="source:source/v22_live:../series100:../rss_expansion" \
  python3 -m unittest discover -s tests -p "test_v2*.py"
```

The unit tests build their own temporary SQLite databases and never read these
fixtures; the fixtures exist so a reviewer can drive the modules by hand.
