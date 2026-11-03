"""Command-line entry point.

    python main.py run                 # run in the mode set in config.yaml (default: paper)
    python main.py run --offline       # paper mode on a synthetic offline market (no internet)
    python main.py backtest            # backtest using config.yaml's backtest section
    python main.py report              # performance report for the current mode's database
    python main.py check               # validate config, test exchange connectivity / key permissions
    python main.py reset-shutdown      # clear the max-drawdown shutdown flag after reviewing

Run ``python main.py <command> -h`` for options.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings, load_settings
from storage.logging_setup import setup_logging

log = logging.getLogger("main")

# Exit codes (used by the systemd unit to decide whether to restart):
#   0 normal stop / kill switch / drawdown shutdown   1 crash (restart)
#   2 in shutdown state   3 STOP file present   4 refused to start (config / live gate)
EXIT_REFUSED = 4


def _ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    from core.runner import Runner, StartupError

    if args.mode:
        settings.mode = args.mode
    if settings.mode == "backtest":
        return cmd_backtest(settings, args)
    if args.engine:
        settings.llm.engine = args.engine
    try:
        runner = Runner(settings, offline=args.offline, max_iterations=args.iterations)
    except StartupError as exc:
        log.error("startup refused: %s", exc)
        return EXIT_REFUSED
    except ValueError as exc:
        log.error("startup failed: %s", exc)
        return EXIT_REFUSED
    if args.interval:
        settings.loop.monitor_interval_seconds = args.interval
    return runner.run()


def cmd_backtest(settings: Settings, args: argparse.Namespace) -> int:
    from backtest.data import get_history
    from backtest.engine import run_backtest
    from core.report import format_report
    from core.runner import build_engine
    from exchange.factory import build_exchange
    from exchange.synthetic import generate_candles
    from storage.db import Store
    from strategy.engines import RuleBasedEngine

    bt = settings.backtest
    start, end = getattr(args, "start", None) or bt.start, getattr(args, "end", None) or bt.end
    engine_name = getattr(args, "engine", None) or bt.engine
    symbols = [args.symbol] if getattr(args, "symbol", None) else settings.exchange.symbols
    tf = settings.exchange.timeframe
    start_ms, end_ms = _ms(start), _ms(end)

    candles = {}
    infos = None
    if getattr(args, "offline", False):
        from exchange.base import timeframe_to_seconds

        n = int((end_ms - start_ms) / (timeframe_to_seconds(tf) * 1000))
        for i, s in enumerate(symbols):
            candles[s] = generate_candles(start_ms, max(n, 100), tf, seed=7 + i)
        log.info("using SYNTHETIC candles (offline) - results say nothing about real markets")
    else:
        ex = build_exchange(settings, with_keys=False)
        cache_dir = settings.path(settings.logging.db_dir) / "cache"
        for s in symbols:
            candles[s] = get_history(ex, s, tf, start_ms, end_ms, cache_dir)
        try:
            infos = {s: ex.market_info(s) for s in symbols}
            for s, inf in infos.items():  # fees from config, not the exchange's default tier
                infos[s] = type(inf)(**{**inf.__dict__, "taker_fee_pct": bt.fee_pct})
        except Exception as exc:  # noqa: BLE001
            log.warning("could not load market rules (%s); using generic ones", exc)

    decide_from = 0
    if engine_name == "claude":
        longest = max(len(df) for df in candles.values())
        decide_from = max(0, longest - bt.claude_sample_candles)
        log.warning("Claude backtest: real API calls on the last %d candles only (cost cap $%.2f/day applies). "
                    "Note: Claude may 'remember' historical prices, so results can look better than reality.",
                    bt.claude_sample_candles, settings.llm.daily_cost_cap_usd)
        settings.llm.engine = "claude"
        engine = build_engine(settings, Store(settings.db_path("backtest_llm")))
    else:
        engine = RuleBasedEngine()

    if settings.logging.level != "DEBUG":
        # One log line per candle is too noisy for a backtest; everything is still in the DB.
        logging.getLogger("core.trader").setLevel(logging.WARNING)
    db = settings.db_path("backtest")
    res = run_backtest(settings, candles, engine, market_infos=infos, db_path=db, decide_from_index=decide_from)
    print(format_report(res.report, f"Backtest {','.join(symbols)} {tf} {start}..{end} engine={engine_name} "
                                    f"fee={bt.fee_pct}% slippage={bt.slippage_pct}%"))
    out = settings.path(settings.logging.db_dir) / "backtest_equity.csv"
    res.equity_curve.to_csv(out, index=False)
    print(f"\nEquity curve: {out}\nTrade log DB : {db}")
    return 0


def cmd_report(settings: Settings, args: argparse.Namespace) -> int:
    from core.report import format_report, performance_report
    from storage.db import Store

    mode = args.mode or settings.mode
    path = settings.db_path(mode)
    if not path.exists():
        print(f"No database for mode '{mode}' yet ({path}). Run the bot first.")
        return 1
    store = Store(path)
    bh = {}
    for sym in store.symbols_seen():
        first, last, _, _ = store.first_and_last_price(sym)
        if first and last:
            bh[sym] = (first, last)
    fee = settings.backtest.fee_pct if mode == "backtest" else settings.paper.fee_pct
    print(format_report(performance_report(store, settings.risk.capital_usdt, bh, fee_pct=fee), f"Report ({mode})"))
    trades = store.closed_positions()[-10:]
    if trades:
        print("\nLast trades:")
        for p in trades:
            print(f"  #{p.id} {p.symbol} {p.opened_at} -> {p.closed_at}  {p.entry_price:.6g} -> {p.exit_price:.6g}  "
                  f"{p.pnl:+.4f} USDT ({p.exit_reason})")
    for p in store.open_positions():
        print(f"  OPEN #{p.id} {p.symbol} {p.amount:.8g} @ {p.entry_price:.6g}  SL {p.stop_loss_price:.6g} TP {p.take_profit_price:.6g}")
    return 0


def cmd_check(settings: Settings, args: argparse.Namespace) -> int:
    from exchange.factory import build_exchange

    print(f"Config OK. mode={settings.mode} exchange={settings.exchange.name} testnet={settings.exchange.testnet} "
          f"symbols={settings.exchange.symbols} timeframe={settings.exchange.timeframe} engine={settings.llm.engine}")
    print(f"Anthropic key: {'set' if settings.secrets.anthropic_api_key else 'MISSING'} | "
          f"exchange key: {'set' if settings.secrets.exchange_api_key else 'not set'} | "
          f"telegram: {'on' if settings.alerts.telegram_enabled else 'off'}")
    ok = True
    try:
        ex = build_exchange(settings, with_keys=bool(settings.secrets.exchange_api_key))
        for s in settings.exchange.symbols:
            info = ex.market_info(s)
            print(f"  {s}: last price {ex.fetch_last_price(s)}  lot={info.amount_step} tick={info.price_step} "
                  f"min_amount={info.min_amount} min_cost={info.min_cost} taker={info.taker_fee_pct}%")
        if settings.secrets.exchange_api_key:
            perm = ex.check_api_permissions()
            flag = "!!! WITHDRAWALS ENABLED - create a new key !!!" if perm.can_withdraw else (
                "no withdrawal permission detected" if perm.checked else "could not verify")
            print(f"  key permissions: {flag} ({perm.details})")
            ok = ok and not perm.can_withdraw
    except Exception as exc:  # noqa: BLE001
        print(f"  exchange check failed: {exc}")
        ok = False
    return 0 if ok else 1


def cmd_reset(settings: Settings, args: argparse.Namespace) -> int:
    from storage.db import Store

    store = Store(settings.db_path(args.mode or settings.mode))
    reason = store.get("shutdown")
    if not reason:
        print("Bot is not in shutdown state.")
        return 0
    print(f"Shutdown reason: {reason}")
    if input("Type RESET to clear it (the drawdown peak is reset to current equity): ").strip() != "RESET":
        print("Not reset.")
        return 1
    store.set("shutdown", "")
    store.set("peak_equity", settings.risk.capital_usdt + store.realized_pnl_total())
    print("Shutdown flag cleared.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Claude-assisted spot crypto trading bot")
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("run", help="run the bot (paper/live per config)")
    r.add_argument("--mode", choices=["paper", "backtest"], help="override mode (live can only be set in config.yaml)")
    r.add_argument("--engine", choices=["claude", "rules"], help="override llm.engine")
    r.add_argument("--offline", action="store_true", help="use a synthetic offline market (paper only)")
    r.add_argument("--iterations", type=int, default=None, help="stop after N monitor ticks (for dry runs)")
    r.add_argument("--interval", type=int, default=None, help="override monitor interval seconds")

    b = sub.add_parser("backtest", help="run a backtest")
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--symbol")
    b.add_argument("--engine", choices=["rules", "claude"])
    b.add_argument("--offline", action="store_true", help="use synthetic candles instead of downloading")

    rep = sub.add_parser("report", help="print the performance report")
    rep.add_argument("--mode", choices=["paper", "live", "backtest"])

    sub.add_parser("check", help="validate config and test exchange connectivity")
    rs = sub.add_parser("reset-shutdown", help="clear the drawdown shutdown flag")
    rs.add_argument("--mode", choices=["paper", "live"])
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    if args.command is None:  # plain `python main.py` == `python main.py run`
        args = build_parser().parse_args(argv + ["run"])
    try:
        settings = load_settings(args.config)
    except Exception as exc:  # noqa: BLE001
        print(f"Config error: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    setup_logging(settings)
    command = args.command
    handlers = {"run": cmd_run, "backtest": cmd_backtest, "report": cmd_report, "check": cmd_check,
                "reset-shutdown": cmd_reset}
    return handlers[command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
