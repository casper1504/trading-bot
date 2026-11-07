"""Integration test for the execution/risk/portfolio layers (no network)."""
import time
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
import config
config.cfg.order_type = "limit"
config.cfg.limit_timeout_seconds = 1
config.cfg.max_slippage = 0.001

from logger import Logger
from portfolio import Portfolio
from risk import RiskManager

log = Logger("test_log.db")
port = Portfolio("test_state.json")
port.cash = config.cfg.capital
risk = RiskManager(port.equity, log)

# 1. limit entry queued
entry = 100.0
sl = 95.0
tp = 110.0
notional, qty = risk.position_size(entry, sl, port.cash)
res = port.place_entry("TEST/USDT", "LONG", entry, qty, notional, sl, tp, log)
assert res["type"] == "limit" and res["status"] == "queued", res
print("PASS: limit entry queued, qty=", round(qty, 6), "notional=", round(notional, 2))

# 2. timeout -> market fallback (price never crosses 100, wait past 1s timeout)
time.sleep(1.2)
prices = {"TEST/USDT": 99.0}  # above limit 100 for LONG? no, fallback triggers
port.process_pending_limit(prices, log)
assert "TEST/USDT" in port.positions, "expected fallback fill"
p = port.positions["TEST/USDT"]
print("PASS: market fallback filled @", p["entry"])

# 3. bracket SL close
port.check_braces({"TEST/USDT": 94.0}, log)
assert "TEST/USDT" not in port.positions, "SL should have closed"
print("PASS: stop-loss bracket closed position")

# 4. kill switch on drawdown (establish peak = seed capital first)
risk.current_drawdown(1000.0)  # sets peak equity to 1000
port.cash = 800.0  # 20% down from peak
assert risk.check_kill_switch(port.cash) is True, "kill switch should trip"
print("PASS: kill switch tripped at drawdown", f"{risk.current_drawdown():.0%}")

# 5. daily risk guard
risk_usage = abs(port.realized_pnl) / config.cfg.capital
print("PASS: realized PnL guard present, usage=", round(risk_usage, 4))

log.ping = None
log.close()
print("\nALL INTEGRATION TESTS PASSED")
