"""Tanıtım videosu için demo sunucusu: paneli gerçekçi ama UYDURMA (demo) verilerle çalıştırır.

Ağ erişimi gerektirmez. Kullanım:  python tools/promo/demo_server.py <veri_klasörü> <port>
SIGUSR1 sinyali gelince analizleri başlatır (açılış sahnesinde coinlerin tek tek dolması görünsün diye).
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cointracker import dashboard  # noqa: E402
from cointracker.config import DEFAULT_CONFIG, _deep_merge  # noqa: E402
from cointracker.data import klines_to_frame  # noqa: E402
from cointracker.engine import Engine  # noqa: E402
from cointracker.exchanges import EXCHANGES, Quote  # noqa: E402

# coin -> (güncel fiyat, sentetik seri tohumu). Tohumlar farklı sinyal seviyeleri verecek şekilde seçildi.
COINS = {
    "BTC": (84250, 20), "ETH": (2650, 0), "SOL": (146.2, 82), "BNB": (592, 2), "XRP": (2.14, 1),
    "DOGE": (0.1712, 3), "ADA": (0.684, 8), "AVAX": (24.3, 116), "LINK": (14.62, 4), "DOT": (4.12, 24),
    "LTC": (88.4, 5), "SUI": (3.41, 159), "TRX": (0.2431, 6), "PEPE": (0.0000088, 15), "NEAR": (2.62, 7),
    "TON": (3.18, 9),
}
INTERVAL_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


def synthetic_prices(days: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(days)
    log_price = np.log(100) + 0.0008 * t + 0.6 * np.sin(t / 120) + np.cumsum(rng.normal(0, 0.02, days))
    return np.exp(log_price)


def ohlcv(closes: np.ndarray, index: pd.DatetimeIndex, seed: int, wick: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 7)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    hi = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, wick, len(closes))))
    lo = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, wick, len(closes))))
    vol = np.exp(rng.normal(0, 0.4, len(closes))) * 1e6 / closes
    return pd.DataFrame({"open": opens, "high": hi, "low": lo, "close": closes, "volume": vol,
                         "quote_volume": vol * closes}, index=index)


def daily_frame(coin: str) -> pd.DataFrame:
    price, seed = COINS[coin]
    raw = synthetic_prices(1900, seed)
    closes = raw / raw[-1] * price
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    index = pd.date_range(end=today, periods=len(closes), freq="D", name="date")
    return ohlcv(closes, index, seed, 0.012)


class DemoClient:
    """Binance istemcisinin demo karşılığı: gün içi mumlar, emir defteri ve 24 saatlik özet üretir."""

    session = None

    def __init__(self, store):
        self.store = store

    def _price(self, symbol: str) -> float:
        return float(self.store.load(symbol)["close"].iloc[-1])

    def recent_klines(self, symbol: str, interval: str = "1h", limit: int = 500) -> pd.DataFrame:
        if interval == "1d":
            return self.store.load(symbol).iloc[-limit:]
        minutes = INTERVAL_MIN[interval]
        seed = abs(hash((symbol, interval))) % 2**32
        rng = np.random.default_rng(seed)
        sigma = 0.035 * np.sqrt(minutes / 1440)
        steps = rng.normal(0, sigma, limit) + 0.15 * sigma * np.sin(np.arange(limit) / 40)
        path = np.exp(np.cumsum(steps))
        closes = path / path[-1] * self._price(symbol)
        now = pd.Timestamp.now(tz="UTC").tz_localize(None).floor(f"{minutes}min")
        index = pd.date_range(end=now, periods=limit, freq=f"{minutes}min", name="date")
        return ohlcv(closes, index, seed, sigma * 0.4)

    def depth(self, symbol: str, limit: int = 500) -> dict:
        price = self._price(symbol)
        rng = np.random.default_rng(len(symbol))
        bids = [[price * (1 - 0.0004 * (i + 1)), float(rng.gamma(2, 4000 / price))] for i in range(60)]
        asks = [[price * (1 + 0.0004 * (i + 1)), float(rng.gamma(2, 3000 / price))] for i in range(60)]
        bids[17][1] *= 12  # belirgin bir alış duvarı
        asks[25][1] *= 9
        return {"bids": bids, "asks": asks}

    def ticker_24h(self, symbol: str) -> dict:
        hourly = self.recent_klines(symbol, "1h", 24)
        price = self._price(symbol)
        return {"lastPrice": price, "priceChangePercent": (price / hourly["open"].iloc[0] - 1) * 100,
                "highPrice": hourly["high"].max(), "lowPrice": hourly["low"].min(),
                "quoteVolume": float(hourly["quote_volume"].sum()) * 40, "count": 1_284_331,
                "weightedAvgPrice": float(hourly["close"].mean())}


def demo_quotes(coin: str, exchanges=None) -> list[Quote]:
    price = COINS[coin.upper()][0]
    offsets = {"binance": 0.0, "okx": -0.0004, "bybit": 0.0002, "kucoin": 0.0006, "gate": -0.0002,
               "coinbase": 0.0011, "kraken": 0.0008, "btcturk": 0.0015}
    quotes = []
    for name, off in offsets.items():
        _, pair, fee, url = EXCHANGES[name]
        quotes.append(Quote(name, pair.format(c=coin), price * (1 + off), fee, url.format(c=coin, lc=coin.lower())))
    return sorted(quotes, key=lambda q: q.effective_buy)


def main() -> None:
    data_dir, port = Path(sys.argv[1]), int(sys.argv[2])
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": str(data_dir)})
    engine = Engine(cfg)
    for coin in COINS:
        engine.store.save(f"{coin}USDT", daily_frame(coin))
    engine.client = DemoClient(engine.store)
    engine.update_one = lambda symbol: len(engine.store.load(symbol))  # indirme yok, önbellek hazır

    analyze = engine.analyze_symbol

    def slow_analyze(symbol, run_backtest=True):  # açılış sahnesi için coinler tek tek dolsun
        time.sleep(0.35)
        return analyze(symbol, run_backtest)

    engine.analyze_symbol = slow_analyze
    dashboard.get_quotes = demo_quotes
    app = dashboard.DashboardApp(cfg, engine=engine, symbols=list(COINS),
                                 notifier=lambda t, b: print("BİLDİRİM:", t, "|", b, flush=True), workers=2)
    app.universe = [f"{c}USDT" for c in COINS]
    signal.signal(signal.SIGUSR1, lambda *_: threading.Thread(target=app.refresh, daemon=True).start())
    server = ThreadingHTTPServer(("127.0.0.1", port), dashboard.make_handler(app))
    print(f"demo sunucusu hazır: http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
