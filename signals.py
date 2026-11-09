"""Indicators & signal layer: momentum strategy via EMA crossover + trend filter + RSI filter."""
from config import cfg


# ---------- pure indicator functions (vector = list of floats, newest = last) ----------
def ema(values: list, period: int) -> list:
    """Full EMA series aligned to input length."""
    k = 2.0 / (period + 1)
    out = []
    prev = None
    for i, v in enumerate(values):
        if prev is None:
            prev = v
        else:
            prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(values: list, period: int = 14) -> list:
    """Wilder RSI aligned to input length; first `period` entries are NaN/None."""
    out = [None] * len(values)
    if len(values) < period + 1:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    for i in range(period + 1, len(values)):
        ch = values[i] - values[i - 1]
        g = max(ch, 0.0)
        l = max(-ch, 0.0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
        out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def atr(highs: list, lows: list, closes: list, period: int = 14) -> list:
    """True range ATR (Wilder) aligned to input length."""
    out = [0.0] * len(closes)
    if len(closes) < period + 1:
        return out
    trs = [0.0]
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    atr_val = sum(trs[1:period + 1]) / period
    out[period] = atr_val
    for i in range(period + 1, len(closes)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
        out[i] = atr_val
    return out


def crossover(a: list, b: list, i: int) -> bool:
    """Fast (a) crosses ABOVE slow (b) at bar i."""
    if i <= 0:
        return False
    return a[i - 1] <= b[i - 1] and a[i] > b[i]


def crossunder(a: list, b: list, i: int) -> bool:
    """Fast (a) crosses BELOW slow (b) at bar i."""
    if i <= 0:
        return False
    return a[i - 1] >= b[i - 1] and a[i] < b[i]


# ---------- data bundle passed from data layer ----------
class BarData:
    def __init__(self, klines: list):
        # klines: list of [open_time, o, h, l, c, volume]
        self.opens = [k[1] for k in klines]
        self.highs = [k[2] for k in klines]
        self.lows = [k[3] for k in klines]
        self.closes = [k[4] for k in klines]
        self.volumes = [k[5] for k in klines]
        self.n = len(klines)


def evaluate_momentum(bars: BarData) -> tuple:
    """Return (signal, meta) where signal in {'LONG','SHORT','FLAT'}.
    Trend filter uses trend_ema; momentum via EMA fast/slow crossover.
    Meta holds indicator values for logging."""
    ema_fast = ema(bars.closes, cfg.ema_fast)
    ema_slow = ema(bars.closes, cfg.ema_slow)
    trend = ema(bars.closes, cfg.trend_ema)
    r = rsi(bars.closes, cfg.rsi_period)
    i = bars.n - 1
    close = bars.closes[i]
    at = atr(bars.highs, bars.lows, bars.closes, cfg.atr_period)

    meta = {"close": close, "ema_fast": ema_fast[i], "ema_slow": ema_slow[i],
            "trend_ema": trend[i], "rsi": r[i] if i < len(r) else None,
            "atr": at[i] if i < len(at) else None}

    # Trend filter (optional): only take longs above trend EMA, shorts below.
    if cfg.use_trend_filter:
        above_trend = close > trend[i]
        below_trend = close < trend[i]
    else:
        above_trend = below_trend = True

    if crossover(ema_fast, ema_slow, i) and above_trend:
        # RSI overbought filter for longs
        if r[i] is not None and r[i] < cfg.rsi_filter_high:
            return "LONG", meta
        return "FLAT", meta
    if crossunder(ema_fast, ema_slow, i) and below_trend:
        if r[i] is not None and r[i] > cfg.rsi_filter_low:
            return "SHORT", meta
        return "FLAT", meta
    return "FLAT", meta
