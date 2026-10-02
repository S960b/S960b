from .base import BaseAdapter, AdapterError
from .binance import BinanceAdapter
from .okx import OKXAdapter
from .bybit import BybitAdapter
from .safetrade import SafeTradeAdapter
from .events import make_event, new_run_id, new_boot_id

ADAPTERS = {
    "binance": BinanceAdapter,
    "okx": OKXAdapter,
    "bybit": BybitAdapter,
    "safetrade": SafeTradeAdapter,
}


def get_adapter(name: str, markets_cfg=None, channels=None, raw_q=None, **kw) -> BaseAdapter:
    cls = ADAPTERS.get(name)
    if cls is None:
        raise AdapterError(f"unknown adapter: {name}")
    return cls(markets_cfg=markets_cfg, channels=channels, raw_q=raw_q, **kw)


__all__ = ["BaseAdapter", "get_adapter", "make_event", "new_run_id", "new_boot_id",
           "BinanceAdapter", "OKXAdapter", "BybitAdapter", "SafeTradeAdapter"]