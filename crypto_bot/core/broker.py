"""Brokers execute approved orders.

SimBroker  - paper trading and backtests: fills instantly at the given price
             plus slippage, charges a fee, tracks a fake USDT balance in SQLite.
LiveBroker - sends real market orders through the exchange adapter.

The trader does not know which one it is using; that is what keeps paper and
live behaviour identical apart from the actual order placement.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod

from exchange.base import ExchangeClient, Fill, MarketInfo
from storage.db import Store

log = logging.getLogger(__name__)


def new_client_order_id(prefix: str = "cbot") -> str:
    # Our own id for each order, so it can be found again after a network error.
    return f"{prefix}{uuid.uuid4().hex[:20]}"


class Broker(ABC):
    @abstractmethod
    def buy(self, info: MarketInfo, quote_cost: float, price_hint: float) -> Fill: ...

    @abstractmethod
    def sell(self, info: MarketInfo, amount: float, price_hint: float) -> Fill: ...

    @abstractmethod
    def quote_available(self) -> float: ...


class SimBroker(Broker):
    BAL_KEY = "paper_quote_balance"

    def __init__(self, store: Store, starting_balance: float, fee_pct: float, slippage_pct: float) -> None:
        self.store = store
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        if store.get(self.BAL_KEY) is None:
            store.set(self.BAL_KEY, starting_balance)

    def quote_available(self) -> float:
        return self.store.get_float(self.BAL_KEY, 0.0)

    def _adjust(self, delta: float) -> None:
        self.store.set(self.BAL_KEY, self.quote_available() + delta)

    def buy(self, info: MarketInfo, quote_cost: float, price_hint: float) -> Fill:
        price = price_hint * (1 + self.slippage_pct / 100)  # you pay a bit more than last price
        amount = info.round_amount_down(quote_cost / price)
        cost = amount * price
        fee = cost * self.fee_pct / 100
        if amount <= 0 or cost + fee > self.quote_available() + 1e-9:
            return Fill("sim-rejected", info.symbol, "buy", 0.0, price, 0.0, 0.0, "failed")
        self._adjust(-(cost + fee))
        return Fill(new_client_order_id("sim"), info.symbol, "buy", amount, price, cost, fee, "closed",
                    net_quote=cost + fee)

    def sell(self, info: MarketInfo, amount: float, price_hint: float) -> Fill:
        price = price_hint * (1 - self.slippage_pct / 100)  # and receive a bit less
        amount = info.round_amount_down(amount) or amount
        cost = amount * price
        fee = cost * self.fee_pct / 100
        self._adjust(cost - fee)
        return Fill(new_client_order_id("sim"), info.symbol, "sell", amount, price, cost, fee, "closed",
                    net_quote=cost - fee)


class LiveBroker(Broker):
    def __init__(self, exchange: ExchangeClient, store: Store, capital_usdt: float, quote_currency: str) -> None:
        self.ex = exchange
        self.store = store
        self.capital = capital_usdt
        self.quote = quote_currency

    def quote_available(self) -> float:
        # The bot may only use the capital you allocated (plus its own profits),
        # even if the exchange account holds more.
        allocated = self.capital + self.store.realized_pnl_total() - sum(p.entry_quote for p in self.store.open_positions())
        free = self.ex.free_balance(self.quote)
        return max(0.0, min(free, allocated))

    def buy(self, info: MarketInfo, quote_cost: float, price_hint: float) -> Fill:
        return self.ex.market_buy(info.symbol, quote_cost, new_client_order_id())

    def sell(self, info: MarketInfo, amount: float, price_hint: float) -> Fill:
        return self.ex.market_sell(info.symbol, amount, new_client_order_id())
