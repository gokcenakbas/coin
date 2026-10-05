import numpy as np
import pandas as pd

from cointracker.indicators import add_indicators
from cointracker.strategy import SetupParams, entry_signals, simulate, stats
from tests.conftest import make_ohlcv, synthetic_prices


def bars(rows, start="2024-01-01"):
    """rows: (open, high, low, close)"""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                      index=pd.date_range(start, periods=len(rows), freq="D"))
    df["atr"] = 1.0
    return df


P = SetupParams(tp_atr=2.0, sl_atr=1.0, max_hold=5)


def test_take_profit_hit():
    df = bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (101, 103, 100.5, 102.5)])
    t = simulate(df, pd.Series([True, False, False], index=df.index), P, fee=0, slippage=0)
    assert len(t) == 1 and t[0].reason == "tp" and t[0].exit == 102 and t[0].days == 2


def test_stop_wins_tie_and_gap_down_fills_at_open():
    # aynı gün hem hedef hem stop görülürse kötümser varsayım: stop
    df = bars([(100, 100, 100, 100), (100, 103, 98, 100)])
    t = simulate(df, pd.Series([True, False], index=df.index), P, fee=0, slippage=0)
    assert t[0].reason == "sl" and t[0].exit == 99
    # ertesi gün stop'un altında açılırsa açılış fiyatından çıkılır
    df = bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (97, 97.5, 96, 97)])
    t = simulate(df, pd.Series([True, False, False], index=df.index), P, fee=0, slippage=0)
    assert t[0].reason == "sl" and t[0].exit == 97


def test_time_exit_and_open_trade_ignored():
    rows = [(100, 100, 100, 100)] + [(100, 100.5, 99.5, 100.2)] * 5
    df = bars(rows)
    t = simulate(df, pd.Series([True] + [False] * 5, index=df.index), P, fee=0, slippage=0)
    assert t[0].reason == "time" and t[0].days == 5 and np.isclose(t[0].exit, 100.2)
    # pencere veri sonunda bitmiyorsa (işlem hâlâ açık) sayılmaz
    t = simulate(df.iloc[:4], pd.Series([True, False, False, False], index=df.index[:4]), P, fee=0, slippage=0)
    assert t == []


def test_fees_reduce_return_and_no_overlapping_trades():
    df = bars([(100, 100, 100, 100), (100, 103, 99.5, 102), (102, 102, 102, 102), (102, 105, 101.5, 104)])
    sig = pd.Series([True, True, True, False], index=df.index)
    t = simulate(df, sig, P, fee=0.001, slippage=0)
    assert t[0].ret < 0.02
    assert all(a.exit_date < b.entry_date for a, b in zip(t, t[1:]))


def test_entry_signals_respect_regime(cfg):
    ind = add_indicators(make_ohlcv(synthetic_prices(seed=3)))
    loose = entry_signals(ind, SetupParams(regime="none"))
    strict = entry_signals(ind, SetupParams(regime="coin"))
    assert strict.sum() <= loose.sum()
    up = (ind["close"] > ind["sma200"]) & (ind["sma50"] > ind["sma200"])
    assert not (strict & ~up).any()
    btc_down = ind.copy()
    btc_down["close"] = btc_down["sma200"] * 0.5  # Bitcoin düşüş trendinde
    assert not entry_signals(ind, SetupParams(regime="coin+btc"), btc_down).any()
    rs = entry_signals(ind, SetupParams(regime="coin+btc+rs"), ind)  # coin = BTC → göreceli güç yok
    assert not rs.any()
    weak_btc = ind.copy()
    weak_btc["close"] = ind["close"] * 0.999 ** np.arange(len(ind))  # BTC yükselişte ama coin'den zayıf
    weak_btc[["sma50", "sma200"]] = 0.0
    rs2 = entry_signals(ind, SetupParams(regime="coin+btc+rs"), weak_btc)
    assert not (rs2 & ~up).any()  # göreceli güç filtresi coin'in kendi trend şartını kaldırmaz


def test_breakeven_stop_moves_to_entry_next_day():
    p = SetupParams(tp_atr=2.0, sl_atr=1.0, max_hold=5, breakeven=0.5)
    # gün 1: +1 (hedef yolunun yarısı) → gün 2'den itibaren stop = giriş; gün 2 girişin altına iner
    df = bars([(100, 100, 100, 100), (100, 101.2, 99.8, 101), (100.5, 100.6, 99.7, 99.9)])
    t = simulate(df, pd.Series([True, False, False], index=df.index), p, fee=0, slippage=0)
    assert t[0].reason == "be" and t[0].exit == 100
    # başabaş tetiklenmeden önceki gün asıl stop geçerli
    df = bars([(100, 100, 100, 100), (100, 100.4, 98.9, 99)])
    t = simulate(df, pd.Series([True, False], index=df.index), p, fee=0, slippage=0)
    assert t[0].reason == "sl" and t[0].exit == 99


def test_stats():
    from cointracker.strategy import Trade
    ts = pd.Timestamp("2024-01-01")
    tr = [Trade(ts, ts, 1, 1, r, "tp", 1) for r in (0.05, 0.02, -0.04)]
    s = stats(tr)
    assert s["trades"] == 3 and np.isclose(s["win_rate"], 2 / 3) and np.isclose(s["profit_factor"], 0.07 / 0.04)
    assert stats([])["win_rate"] is None
