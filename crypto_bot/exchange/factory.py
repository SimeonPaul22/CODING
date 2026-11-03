"""Build the right exchange adapter from config."""
from __future__ import annotations

from config.settings import Settings
from exchange.base import ExchangeClient
from exchange.ccxt_client import CcxtExchange
from exchange.quidax import QuidaxExchange
from exchange.synthetic import SyntheticExchange


def build_exchange(settings: Settings, *, with_keys: bool, offline: bool = False) -> ExchangeClient:
    """Create the adapter.

    with_keys=False is used in paper and backtest modes: public market data
    needs no keys, and not loading them means a paper run can never place a
    real order even by accident.
    """
    ex = settings.exchange
    if offline:
        return SyntheticExchange(timeframe=ex.timeframe)
    s = settings.secrets
    if ex.name == "quidax":
        return QuidaxExchange(
            secret=s.exchange_api_secret if with_keys else None,
            timeout_ms=ex.timeout_ms,
            max_retries=ex.max_retries,
            live_acknowledged=ex.quidax_live_acknowledged,
        )
    return CcxtExchange(
        ex.name,
        api_key=s.exchange_api_key if with_keys else None,
        secret=s.exchange_api_secret if with_keys else None,
        password=s.exchange_api_password if with_keys else None,
        testnet=ex.testnet,
        timeout_ms=ex.timeout_ms,
        max_retries=ex.max_retries,
    )
