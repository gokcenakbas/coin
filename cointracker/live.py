"""Anlık analizler: gün içi zaman dilimlerinde sinyal, emir defteri (order book) ve 24 saatlik özet."""

from __future__ import annotations

import numpy as np
import pandas as pd

from cointracker.indicators import add_indicators
from cointracker.signals import LABELS_TR, score_frame

INTRADAY_TIMEFRAMES = [("15m", "15 dk"), ("1h", "1 saat"), ("4h", "4 saat")]


def _trend(last: pd.Series) -> str | None:
    if pd.isna(last["sma200"]):
        return None
    return "up" if last["close"] > last["sma200"] else "down"


def timeframe_signal(df: pd.DataFrame, cfg: dict, interval: str, label: str) -> dict:
    """Herhangi bir zaman diliminin mumlarından sinyal üretir.

    "5 yıllık değer" bileşeni yalnızca günlük veride anlamlı olduğu için gün içinde devre dışıdır.
    """
    ind = add_indicators(df)
    if interval != "1d":
        ind["pct_rank_5y"] = np.nan
    last = score_frame(ind, cfg).iloc[-1]
    prev_close = ind["close"].iloc[-2] if len(ind) > 1 else np.nan
    return {
        "interval": interval,
        "label": label,
        "score": int(last["score"]),
        "level": last["level"],
        "level_label": LABELS_TR[last["level"]],
        "rsi": None if pd.isna(last["rsi"]) else float(last["rsi"]),
        "trend": _trend(last),
        "macd": None if pd.isna(last["macd_signal"]) else ("up" if last["macd"] > last["macd_signal"] else "down"),
        "change": None if pd.isna(prev_close) else float(last["close"] / prev_close - 1),
    }


def orderbook_summary(depth: dict, band: float = 0.02) -> dict:
    """Emir defterinden alıcı/satıcı dengesini ve en büyük emir duvarlarını çıkarır.

    `band`: orta fiyatın ±%2'si içindeki emirler hesaba katılır.
    """
    bids = np.asarray(depth.get("bids", []), dtype=float).reshape(-1, 2)
    asks = np.asarray(depth.get("asks", []), dtype=float).reshape(-1, 2)
    if not len(bids) or not len(asks):
        return {}
    best_bid, best_ask = bids[:, 0].max(), asks[:, 0].min()
    mid = (best_bid + best_ask) / 2
    near_bids = bids[bids[:, 0] >= mid * (1 - band)]
    near_asks = asks[asks[:, 0] <= mid * (1 + band)]
    bid_usd = near_bids[:, 0] @ near_bids[:, 1]
    ask_usd = near_asks[:, 0] @ near_asks[:, 1]
    total = bid_usd + ask_usd
    imbalance = bid_usd / total if total else 0.5

    def wall(levels: np.ndarray) -> dict | None:
        if not len(levels):
            return None
        notional = levels[:, 0] * levels[:, 1]
        i = int(notional.argmax())
        return {"price": float(levels[i, 0]), "usd": float(notional[i])}

    if imbalance >= 0.6:
        verdict = "Alıcılar baskın"
    elif imbalance <= 0.4:
        verdict = "Satıcılar baskın"
    else:
        verdict = "Dengeli"
    return {
        "best_bid": float(best_bid),
        "best_ask": float(best_ask),
        "spread_pct": float((best_ask - best_bid) / mid),
        "band": band,
        "bid_usd": float(bid_usd),
        "ask_usd": float(ask_usd),
        "imbalance": float(imbalance),
        "verdict": verdict,
        "bid_wall": wall(near_bids),
        "ask_wall": wall(near_asks),
    }


def ticker_summary(t: dict) -> dict:
    return {
        "price": float(t["lastPrice"]),
        "change_pct": float(t["priceChangePercent"]) / 100,
        "high": float(t["highPrice"]),
        "low": float(t["lowPrice"]),
        "quote_volume": float(t["quoteVolume"]),
        "trades": int(t.get("count", 0)),
        "vwap": float(t.get("weightedAvgPrice", 0)),
    }
