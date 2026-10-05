import numpy as np
import pandas as pd

from cointracker.signals import analyze, classify, forward_return_stats, score_frame
from tests.conftest import make_ohlcv


def test_classify_thresholds(cfg):
    assert classify(5, cfg) == "STRONG_BUY"
    assert classify(2, cfg) == "BUY"
    assert classify(0, cfg) == "HOLD"
    assert classify(-2, cfg) == "SELL"
    assert classify(-6, cfg) == "STRONG_SELL"


def test_score_is_sum_of_components(cfg, ohlcv):
    scored = score_frame(ohlcv, cfg)
    comps = scored[["s_trend", "s_cross", "s_rsi", "s_macd", "s_bb", "s_value"]].sum(axis=1)
    assert (scored["score"] == comps).all()
    assert set(scored["level"]) <= {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"}


def test_crash_after_long_rally_gives_buy_side_value_signals(cfg):
    # 4 yıl yükseliş, ardından sert çöküş -> aşırı satım + tarihsel ucuz bölge
    prices = np.concatenate([np.linspace(10, 100, 1500), np.linspace(100, 8, 120)])
    scored = score_frame(make_ohlcv(prices), cfg)
    last = scored.iloc[-1]
    assert last["s_rsi"] == 2
    assert last["s_value"] == 1
    assert last["s_trend"] == -1


def test_parabolic_rally_gives_overbought(cfg):
    prices = np.concatenate([np.full(600, 50.0), np.linspace(50, 200, 60)])
    last = score_frame(make_ohlcv(prices), cfg).iloc[-1]
    assert last["s_rsi"] == -2
    assert last["s_value"] == -1
    assert last["s_trend"] == 1


def test_forward_return_stats():
    close = pd.Series([100.0, 110, 121, 100, 90])
    mask = pd.Series([True, True, False, True, True])
    stats = forward_return_stats(close, mask, horizon=1)
    # günler 0,1,3 kullanılabilir (4'ün geleceği yok)
    assert stats["count"] == 3
    assert np.isclose(stats["avg_return"], (0.10 + 0.10 - 0.10) / 3)
    assert np.isclose(stats["win_rate"], 2 / 3)


def test_analyze_produces_signal_with_reasons(cfg, ohlcv):
    sig = analyze("TESTUSDT", ohlcv, cfg)
    assert sig.symbol == "TESTUSDT"
    assert sig.price == ohlcv["close"].iloc[-1]
    assert sig.reasons
    assert sig.history["horizon"] == cfg["signals"]["horizon_days"]
    assert sig.history["baseline"]["count"] > 1000
    assert sig.stop_loss < sig.price < sig.take_profit
