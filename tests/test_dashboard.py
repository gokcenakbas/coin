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
    engine.store.save("NEWUSDT", make_ohlcv(synthetic_prices(days=60, seed=4)))  # 220 günden kısa
    engine.store.save("BTCUSDT", make_ohlcv(synthetic_prices(seed=9)))  # piyasa rejimi
    monkeypatch.setattr(dashboard, "get_quotes", lambda coin, exchanges=None: [])
    sent = []
    a = DashboardApp(cfg, engine=engine, symbols=["AAA", "BBB", "NEW"], notifier=lambda t, b: sent.append((t, b)))
    a.sent = sent
    a.refresh(update=False)
    return a


def test_signals_payload(app):
    payload = app.signals_payload()
    assert payload["status"]["phase"] == "ready" and payload["status"]["app"] is True
    levels = {i["symbol"]: i["level"] for i in payload["items"]}
    assert set(levels) == {"AAAUSDT", "BBBUSDT", "NEWUSDT"}
    assert levels["NEWUSDT"] == "NODATA" and payload["items"][-1]["symbol"] == "NEWUSDT"
    market = payload["status"]["market"]
    assert market["regime"] in ("up", "down", "mixed") and market["label"]
    rel = payload["status"]["reliability"]
    closed = sum(1 for r in app.results.values() for t in r.signal.rule["trades_all"] if not t["open"])
    assert (rel is None and closed == 0) or (rel["coins"] == 2 and rel["trades"] == closed and 0 <= rel["win_rate"] <= 1)
    assert all(i["level"] in ("STRONG_BUY", "TREND", "SELL", "HOLD", "NODATA") for i in payload["items"])
    assert payload["watch"] == {"favorites": [], "alarms": [], "positions": [], "settings": {"capital": None, "risk_pct": 1.0}}
    json.dumps(payload, allow_nan=False)


def test_all_coins_listed_as_pending_before_analysis(cfg):
    a = DashboardApp(cfg, engine=Engine(cfg, client=FakeLiveClient()), symbols=["AAA"])
    a.universe = ["AAAUSDT", "BBBUSDT"]
    items = a.signals_payload()["items"]
    assert [(i["base"], i["level"]) for i in items] == [("AAA", "PENDING"), ("BBB", "PENDING")]


def test_watchlist_and_alarms_persist(app, cfg):
    app.watch.apply({"action": "toggle", "symbol": "aaa"}, "USDT")
    data = app.watch.apply({"action": "add_alarm", "symbol": "AAA", "kind": "above", "price": "123.5"}, "USDT")
    assert data["favorites"] == ["AAAUSDT"]
    alarm = data["alarms"][0]
    assert alarm["symbol"] == "AAAUSDT" and alarm["price"] == 123.5
    again = app.watch.apply({"action": "add_alarm", "symbol": "AAA", "kind": "above", "price": 123.5}, "USDT")
    assert len(again["alarms"]) == 1  # aynı alarm iki kez eklenmez
    reloaded = DashboardApp(cfg, engine=app.engine, symbols=["AAA"]).watch.snapshot()
    assert reloaded == data
    assert app.watch.apply({"action": "remove_alarm", "id": alarm["id"]}, "USDT")["alarms"] == []
    assert app.watch.apply({"action": "toggle", "symbol": "AAAUSDT"}, "USDT")["favorites"] == []
    st = app.watch.apply({"action": "settings", "capital": "2500", "risk_pct": 1.5}, "USDT")["settings"]
    assert st == {"capital": 2500.0, "risk_pct": 1.5}
    for bad in ({"action": "settings", "capital": -1, "risk_pct": 1},
                {"action": "settings", "capital": 100, "risk_pct": 50},
                {"action": "add_alarm", "symbol": "AAA", "kind": "sideways", "price": 1},
                {"action": "add_alarm", "symbol": "AAA", "kind": "above", "price": -5},
                {"action": "nuke"}):
        with pytest.raises(ValueError):
            app.watch.apply(bad, "USDT")


