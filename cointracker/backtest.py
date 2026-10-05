"""Sinyal kuralının son 5 yıl üzerinde geriye dönük testi.

Kural (yalnızca uzun pozisyon):
  * Gün sonunda puan >= AL eşiği ise ertesi gün açılışta al.
  * Gün sonunda puan <= SAT eşiği ise ertesi gün açılışta sat.
  * ATR tabanlı iz süren stop: stop = max(stop, kapanış - k * ATR).
  * Her alım/satımda komisyon düşülür.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    symbol: str
    start: pd.Timestamp
    end: pd.Timestamp
    total_return: float
    buy_hold_return: float
    max_drawdown: float
    buy_hold_max_drawdown: float
    trades: int
    win_rate: float | None
    avg_trade_return: float | None
    exposure: float

    @property
    def beats_buy_hold(self) -> bool:
        return self.total_return > self.buy_hold_return


def _max_drawdown(equity: np.ndarray) -> float:
    peaks = np.maximum.accumulate(equity)
    return float((equity / peaks - 1).min()) if equity.size else 0.0


def backtest(scored: pd.DataFrame, cfg: dict, symbol: str = "", years: float | None = None) -> BacktestResult:
    years = years if years is not None else cfg["history_years"]
    data = scored.loc[scored.index >= scored.index[-1] - pd.Timedelta(days=int(365.25 * years))]
    data = data.dropna(subset=["sma200"])  # göstergeler oturmadan işlem yapma
    if len(data) < 2:
        raise ValueError(f"{symbol}: geriye dönük test için yeterli veri yok")

    s = cfg["signals"]
    fee = cfg["backtest"]["fee"]
    mult = cfg["risk"]["stop_atr_mult"]
    o, l, c = (data[k].to_numpy() for k in ("open", "low", "close"))
    score, atr = data["score"].to_numpy(), data["atr"].to_numpy()

    cash, units, stop, entry_value = 1.0, 0.0, 0.0, 0.0
    equity = np.empty(len(data))
    equity[0] = cash
    trade_returns: list[float] = []
    days_in_market = 0

    def close_position(price: float) -> None:
        nonlocal cash, units
        cash = units * price * (1 - fee)
        trade_returns.append(cash / entry_value - 1)
        units = 0.0

    for i in range(1, len(data)):
        if units == 0 and score[i - 1] >= s["buy_threshold"] and not np.isnan(atr[i - 1]):
            entry_value = cash
            units = cash * (1 - fee) / o[i]
            cash = 0.0
            stop = o[i] - mult * atr[i - 1]
        elif units > 0 and score[i - 1] <= s["sell_threshold"]:
            close_position(o[i])

        if units > 0:
            if l[i] <= stop:
                close_position(min(o[i], stop))
            else:
                stop = max(stop, c[i] - mult * atr[i])
                days_in_market += 1
        equity[i] = units * c[i] if units > 0 else cash

    if units > 0:
        close_position(c[-1])
        equity[-1] = cash

    wins = [r for r in trade_returns if r > 0]
    return BacktestResult(
        symbol=symbol,
        start=data.index[0],
        end=data.index[-1],
        total_return=float(equity[-1] - 1),
        buy_hold_return=float(c[-1] / c[0] - 1),
        max_drawdown=_max_drawdown(equity),
        buy_hold_max_drawdown=_max_drawdown(c),
        trades=len(trade_returns),
        win_rate=len(wins) / len(trade_returns) if trade_returns else None,
        avg_trade_return=float(np.mean(trade_returns)) if trade_returns else None,
        exposure=days_in_market / max(len(data) - 1, 1),
    )
