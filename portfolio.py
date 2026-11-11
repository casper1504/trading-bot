"""Portfolio & execution layer: paper-trading account with fee/slippage fidelity,
order lifecycle (limit w/ timeout -> market fallback), bracket SL/TP attached on entry."""
import json
import os
import time
import math
from datetime import datetime, timezone

from config import cfg
from logger import Logger
from risk import RiskManager, RiskError


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Portfolio:
    """Paper-trading account state, persisted to JSON for state recovery.
    In live mode the exchange account is the source of truth (reconciled on start)."""

    def __init__(self, state_file: str | None = None):
        self.state_file = state_file or cfg.state_file
        self.cash = cfg.capital
        self.positions = {}   # symbol -> {side, qty, entry, sl, tp, notional, opened_ts, trade_id}
        self.pending_orders = {}  # symbol -> order dicts awaiting entry fill (limit queue)
        self.realized_pnl = 0.0
        self._load()

    # ---- persistence ----
    def _load(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.cash = data.get("cash", self.cash)
                self.positions = data.get("positions", {})
                self.pending_orders = data.get("pending_orders", {})
                self.realized_pnl = data.get("realized_pnl", 0.0)
            except (json.JSONDecodeError, OSError):
                pass  # start fresh

    def save(self):
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump({"cash": self.cash, "positions": self.positions,
                       "pending_orders": self.pending_orders,
                       "realized_pnl": self.realized_pnl}, f, indent=2)

    # ---- accounting ----
    @property
    def gross_notional(self):
        return sum(p["notional"] for p in self.positions.values())

    def equity(self, prices: dict | None = None) -> float:
        prices = prices or {}
        pos_val = 0.0
        for sym, p in self.positions.items():
            px = prices.get(sym)
            if px is None:
                px = p["entry"]
            pos_val += p["qty"] * px
        # reserve for pending limit orders not yet filled (cost already debited on fill)
        return self.cash + pos_val

    def mark_to_market(self, prices: dict) -> dict:
        """Return per-position unrealized PnL."""
        result = {}
        for sym, p in self.positions.items():
            px = prices.get(sym, p["entry"])
            if p["side"] == "LONG":
                upnl = (px - p["entry"]) * p["qty"]
            else:
                upnl = (p["entry"] - px) * p["qty"]
            result[sym] = upnl
        return result

    # ---- order placement ----
    def place_entry(self, symbol: str, side: str, entry: float, qty: float,
                    notional: float, sl: float, tp: float, log: Logger) -> dict:
        """Place entry order. For limit type: queue it with a timeout. For market: fill now.
        Returns a dict describing the order outcome."""
        if cfg.order_type == "limit":
            oid = f"{symbol}-{int(time.time()*1000)}"
            self.pending_orders[oid] = {
                "symbol": symbol, "side": side, "price": entry, "qty": qty,
                "notional": notional, "sl": sl, "tp": tp,
                "placed": int(time.time()), "timeout": cfg.limit_timeout_seconds,
            }
            self.save()
            log.info("exec", f"LIMIT entry {side} {symbol} @ {entry} qty={qty:.6f} "
                             f"queued (timeout {cfg.limit_timeout_seconds}s)")
            return {"type": "limit", "order_id": oid, "status": "queued"}
        # market
        self._fill_entry(symbol, side, entry, qty, notional, sl, tp, log)
        return {"type": "market", "status": "filled"}

    def _fill_entry(self, symbol, side, entry, qty, notional, sl, tp, log):
        fee = notional * cfg.taker_fee
        # Debit the FULL notional cost of the spot buy (cash -> base asset).
        # On close the proceeds are credited back, so equity = cash + position value
        # stays consistent across round-trips. (Previously only the fee was debited,
        # which inflated cash on every close — a real accounting bug.)
        self.cash -= notional
        # buy costs cash; short (spot) holds base asset qty position
        self.positions[symbol] = {
            "side": side, "qty": qty, "entry": entry, "sl": sl, "tp": tp,
            "notional": notional, "opened_ts": int(time.time()*1000),
            "trade_id": None,
        }
        self.save()
        log.trade_open(symbol, side, entry, qty, notional, sl, tp, fee)
        log.info("exec", f"ENTRY {side} {symbol} @ {entry:.4f} qty={qty:.6f} "
                         f"notional=${notional:.2f} fee=${fee:.2f} SL={sl:.4f} TP={tp:.4f}")

    def process_pending_limit(self, prices: dict, log: Logger, allow_fill_slip=True):
        """Scan queued limit orders: fill if market moved to/through limit price; otherwise
        on timeout fall back to a market order (slippage applied)."""
        if not self.pending_orders:
            return
        now = int(time.time())
        expired = []
        for oid, o in self.pending_orders.items():
            px = prices.get(o["symbol"])
            if px is None:
                continue
            fill_ts = int(o["placed"]) + o["timeout"]
            crossed = (o["side"] == "LONG" and px <= o["price"]) or \
                      (o["side"] == "SHORT" and px >= o["price"])
            if crossed:
                # fill at limit price (maker) - better fidelity than market
                self._fill_entry(o["symbol"], o["side"], o["price"], o["qty"],
                                 o["notional"], o["sl"], o["tp"], log)
                expired.append(oid)
            elif now >= fill_ts:
                # timeout: fallback to market with slippage
                slip = px * (1 - cfg.max_slippage) if o["side"] == "LONG" else \
                       px * (1 + cfg.max_slippage)
                log.warn("exec", f"LIMIT {o['symbol']} timed out -> MARKET fallback @ {slip:.4f}")
                self._fill_entry(o["symbol"], o["side"], slip, o["qty"],
                                 o["notional"], o["sl"], o["tp"], log)
                expired.append(oid)
        for oid in expired:
            self.pending_orders.pop(oid, None)
        if expired:
            self.save()

    # ---- bracket monitoring (SL/TP) ----
    def check_braces(self, prices: dict, log: Logger):
        """Close positions that hit SL or TP. Returns list of closed descriptions."""
        closed = []
        for sym in list(self.positions.keys()):
            p = self.positions[sym]
            px = prices.get(sym)
            if px is None:
                continue
            reason = None
            if p["side"] == "LONG":
                if px <= p["sl"]:
                    reason = "STOP_LOSS"
                    fill = p["sl"]
                elif px >= p["tp"]:
                    reason = "TAKE_PROFIT"
                    fill = p["tp"]
            else:
                if px >= p["sl"]:
                    reason = "STOP_LOSS"
                    fill = p["sl"]
                elif px <= p["tp"]:
                    reason = "TAKE_PROFIT"
                    fill = p["tp"]
            if reason:
                self._close(sym, fill, reason, log)
                closed.append((sym, reason, fill))
        return closed

    def _close(self, symbol, exit_price, reason, log):
        p = self.positions.pop(symbol)
        if p["side"] == "LONG":
            pnl = (exit_price - p["entry"]) * p["qty"]
        else:
            pnl = (p["entry"] - exit_price) * p["qty"]
        fee = exit_price * p["qty"] * cfg.taker_fee
        pnl -= fee
        self.realized_pnl += pnl
        self.cash += exit_price * p["qty"]  # liquidate base back to cash (paper spot)
        self.save()
        log.trade_closed(p["trade_id"] or 0, exit_price, int(time.time()*1000), reason, pnl)
        log.info("exec", f"CLOSE {symbol} {reason} @ {exit_price:.4f} PnL=${pnl:.2f}")

    def liquidate_all(self, prices: dict, log: Logger, reason="KILL_SWITCH"):
        """Close all open positions & clear pending orders (kill switch / shutdown)."""
        for sym in list(self.positions.keys()):
            px = prices.get(sym)
            if px is None:
                px = self.positions[sym]["entry"]
            self._close(sym, px, reason, log)
        n = len(self.pending_orders)
        self.pending_orders = {}
        self.save()
        log.info("exec", f"LIQUIDATED all positions ({reason}); cleared {n} pending orders")


class PositionChecker:
    """Hard rule: reconcile positions/orders/balance BEFORE any order action."""
    def __init__(self, portfolio, log):
        self.portfolio = portfolio
        self.log = log

    def reconcile(self, prices: dict) -> dict:
        eq = self.portfolio.equity(prices)
        positions = list(self.portfolio.positions.keys())
        pending = list(self.portfolio.pending_orders.keys())
        self.log.info("state", f"RECONCILE equity=${eq:.2f} open_positions={positions} "
                              f"pending_orders={pending}")
        return {"equity": eq, "positions": positions, "pending_orders": pending}


def qty_for_asset(qty, asset="USDT"):
    # Binance spot LOT_SIZE 8 dp-a-like; keep simple rounding for paper
    return round(qty, 8)
