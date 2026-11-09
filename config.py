"""Environment & security layer: load all config via .env. Never hardcode credentials."""
import os
from dataclasses import dataclass, field


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (stdlib, no dependency) — does not overwrite existing env vars."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            # strip inline comments (space + #)
            if " #" in value:
                value = value.split(" #")[0].rstrip()
            if value and value.startswith("#"):
                continue
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass
class Config:
    symbols: list = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    timeframe: str = "15m"
    capital: float = 1000.0
    strategy: str = "momentum"
    mode: str = "paper"
    order_type: str = "limit"
    limit_timeout_seconds: int = 60
    taker_fee: float = 0.001
    maker_fee: float = 0.001
    max_slippage: float = 0.0005
    risk_per_trade: float = 0.01
    max_drawdown: float = 0.10
    max_daily_risk: float = 0.05
    max_leverage: int = 3
    atr_period: int = 14
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 3.0
    ema_fast: int = 8
    ema_slow: int = 21
    trend_ema: int = 50
    use_trend_filter: bool = False
    rsi_period: int = 14
    rsi_filter_high: float = 70.0
    rsi_filter_low: float = 30.0
    min_trades_review: int = 100
    min_runtime_review_days: int = 7
    api_key: str = ""
    api_secret: str = ""
    state_file: str = "state.json"
    log_db: str = "trading_log.db"
    # Internal / not user-facing
    base_url: str = "https://api.binance.com"
    test_url: str = "https://testnet.binance.vision"

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "Config":
        _load_dotenv(env_path)
        get = os.environ.get

        def _symbols():
            return [s.strip() for s in get("SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,BNB/USDT").split(",") if s.strip()]

        return cls(
            symbols=_symbols(),
            timeframe=get("TIMEFRAME", "15m") or "15m",
            capital=float(get("CAPITAL", "1000.0")),
            strategy=(get("STRATEGY", "momentum") or "momentum").lower(),
            mode=(get("MODE", "paper") or "paper").lower(),
            order_type=(get("ORDER_TYPE", "limit") or "limit").lower(),
            limit_timeout_seconds=int(float(get("LIMIT_TIMEOUT_SECONDS", "60"))),
            taker_fee=float(get("TAKER_FEE", "0.001")),
            maker_fee=float(get("MAKER_FEE", "0.001")),
            max_slippage=float(get("MAX_SLIPPAGE", "0.0005")),
            risk_per_trade=float(get("RISK_PER_TRADE", "0.01")),
            max_drawdown=float(get("MAX_DRAWDOWN", "0.10")),
            max_daily_risk=float(get("MAX_DAILY_RISK", "0.05")),
            max_leverage=int(float(get("MAX_LEVERAGE", "3"))),
            atr_period=int(float(get("ATR_PERIOD", "14"))),
            atr_sl_mult=float(get("ATR_SL_MULT", "1.5")),
            atr_tp_mult=float(get("ATR_TP_MULT", "3.0")),
            ema_fast=int(float(get("EMA_FAST", "8"))),
            ema_slow=int(float(get("EMA_SLOW", "21"))),
            trend_ema=int(float(get("TREND_EMA", "50"))),
            use_trend_filter=(get("USE_TREND_FILTER", "false").lower() in ("1","true","yes")),
            rsi_period=int(float(get("RSI_PERIOD", "14"))),
            rsi_filter_high=float(get("RSI_FILTER_HIGH", "70")),
            rsi_filter_low=float(get("RSI_FILTER_LOW", "30")),
            min_trades_review=int(float(get("MIN_TRADES_REVIEW", "100"))),
            min_runtime_review_days=int(float(get("MIN_RUNTIME_REVIEW_DAYS", "7"))),
            api_key=get("BINANCE_API_KEY", "") or "",
            api_secret=get("BINANCE_API_SECRET", "") or "",
        )

    def validate(self) -> list:
        errors = []
        if self.risk_per_trade <= 0 or self.risk_per_trade > 0.05:
            errors.append(f"risk_per_trade must be >0 and <=5% (got {self.risk_per_trade})")
        if self.max_drawdown <= 0 or self.max_drawdown > 0.5:
            errors.append(f"max_drawdown must be >0 and <=50% (got {self.max_drawdown})")
        if self.mode not in ("paper", "live"):
            errors.append(f"mode must be 'paper' or 'live' (got {self.mode})")
        if self.order_type not in ("limit", "market"):
            errors.append(f"order_type must be 'limit' or 'market' (got {self.order_type})")
        if self.strategy not in ("momentum", "mean-reversion"):
            errors.append(f"strategy must be 'momentum' or 'mean-reversion' (got {self.strategy})")
        if self.mode == "live" and (not self.api_key or not self.api_secret):
            errors.append("live mode requires BINANCE_API_KEY and BINANCE_API_SECRET in .env")
        return errors


cfg = Config.from_env()
