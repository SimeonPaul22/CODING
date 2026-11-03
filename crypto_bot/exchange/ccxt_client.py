"""Exchange adapter built on ccxt (Bybit, Binance, Bitget, KuCoin, ...).

ccxt gives one unified API over 100+ exchanges. We still need care around:
  * rate limits      -> ccxt's built-in limiter (enableRateLimit=True)
  * network blips    -> retry_call() with exponential backoff, for READS only
  * order placement  -> never blindly retried (could double-buy); instead, after
                        a network error we look the order up by our own
                        client order id to find out whether it went through
  * precision/limits -> read from ccxt market info and applied before sending
  * partial fills    -> we wait for the order, cancel any unfilled remainder,
                        and record only what actually filled
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import ccxt
import pandas as pd

from exchange.base import (
    ExchangeClient,
    ExchangeError,
    Fill,
    MarketInfo,
    PermissionReport,
    TransientExchangeError,
    ohlcv_to_frame,
    retry_call,
)

log = logging.getLogger(__name__)

# Errors worth retrying: timeouts, exchange down, rate limiting (all NetworkError
# subclasses in ccxt). Auth errors, bad symbols, insufficient funds are not.
RETRYABLE = (ccxt.NetworkError,)


class CcxtExchange(ExchangeClient):
    supports_live = True

    def __init__(
        self,
        name: str,
        *,
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        password: Optional[str] = None,
        testnet: bool = False,
        timeout_ms: int = 15000,
        max_retries: int = 4,
    ) -> None:
        if not hasattr(ccxt, name):
            raise ExchangeError(f"ccxt does not support exchange '{name}'")
        self.name = name
        self.max_retries = max_retries
        params: dict[str, Any] = {
            "enableRateLimit": True,  # ccxt sleeps between calls to respect limits
            "timeout": timeout_ms,
            # Force spot so no unified call can touch futures/margin accounts.
            "options": {"defaultType": "spot"},
        }
        if api_key and secret:
            params.update({"apiKey": api_key, "secret": secret})
            if password:
                params["password"] = password
        self.ex: ccxt.Exchange = getattr(ccxt, name)(params)
        if testnet:
            try:
                self.ex.set_sandbox_mode(True)
            except ccxt.NotSupported as exc:
                raise ExchangeError(f"{name} has no testnet/sandbox in ccxt; set exchange.testnet: false") from exc
        self._markets_loaded = False

    # ------------------------------------------------------------------ helpers
    def _retry(self, fn, what: str):
        return retry_call(fn, retries=self.max_retries, retry_on=RETRYABLE, what=f"{self.name} {what}")

    def _load_markets(self) -> None:
        if not self._markets_loaded:
            self._retry(self.ex.load_markets, "load_markets")
            self._markets_loaded = True

    def _market(self, symbol: str) -> dict[str, Any]:
        self._load_markets()
        if symbol not in self.ex.markets:
            raise ExchangeError(f"{self.name}: unknown symbol {symbol}")
        m = self.ex.markets[symbol]
        if not m.get("spot", False):
            raise ExchangeError(f"{symbol} is not a spot market on {self.name}")
        return m

    def _precision_to_step(self, value: Any) -> Optional[float]:
        """ccxt expresses precision either as a tick size or as decimal places."""
        if value is None:
            return None
        value = float(value)
        if self.ex.precisionMode == ccxt.DECIMAL_PLACES:
            return 10 ** (-int(value))
        if self.ex.precisionMode == ccxt.SIGNIFICANT_DIGITS:
            return None  # rare; fall back to ccxt's own amount_to_precision
        return value  # TICK_SIZE

    # ------------------------------------------------------------- market data
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int, since_ms: Optional[int] = None) -> pd.DataFrame:
        rows = self._retry(lambda: self.ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit), "fetch_ohlcv")
        return ohlcv_to_frame(rows)

    def fetch_last_price(self, symbol: str) -> float:
        t = self._retry(lambda: self.ex.fetch_ticker(symbol), "fetch_ticker")
        price = t.get("last") or t.get("close") or t.get("bid")
        if not price or float(price) <= 0:
            raise TransientExchangeError(f"no valid price in ticker for {symbol}")
        return float(price)

    def market_info(self, symbol: str) -> MarketInfo:
        m = self._market(symbol)
        limits = m.get("limits") or {}
        prec = m.get("precision") or {}
        taker = m.get("taker")
        return MarketInfo(
            symbol=symbol,
            base=m["base"],
            quote=m["quote"],
            amount_step=self._precision_to_step(prec.get("amount")),
            price_step=self._precision_to_step(prec.get("price")),
            min_amount=(limits.get("amount") or {}).get("min"),
            min_cost=(limits.get("cost") or {}).get("min"),
            taker_fee_pct=float(taker) * 100 if taker is not None else 0.1,
        )

    # --------------------------------------------------------------- account
    def free_balance(self, currency: str) -> float:
        bal = self._retry(self.ex.fetch_balance, "fetch_balance")
        return float((bal.get("free") or {}).get(currency) or 0.0)

    def check_api_permissions(self) -> PermissionReport:
        """Best-effort check that the key cannot withdraw funds.

        ccxt has no unified call for this, so we use each exchange's own
        "API key info" endpoint. If the check cannot run we say so loudly.
        """
        try:
            if self.name == "bybit":
                r = self.ex.privateGetV5UserQueryApi()
                perms = (r.get("result") or {}).get("permissions") or {}
                flat = json.dumps(perms).lower()
                return PermissionReport(True, "withdraw" in flat, f"bybit permissions: {perms}")
            if self.name == "binance":
                r = self.ex.sapiGetAccountApiRestrictions()
                w = bool(r.get("enableWithdrawals"))
                extra = []
                for k in ("enableMargin", "enableFutures", "enableInternalTransfer"):
                    if r.get(k):
                        extra.append(k)
                return PermissionReport(True, w, f"binance enableWithdrawals={w}; also enabled: {extra or 'none'}")
            if self.name == "bitget":
                r = self.ex.privateSpotGetV2SpotAccountInfo()
                auth = (r.get("data") or {}).get("authorities")
                return PermissionReport(True, "withdraw" in json.dumps(auth).lower(), f"bitget authorities: {auth}")
            if self.name == "kucoin":
                r = self.ex.privateGetUserApiKey()
                perm = (r.get("data") or {}).get("permission")
                return PermissionReport(True, "withdraw" in str(perm).lower(), f"kucoin permission: {perm}")
        except Exception as exc:  # noqa: BLE001 - any failure here is non-fatal
            return PermissionReport(False, None, f"could not query key permissions: {exc}")
        return PermissionReport(False, None, f"no permission check available for {self.name}")

    # ---------------------------------------------------------------- orders
    def _find_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[dict[str, Any]]:
        """After a network error, find out whether our order actually reached the exchange."""
        since = int(time.time() * 1000) - 10 * 60 * 1000
        for fetch in ("fetch_open_orders", "fetch_closed_orders"):
            try:
                orders = self._retry(lambda f=fetch: getattr(self.ex, f)(symbol, since), fetch)
            except Exception as exc:  # noqa: BLE001
                log.warning("order lookup via %s failed: %s", fetch, exc)
                continue
            for o in orders or []:
                cid = o.get("clientOrderId") or (o.get("info") or {}).get("orderLinkId")
                if cid == client_order_id:
                    return o
        return None

    def _fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        params = {"acknowledged": True} if self.name == "bybit" else {}
        return self._retry(lambda: self.ex.fetch_order(order_id, symbol, params), "fetch_order")

    def _wait_for_fill(self, order: dict[str, Any], symbol: str, timeout_s: float = 20.0) -> dict[str, Any]:
        """Poll until the order is done; cancel whatever is left after timeout."""
        deadline = time.time() + timeout_s
        while order.get("status") not in ("closed", "canceled", "expired", "rejected"):
            if time.time() > deadline:
                log.warning("order %s still %s after %.0fs; cancelling remainder", order.get("id"), order.get("status"), timeout_s)
                try:
                    self.ex.cancel_order(order["id"], symbol)
                except Exception as exc:  # noqa: BLE001 - it may have just filled
                    log.warning("cancel failed (may already be filled): %s", exc)
                order = self._fetch_order(order["id"], symbol)
                break
            time.sleep(1.0)
            try:
                order = self._fetch_order(order["id"], symbol)
            except Exception as exc:  # noqa: BLE001
                log.warning("fetch_order failed while waiting: %s", exc)
        return order

    def _to_fill(self, order: dict[str, Any], symbol: str, side: str, requested: Optional[float], client_order_id: str) -> Fill:
        info = self.market_info(symbol)
        filled = float(order.get("filled") or 0.0)
        avg = order.get("average") or order.get("price")
        cost = float(order.get("cost") or 0.0)
        if (not avg or float(avg) <= 0) and filled > 0 and cost > 0:
            avg = cost / filled
        avg = float(avg or 0.0)
        if cost <= 0:
            cost = filled * avg

        base_fee = quote_fee = 0.0
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        fee_known = False
        for f in fees:
            if not f or f.get("cost") is None:
                continue
            fee_known = True
            c = float(f["cost"])
            if f.get("currency") == info.base:
                base_fee += c
            elif f.get("currency") == info.quote:
                quote_fee += c
            else:  # e.g. paid in BNB - estimate the quote value
                quote_fee += cost * info.taker_fee_pct / 100
        if not fee_known:
            quote_fee = cost * info.taker_fee_pct / 100

        net_amount = filled - base_fee if side == "buy" else filled
        status = order.get("status") or "unknown"
        if filled <= 0:
            status = "failed" if status != "canceled" else "canceled"
        elif status != "closed" or (requested and filled + 1e-12 < requested * 0.999):
            status = "partial"
        return Fill(
            order_id=str(order.get("id")),
            symbol=symbol,
            side=side,
            amount=max(net_amount, 0.0),
            price=avg,
            cost=cost,
            fee_quote=quote_fee + base_fee * avg,
            status=status,
            # Base-currency fees are already reflected in ``amount``.
            net_quote=cost + quote_fee if side == "buy" else cost - quote_fee,
            client_order_id=client_order_id,
            raw={k: order.get(k) for k in ("id", "status", "filled", "amount", "average", "cost", "fee", "timestamp")},
        )

    def _place(self, place_fn, symbol: str, client_order_id: str) -> dict[str, Any]:
        try:
            return place_fn()
        except ccxt.InsufficientFunds as exc:
            raise ExchangeError(f"insufficient funds: {exc}") from exc
        except ccxt.InvalidOrder as exc:
            raise ExchangeError(f"order rejected: {exc}") from exc
        except ccxt.NetworkError as exc:
            # The request may or may not have reached the exchange. Do NOT resend;
            # look it up instead so we never accidentally buy twice.
            log.error("network error while placing order %s: %s - checking exchange", client_order_id, exc)
            time.sleep(2)
            found = self._find_order_by_client_id(symbol, client_order_id)
            if found:
                log.info("order %s did reach the exchange", client_order_id)
                return found
            raise TransientExchangeError(
                f"order {client_order_id} status unknown after network error; it was not found on the exchange"
            ) from exc

    def market_buy(self, symbol: str, quote_cost: float, client_order_id: str) -> Fill:
        info = self.market_info(symbol)
        if info.min_cost and quote_cost < info.min_cost:
            raise ExchangeError(f"order cost {quote_cost} below exchange minimum {info.min_cost}")
        params = {"clientOrderId": client_order_id}
        requested_amount: Optional[float] = None
        if self.ex.has.get("createMarketBuyOrderWithCost"):
            cost = float(self.ex.cost_to_precision(symbol, quote_cost)) if hasattr(self.ex, "cost_to_precision") else quote_cost
            order = self._place(lambda: self.ex.create_market_buy_order_with_cost(symbol, cost, params), symbol, client_order_id)
        else:
            price = self.fetch_last_price(symbol)
            amount = float(self.ex.amount_to_precision(symbol, quote_cost / price))
            if info.min_amount and amount < info.min_amount:
                raise ExchangeError(f"amount {amount} below exchange minimum {info.min_amount}")
            requested_amount = amount
            order = self._place(lambda: self.ex.create_order(symbol, "market", "buy", amount, price, params), symbol, client_order_id)
        order = self._wait_for_fill(order, symbol)
        return self._to_fill(order, symbol, "buy", requested_amount, client_order_id)

    def market_sell(self, symbol: str, amount: float, client_order_id: str) -> Fill:
        info = self.market_info(symbol)
        # Never try to sell more than we hold (fees may have reduced it).
        free = self.free_balance(info.base)
        qty = float(self.ex.amount_to_precision(symbol, min(amount, free)))
        if qty <= 0 or (info.min_amount and qty < info.min_amount):
            raise ExchangeError(f"sell amount {qty} {info.base} below exchange minimum ({info.min_amount}); free={free}")
        params = {"clientOrderId": client_order_id}
        order = self._place(lambda: self.ex.create_order(symbol, "market", "sell", qty, None, params), symbol, client_order_id)
        order = self._wait_for_fill(order, symbol)
        return self._to_fill(order, symbol, "sell", qty, client_order_id)
