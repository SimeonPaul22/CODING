"""Performance report: win rate, P&L, max drawdown, trade count, buy & hold.

How to read it:
  win_rate          share of closed trades with positive P&L (after fees)
  total_pnl_usdt    sum of realised P&L of closed trades
  return_pct        total_pnl / capital
  max_drawdown_pct  worst peak-to-trough fall of equity
  profit_factor     gross wins / gross losses (>1 means wins outweigh losses)
  buy_and_hold      what you would have made by buying with the whole capital
                    at the start and holding (one fee in, one fee out). If the
                    bot cannot beat this, simply holding would have been better.
"""
from __future__ import annotations

from typing import Optional, Sequence

from storage.db import Store


def max_drawdown_pct(equity: Sequence[float]) -> float:
    peak = None
    worst = 0.0
    for e in equity:
        peak = e if peak is None or e > peak else peak
        if peak and peak > 0:
            worst = max(worst, (peak - e) / peak * 100)
    return worst


def performance_report(store: Store, capital: float, buy_hold_prices: dict[str, tuple[float, float]], *,
                       equity_curve: Optional[Sequence[float]] = None, fee_pct: float = 0.1) -> dict:
    closed = store.closed_positions()
    pnls = [p.pnl or 0.0 for p in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    total = sum(pnls)
    if equity_curve is None:
        equity_curve = [capital]
        for x in pnls:
            equity_curve.append(equity_curve[-1] + x)
    exit_reasons: dict[str, int] = {}
    for p in closed:
        key = (p.exit_reason or "?").replace(" (partial fill)", "")
        exit_reasons[key] = exit_reasons.get(key, 0) + 1

    bh = {}
    for sym, (first, last) in buy_hold_prices.items():
        if first and last:
            gross = last / first
            net = gross * (1 - fee_pct / 100) ** 2
            bh[sym] = {"start_price": first, "end_price": last, "return_pct": round((net - 1) * 100, 3),
                       "pnl_usdt_on_capital": round(capital * (net - 1), 4)}
    llm = store.llm_usage_all()
    return {
        "trades": len(closed),
        "open_positions": len(store.open_positions()),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "total_pnl_usdt": round(total, 4),
        "return_pct": round(total / capital * 100, 3) if capital else 0.0,
        "avg_win_usdt": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss_usdt": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) != 0 else None,
        "fees_usdt": round(sum((p.entry_fee or 0) + (p.exit_fee or 0) for p in closed), 4),
        "max_drawdown_pct": round(max_drawdown_pct(equity_curve), 3),
        "exit_reasons": exit_reasons,
        "buy_and_hold": bh,
        "llm_calls": sum(int(r["calls"]) for r in llm),
        "llm_cost_usd": round(sum(float(r["cost_usd"]) for r in llm), 5),
    }


def format_report(r: dict, title: str = "Performance report") -> str:
    lines = [f"=== {title} ===",
             f"Closed trades      : {r['trades']}  (open: {r['open_positions']})",
             f"Wins / losses      : {r['wins']} / {r['losses']}   win rate {r['win_rate_pct']}%",
             f"Total P&L          : {r['total_pnl_usdt']:+.4f} USDT  ({r['return_pct']:+.3f}% of capital)",
             f"Avg win / avg loss : {r['avg_win_usdt']:+.4f} / {r['avg_loss_usdt']:+.4f} USDT",
             f"Profit factor      : {r['profit_factor'] if r['profit_factor'] is not None else 'n/a'}",
             f"Fees paid          : {r['fees_usdt']:.4f} USDT",
             f"Max drawdown       : {r['max_drawdown_pct']:.3f}%",
             f"Exit reasons       : {r['exit_reasons'] or '-'}",
             f"Claude calls/cost  : {r['llm_calls']} / ${r['llm_cost_usd']:.4f}"]
    for sym, b in r["buy_and_hold"].items():
        verdict = "bot BEAT buy&hold" if r["return_pct"] > b["return_pct"] else "buy&hold did better"
        lines.append(f"Buy & hold {sym:<8}: {b['return_pct']:+.3f}% ({b['start_price']:.6g} -> {b['end_price']:.6g})  -> {verdict}")
    if r.get("note"):
        lines.append(f"NOTE: {r['note']}")
    return "\n".join(lines)
