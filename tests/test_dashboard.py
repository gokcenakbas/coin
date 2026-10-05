import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pytest

from cointracker import dashboard
from cointracker.dashboard import DashboardApp, clean, make_handler
from cointracker.engine import Engine
from cointracker.live import orderbook_summary, ticker_summary, timeframe_signal
from tests.conftest import make_ohlcv, synthetic_prices


class FakeLiveClient:
    session = None

    def recent_klines(self, symbol, interval="1h", limit=500):
        df = make_ohlcv(synthetic_prices(days=limit, seed=3), start="2026-09-01")
        df.index = df.index[0] + np.arange(limit) * np.timedelta64(1, "h")
        return df

    def depth(self, symbol, limit=500):
        return {"bids": [["99", "10"], ["98.5", "50"], ["90", "999"]], "asks": [["101", "5"], ["101.5", "3"]]}

    def ticker_24h(self, symbol):
        return {"lastPrice": "100", "priceChangePercent": "-2.5", "highPrice": "105", "lowPrice": "97",
                "quoteVolume": "1234567", "count": 4321, "weightedAvgPrice": "101"}


def test_orderbook_summary_ignores_far_orders_and_finds_walls():
    ob = orderbook_summary(FakeLiveClient().depth("X"))
    assert ob["best_bid"] == 99 and ob["best_ask"] == 101
    assert np.isclose(ob["bid_usd"], 99 * 10 + 98.5 * 50)  # 90'daki emir ±%2 dışında
    assert ob["bid_wall"]["price"] == 98.5
    assert ob["imbalance"] > 0.6 and ob["verdict"] == "Alıcılar baskın"
    assert orderbook_summary({"bids": [], "asks": []}) == {}


def test_ticker_summary():
    t = ticker_summary(FakeLiveClient().ticker_24h("X"))
    assert t["change_pct"] == -0.025 and t["trades"] == 4321


def test_timeframe_signal_has_no_value_component_intraday(cfg):
    sig = timeframe_signal(FakeLiveClient().recent_klines("X"), cfg, "1h", "1 saat")
    assert sig["interval"] == "1h" and sig["level"] in {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"}
    assert -7 <= sig["score"] <= 7
    assert sig["trend"] in {"up", "down"}


def test_clean_handles_nan_and_numpy():
    assert clean({"a": float("nan"), "b": np.float64(1.5), "c": [np.int64(2)]}) == {"a": None, "b": 1.5, "c": [2]}


@pytest.fixture
def app(cfg, monkeypatch):
    engine = Engine(cfg, client=FakeLiveClient())
    engine.store.save("AAAUSDT", make_ohlcv(synthetic_prices(seed=1)))
    engine.store.save("BBBUSDT", make_ohlcv(synthetic_prices(seed=2)))
    monkeypatch.setattr(dashboard, "get_quotes", lambda coin, exchanges=None: [])
    a = DashboardApp(cfg, engine=engine, symbols=["AAA", "BBB"])
    a.refresh(update=False)
    return a


def test_signals_payload(app):
    payload = app.signals_payload()
    assert payload["status"]["phase"] == "ready"
    assert [i["base"] for i in payload["items"]] and {i["symbol"] for i in payload["items"]} == {"AAAUSDT", "BBBUSDT"}
    json.dumps(payload, allow_nan=False)


def test_coin_payload(app):
    d = app.coin_payload("AAAUSDT")
    assert [t["interval"] for t in d["timeframes"]] == ["15m", "1h", "4h", "1d"]
    assert d["orderbook"]["verdict"] and d["ticker"]["price"] == 100
    assert d["signal"]["label"] and d["backtest"]["trades"] >= 0
    assert d["errors"] == []
    json.dumps(d, allow_nan=False)


def test_klines_payload_daily_and_intraday(app):
    daily = app.klines_payload("AAAUSDT", "1d")
    assert len(daily["candles"]) == 1900 and daily["sma200"]
    times = [c["time"] for c in daily["candles"]]
    assert times == sorted(times) and times[1] - times[0] == 86400
    hourly = app.klines_payload("AAAUSDT", "1h")
    assert hourly["candles"][1]["time"] - hourly["candles"][0]["time"] == 3600
    json.dumps(daily, allow_nan=False)


def test_http_server_routes(app):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path):
        with opener.open(base + path) as resp:
            return resp.read()

    def status_of(path):
        with pytest.raises(urllib.error.HTTPError) as err:
            get(path)
        err.value.close()
        return err.value.code

    try:
        assert b"Coin Canl" in get("/")
        assert get("/vendor/lightweight-charts.js").startswith(b"/*!")
        assert len(json.loads(get("/api/signals"))["items"]) == 2
        assert json.loads(get("/api/klines?symbol=aaa&interval=1d"))["candles"]
        assert status_of("/api/klines?symbol=aaa&interval=7x") == 400
        assert status_of("/../etc/passwd") == 404
    finally:
        server.shutdown()
        server.server_close()
