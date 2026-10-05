import numpy as np
import pytest

from cointracker.backtest import backtest
from cointracker.signals import score_frame
from tests.conftest import make_ohlcv


def test_backtest_result_shape(cfg, ohlcv):
    res = backtest(score_frame(ohlcv, cfg), cfg, "TEST")
    assert res.trades > 0
    assert 0 <= res.exposure <= 1
    assert res.max_drawdown <= 0
    assert -1 < res.total_return
    assert (res.end - res.start).days <= 366 * 5


def test_buy_hold_return_matches_window(cfg, ohlcv):
    scored = score_frame(ohlcv, cfg)
    res = backtest(scored, cfg)
    window = scored.loc[res.start:res.end, "close"]
    assert np.isclose(res.buy_hold_return, window.iloc[-1] / window.iloc[0] - 1)


def test_no_trades_when_threshold_unreachable(cfg, ohlcv):
    cfg["signals"]["buy_threshold"] = 99
    res = backtest(score_frame(ohlcv, cfg), cfg)
    assert res.trades == 0
    assert res.total_return == 0
    assert res.win_rate is None


def test_steady_uptrend_is_profitable(cfg):
    prices = 100 * np.exp(np.arange(900) * 0.002 + 0.01 * np.sin(np.arange(900)))
    res = backtest(score_frame(make_ohlcv(prices), cfg), cfg)
    assert res.total_return > 0


def test_too_short_history_raises(cfg):
    with pytest.raises(ValueError):
        backtest(score_frame(make_ohlcv(np.linspace(1, 2, 150)), cfg), cfg)
