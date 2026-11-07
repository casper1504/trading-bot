# Momentum Trading Bot — Binance Spot (Paper)

A self-contained, **stdlib-only** (zero pip dependencies) momentum trading bot for Binance
spot USDT pairs, configured as **paper trading** — no API keys needed for paper mode.

## Confirmed configuration
| Parameter | Value |
|---|---|
| Asset class | Crypto (spot/USDT) |
| Exchange | Binance |
| Strategy | Momentum: EMA(12/26) crossover + EMA(200) trend filter + RSI filter |
| Timeframe | 15m |
| Capital | $1,000 |
| Order type | Limit, 60s timeout → Market fallback |
| Mode | **Paper** (default) |
| Risk | 1% per trade; 10% total kill-switch drawdown |

## Quick start
```bash
cd trading_bot
cp .env.example .env        # defaults are paper-safe; edit if needed
python bot.py               # run continuously
python bot.py --once        # single pass (verify a clean run)
```

Paper mode hits only Binance **public** endpoints, so you can run it with an empty `.env`.
For live mode, fill `BINANCE_API_KEY`/`BINANCE_API_SECRET` and set `MODE=live`; the bot
then demands a typed `CONFIRM` human gatekeeper before trading.

## Hard rules enforced (Section 3)
- **Reconcile before orders**: every loop iter calls the position/balance reconciliation
  before any order action (`PositionChecker.reconcile`).
- **Kill switch**: if equity drops ≥10% from peak → liquidate all, cancel orders, halt.
- **Leverage/risk cap**: position notional never exceeds `max_leverage × equity`; risk per
  trade is `equity × risk_per_trade`.
- **Broker-side SL/TP**: every entry attaches SL (1.5×ATR) and TP (3×ATR) brackets that
  are monitored every poll.
- **API failure safeguard**: on exchange errors the loop pauses (no trades on missing data)
  and logs an error.
- **Human gatekeeper**: live mode only, before the first order of the session.

## Paper-trading fidelity
Maker/taker fees (`TAKER_FEE`=0.1%, `MAKER_FEE`=0.1%) are charged on fill; limit fills
pay entry at limit price, timeouts fall back to market with up to `MAX_SLIPPAGE` (0.05%)
slippage.

## State recovery & logging
- `state.json` — portfolio cash, positions, SL/TP, pending orders (reloaded at startup).
- `trading_log.db` (SQLite) — `signals`, `trades`, `equity`, `errors`, `logs` tables.
- Structured JSON log lines printed to stdout.

## Self-improvement loop (Section 4)
After ≥100 closed trades **or** ≥7 days:
```bash
python strategy_update.py --review            # performance + loss-pattern review trigger
python strategy_update.py --backtest BTC/USDT # hard gate: historical backtest
```
`write_amendment()` writes a `strategy_amend_*.md` only after passing the syntax gate and
a hard-rule gate that **rejects** any change softening risk_per_trade, the kill switch,
leverage cap, or pre-order reconciliation.

## Tests
```bash
python test_integration.py   # execution layer: limit entry, market fallback, SL bracket, kill switch
```

## Status report format (Section 5)
Run `python bot.py --status` for the live report (equity/drawdown, open positions with
SL/TP, signal state, risk utilization, strategy-update status).
