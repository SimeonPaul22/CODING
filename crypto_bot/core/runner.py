"""Main loop for paper and live modes, plus startup safety checks."""
from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime
from typing import Optional

from alerts.telegram import Notifier
from config.settings import LIVE_CONFIRM_PHRASE, Settings
from core.broker import LiveBroker, SimBroker
from core.trader import Trader
from exchange.base import ExchangeClient, drop_unclosed_candle, timeframe_to_seconds
from exchange.factory import build_exchange
from storage.db import Store, day_of, utcnow
from strategy.engines import DecisionEngine, RuleBasedEngine

log = logging.getLogger(__name__)


class StartupError(Exception):
    pass


def confirm_live(settings: Settings, input_fn=input) -> None:
    """Live mode needs: mode=live, live_trading_enabled=true, AND confirmation."""
    if not settings.live_trading_enabled:
        raise StartupError("mode is 'live' but live_trading_enabled is false in config.yaml - refusing to trade real money")
    banner = (
        "\n" + "!" * 70 + "\n"
        f"  LIVE TRADING on {settings.exchange.name.upper()} with REAL money.\n"
        f"  Symbols: {', '.join(settings.exchange.symbols)} | size {settings.risk.position_size_usdt} USDT/trade | "
        f"capital {settings.risk.capital_usdt} USDT\n" + "!" * 70
    )
    if sys.stdin is not None and sys.stdin.isatty():
        print(banner)
        answer = input_fn(f"Type {LIVE_CONFIRM_PHRASE} to continue: ").strip()
        if answer != LIVE_CONFIRM_PHRASE:
            raise StartupError("live trading not confirmed")
        return
    # No terminal (e.g. systemd): require the confirmation phrase in .env.
    if settings.secrets.confirm_live_trading != LIVE_CONFIRM_PHRASE:
        raise StartupError(f"no terminal for the confirmation prompt; set CONFIRM_LIVE_TRADING={LIVE_CONFIRM_PHRASE} in .env")
    log.warning("live trading confirmed via CONFIRM_LIVE_TRADING in .env")


def live_startup_checks(settings: Settings, ex: ExchangeClient, notifier: Notifier) -> None:
    if not ex.supports_live:
        raise StartupError(f"the {ex.name} adapter is not enabled for live trading")
    s = settings.secrets
    if not (s.exchange_api_key or ex.name == "quidax") or not s.exchange_api_secret:
        raise StartupError("EXCHANGE_API_KEY / EXCHANGE_API_SECRET missing in .env")
    perm = ex.check_api_permissions()
    if perm.can_withdraw:
        msg = ("SECURITY WARNING: this API key appears to have WITHDRAWAL permission. Create a new key with "
               f"read + spot-trade only. ({perm.details})")
        log.critical(msg)
        notifier.send("error", msg)
        raise StartupError("refusing to run with a withdrawal-enabled API key")
    if not perm.checked:
        log.warning("Could not verify API key permissions: %s. Make sure withdrawals are DISABLED for this key.", perm.details)
        notifier.send("error", f"Could not verify API key permissions ({perm.details}). Ensure withdrawals are disabled.")
    else:
        log.info("API key permission check passed: %s", perm.details)
    bal = ex.free_balance(settings.exchange.quote_currency)
    log.info("free %s balance: %.4f", settings.exchange.quote_currency, bal)
    if bal < settings.risk.position_size_usdt:
        log.warning("free balance %.4f is below one position (%.2f) - the bot will not be able to buy",
                    bal, settings.risk.position_size_usdt)


def reconcile_positions(trader: Trader, ex: ExchangeClient) -> None:
    """On live restart, make sure the coins for remembered positions still exist."""
    for pos in trader.store.open_positions():
        info = trader.info(pos.symbol)
        try:
            held = ex.free_balance(info.base)
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot verify %s balance: %s", info.base, exc)
            continue
        if held + 1e-12 < pos.amount * 0.98:
            msg = (f"Position {pos.id} expects {pos.amount} {info.base} but only {held} is free on the exchange. "
                   "Did you sell manually? Fix the position or close it in the DB before continuing.")
            log.critical(msg)
            trader.notify.send("error", msg)


def build_engine(settings: Settings, store: Store) -> DecisionEngine:
    if settings.llm.engine == "rules":
        return RuleBasedEngine()
    from llm.claude import ClaudeEngine

    return ClaudeEngine(settings.llm, settings.secrets.anthropic_api_key, store)


