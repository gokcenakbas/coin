"""'Nereden alınır?' — bir coinin farklı borsalardaki anlık fiyatlarını karşılaştırır.

Yalnızca herkese açık fiyat uç noktaları kullanılır; API anahtarı gerekmez.
Komisyon oranları standart (en düşük kademe) taker oranlarının yaklaşık değerleridir;
güncel oranı her zaman borsanın kendi sitesinden kontrol edin.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import requests

log = logging.getLogger(__name__)


@dataclass
class Quote:
    exchange: str
    pair: str
    price: float
    fee: float
    url: str

    @property
    def effective_buy(self) -> float:
        """Komisyon dahil alış maliyeti (1 adet için)."""
        return self.price * (1 + self.fee)

    @property
    def effective_sell(self) -> float:
        """Komisyon düşülmüş satış geliri (1 adet için)."""
        return self.price * (1 - self.fee)


def _binance(s: requests.Session, coin: str) -> float:
    r = s.get("https://data-api.binance.vision/api/v3/ticker/price", params={"symbol": f"{coin}USDT"}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])


def _okx(s: requests.Session, coin: str) -> float:
    r = s.get("https://www.okx.com/api/v5/market/ticker", params={"instId": f"{coin}-USDT"}, timeout=10)
    r.raise_for_status()
    return float(r.json()["data"][0]["last"])


def _bybit(s: requests.Session, coin: str) -> float:
    r = s.get(
        "https://api.bybit.com/v5/market/tickers", params={"category": "spot", "symbol": f"{coin}USDT"}, timeout=10
    )
    r.raise_for_status()
    return float(r.json()["result"]["list"][0]["lastPrice"])


def _kucoin(s: requests.Session, coin: str) -> float:
    r = s.get(
        "https://api.kucoin.com/api/v1/market/orderbook/level1", params={"symbol": f"{coin}-USDT"}, timeout=10
    )
    r.raise_for_status()
    return float(r.json()["data"]["price"])


def _gate(s: requests.Session, coin: str) -> float:
    r = s.get("https://api.gateio.ws/api/v4/spot/tickers", params={"currency_pair": f"{coin}_USDT"}, timeout=10)
    r.raise_for_status()
    return float(r.json()[0]["last"])


def _coinbase(s: requests.Session, coin: str) -> float:
    r = s.get(f"https://api.exchange.coinbase.com/products/{coin}-USD/ticker", timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])


def _kraken(s: requests.Session, coin: str) -> float:
    pair = ("XBT" if coin == "BTC" else coin) + "USD"
    r = s.get("https://api.kraken.com/0/public/Ticker", params={"pair": pair}, timeout=10)
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise ValueError(payload["error"])
    return float(next(iter(payload["result"].values()))["c"][0])


def _btcturk(s: requests.Session, coin: str) -> float:
    r = s.get("https://api.btcturk.com/api/v2/ticker", params={"pairSymbol": f"{coin}USDT"}, timeout=10)
    r.raise_for_status()
    return float(r.json()["data"][0]["last"])


# ad -> (fiyat fonksiyonu, parite şablonu, yaklaşık taker komisyonu, işlem sayfası şablonu)
EXCHANGES: dict[str, tuple[Callable[[requests.Session, str], float], str, float, str]] = {
    "binance": (_binance, "{c}/USDT", 0.0010, "https://www.binance.com/tr/trade/{c}_USDT"),
    "okx": (_okx, "{c}/USDT", 0.0010, "https://www.okx.com/tr/trade-spot/{lc}-usdt"),
    "bybit": (_bybit, "{c}/USDT", 0.0010, "https://www.bybit.com/tr-TR/trade/spot/{c}/USDT"),
    "kucoin": (_kucoin, "{c}/USDT", 0.0010, "https://www.kucoin.com/tr/trade/{c}-USDT"),
    "gate": (_gate, "{c}/USDT", 0.0020, "https://www.gate.io/tr/trade/{c}_USDT"),
    "coinbase": (_coinbase, "{c}/USD", 0.0060, "https://www.coinbase.com/advanced-trade/spot/{c}-USD"),
    "kraken": (_kraken, "{c}/USD", 0.0040, "https://pro.kraken.com/app/trade/{c}-USD"),
    "btcturk": (_btcturk, "{c}/USDT", 0.0020, "https://pro.btcturk.com/pro/al-sat/{c}_USDT"),
}


def get_quotes(coin: str, exchanges: list[str] | None = None, session: requests.Session | None = None) -> list[Quote]:
    """Coinin listelendiği borsalardaki fiyatları döndürür (komisyon dahil alış maliyetine göre ucuzdan pahalıya)."""
    coin = coin.upper()
    names = [n for n in (exchanges or list(EXCHANGES)) if n in EXCHANGES]
    session = session or requests.Session()

    def fetch(name: str) -> Quote | None:
        fn, pair, fee, url = EXCHANGES[name]
        try:
            price = fn(session, coin)
        except Exception as exc:  # listelenmemiş coin, ağ hatası vb.
            log.debug("%s %s fiyatı alınamadı: %s", name, coin, exc)
            return None
        if price <= 0:
            return None
        return Quote(name, pair.format(c=coin), price, fee, url.format(c=coin, lc=coin.lower()))

    with ThreadPoolExecutor(max_workers=len(names) or 1) as pool:
        quotes = [q for q in pool.map(fetch, names) if q is not None]
    return sorted(quotes, key=lambda q: q.effective_buy)
