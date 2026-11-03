"""EXPERIMENTAL adapter for Quidax (Nigerian exchange), which ccxt does not support.

Status: written against Quidax's public REST API documentation (v1) but NOT
tested against the real API. Public market data is used for paper trading;
the live order path is disabled unless ``exchange.quidax_live_acknowledged`` is
true in config.yaml. Verify every endpoint against https://docs.quidax.io
before enabling live trading, and start with the smallest possible size.

Quidax uses lowercase market ids without a slash ("btcusdt", "usdtngn").
Authentication is a Bearer token: your Quidax *secret key*.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import pandas as pd
import requests

from exchange.base import (
    ExchangeClient,
    ExchangeError,
    Fill,
    MarketInfo,
    PermissionReport,
    TransientExchangeError,
    ohlcv_to_frame,
    retry_call,
    timeframe_to_seconds,
)

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://app.quidax.io/api/v1"


class QuidaxExchange(ExchangeClient):
    name = "quidax"

    def __init__(self, *, secret: Optional[str] = None, timeout_ms: int = 15000, max_retries: int = 4,
                 live_acknowledged: bool = False) -> None:
        self.base_url = os.getenv("QUIDAX_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.secret = secret
        self.timeout = timeout_ms / 1000
        self.max_retries = max_retries
        self.supports_live = live_acknowledged
        self.session = requests.Session()
        self._last_call = 0.0

    @staticmethod
    def market_id(symbol: str) -> str:
        return symbol.replace("/", "").lower()

    def _request(self, method: str, path: str, *, params: Optional[dict] = None, body: Optional[dict] = None,
                 private: bool = False) -> Any:
        def do() -> Any:
            # Simple client-side rate limiting: at most ~5 requests per second.
            wait = 0.2 - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            headers = {"Accept": "application/json"}
            if private:
                if not self.secret:
                    raise ExchangeError("Quidax secret key missing (EXCHANGE_API_SECRET)")
                headers["Authorization"] = f"Bearer {self.secret}"
            try:
                r = self.session.request(method, f"{self.base_url}{path}", params=params, json=body,
                                         headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                raise TransientExchangeError(str(exc)) from exc
            if r.status_code == 429 or r.status_code >= 500:
                raise TransientExchangeError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise ExchangeError(f"Quidax HTTP {r.status_code}: {r.text[:200]}")
            payload = r.json()
            return payload.get("data", payload)

        return retry_call(do, retries=self.max_retries, retry_on=(TransientExchangeError,), what=f"quidax {path}")

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int, since_ms: Optional[int] = None) -> pd.DataFrame:
        params: dict[str, Any] = {"period": timeframe_to_seconds(timeframe) // 60, "limit": min(limit, 1000)}
        if since_ms:
            params["timestamp"] = since_ms // 1000
        data = self._request("GET", f"/markets/{self.market_id(symbol)}/k", params=params)
        rows = []
        for k in data or []:
            # Expected: [timestamp_seconds, open, high, low, close, volume]
            if isinstance(k, (list, tuple)) and len(k) >= 6:
                rows.append([int(k[0]) * 1000, k[1], k[2], k[3], k[4], k[5]])
        return ohlcv_to_frame(rows)

    def fetch_last_price(self, symbol: str) -> float:
        data = self._request("GET", f"/markets/tickers/{self.market_id(symbol)}")
        ticker = (data or {}).get("ticker", data) or {}
        price = float(ticker.get("last") or 0)
        if price <= 0:
            raise TransientExchangeError("no valid Quidax price")
        return price

    def market_info(self, symbol: str) -> MarketInfo:
        base, quote = symbol.split("/")
        # Quidax does not publish precision via a markets endpoint that we rely
        # on; these conservative defaults keep orders reasonably sized.
        return MarketInfo(symbol=symbol, base=base, quote=quote, amount_step=1e-6, price_step=1e-2,
                          min_amount=None, min_cost=1.0, taker_fee_pct=0.5)

    def free_balance(self, currency: str) -> float:
        data = self._request("GET", f"/users/me/wallets/{currency.lower()}", private=True)
        return float((data or {}).get("balance") or 0)

    def check_api_permissions(self) -> PermissionReport:
        return PermissionReport(False, None, "Quidax exposes no key-permission endpoint; verify in the dashboard that the key cannot withdraw")

    def _order(self, symbol: str, side: str, volume: float, client_order_id: str) -> Fill:
        if not self.supports_live:
            raise ExchangeError("Quidax live trading is disabled (set exchange.quidax_live_acknowledged: true after verifying the API)")
        info = self.market_info(symbol)
        body = {"market": self.market_id(symbol), "side": side, "ord_type": "market",
                "volume": str(info.round_amount_down(volume))}
        order = self._request("POST", "/users/me/orders", body=body, private=True)
        oid = str(order.get("id"))
        for _ in range(20):
            if order.get("state") in ("done", "cancel"):
                break
            time.sleep(1)
            order = self._request("GET", f"/users/me/orders/{oid}", private=True)
        filled = float(order.get("executed_volume") or 0)
        avg = float(order.get("avg_price", {}).get("amount") if isinstance(order.get("avg_price"), dict) else order.get("avg_price") or 0)
        cost = filled * avg
        status = "closed" if order.get("state") == "done" and filled > 0 else ("partial" if filled > 0 else "failed")
        fee = cost * info.taker_fee_pct / 100
        return Fill(order_id=oid, symbol=symbol, side=side, amount=filled, price=avg, cost=cost,
                    fee_quote=fee, status=status,
                    net_quote=cost + fee if side == "buy" else cost - fee, client_order_id=client_order_id, raw={"state": order.get("state")})

    def market_buy(self, symbol: str, quote_cost: float, client_order_id: str) -> Fill:
        price = self.fetch_last_price(symbol)
        return self._order(symbol, "buy", quote_cost / price, client_order_id)

    def market_sell(self, symbol: str, amount: float, client_order_id: str) -> Fill:
        base = symbol.split("/")[0]
        return self._order(symbol, "sell", min(amount, self.free_balance(base)), client_order_id)
