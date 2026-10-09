import numpy as np
import pytest

from cointracker.levels import cluster_levels, support_resistance, support_state, swing_points, trade_plan
from cointracker.signals import analyze, score_frame
from tests.conftest import make_ohlcv, synthetic_prices


def test_cluster_levels_groups_nearby_prices_without_chaining():
    levels = cluster_levels([100, 100.5, 101, 110, 110.2], tolerance=0.01)
    assert [lv["touches"] for lv in levels] == [3, 2]
    assert np.isclose(levels[0]["price"], 100.5)
    # 100, 100.9, 101.8, ... her adım %1'den az ama toplam %1'i aşıyor: tek seviyeye zincirlenmemeli
    chain = cluster_levels([100 * 1.009 ** i for i in range(10)], tolerance=0.01)
    assert len(chain) >= 5


def test_swing_points_find_local_extremes():
    prices = [10, 11, 12, 11, 10, 9, 10, 11, 12, 13, 12, 11, 10]
    highs, lows = swing_points(make_ohlcv(prices), window=2)
    assert len(highs) >= 2 and len(lows) >= 1
    assert lows.idxmin() == make_ohlcv(prices).index[5]


def test_support_resistance_sides_and_order(cfg):
    df = make_ohlcv(synthetic_prices(seed=1))
    atr = float(score_frame(df, cfg)["atr"].iloc[-1])
    price = df["close"].iloc[-1]
    supports, resistances = support_resistance(df, atr)
    assert all(s["price"] < price for s in supports)
    assert all(r["price"] > price for r in resistances)
    assert [s["price"] for s in supports] == sorted((s["price"] for s in supports), reverse=True)
    assert [r["price"] for r in resistances] == sorted(r["price"] for r in resistances)


def test_falls_back_to_five_years_when_price_is_at_yearly_low(cfg):
    # 4 yıl yatay (100 civarı dalgalı), son yıl 100'ün altına sert düşüş: 1 yılda destek yok
    t = np.arange(1500)
    prices = np.concatenate([100 + 10 * np.sin(t / 15), np.linspace(100, 60, 400)])
    scored = score_frame(make_ohlcv(prices), cfg)
    plan = trade_plan(scored, cfg)
    assert plan["resistances"]  # yukarıda eski seviyeler var
    assert plan["stop"] < plan["buy_low"] <= plan["buy_high"] <= plan["price"]


@pytest.mark.parametrize("seed", range(12))
def test_plan_levels_are_consistent(cfg, seed):
    market = make_ohlcv(np.linspace(100, 300, 1900))
    sig = analyze("X", make_ohlcv(synthetic_prices(seed=seed)), cfg, market=market)
    p = sig.plan
    assert p["action"] == {"STRONG_BUY": "AL", "TREND": "TUT", "SELL": "SAT"}.get(sig.level, "BEKLE")
    assert p["summary"] and len(p["conditions"]) == 4
    assert sig.stop_loss == p["stop"] and p["stop"] < p["price"]
    assert p["exit_level"] > 0
    if p["action"] == "AL":
        assert p["buy_low"] <= p["price"] <= p["buy_high"] and p["in_buy_zone"]
    if p["action"] == "SAT":
        assert p["price"] < p["exit_level"]


def _bounce_then(tail):
    """100'de iki kez dönen (destek), sonra yükselen fiyat; ardından `tail` ile biten seri."""
    legs = [np.linspace(130, 100, 40), np.linspace(100, 125, 40), np.linspace(125, 100, 40),
            np.linspace(100, 130, 60), np.asarray(tail, dtype=float)]
    return make_ohlcv(np.concatenate(legs))


def test_support_state_near_at_and_far(cfg):
    far = score_frame(_bounce_then(np.linspace(131, 150, 20)), cfg)  # yükseliyor: yaklaşmıyor
    st = support_state(far, float(far["atr"].iloc[-1]))
    assert st["state"] == "FAR" and not st["falling"] and st["price"] < 150

    df = score_frame(_bounce_then(np.linspace(130, 100, 25)[:-5]), cfg)  # düşüyor, desteğin biraz üstünde
    atr = float(df["atr"].iloc[-1])
    st = support_state(df, atr)
    dist = (df["close"].iloc[-1] - st["price"]) / atr
    assert np.isclose(st["dist_atr"], dist) and st["falling"]
    expected = "AT" if dist <= 0.5 else "NEAR" if dist <= 2.5 else "FAR"
    assert st["state"] == expected
    if expected == "NEAR":
        assert 0 < st["hit_prob"] < 1

    at = score_frame(_bounce_then(np.linspace(130, 100.3, 25)), cfg)
    st = support_state(at, float(at["atr"].iloc[-1]))
    assert st["state"] == "AT"  # desteğin hemen üstündeki seviye gizlenmez


def test_support_listed_when_price_sits_right_on_it(cfg):
    df = score_frame(_bounce_then(np.linspace(130, 100.3, 25)), cfg)
    atr = float(df["atr"].iloc[-1])
    hidden, _ = support_resistance(df, atr)
    shown, _ = support_resistance(df, atr, gap_atr=0)
    assert shown and 98 < shown[0]["price"] < 102
    assert not hidden or hidden[0]["price"] < 98
