import numpy as np
import pandas as pd

from cointracker.indicators import add_indicators, atr, percentile_rank, rsi, sma
from tests.conftest import make_ohlcv


def test_sma_matches_manual_mean():
    s = pd.Series(np.arange(1, 11, dtype=float))
    out = sma(s, 3)
    assert out.iloc[:2].isna().all()
    assert out.iloc[-1] == (8 + 9 + 10) / 3


def test_rsi_extremes_and_flat():
    up = pd.Series(np.arange(1, 60, dtype=float))
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    flat = pd.Series(np.full(40, 5.0))
    assert rsi(up).iloc[-1] == 100
    assert rsi(down).iloc[-1] < 1
    assert rsi(flat).iloc[-1] == 50
    assert rsi(up).iloc[:13].isna().all()


def test_rsi_stays_in_range(ohlcv):
    r = rsi(ohlcv["close"]).dropna()
    assert r.between(0, 100).all()


def test_atr_positive(ohlcv):
    assert (atr(ohlcv).dropna() > 0).all()


def test_percentile_rank_bounds():
    s = pd.Series(np.arange(400, dtype=float))
    rank = percentile_rank(s, window=400, min_periods=365)
    assert rank.iloc[-1] == 1.0
    assert rank.iloc[:364].isna().all()
    falling = pd.Series(np.arange(400, 0, -1, dtype=float))
    assert percentile_rank(falling, window=400, min_periods=365).iloc[-1] == 1 / 400


def test_add_indicators_columns(ohlcv):
    out = add_indicators(ohlcv)
    for col in ["sma50", "sma200", "rsi", "macd", "macd_signal", "bb_upper", "bb_lower", "atr",
                "pct_rank_5y", "drawdown_5y"]:
        assert col in out.columns
    assert (out["drawdown_5y"] <= 0).all()


def test_drawdown_from_peak():
    df = make_ohlcv([100, 200, 100])
    assert add_indicators(df)["drawdown_5y"].iloc[-1] == -0.5
