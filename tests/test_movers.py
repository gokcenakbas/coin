import numpy as np
import pandas as pd

from cointracker.movers import candidate, features


def hourly(closes, volumes=None, wick=0.002):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="h")
    opens = np.concatenate([[closes[0]], closes[:-1]])
    vol = np.full(len(closes), 1e6) if volumes is None else np.asarray(volumes, dtype=float)
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * (1 + wick),
                         "low": np.minimum(opens, closes) * (1 - wick), "close": closes,
                         "volume": vol / closes, "quote_volume": vol}, index=idx)


def noisy(n=800, sigma=0.01, seed=1):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(0, sigma, n)))


def test_features_use_only_past_data():
    df = hourly(noisy(), np.exp(np.random.default_rng(2).normal(0, 0.3, 800)) * 1e6)
    future = df.copy()
    future.iloc[600:, :] *= 3  # gelecek değişse de geçmiş satırlar aynı kalmalı
    a, b = features(df).iloc[:600], features(future).iloc[:600]
    pd.testing.assert_frame_equal(a, b)


def test_normal_market_is_not_a_candidate():
    rng = np.random.default_rng(3)
    df = hourly(noisy(seed=3), np.exp(rng.normal(0, 0.2, 800)) * 1e6)
    assert candidate(df) is None


def test_volume_spike_detected_on_last_closed_hour():
    vol = np.full(800, 1e6)
    vol[-2] = 6e6  # kapanmış son saat
    c = candidate(hourly(noisy(seed=4), vol))
    assert c and "volume" in c["tags"] and c["rvol"] >= 5.9
    vol2 = np.full(800, 1e6)
    vol2[-1] = 6e6  # henüz kapanmamış saat sayılmaz
    assert candidate(hourly(noisy(seed=4), vol2)) is None or "volume" not in candidate(hourly(noisy(seed=4), vol2))["tags"]


def test_squeeze_alone_is_not_a_candidate():
    """Gerçek veride sıkışma büyük hareket olasılığını düşürdü; tek başına aday sayılmaz."""
    closes = np.concatenate([noisy(770, 0.02, seed=5), np.full(30, 1.0)])
    closes[-30:] = closes[-31] * (1 + 0.0002 * np.sin(np.arange(30)))
    df = hourly(closes)
    assert features(df).iloc[-2]["squeeze_pct"] <= 0.10
    assert candidate(df) is None


def test_downside_breakout_is_not_a_candidate():
    closes = np.concatenate([100 + np.sin(np.arange(798) / 5), [90, 90]])
    vol = np.full(800, 1e6)
    vol[-2] = 2.5e6
    assert candidate(hourly(closes, vol)) is None


def test_breakout_needs_volume():
    closes = np.concatenate([100 + np.sin(np.arange(798) / 5), [110, 110]])
    vol = np.full(800, 1e6)
    assert candidate(hourly(closes, vol)) is None or "breakout_up" not in candidate(hourly(closes, vol))["tags"]
    vol[-2] = 2.5e6
    c = candidate(hourly(closes, vol))
    assert c and c["breakout"] == "up"


def test_too_little_history():
    assert candidate(hourly(noisy(100))) is None
