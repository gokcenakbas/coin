import numpy as np
import pandas as pd

from cointracker.signals import analyze
from cointracker.trend import rule_stats, rule_trades, setup, trend_frame
from tests.conftest import make_ohlcv

UP_BTC = make_ohlcv(np.linspace(100, 300, 700))
DOWN_BTC = make_ohlcv(np.linspace(300, 100, 700))


def base_series(n_trend=640, flat=40):
    """Uzun yükseliş, ardından dar yatay bant (100 civarı)."""
    rise = np.linspace(50, 100, n_trend)
    side = 100 + np.sin(np.arange(flat)) * 0.8
    return np.concatenate([rise, side])


def frame(prices, btc=UP_BTC):
    return trend_frame(make_ohlcv(prices), btc)


def test_breakout_in_uptrend_is_strong_buy_with_levels():
    prices = np.append(base_series(), 104.0)  # son gün 20 günün zirvesi
    tf = frame(prices)
    st = setup(tf)
    assert tf["entry_sig"].iloc[-1]
    assert st["state"] == "BUY" and st["level"] == "STRONG_BUY" and st["label"] == "GÜÇLÜ AL"
    assert all(c["ok"] for c in st["conditions"])
    assert st["buy_low"] < st["price"] <= st["buy_high"]
    assert st["buy_low"] == tf["close"].iloc[-20:-1].max()  # kırılım seviyesi = önceki 19 günün zirvesi
    assert np.isclose(st["stop"], st["price"] - 2 * tf["atr"].iloc[-1])
    assert np.isclose(st["exit_level"], tf["close"].iloc[-20:].mean())


def test_no_buy_when_bitcoin_is_below_its_200_day_average():
    prices = np.append(base_series(), 104.0)
    st = setup(frame(prices, DOWN_BTC))
    assert st["level"] != "STRONG_BUY" and not st["market_open"]
    assert not st["conditions"][2]["ok"]


def test_signal_stays_valid_for_three_days_inside_the_zone_only():
    prices = np.append(base_series(), [104.0, 104.3, 104.1])
    tf = frame(prices)
    st = setup(tf)
    assert st["state"] == "BUY" and st["signal"]["days_ago"] == 2
    # aynı sinyal, fiyat bölgenin çok üstündeyken: kovalanmaz
    high = setup(tf, price=st["buy_high"] * 1.05)
    assert high["state"] == "ABOVE_ZONE" and high["level"] == "TREND"
    # 4 gün sonra sinyal geçerliliğini yitirir
    later = setup(frame(np.append(prices, 104.2)))
    assert later["state"] in ("TREND", "BUY") and (later["signal"] is None or later["signal"]["days_ago"] < 3)


def test_close_below_20_day_average_is_sell():
    prices = np.append(base_series(), [104, 106, 108, 96, 93])
    st = setup(frame(prices))
    assert st["state"] == "EXIT" and st["level"] == "SELL" and st["label"] == "SAT"


def test_trend_frame_uses_only_past_data():
    prices = np.append(base_series(), [104, 106, 108, 96, 93, 99, 101])
    full = frame(prices)
    part = frame(prices[:-4])
    cols = ["sma20", "in_trend", "brk_level", "entry_sig"]
    pd.testing.assert_frame_equal(full[cols].iloc[: len(part)], part[cols])


def test_rule_trades_enter_next_open_and_exit_below_average():
    prices = np.append(base_series(), [104, 107, 110, 113, 116, 118, 119, 120, 121, 122, 123, 124, 110, 104, 103])
    tf = frame(prices)
    start = tf.index[len(base_series())]
    trades = rule_trades(tf, since=start)
    assert len(trades) == 1
    t = trades[0]
    i = int(np.flatnonzero(tf["entry_sig"].to_numpy() & (tf.index >= start))[0])
    assert t["entry_date"] == tf.index[i + 1] and t["entry"] == tf["open"].iloc[i + 1]
    assert not t["open"] and t["reason"] == "20G ort. altı" and t["ret"] > 0
    # 20G ortalamanın altındaki ilk kapanıştan sonraki günün açılışında satılır
    j = int(np.flatnonzero((tf["close"] < tf["sma20"]).to_numpy() & (tf.index > t["entry_date"]))[0])
    assert t["exit_date"] == tf.index[j + 1] and t["exit"] == tf["open"].iloc[j + 1]
    st = rule_stats(trades)
    assert st["trades"] == 1 and st["win_rate"] == 1.0


def test_analyze_uses_the_trend_rule(cfg):
    prices = np.append(base_series(), 104.0)
    sig = analyze("X", make_ohlcv(prices), cfg, market=UP_BTC)
    assert sig.level == "STRONG_BUY" and sig.label == "GÜÇLÜ AL" and sig.is_buy
    p = sig.plan
    assert p["action"] == "AL" and p["in_buy_zone"] and p["summary"].startswith("GÜÇLÜ AL")
    assert p["stop"] < p["buy_low"] < p["buy_high"] and sig.stop_loss == p["stop"]
    no_market = analyze("X", make_ohlcv(prices), cfg)
    assert no_market.level != "STRONG_BUY"  # BTC verisi yoksa yeni alım önerilmez


def test_fixed_stop_two_atr_below_entry():
    prices = np.append(base_series(), [104, 107, 110, 113, 116, 118, 104, 99, 98, 97])
    tf = frame(prices)
    t = rule_trades(tf, since=tf.index[len(base_series())])[0]
    i = tf.index.get_loc(t["entry_date"]) - 1
    assert t["reason"] == "zarar-durdur" and np.isclose(t["exit"], t["entry"] - 2 * tf["atr"].iloc[i])


def test_todays_unfinished_candle_does_not_create_a_signal(cfg):
    prices = np.append(base_series(), 104.0)
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    start = today - pd.Timedelta(days=len(prices) - 1)
    btc = make_ohlcv(np.linspace(100, 300, len(prices)), start=start)
    sig = analyze("X", make_ohlcv(prices, start=start), cfg, market=btc)
    assert sig.level != "STRONG_BUY"  # kırılım yalnızca bugünün (kapanmamış) mumunda
    assert sig.plan["as_of"] == today - pd.Timedelta(days=1) and sig.plan["price"] == 104.0
    # aynı veri bir gün sonra (mum kapanmış) GÜÇLÜ AL verir
    shifted = analyze("X", make_ohlcv(prices, start=start - pd.Timedelta(days=1)), cfg,
                      market=make_ohlcv(np.linspace(100, 300, len(prices)), start=start - pd.Timedelta(days=1)))
    assert shifted.level == "STRONG_BUY"
