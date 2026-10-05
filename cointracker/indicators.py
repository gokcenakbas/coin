"""Teknik göstergeler (yalnızca pandas/numpy, harici kütüphane gerekmez)."""

from __future__ import annotations

import numpy as np
import pandas as pd

FIVE_YEARS_DAYS = 1826


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + gain / loss)
    out = out.where(loss != 0, 100.0)
    out = out.where(~((gain == 0) & (loss == 0)), 50.0)
    return out.where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + k * std, "bb_lower": mid - k * std})


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def percentile_rank(close: pd.Series, window: int = FIVE_YEARS_DAYS, min_periods: int = 365) -> pd.Series:
    """Bugünkü fiyatın son `window` gündeki fiyatlar içindeki yüzdelik konumu (0=en ucuz, 1=en pahalı)."""
    return close.rolling(window, min_periods=min_periods).apply(lambda x: (x <= x[-1]).mean(), raw=True)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    out["sma50"] = sma(close, 50)
    out["sma200"] = sma(close, 200)
    out["rsi"] = rsi(close)
    out = out.join(macd(close)).join(bollinger(close))
    out["atr"] = atr(out)
    out["pct_rank_5y"] = percentile_rank(close)
    ath = close.rolling(FIVE_YEARS_DAYS, min_periods=1).max()
    out["drawdown_5y"] = close / ath - 1
    out["volatility_30d"] = np.log(close).diff().rolling(30).std() * np.sqrt(365)
    return out
