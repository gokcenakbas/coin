from datetime import datetime, timezone

import pandas as pd

from cointracker.alerts import AlertState, format_signal, price_alerts, signal_changes
from cointracker.data import DataStore, base_asset, is_tradeable_base, klines_to_frame, list_universe, normalize_symbol
from cointracker.exchanges import get_quotes
from cointracker.signals import Signal

DAY_MS = 86_400_000
T0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def kline(i, close):
    return [T0 + i * DAY_MS, close, close, close, close, 1, T0 + (i + 1) * DAY_MS - 1, close, 1, 0, 0, 0]


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.session = None

    def klines(self, symbol, start, end=None):
        self.calls.append(start)
        ms = int(start.timestamp() * 1000)
        return klines_to_frame([r for r in self.rows if r[0] >= ms])

    def trading_symbols(self, quote="USDT"):
        return {"BTCUSDT", "ETHUSDT", "USDCUSDT", "JUPUSDT", "BTCUPUSDT", "DOGEUSDT"}

    def tickers_24h(self):
        vols = {"BTCUSDT": 900, "ETHUSDT": 800, "USDCUSDT": 990, "JUPUSDT": 10, "BTCUPUSDT": 50,
                "DOGEUSDT": 100, "OLDUSDT": 5000}
        return [{"symbol": s, "quoteVolume": str(v)} for s, v in vols.items()]


def test_symbol_helpers():
    assert normalize_symbol("btc") == "BTCUSDT"
    assert normalize_symbol("ETH/USDT") == "ETHUSDT"
    assert normalize_symbol("SOLUSDT") == "SOLUSDT"
    assert base_asset("SOLUSDT") == "SOL"
    assert not is_tradeable_base("USDC", set())
    assert not is_tradeable_base("BTCUP", set())
    assert is_tradeable_base("JUP", set())
    assert is_tradeable_base("SYRUP", set())


def test_list_universe_top_filters_stables_and_sorts(cfg):
    cfg["universe"]["top_n"] = 3
    assert list_universe(cfg, FakeClient([])) == ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
    cfg["universe"]["mode"] = "list"
    cfg["universe"]["symbols"] = ["sol", "AVAX"]
    assert list_universe(cfg, FakeClient([])) == ["SOLUSDT", "AVAXUSDT"]


def test_datastore_incremental_update(tmp_path):
    store = DataStore(tmp_path)
    client = FakeClient([kline(i, 100 + i) for i in range(5)])
    df = store.update("BTCUSDT", client, years=5)
    assert len(df) == 5

    # Yeni gün eklendi ve son (yarım) gün güncellendi
    client.rows = [kline(i, 100 + i) for i in range(4)] + [kline(4, 999), kline(5, 105)]
    df = store.update("BTCUSDT", client, years=5)
    assert len(df) == 6
    assert df["close"].iloc[4] == 999
    assert client.calls[-1] == datetime(2024, 1, 5, tzinfo=timezone.utc)
    assert len(store.load("BTCUSDT")) == 6


def _sig(symbol, level, score=3, price=100.0):
    plan = {"action": "AL" if level == "STRONG_BUY" else "SAT", "summary": "özet", "buy_low": 95.0, "buy_high": 101.0,
            "stop": 90.0, "exit_level": 97.0, "conditions": [{"text": "koşul", "ok": True}]}
    return Signal(symbol=symbol, date=pd.Timestamp("2026-01-01"), price=price, score=score, level=level,
                  reasons=["neden"], stop_loss=90.0, plan=plan,
                  rule={"stats": {"trades": 4, "win_rate": 0.5, "avg": 0.08}})


def test_signal_changes_only_alert_on_transition(cfg, tmp_path):
    state = AlertState(tmp_path / "s.json")
    first = signal_changes([_sig("BTCUSDT", "STRONG_BUY"), _sig("ETHUSDT", "HOLD", 0)], state, cfg)
    assert [s.symbol for s in first] == ["BTCUSDT"]
    assert signal_changes([_sig("BTCUSDT", "STRONG_BUY")], state, cfg) == []
    assert signal_changes([_sig("BTCUSDT", "TREND")], state, cfg) == []  # trendde: bildirim gerekmez
    assert [s.level for s in signal_changes([_sig("BTCUSDT", "SELL", 5)], state, cfg)] == ["SELL"]
    state.save()
    assert AlertState(tmp_path / "s.json").data["levels"]["BTCUSDT"] == "SELL"


def test_price_alerts_fire_once_and_rearm(cfg, tmp_path):
    cfg["alerts"]["price_alerts"] = [{"coin": "BTC", "above": 100000, "below": 50000}]
    state = AlertState(tmp_path / "s.json")
    assert len(price_alerts({"BTCUSDT": 101000}, state, cfg)) == 1
    assert price_alerts({"BTCUSDT": 102000}, state, cfg) == []
    assert price_alerts({"BTCUSDT": 90000}, state, cfg) == []
    assert len(price_alerts({"BTCUSDT": 100500}, state, cfg)) == 1
    assert len(price_alerts({"BTCUSDT": 40000}, state, cfg)) == 1


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        if isinstance(self.payload, Exception):
            raise self.payload

    def json(self):
        return self.payload


class FakeSession:
    def get(self, url, params=None, timeout=None):
        if "binance" in url:
            return FakeResp({"price": "101"})
        if "okx" in url:
            return FakeResp({"data": [{"last": "100"}]})
        if "kraken" in url:
            assert params["pair"] == "XBTUSD"
            return FakeResp({"error": [], "result": {"XXBTZUSD": {"c": ["99.8", "1"]}}})
        return FakeResp(RuntimeError("listelenmemiş"))


def test_get_quotes_sorted_by_effective_cost():
    quotes = get_quotes("btc", ["binance", "okx", "kraken", "bybit"], session=FakeSession())
    assert [q.exchange for q in quotes] == ["okx", "kraken", "binance"]  # kraken daha ucuz ama komisyonla: 99.8*1.004 > 100*1.001
    assert quotes[0].url.endswith("btc-usdt")


def test_format_signal_mentions_exchange():
    quotes = get_quotes("BTC", ["binance", "okx"], session=FakeSession())
    text = format_signal(_sig("BTCUSDT", "STRONG_BUY"), quotes)
    assert "GÜÇLÜ AL" in text and "okx" in text and "zarar-durdur" in text and "20G ort." in text
    assert "4 işlem" in text
    sell_text = format_signal(_sig("BTCUSDT", "SELL", -3), quotes)
    assert "En iyi satış: binance" in sell_text