def test_signal_change_notifies_only_for_favorites(app):
    app.watch.apply({"action": "toggle", "symbol": "AAA"}, "USDT")
    real = app.results["AAAUSDT"].signal.level
    fake = "STRONG_SELL" if real != "STRONG_SELL" else "STRONG_BUY"
    app.results["AAAUSDT"].signal.level = fake
    app.results["BBBUSDT"].signal.level = fake
    app.refresh(update=False)
    assert len(app.sent) == 1 and app.sent[0][0].startswith("AAA:")


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
        assert b"Coin Takip" in get("/")
        assert get("/vendor/lightweight-charts.js").startswith(b"/*!")
        assert len(json.loads(get("/api/signals"))["items"]) == 3
        assert json.loads(get("/api/klines?symbol=aaa&interval=1d"))["candles"]
        assert status_of("/api/klines?symbol=aaa&interval=7x") == 400
        assert status_of("/../etc/passwd") == 404

        def post(path, body, ctype="application/json", host=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": ctype, **({"Host": host} if host else {})})
            try:
                with opener.open(req) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as err:
                with err:
                    return err.code, None

        code, data = post("/api/watchlist", {"action": "toggle", "symbol": "BBB"})
        assert code == 200 and data["favorites"] == ["BBBUSDT"]
        assert post("/api/watchlist", {"action": "toggle", "symbol": "AAA"}, ctype="text/plain")[0] == 403
        assert post("/api/watchlist", {"action": "nuke"})[0] == 400
        assert post("/api/notify", {"title": "t", "body": "b"}) == (200, {"ok": True})
        code, data = post("/api/refresh", {})
        assert code == 200 and isinstance(data["started"], bool)
        assert app.sent[-1] == ("t", "b")
        assert post("/api/notify", {"title": "x"}, host="evil.example.com")[0] == 403
        req = urllib.request.Request(base + "/api/signals", headers={"Host": "evil.example.com:80"})
        with pytest.raises(urllib.error.HTTPError) as err:
            opener.open(req)
        err.value.close()
        assert err.value.code == 403
    finally:
        server.shutdown()
        server.server_close()


def test_refresh_does_not_run_twice_at_once(app):
    import time
    started = []

    def slow(update):
        started.append(1)
        time.sleep(0.3)

    app._refresh = slow
    assert app.refresh_now() is True
    time.sleep(0.05)
    assert app.refresh_now() is False  # ilki sürüyor
    assert app.refresh(update=False) is False
    time.sleep(0.4)
    assert app.refresh_now() is True
    time.sleep(0.4)
    assert len(started) == 2


def test_movers_scan_and_endpoint(app):
    app.scan_movers(["AAAUSDT", "BBBUSDT"])
    payload = app.movers_payload()
    assert payload["updated"] and set(payload["baseline"]) == {"AAAUSDT", "BBBUSDT"}
    assert all(v > 0 for v in payload["baseline"].values())
    for item in payload["items"]:
        assert item["tags"] and item["base"] in ("AAA", "BBB")
    json.dumps(payload, allow_nan=False)


def test_positions_and_sell_notification(app):
    sig = app.results["AAAUSDT"].signal
    price, exit_level = sig.setup["price"], sig.setup["exit_level"]
    with pytest.raises(ValueError):
        app.watch.apply({"action": "add_position", "symbol": "AAA", "entry": 100, "stop": 120}, "USDT")
    # Alış fiyatının hemen altında stop: fiyat stop'un altındaysa SAT
    app.watch.apply({"action": "add_position", "symbol": "AAA", "entry": price * 2, "stop": price * 1.5}, "USDT")
    pos = app.watch.positions()[0]
    assert app.position_status(pos)["action"] == "SAT" and "zarar-durdur" in app.position_status(pos)["reason"]
    app._check_positions()
    app._check_positions()  # aynı durum için ikinci kez bildirim yok
    assert sum("SAT zamanı" in t for t, _ in app.sent) == 1
    # Stop çok aşağıda: karar 20G ortalamaya göre
    app.watch.apply({"action": "add_position", "symbol": "AAA", "entry": price, "stop": price * 0.5}, "USDT")
    assert len(app.watch.positions()) == 1  # aynı coin için tek pozisyon
    st = app.position_status(app.watch.positions()[0])
    assert st["action"] == ("SAT" if price < exit_level else "TUT")
    payload = app.signals_payload()
    assert payload["watch"]["positions"][0]["status"]["action"] == st["action"]
    app.watch.apply({"action": "remove_position", "id": app.watch.positions()[0]["id"]}, "USDT")
    assert app.watch.positions() == []


def test_take_profit_notification_once(app, monkeypatch):
    price = app.results["AAAUSDT"].signal.setup["price"]
    monkeypatch.setattr(app, "position_status", lambda pos: {"action": "TUT", "price": price, "exit_level": None})
    # Kâr alma yeri fiyatın altında kalmış bir pozisyon (alış 0,8×, hedef 0,9×)
    app.watch.apply({"action": "add_position", "symbol": "AAA", "entry": price * 0.8, "stop": price * 0.5,
                     "target": price * 0.9}, "USDT")
    pos = app.watch.positions()[0]
    assert app.take_profit_target(pos) == pytest.approx(price * 0.9)
    app._check_positions()
    app._check_positions()
    assert sum("kâr alma yerine geldi" in t for t, _ in app.sent) == 1
    assert app.watch.positions()[0]["tp_notified"] is True
    # hedef alıştan düşükse kaydedilmez
    app.watch.apply({"action": "add_position", "symbol": "AAA", "entry": price, "stop": price * 0.5,
                     "target": price * 0.9}, "USDT")
    assert app.watch.positions()[0]["target"] is None
