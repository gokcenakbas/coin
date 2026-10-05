import numpy as np
import pytest

from cointracker.levels import cluster_levels, support_resistance, swing_points, trade_plan
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
    sig = analyze("X", make_ohlcv(synthetic_prices(seed=seed)), cfg)
    p = sig.plan
    assert p["stop"] < p["buy_low"] <= p["buy_high"] <= p["price"]
    assert p["price"] < p["target1"] < p["target2"]
    assert p["sell_low"] <= p["sell_high"]
    assert p["risk_reward"] is None or p["risk_reward"] > 0
    assert p["action"] == {"BUY": "AL", "STRONG_BUY": "AL", "SELL": "SAT", "STRONG_SELL": "SAT"}.get(sig.level, "BEKLE")
    assert p["summary"]
    assert sig.stop_loss == p["stop"] and sig.take_profit == p["target1"]
    if p["action"] == "AL":
        assert p["buy_high"] == p["price"] and p["in_buy_zone"]
    if p["action"] == "SAT":
        assert p["sell_low"] == p["price"]
    assert p["cheap_5y"] < p["expensive_5y"]
