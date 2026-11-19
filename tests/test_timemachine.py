import numpy as np
import pytest

from cointracker.indicators import add_indicators
from cointracker.timemachine import build_pool, history, simulate, time_machine, verdict
from tests.conftest import make_ohlcv


def frame(seed=4, drift=0.0, n=480):
    rng = np.random.default_rng(seed)
    p = 100 * np.exp(np.cumsum(rng.normal(drift, 0.025, n)))
    p = np.append(p, p[-20:].max() * 1.03)  # kırılım günü
    return add_indicators(make_ohlcv(p))


def test_simulation_is_reproducible_and_complete():
    df = frame()
    e, a = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    mc = simulate(df.iloc[:-1], e, e - 2 * a, e + 4 * a, replays=3000)
    again = simulate(df.iloc[:-1], e, e - 2 * a, e + 4 * a, replays=3000)
    assert mc["p_stop"] == again["p_stop"] and mc["ret_p50"] == again["ret_p50"]  # aynı tohum, aynı sonuç
    assert mc["p_target"] + mc["p_stop"] + mc["p_ma_exit"] + mc["p_open"] == pytest.approx(1)
    assert mc["ret_p10"] <= mc["ret_p50"] <= mc["ret_p90"]
    # zarar-durdurda kayıp riskin kendisi + masraf kadar (boşlukla daha kötü olabilir), daha iyi olamaz
    assert mc["ret_p10"] >= -1 and len(mc["fan"]) == 12


def test_tighter_stop_is_hit_more_often():
    df = frame()
    e, a = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    wide = simulate(df.iloc[:-1], e, e - 3 * a, None, replays=2000)
    tight = simulate(df.iloc[:-1], e, e - 0.5 * a, None, replays=2000)
    assert tight["p_stop"] > wide["p_stop"]


def test_neutral_pool_removes_past_trend():
    df = frame(drift=0.01)  # her gün ortalama %1 yükselmiş coin
    raw = build_pool(df, neutral=False)
    neutral = build_pool(df, neutral=True)
    assert np.exp(np.log(raw[:, 3]).mean()) > 1.005
    assert np.exp(np.log(neutral[:, 3]).mean()) == pytest.approx(1, abs=1e-9)


def test_invalid_plans_are_refused():
    df = frame()
    e = float(df["close"].iloc[-1])
    assert simulate(df, e, e * 1.01) is None  # stop girişin üstünde
    assert simulate(df.iloc[:40], e, e * 0.9) is None  # veri az


def test_verdict_uses_coin_history_only_when_enough():
    mc = simulate(*(lambda d: (d.iloc[:-1], float(d["close"].iloc[-1]), float(d["close"].iloc[-1]) * 0.9))(frame()),
                  replays=1000)
    general = {"train_avg": 0.115, "test_avg": 0.222, "train_win": 0.35, "test_win": 0.27}
    few = verdict(mc, history([{"ret": 0.3, "open": False}] * 3), general)
    assert few["tone"] == "flat" and "yeterli geçmişi yok" in few["text"] and "+11,5%" in few["text"]
    good = verdict(mc, history([{"ret": 0.2, "open": False}, {"ret": -0.05, "open": False}] * 4), general)
    assert good["tone"] == "up" and "8 işlem" in good["text"] and "şansa" in good["text"]
    bad = verdict(mc, history([{"ret": -0.04, "open": False, "reason": "zarar-durdur"}] * 6), general)
    assert bad["tone"] == "down"
    assert "zarar-durdur %" in good["risk"] and "senaryo" in good["risk"]


def test_time_machine_uses_first_resistance_as_target():
    df = frame()
    e, a = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    plan = {"price": e, "stop": e - 2 * a, "atr": a, "resistances": [{"price": e + 0.5 * a}, {"price": e + 3 * a}]}
    tm = time_machine(df.iloc[:-1], plan, [], replays=1000)
    assert tm["mc"]["target"] == pytest.approx(e + 3 * a)  # 1 ATR'den yakın direnç atlanır
    assert tm["mc"]["target_source"].startswith("ilk direnç")
    no_res = time_machine(df.iloc[:-1], {**plan, "resistances": []}, [], replays=1000)
    assert no_res["mc"]["target"] == pytest.approx(e + 4 * a) and no_res["mc"]["target_source"].startswith("2R")
    assert time_machine(df, {"price": e, "stop": None}, []) is None
