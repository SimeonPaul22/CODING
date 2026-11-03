"""Download historical candles via the exchange adapter and cache them as CSV.

Cache files live in data/cache/<exchange>_<BASE-QUOTE>_<timeframe>.csv. Later
runs only download the parts of the requested range that are missing.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from exchange.base import ExchangeClient, ohlcv_to_frame, timeframe_to_seconds

log = logging.getLogger(__name__)


def cache_path(cache_dir: Path, exchange: str, symbol: str, timeframe: str) -> Path:
    return cache_dir / f"{exchange}_{symbol.replace('/', '-')}_{timeframe}.csv"


def load_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return ohlcv_to_frame([])
    try:
        df = pd.read_csv(path)
        return ohlcv_to_frame(df.values.tolist())
    except Exception as exc:  # noqa: BLE001 - corrupt cache: just re-download
        log.warning("ignoring unreadable cache %s: %s", path, exc)
        return ohlcv_to_frame([])


def get_history(ex: ExchangeClient, symbol: str, timeframe: str, start_ms: int, end_ms: int, cache_dir: Path) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, ex.name, symbol, timeframe)
    df = load_cache(path)
    tf_ms = timeframe_to_seconds(timeframe) * 1000
    parts = [df]
    if df.empty:
        parts.append(ex.fetch_history(symbol, timeframe, start_ms, end_ms))
    else:
        first, last = int(df["timestamp"].iloc[0]), int(df["timestamp"].iloc[-1])
        if start_ms < first:
            parts.append(ex.fetch_history(symbol, timeframe, start_ms, first))
        if end_ms > last + tf_ms:
            parts.append(ex.fetch_history(symbol, timeframe, last + tf_ms, end_ms))
    merged = pd.concat([p for p in parts if not p.empty]) if any(not p.empty for p in parts) else df
    merged = ohlcv_to_frame(merged.values.tolist())
    if len(merged) > len(df):
        merged.to_csv(path, index=False)
        log.info("cached %d candles -> %s", len(merged), path)
    out = merged[(merged["timestamp"] >= start_ms) & (merged["timestamp"] < end_ms)].reset_index(drop=True)
    if out.empty:
        raise ValueError(f"no candles for {symbol} {timeframe} in the requested range")
    return out
