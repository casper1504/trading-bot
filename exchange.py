"""Data & exchange layer: fetch live/historical data from Binance with rate-limit
(HTTP 429) and exponential backoff retry handling."""
import time
import json
import hmac
import hashlib
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

from config import cfg

MAX_RETRIES = 5
BACKOFF_BASE = 2.0  # seconds


class ExchangeError(Exception):
    pass


class RateLimitError(ExchangeError):
    pass


class APIClient:
    """Thin Binance REST client. Public endpoints need no auth (paper mode).
    Signed private endpoints (live only). Public data hosts are tried in order
    with fallback, because api.binance.com geo-blocks some datacenter IPs
    (e.g. GitHub Actions runners in the US) while data-api.binance.vision does not."""

    # public-data hosts, tried in order; data-api.binance.vision is the
    # geo-block-safe public mirror used when the primary is unreachable
    PUBLIC_HOSTS = ("https://api.binance.com", "https://data-api.binance.vision",
                    "https://api.binance.vision")

    def __init__(self, base_url: str | None = None):
        self.key = cfg.api_key
        self.secret = cfg.api_secret
        self._private_host = base_url or (cfg.test_url if cfg.mode == "paper" else cfg.base_url)

    def _hosts_for(self, signed: bool) -> tuple:
        if signed:
            return (self._private_host,)
        return self.PUBLIC_HOSTS

    # ---- low level with retry / backoff ----
    def _request(self, method: str, path: str, params: dict | None = None,
                 signed: bool = False) -> dict | list:
        params = params or {}
        qs = urllib.parse.urlencode(params)

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            qs = urllib.parse.urlencode(params)
            params["signature"] = hmac.new(
                self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
            qs = urllib.parse.urlencode(params)

        hosts = self._hosts_for(signed)
        last_err = None
        for host in hosts:
            full = f"{host}{path}?{qs}" if qs else host + path
            try:
                return self._request_once(method, full, signed)
            except ExchangeError as e:
                last_err = e
                # try the next host
        raise last_err if last_err else ExchangeError("all hosts failed")

    def _request_once(self, method: str, full: str, signed: bool) -> dict | list:
        req = urllib.request.Request(full, method=method)
        if signed:
            req.add_header("X-MBX-APIKEY", self.key)

        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read().decode()
                    if not data:
                        return {}
                    obj = json.loads(data)
                    if isinstance(obj, dict) and obj.get("code") not in (None, 200, 0):
                        raise ExchangeError(f"API error {obj.get('code')}: {obj.get('msg')}")
                    return obj
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                if e.code == 429 or e.code == 418:
                    sleep_s = BACKOFF_BASE ** attempt
                    last_err = RateLimitError(f"HTTP {e.code} rate limited: {body[:200]}")
                elif 500 <= e.code < 600:
                    sleep_s = BACKOFF_BASE ** (attempt - 1)
                    last_err = ExchangeError(f"HTTP {e.code} server error: {body[:200]}")
                else:
                    raise ExchangeError(f"HTTP {e.code}: {body[:200]}") from e
            except urllib.error.URLError as e:
                sleep_s = BACKOFF_BASE ** (attempt - 1)
                last_err = ExchangeError(f"network error: {e.reason}")
            except json.JSONDecodeError as e:
                raise ExchangeError(f"bad json: {e}") from e
            # backoff within a host's retries
            if attempt < MAX_RETRIES:
                time.sleep(min(sleep_s, 30))
        raise last_err if last_err else ExchangeError("unknown request failure")

    # ---- public data ----
    def ping(self) -> bool:
        try:
            self._request("GET", "/api/v3/ping")
            return True
        except ExchangeError:
            return False

    def server_time(self) -> int:
        return int(self._request("GET", "/api/v3/time")["serverTime"])

    def klines(self, symbol: str, interval: str, limit: int = 300) -> list:
        """Return list of (open_time, o, h, l, c, volume) floats, newest last."""
        raw = self._request("GET", "/api/v3/klines", {
            "symbol": symbol.replace("/", ""), "interval": interval, "limit": limit})
        return [[float(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])]
                for x in raw]

    def ticker(self, symbol: str) -> dict:
        return self._request("GET", "/api/v3/ticker/24hr", {"symbol": symbol.replace("/", "")})

    def exchange_info(self) -> dict:
        return self._request("GET", "/api/v3/exchangeInfo")

    # ---- private (live) ----
    def balances(self) -> dict:
        return self._request("GET", "/api/v3/account", signed=True)

    def open_orders(self, symbol: str | None = None) -> list:
        return self._request("GET", "/api/v3/openOrders",
                             {"symbol": symbol.replace("/", "")} if symbol else {}, signed=True)

    def place_order(self, symbol: str, side: str, qty: str, price: str | None = None,
                    order_type: str = "LIMIT", time_in_force: str = "GTC") -> dict:
        p = {"symbol": symbol.replace("/", ""), "side": side, "type": order_type,
             "quantity": qty}
        if order_type == "LIMIT":
            p["timeInForce"] = time_in_force
            p["price"] = price
        return self._request("POST", "/api/v3/order", p, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._request("DELETE", "/api/v3/order",
                             {"symbol": symbol.replace("/", ""), "orderId": order_id}, signed=True)
