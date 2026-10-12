import json

import numpy as np
import pytest

from cointracker import marketcap
from cointracker.marketcap import MarketCapStore, evaluate, price_quality
from cointracker.signals import score_frame
from tests.conftest import make_ohlcv

ORIGINAL_FETCH = marketcap.fetch_markets  # autouse test yaması uygulanmadan önceki gerçek fonksiyon


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params, timeout):
        self.calls.append(params["page"])
        return FakeResp(self.pages[params["page"] - 1])


def test_fetch_markets_keeps_largest_coin_per_symbol(monkeypatch):
    monkeypatch.setattr(marketcap.time, "sleep", lambda s: None)
    page1 = [{"symbol": "eth", "name": "Ethereum", "market_cap_rank": 2, "market_cap": 4e11,
              "circulating_supply": 120e6, "total_supply": 120e6, "max_supply": None},
             {"symbol": "eth", "name": "Bridged ETH", "market_cap_rank": 90, "market_cap": 1e9}]
    page2 = [{"symbol": "met", "name": "Meteora", "market_cap_rank": 133, "market_cap": 2.3e8,
              "circulating_supply": 551e6, "total_supply": 998e6, "max_supply": 1e9}]
    s = FakeSession([page1, page2])
    out = ORIGINAL_FETCH(2, s)
    assert s.calls == [1, 2]
    assert out["ETH"]["name"] == "Ethereum" and out["ETH"]["rank"] == 2
    assert out["MET"]["circulating"] == 551e6


def test_store_caches_and_survives_network_errors(tmp_path):
    calls = []

    def ok():
        calls.append(1)
        return {"BTC": {"rank": 1, "circulating": 19.9e6, "total": 19.9e6}}

    st = MarketCapStore(tmp_path, fetch=ok)
    assert st.refresh()["BTC"]["rank"] == 1
    st.refresh()  # önbellek taze: yeniden indirmez
    assert len(calls) == 1
    saved = json.loads((tmp_path / "marketcap.json").read_text())
    assert saved["coins"]["BTC"]["rank"] == 1

    def boom():
        raise OSError("ağ yok")

    again = MarketCapStore(tmp_path, fetch=boom)  # diskteki önbellekle açılır
    assert again.refresh(force=True)["BTC"]["rank"] == 1 and again.get("btc")["rank"] == 1


def test_price_quality_and_evaluate(cfg):
    calm = score_frame(make_ohlcv(np.linspace(100, 120, 500)), cfg)
    pq = price_quality(calm)
    assert pq["age_days"] == 500 and 0 < pq["atr_pct"] < 0.06
    q = evaluate(pq, {"rank": 10, "circulating": 90, "total": 100, "market_cap": 1e10})
    assert q["calm_list"] and q["float"] == pytest.approx(0.9) and not q["reasons"]

    # MET benzeri: arzın %55'i dolaşımda değil, yeni coin, oynak
    met = evaluate({"atr_pct": 0.12, "age_days": 400}, {"rank": 133, "circulating": 40, "total": 100})
    assert not met["calm_list"] and met["checks"] == {"calm": False, "mature": True, "float": False, "rank": True}
    assert any("oynak" in r for r in met["reasons"]) and any("dolaşımda" in r for r in met["reasons"])

    # CoinGecko verisi yoksa yalnızca fiyat ölçüleri karar verir
    unknown = evaluate(pq, None)
    assert unknown["calm_list"] and unknown["checks"]["float"] is None and unknown["rank"] is None
    # liste alındı ama coin ilk 500'de yok: küçük coin, dışarıda
    tiny = evaluate(pq, None, market_known=True)
    assert not tiny["calm_list"] and "ilk 500" in tiny["reasons"][0]
    young = evaluate({"atr_pct": 0.03, "age_days": 120}, None)
    assert not young["calm_list"] and "en az 365 gün" in young["reasons"][0]