class Runner:
    def __init__(self, settings: Settings, *, offline: bool = False, max_iterations: Optional[int] = None,
                 engine: Optional[DecisionEngine] = None) -> None:
        self.s = settings
        self.mode = settings.mode
        self.max_iterations = max_iterations
        self._stop = False
        self.notifier = Notifier(settings.alerts.telegram_enabled, settings.secrets.telegram_bot_token,
                                 settings.secrets.telegram_chat_id, prefix=f"[{self.mode}] ")
        self.store = Store(settings.db_path())
        live = self.mode == "live"
        if live:
            if offline:
                raise StartupError("--offline cannot be used with live mode")
            confirm_live(settings)
        self.ex = build_exchange(settings, with_keys=live, offline=offline)
        if live:
            live_startup_checks(settings, self.ex, self.notifier)
            broker = LiveBroker(self.ex, self.store, settings.risk.capital_usdt, settings.exchange.quote_currency)
            fee_est = self.ex.market_info(settings.exchange.symbols[0]).taker_fee_pct
        else:
            broker = SimBroker(self.store, settings.risk.capital_usdt, settings.paper.fee_pct, settings.paper.slippage_pct)
            fee_est = settings.paper.fee_pct
        self.trader = Trader(settings, self.ex, engine or build_engine(settings, self.store), self.store, broker,
                             self.notifier, fee_pct_estimate=fee_est)
        if live:
            reconcile_positions(self.trader, self.ex)
        self.tf_ms = timeframe_to_seconds(settings.exchange.timeframe) * 1000
        self.last_decided: dict[str, int] = {}
        self._error_alerts: dict[str, float] = {}

    # ----------------------------------------------------------------- utils
    def _install_signals(self) -> None:
        def handler(signum, _frame):
            log.warning("signal %s received - stopping after this iteration", signum)
            self._stop = True

        signal.signal(signal.SIGINT, handler)
        try:
            signal.signal(signal.SIGTERM, handler)  # systemd stop / restart
        except (AttributeError, ValueError):
            pass

    def _alert_error(self, key: str, msg: str) -> None:
        # At most one alert per error type every 15 minutes, to avoid spam.
        now = time.time()
        if now - self._error_alerts.get(key, 0) > 900:
            self._error_alerts[key] = now
            self.notifier.send("error", msg)

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self._stop and not self.trader.kill_switch_active():
            time.sleep(min(1.0, end - time.time()))

    # ------------------------------------------------------------------ loop
    def handle_kill_switch(self, now: datetime) -> None:
        action = self.s.risk.kill_switch_action
        log.warning("KILL SWITCH file %s found - action: %s", self.s.kill_switch_path, action)
        if action == "close_and_exit":
            self.trader.close_all("kill_switch", now)
        self.notifier.send("shutdown", f"Kill switch activated ({action}). Bot stopped. Delete the STOP file to allow restarts.")

    def iteration(self) -> bool:
        """One monitor tick. Returns False when the bot must stop."""
        now = utcnow()
        if self.trader.kill_switch_active():
            self.handle_kill_switch(now)
            return False

        # 1) Stop-loss / take-profit on every tick.
        for symbol in self.s.exchange.symbols:
            try:
                price = self.ex.fetch_last_price(symbol)
                self.trader.check_exits(symbol, now, price=price)
            except Exception as exc:  # noqa: BLE001
                log.exception("exit check failed for %s", symbol)
                self._alert_error(f"exit:{symbol}", f"Exit check failed for {symbol}: {exc}")

        # 2) Global limits.
        if self.trader.enforce_limits(now) == "shutdown":
            return False

        # 3) A decision once per newly closed candle.
        for symbol in self.s.exchange.symbols:
            try:
                candles = self.ex.fetch_ohlcv(symbol, self.s.exchange.timeframe, self.s.exchange.candles_limit + 1)
                candles = drop_unclosed_candle(candles, self.s.exchange.timeframe)
                if candles.empty:
                    log.warning("%s: no closed candles returned", symbol)
                    continue
                last_ts = int(candles["timestamp"].iloc[-1])
                if self.last_decided.get(symbol) == last_ts:
                    continue
                self.last_decided[symbol] = last_ts
                self.trader.decision_cycle(symbol, candles, now, last_price=self.trader.last_prices.get(symbol))
            except Exception as exc:  # noqa: BLE001
                log.exception("decision cycle failed for %s", symbol)
                self._alert_error(f"cycle:{symbol}", f"Decision cycle failed for {symbol}: {exc}")

        # 4) Daily summary when the UTC day changes.
        today = day_of(now)
        last = self.store.get("last_summary_day")
        if last is None:
            self.store.set("last_summary_day", today)
        elif last != today:
            self.notifier.send("summary", self.trader.daily_summary(last))
            self.store.set("last_summary_day", today)
        return True

    def run(self) -> int:
        reason = self.trader.shutdown_reason()
        if reason:
            log.critical("bot is in SHUTDOWN state (%s). Review, then run `python main.py reset-shutdown`.", reason)
            return 2
        if self.trader.kill_switch_active():
            log.error("kill switch file %s exists - delete it to start the bot", self.s.kill_switch_path)
            return 3
        self._install_signals()
        self.notifier.send("info", f"Bot started: mode={self.mode} exchange={self.ex.name} "
                                   f"symbols={','.join(self.s.exchange.symbols)} engine={self.trader.engine.name}")
        n = 0
        while not self._stop:
            try:
                if not self.iteration():
                    break
            except Exception as exc:  # noqa: BLE001 - last line of defence
                log.exception("unexpected error in main loop")
                self._alert_error("loop", f"Unexpected error: {exc}")
            n += 1
            if self.max_iterations is not None and n >= self.max_iterations:
                break
            self._sleep(self.s.loop.monitor_interval_seconds)
        log.info("bot stopped (equity %.4f USDT)", self.trader.equity())
        self.notifier.send("shutdown", f"Bot stopped. Equity {self.trader.equity():.2f} USDT, "
                                       f"open positions {len(self.store.open_positions())}")
        return 0
