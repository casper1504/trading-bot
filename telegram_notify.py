"""Telegram notification layer for the trading bot.

Sends alerts (position opened / closed / kill switch / status) to a Telegram chat
via the Bot API. Token is read from env (TELEGRAM_BOT_TOKEN) or .env — never hardcoded.
In GitHub Actions it is provided as a repository secret.

Safe to import even when Telegram is unconfigured: send_* calls become no-ops.
"""
import os
import urllib.parse
import urllib.request
import json

from config import _load_dotenv, cfg

_load_dotenv(".env")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

_enabled = bool(BOT_TOKEN and CHAT_ID)


def send_text(text: str, parse: str = "Markdown") -> bool:
    """Send a plain/rich text message. Returns True on success, False if sent."""
    if not _enabled:
        return False
    payload = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": parse}).encode()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[telegram] send failed: {e}")
        return False


def notify_open(symbol, side, entry, qty, sl, tp):
    return send_text(
        f"📈 *TRADE OPENED*\n{symbol} {side}\nEntry: {entry:.4f}\nQty: {qty:.6f}\n"
        f"SL: {sl:.4f} | TP: {tp:.4f}\n*Paper mode*")


def notify_close(symbol, reason, exit_price, pnl, equity, drawdown):
    emoji = "✅" if pnl >= 0 else "❌"
    return send_text(
        f"{emoji} *TRADE CLOSED* — {reason}\n{symbol} @ {exit_price:.4f}\n"
        f"PnL: `${pnl:+.2f}`\nEquity: `${equity:,.2f}` (drawdown {drawdown:.2%})")


def notify_kill_switch(equity, drawdown):
    return send_text(
        f"🛑 *KILL SWITCH TRIPPED*\nEquity `${equity:,.2f}`, drawdown {drawdown:.2%}.\n"
        f"All positions liquidated, trading halted.")


def notify_status(equity, drawdown, positions, mode="paper"):
    pos_txt = "\n".join(f"- {s}: {p['side']} @ {p['entry']:.4f}" for s, p in positions.items()) \
        if positions else "None"
    return send_text(
        f"📊 *BOT STATUS*\nMode: {mode.upper()}\nEquity: `${equity:,.2f}` "
        f"({drawdown:.2%} dd)\nOpen: {pos_txt}")


def configured() -> bool:
    return _enabled
