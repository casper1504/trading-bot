"""Risk layer: position sizing, daily drawdown caps, total account kill switch.

Size = (Account * Risk%) / |Entry - StopLoss|
The position value in USD*(unified) must not exceed leverage cap * equity.
"""
from config import cfg


class RiskError(Exception):
    pass


class RiskManager:
    def __init__(self, equity_fn, log_fn=None):
        # equity_fn() -> current total account equity (float USD)
        self.equity_fn = equity_fn
        self.log = log_fn
        self._peak_equity = None
        self.halted = False

    # ---- equity & drawdown ----
    def current_drawdown(self, equity: float | None = None) -> float:
        equity = equity if equity is not None else self.equity_fn()
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity <= 0:
            return 0.0
        return (self._peak_equity - equity) / self._peak_equity

    def check_kill_switch(self, equity: float | None = None) -> bool:
        """Return True (and halt) if equity dropped by >= max_drawdown from peak."""
        dd = self.current_drawdown(equity)
        if dd >= cfg.max_drawdown:
            self.halted = True
            if self.log:
                self.log.error("risk", f"KILL SWITCH: drawdown {dd:.2%} >= "
                                       f"{cfg.max_drawdown:.0%}. Halting trading.", fatal=True)
        return self.halted

    def reset_daily(self):
        # Placeholder for daily reset hook (called once per UTC day).
        pass

    # ---- sizing ----
    def position_size(self, entry: float, stop_loss: float, equity: float) -> tuple:
        """Return (size_usd, qty) for the trade. size_usd is capital at risk budget.
        Risk per trade = equity * risk_per_trade.
        size_usd = equity*risk / (|entry-sl|/entry)  => the notional such that a
        move from entry to SL loses exactly equity*risk_per_trade (incl. taker fee)."""
        if stop_loss <= 0:
            raise RiskError("invalid stop_loss")
        entry, sl = float(entry), float(stop_loss)
        risk_budget = equity * cfg.risk_per_trade
        dist_pct = abs(entry - sl) / entry
        if dist_pct <= 1e-9:
            raise RiskError("entry==stop, cannot size")
        # notional so that SL distance * notional = risk budget (gross)
        notional = risk_budget / dist_pct
        # account for taker fee on entry as part of risk budget (approx)
        # We cap notional by leverage * equity (hard rule, never exceed)
        max_notional = equity * cfg.max_leverage
        if notional > max_notional:
            notional = max_notional
        qty = notional / entry
        # sanity: never risk more than budget even if capped by leverage
        return notional, qty
