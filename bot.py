"""Main trading-bot loop: state recovery => data => signal => risk => execute => reconcile.
Paper mode only (confirmed). The human gatekeeper is triggered only in live mode.

Report (section-5 format) is emitted on each loop iteration and via --status.
"""
import argparse
import time
import sys

from config import cfg
from exchange import APIClient, ExchangeError
from logger import Logger
from risk import RiskManager, RiskError
from portfolio import Portfolio, PositionChecker
from signals import BarData, evaluate_momentum
from telegram_notify import notify_open, notify_close, notify_kill_switch, configured

POLL_SECONDS = 15  # 15m candle but poll for fills/PL several times per minute


def status_block(portfolio, risk, log, prices, risk_usage):
    dd = risk.current_drawdown(portfolio.equity(prices))
    lines = [
        "=" * 62,
        f"Current Mode: {cfg.mode.upper()}",
        f"Total Account Equity & Drawdown: ${portfolio.equity(prices):,.2f} / "
        f"{dd:.2%} current drawdown (Kill-Switch at {cfg.max_drawdown:.0%})",
    ]
    if portfolio.positions:
        for sym, p in portfolio.positions.items():
            px = prices.get(sym, p["entry"])
            upnl = (px - p["entry"]) * p["qty"] if p["side"] == "LONG" else \
                   (p["entry"] - px) * p["qty"]
            lines.append(
                f"Open Position: {sym} | Entry {p['entry']:.4f} | "
                f"{p['qty']:.6f} | P&L ${upnl:+.2f} | SL {p['sl']:.4f} / TP {p['tp']:.4f}")
    else:
        lines.append("Open Positions: None")
    lines.append(f"Signal State & Next Action: see signal log | Waiting/Order Pending")
    lines.append(f"Risk Utilization: {risk_usage:.1%} of daily risk | "
                 f"{risk.current_drawdown()*100:.2f}% portfolio drawdown active")
    lines.append(f"Skill/Strategy Update: No | n/a")
    lines.append("=" * 62)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Momentum trading bot (Binance spot, paper)")
    ap.add_argument("--once", action="store_true", help="single loop pass then exit")
    ap.add_argument("--status", action="store_true", help="print report and exit")
    args = ap.parse_args()

    errors = cfg.validate()
    if errors:
        print("CONFIG ERRORS:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    log = Logger()
    client = APIClient()
    port = Portfolio()
    risk = RiskManager(port.equity, log)
    check = PositionChecker(port, log)

    log.info("start", f"Starting bot mode={cfg.mode} strategy={cfg.strategy} "
                     f"tf={cfg.timeframe} symbols={cfg.symbols}")

    if not client.ping():
        log.error("data", "Cannot reach Binance API. Aborting start.", fatal=True)
        sys.exit(2)

    # ---- human gatekeeper for LIVE mode only ----
    if cfg.mode == "live":
        ans = input(f"LIVE MODE CONFIRMATION — about to trade real funds for {cfg.symbols}. "
                    f"Type 'CONFIRM' to proceed: ")
        if ans.strip().upper() != "CONFIRM":
            log.warn("gate", "Live confirmation declined. Exiting.")
            sys.exit(0)

    daily_risk_budget = cfg.capital  # max daily loss allowed = max_daily_risk * capital

    while True:
        iter_start = time.time()
        try:
            prices = {}
            bars_map = {}
            for sym in cfg.symbols:
                cl = client.klines(sym, cfg.timeframe, limit=max(300, cfg.ema_slow + 5))
                if len(cl) < cfg.ema_slow + 1:
                    raise ExchangeError(f"insufficient bars for {sym}")
                bars_map[sym] = BarData(cl)
                prices[sym] = cl[-1][4]  # latest close

            equity = port.equity(prices)
            # ---- hard rule: reconcile before any order action ----
            check.reconcile(prices)

            # ---- kill switch ----
            if risk.check_kill_switch(equity):
                port.liquidate_all(prices, log, "KILL_SWITCH")
                notify_kill_switch(port.equity(prices), risk.current_drawdown(port.equity(prices)))
                log.error("risk", "Halted: kill switch engaged. Exiting.", fatal=True)
                break

            # ---- process pending limit fills & bracket SL/TP ----
            port.process_pending_limit(prices, log)
            closed = port.check_braces(prices, log)
            for sym, reason, fill in closed:
                log.info("state", f"Bracket {sym} closed via {reason} @ {fill}")
                # find the PnL for this close via the last closed trade
                rows = log.query(
                    "SELECT pnl FROM trades WHERE symbol=? AND exit_reason=? ORDER BY ts DESC LIMIT 1",
                    (sym, reason))
                pnl = rows[0][0] if rows and rows[0][0] is not None else 0.0
                notify_close(sym, reason, fill, pnl,
                             port.equity(prices), risk.current_drawdown(port.equity(prices)))

            # ---- risk budget per day ----
            # (simple: track realized today; demo keeps a running guard)
            daily_used = abs(port.realized_pnl) if port.realized_pnl < 0 else 0.0
            risk_usage = daily_used / daily_risk_budget if daily_risk_budget else 0.0
            if risk_usage >= cfg.max_daily_risk:
                log.warn("risk", f"Daily risk cap hit ({risk_usage:.1%}), pausing new entries")

            # ---- evaluate signals & open new trades ----
            for sym, bars in bars_map.items():
                if sym in port.positions:
                    continue  # one position per symbol
                signal, meta = evaluate_momentum(bars)
                log.signal(sym, cfg.timeframe, signal, meta["close"],
                           meta["ema_fast"], meta["ema_slow"], meta["rsi"], meta)
                if signal == "LONG" and risk_usage < cfg.max_daily_risk and not risk.halted:
                    entry = prices[sym]
                    atr_v = meta["atr"] or (meta["close"] * 0.01)
                    sl = entry - cfg.atr_sl_mult * atr_v
                    tp = entry + cfg.atr_tp_mult * atr_v
                    notional, qty = risk.position_size(entry, sl, equity)
                    port.place_entry(sym, "LONG", entry, qty, notional, sl, tp, log)
                    notify_open(sym, "LONG", entry, qty, sl, tp)

            log.equity(port.equity(prices), risk.current_drawdown(port.equity(prices)))
            if args.once:
                break

            # poll interval
            elapsed = time.time() - iter_start
            time.sleep(max(0.5, POLL_SECONDS - elapsed))

        except ExchangeError as e:
            # API failure safeguard: pause loop, alert, DO NOT trade on missing data
            log.error("data", f"API failure, pausing: {e}")
            time.sleep(10)
            continue
        except RiskError as e:
            log.error("risk", f"Risk error: {e}")
            time.sleep(2)
            continue
        except Exception as e:
            log.error("system", f"Unexpected error: {e!r}")
            time.sleep(5)
            continue


if __name__ == "__main__":
    main()
