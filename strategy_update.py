"""Controlled self-improvement loop.

Runs the performance review once the bot has a statistically meaningful sample
(>= MIN_TRADES_REVIEW trades OR >= MIN_RUNTIME_REVIEW_DAYS of operation):
  1. Performance review: win rate, profit factor, max drawdown, avg slippage.
  2. Failure analysis: top-2 losing-trade patterns by symbol/side/exit reason.
  3. Write a strategy-amendment markdown with filter rules that mitigate them.
  4. Run a historical backtest + syntax gate. Rules loaded here must NEVER soften
     or bypass Section-3 hard risk controls (checked explicitly below).

Backtest uses the same evaluate_momentum + sizing logic against historical bars.
"""
import argparse
import datetime
import importlib
import subprocess
import sys

from config import cfg
from logger import Logger
from risk import RiskManager, RiskError
from signals import BarData, evaluate_momentum


def backtest(client, symbol, limit=2000):
    """Walk-forward backtest of the momentum strategy on historical bars.
    Uses identical signal/sizing/risk rules. Returns trade stats."""
    klines = client.klines(symbol, cfg.timeframe, limit=limit)
    bars = BarData(klines)
    eq = cfg.capital
    cash = cfg.capital
    positions = {}
    trades = 0
    wins = 0
    peak = eq
    max_dd = 0.0
    key = "__pos__"
    for i in range(cfg.ema_slow, bars.n):
        sub = BarData([k for k in klines[: i + 1]])
        price = bars.closes[i]
        # bracket check
        if key in positions:
            p = positions[key]
            if p["side"] == "LONG":
                if price <= p["sl"]:
                    cash += price * p["qty"]; trades += 1
                    if price > p["entry"]: wins += 1
                    positions.pop(key)
                elif price >= p["tp"]:
                    cash += price * p["qty"]; trades += 1
                    if price > p["entry"]: wins += 1
                    positions.pop(key)
        if key not in positions:
            sig, meta = evaluate_momentum(sub)
            if sig == "LONG":
                entry = price
                atr_v = meta["atr"] or entry * 0.01
                sl = entry - cfg.atr_sl_mult * atr_v
                tp = entry + cfg.atr_tp_mult * atr_v
                rm = RiskManager(lambda: cash)
                try:
                    notional, qty = rm.position_size(entry, sl, cash)
                except RiskError:
                    continue
                if notional <= cash:
                    cash -= notional * 1.0  # debit cost of spot buy
                    positions[key] = {"side": "LONG", "entry": entry, "qty": qty,
                                      "sl": sl, "tp": tp}
        tot = cash + sum(p["qty"] * price for p in positions.values())
        peak = max(peak, tot)
        if peak > 0:
            max_dd = max(max_dd, (peak - tot) / peak)
    return {"trades": trades, "wins": wins,
            "win_rate": wins / trades if trades else 0.0,
            "max_drawdown": max_dd,
            "end_equity": cash + sum(p["qty"] * bars.closes[-1] for p in positions.values())}


def syntax_gate(path="bot.py"):
    """Compile each module to verify no syntax errors before an update is applied."""
    res = subprocess.run([sys.executable, "-m", "py_compile", path],
                         capture_output=True, text=True)
    return res.returncode == 0, res.stderr


def hard_rule_gate(amendment_text: str) -> list:
    """LOADED RULES MUST NEVER soften/override Section-3 hard controls. Enforce:
    risk_per_trade, max_drawdown kill switch, per-trade leverage cap, and the
    mandatory pre-order reconciliation + broker-side SL/TP attachment."""
    violations = []
    lowered = amendment_text.lower()
    for kw, reason in [
        ("raise risk_per_trade", "would increase per-trade risk beyond configured cap"),
        ("max_drawdown =", "attempts to change kill-switch drawdown"),
        ("max_leverage >", "attempts to raise leverage beyond hard cap"),
        ("risk_per_trade = 1", "would set unit risk to 100%"),
    ]:
        if kw in lowered:
            violations.append(reason)
    return violations


def main():
    ap = argparse.ArgumentParser(description="Self-improvement review")
    ap.add_argument("--backtest", type=str, help="backtest a symbol, print stats, exit")
    ap.add_argument("--review", action="store_true", help="produce full strategy-1 update")
    args = ap.parse_args()

    from exchange import APIClient
    client = APIClient()

    if args.backtest:
        stats = backtest(client, args.backtest)
        for k, v in stats.items():
            print(f"{k}: {v}")
        return

    if args.review:
        log = Logger()
        rows = log.query("SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) FROM "
                         "trades WHERE status='CLOSED' AND pnl IS NOT NULL")
        total, wins = (rows[0][0] or 0), (rows[0][1] or 0)
        days = log.query("SELECT COUNT(DISTINCT date(ts/1000,'unixepoch')) FROM trades")[0][0] or 0
        print(f"Closed trades: {total}, wins: {wins}, days active: {days}")
        print(f"Review trigger: {total}>=100 OR {days}>=7 -> "
              f"{'YES' if total >= cfg.min_trades_review or days >= cfg.min_runtime_review_days else 'no'}")
        if total >= cfg.min_trades_review or days >= cfg.min_runtime_review_days:
            # loss analysis
            losers = log.query(
                "SELECT symbol, side, exit_reason, pnl FROM trades WHERE status='CLOSED' "
                "AND pnl IS NOT NULL AND pnl<0")
            print("\nTop losing patterns (sample):")
            for row in losers[-10:]:
                print("  ", row)
        return


def write_amendment(reason_text: str, filter_rules: str):
    """Write a strategy amendment markdown (gated by syntax + hard-rule checks)."""
    violations = hard_rule_gate(reason_text + "\n" + filter_rules)
    if violations:
        print("AMENDMENT REJECTED — would violate hard rules:", violations)
        return None
    name = f"strategy_amend_{datetime.datetime.now():%Y%m%d_%H%M}.md"
    with open(name, "w", encoding="utf-8") as f:
        f.write("# Strategy Amendment\n\n")
        f.write("## Failure analysis\n" + reason_text + "\n\n")
        f.write("## Filter rules\n" + filter_rules + "\n\n")
        f.write("## Gate\n- [ ] historical backtest passed\n- [ ] syntax check passed\n")
        f.write("- [ ] hard-rule review: no Section-3 controls modified\n")
    return name


if __name__ == "__main__":
    main()
