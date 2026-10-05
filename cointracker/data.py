"""Geçmiş fiyat verisinin indirilmesi ve yerel olarak önbelleğe alınması.

Birincil kaynak Binance'in herkese açık piyasa verisi API'sidir (anahtar gerekmez).
Binance'te olmayan coinler için CryptoCompare günlük verisi yedek kaynak olarak kullanılır.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from cointracker.config import STABLECOINS

log = logging.getLogger(__name__)

BINANCE_BASES = ("https://data-api.binance.vision", "https://api.binance.com")
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/v2/histoday"
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
LEVERAGED_UNDERLYINGS = {
    "BTC", "ETH", "BNB", "XRP", "ADA", "DOT", "LINK", "LTC", "TRX", "EOS", "XTZ", "SXP",
    "FIL", "YFI", "UNI", "AAVE", "SUSHI", "XLM", "BCH", "1INCH",
}
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume", "quote_volume"]
DAY_MS = 86_400_000
# SMA200 gibi göstergelerin ısınması için analiz penceresinin önüne eklenen gün sayısı
WARMUP_DAYS = 250


def normalize_symbol(coin: str, quote: str = "USDT") -> str:
    """'btc' -> 'BTCUSDT'. Zaten pariteyse olduğu gibi döner."""
    coin = coin.strip().upper().replace("/", "").replace("-", "")
    return coin if coin.endswith(quote) and coin != quote else coin + quote


def base_asset(symbol: str, quote: str = "USDT") -> str:
    return symbol[: -len(quote)] if symbol.endswith(quote) else symbol


class BinanceClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 20):
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        last_exc: Exception | None = None
        for base in BINANCE_BASES:
            try:
                resp = self.session.get(base + path, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                log.debug("Binance %s isteği başarısız (%s): %s", path, base, exc)
        assert last_exc is not None
        raise last_exc

    def klines(self, symbol: str, start: datetime, end: datetime | None = None) -> pd.DataFrame:
        """Günlük mumları sayfalayarak indirir (istek başına en fazla 1000 mum)."""
        start_ms = int(start.timestamp() * 1000)
        end_ms = int((end or datetime.now(timezone.utc)).timestamp() * 1000)
        rows: list[list] = []
        while start_ms <= end_ms:
            batch = self._get(
                "/api/v3/klines",
                {"symbol": symbol, "interval": "1d", "startTime": start_ms, "endTime": end_ms, "limit": 1000},
            )
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < 1000:
                break
            start_ms = batch[-1][0] + DAY_MS
        return klines_to_frame(rows)

    def tickers_24h(self) -> list[dict]:
        return self._get("/api/v3/ticker/24hr")

    def trading_symbols(self, quote: str = "USDT") -> set[str]:
        info = self._get("/api/v3/exchangeInfo", {"permissions": "SPOT"})
        return {
            s["symbol"]
            for s in info.get("symbols", [])
            if s.get("status") == "TRADING" and s.get("quoteAsset") == quote
        }


def klines_to_frame(rows: list[list]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([], name="date"))
    df = pd.DataFrame(
        [[r[0], r[1], r[2], r[3], r[4], r[5], r[7]] for r in rows],
        columns=["open_time", *OHLCV_COLUMNS],
    )
    df.index = pd.to_datetime(df.pop("open_time"), unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    df.index.name = "date"
    return df.astype(float)


def fetch_cryptocompare(coin: str, days: int, session: requests.Session | None = None) -> pd.DataFrame:
    """Yedek kaynak: CryptoCompare'den USD bazlı günlük veri (en fazla 2000 gün/istek)."""
    session = session or requests.Session()
    resp = session.get(
        CRYPTOCOMPARE_URL, params={"fsym": coin, "tsym": "USD", "limit": min(days, 2000)}, timeout=20
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("Response") == "Error":
        raise ValueError(payload.get("Message", "CryptoCompare hatası"))
    data = [d for d in payload["Data"]["Data"] if d.get("close")]
    if not data:
        return klines_to_frame([])
    df = pd.DataFrame(data)
    df.index = pd.to_datetime(df["time"], unit="s").dt.normalize()
    df.index.name = "date"
    df = df.rename(columns={"volumefrom": "volume", "volumeto": "quote_volume"})
    return df[OHLCV_COLUMNS].astype(float)


def is_tradeable_base(base: str, exclude: set[str]) -> bool:
    if base in STABLECOINS or base in exclude:
        return False
    # Eski kaldıraçlı tokenlar (BTCUP, ETHDOWN...) — SYRUP/JUP gibi gerçek coinleri dışlamamak için
    # yalnızca kalan kısım bilinen bir büyük coinse ele.
    for suffix in LEVERAGED_SUFFIXES:
        if base.endswith(suffix) and base[: -len(suffix)] in LEVERAGED_UNDERLYINGS:
            return False
    return True


def list_universe(cfg: dict, client: BinanceClient) -> list[str]:
    """Ayarlara göre takip edilecek pariteleri döndürür (ör. ['BTCUSDT', ...])."""
    uni = cfg["universe"]
    quote = uni["quote"]
    exclude = {e.upper() for e in uni.get("exclude", [])}
    if uni["mode"] == "list":
        return [normalize_symbol(s, quote) for s in uni["symbols"]]

    trading = client.trading_symbols(quote)
    tickers = [
        t for t in client.tickers_24h()
        if t["symbol"] in trading and is_tradeable_base(base_asset(t["symbol"], quote), exclude)
    ]
    tickers.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
    symbols = [t["symbol"] for t in tickers]
    if uni["mode"] == "top":
        symbols = symbols[: int(uni["top_n"])]
    return symbols


class DataStore:
    """Her parite için günlük OHLCV verisini data/ohlcv/<SEMBOL>.csv içinde tutar."""

    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir) / "ohlcv"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, symbol: str) -> Path:
        return self.dir / f"{symbol}.csv"

    def load(self, symbol: str) -> pd.DataFrame | None:
        p = self.path(symbol)
        if not p.exists():
            return None
        df = pd.read_csv(p, index_col="date", parse_dates=["date"])
        return df if not df.empty else None

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        df.to_csv(self.path(symbol))

    def update(self, symbol: str, client: BinanceClient, years: int, quote: str = "USDT") -> pd.DataFrame:
        """Eksik günleri indirir. İlk çalıştırmada son `years` yıl + ısınma süresi kadar veri çeker."""
        now = datetime.now(timezone.utc)
        existing = self.load(symbol)
        if existing is not None:
            # Son mum (bugün) tamamlanmamış olabilir; onu yeniden indir.
            start = existing.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)
        else:
            start = now - timedelta(days=int(365.25 * years) + WARMUP_DAYS)

        try:
            fresh = client.klines(symbol, start, now)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 400:
                raise
            fresh = klines_to_frame([])  # Binance'te bu parite yok

        if fresh.empty and existing is None:
            days = int(365.25 * years) + WARMUP_DAYS
            log.info("%s Binance'te bulunamadı, CryptoCompare deneniyor", symbol)
            fresh = fetch_cryptocompare(base_asset(symbol, quote), days, client.session)

        if existing is not None:
            fresh = pd.concat([existing, fresh])
        fresh = fresh[~fresh.index.duplicated(keep="last")].sort_index()
        if not fresh.empty:
            self.save(symbol, fresh)
        return fresh
