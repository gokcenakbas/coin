"""Coin analizi: asıl sinyal trend kırılımı kuralıdır (cointracker/trend.py).

Aşağıdaki teknik puan eski sistemdir; gerçek veride GÜÇLÜ AL/AL rastgele alımdan iyi çıkmadığı için artık
sinyal olarak kullanılmaz, yalnızca bilgi olarak gösterilir.

Puan bileşenleri (her gün için hesaplanır, böylece aynı kural geriye dönük test edilebilir):
  trend  : fiyat 200 günlük ortalamanın üstünde +1 / altında -1
  cross  : 50G ort. 200G ort. üstünde (golden cross bölgesi) +1 / altında -1
  rsi    : aşırı satım +2 (yakın +1) / aşırı alım -2 (yakın -1)
  macd   : MACD sinyal çizgisinin üstünde +1 / altında -1
  bb     : fiyat alt Bollinger bandının altında +1 / üst bandın üstünde -1
  value  : fiyat son 5 yılın ucuz diliminde +1 / pahalı diliminde -1
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cointracker.indicators import add_indicators
from cointracker.levels import support_resistance
from cointracker.trend import rule_stats, rule_trades, setup as trend_setup, trend_frame

LEVELS = ["STRONG_SELL", "SELL", "HOLD", "BUY", "TREND", "STRONG_BUY"]
LABELS_TR = {
    "STRONG_BUY": "GÜÇLÜ AL",
    "TREND": "TRENDDE",
    "BUY": "AL",
    "HOLD": "BEKLE",
    "SELL": "SAT",
    "STRONG_SELL": "GÜÇLÜ SAT",
}
COMPONENTS = ["s_trend", "s_cross", "s_rsi", "s_macd", "s_bb", "s_value"]


def _sign(cond_pos: pd.Series, cond_neg: pd.Series) -> pd.Series:
    return pd.Series(np.select([cond_pos, cond_neg], [1, -1], 0), index=cond_pos.index)


def score_frame(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """OHLCV verisine göstergeleri, bileşen puanlarını, toplam puanı ve sinyal seviyesini ekler."""
    s = cfg["signals"]
    out = df if "rsi" in df.columns else add_indicators(df)
    out = out.copy()
    close = out["close"]

    out["s_trend"] = _sign(close > out["sma200"], close < out["sma200"])
    out["s_cross"] = _sign(out["sma50"] > out["sma200"], out["sma50"] < out["sma200"])

    r = out["rsi"]
    oversold, overbought = s["rsi_oversold"], s["rsi_overbought"]
    out["s_rsi"] = np.select(
        [r < oversold, r < oversold + 5, r > overbought, r > overbought - 5], [2, 1, -2, -1], 0
    )
    out["s_macd"] = _sign(out["macd"] > out["macd_signal"], out["macd"] < out["macd_signal"])
    out["s_bb"] = _sign(close < out["bb_lower"], close > out["bb_upper"])
    rank = out["pct_rank_5y"]
    out["s_value"] = _sign(rank < s["cheap_percentile"], rank > s["expensive_percentile"])

    out["score"] = out[COMPONENTS].sum(axis=1).astype(int)
    out["level"] = out["score"].map(lambda v: classify(v, cfg))
    return out


REGIME_LABELS = {"up": "Yükseliş", "down": "Düşüş", "mixed": "Kararsız"}


def market_regime(btc: pd.DataFrame) -> pd.Series:
    """Bitcoin'in trendine göre piyasa rejimi: 'up' | 'down' | 'mixed'.

    yükseliş: BTC 200G ortalamanın üstünde ve 50G > 200G; düşüş: ikisi de tersi; diğerleri kararsız.
    """
    if "sma200" not in btc.columns:
        btc = add_indicators(btc)
    known = btc["sma200"].notna()
    above = btc["close"] > btc["sma200"]
    golden = btc["sma50"] > btc["sma200"]
    regime = np.select([above & golden, known & ~above & ~golden], ["up", "down"], "mixed")
    return pd.Series(regime, index=btc.index)


def classify(score: float, cfg: dict) -> str:
    s = cfg["signals"]
    if score >= s["strong_buy_threshold"]:
        return "STRONG_BUY"
    if score >= s["buy_threshold"]:
        return "BUY"
    if score <= s["strong_sell_threshold"]:
        return "STRONG_SELL"
    if score <= s["sell_threshold"]:
        return "SELL"
    return "HOLD"


def forward_return_stats(close: pd.Series, mask: pd.Series, horizon: int) -> dict:
    """`mask` doğru olan günlerden `horizon` gün sonraki getirilerin istatistikleri."""
    fwd = close.shift(-horizon) / close - 1
    sample = fwd[mask.fillna(False) & fwd.notna()]
    if sample.empty:
        return {"count": 0, "avg_return": None, "median_return": None, "win_rate": None}
    return {
        "count": int(sample.size),
        "avg_return": float(sample.mean()),
        "median_return": float(sample.median()),
        "win_rate": float((sample > 0).mean()),
    }


@dataclass
class Signal:
    symbol: str
    date: pd.Timestamp
    price: float
    score: int
    level: str
    reasons: list[str] = field(default_factory=list)
    rsi: float | None = None
    pct_rank_5y: float | None = None
    drawdown_5y: float | None = None
    change_7d: float | None = None
    change_30d: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    history: dict = field(default_factory=dict)
    plan: dict | None = None
    market: dict | None = None
    edge: dict = field(default_factory=dict)  # eski puanın AL/SAT isabeti ve rastgele günle kıyası
    setup: dict | None = None  # trend kırılımı kuralına göre durum, alım bölgesi, stop, satış seviyesi
    rule: dict = field(default_factory=dict)  # kuralın bu coindeki son 5 yıllık işlemleri

    @property
    def label(self) -> str:
        return LABELS_TR[self.level]

    @property
    def is_buy(self) -> bool:
        return self.level == "STRONG_BUY"

    @property
    def is_sell(self) -> bool:
        return self.level in ("SELL", "STRONG_SELL")


def _crossed(a: pd.Series, b: pd.Series, lookback: int) -> int:
    """Son `lookback` günde a, b'yi yukarı kestiyse +1, aşağı kestiyse -1, yoksa 0."""
    diff = np.sign(a - b).dropna()
    recent = diff.iloc[-(lookback + 1):]
    if len(recent) < 2:
        return 0
    changes = recent.diff().iloc[1:]
    if (changes > 0).any() and recent.iloc[-1] > 0:
        return 1
    if (changes < 0).any() and recent.iloc[-1] < 0:
        return -1
    return 0


def _pct_change(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - days] - 1)


def _num(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def build_reasons(scored: pd.DataFrame, cfg: dict) -> list[str]:
    last = scored.iloc[-1]
    s = cfg["signals"]
    reasons: list[str] = []

    if pd.notna(last["sma200"]):
        if last["close"] > last["sma200"]:
            reasons.append(f"Fiyat 200 günlük ortalamanın %{(last['close'] / last['sma200'] - 1) * 100:.1f} üzerinde (yükseliş trendi)")
        else:
            reasons.append(f"Fiyat 200 günlük ortalamanın %{(1 - last['close'] / last['sma200']) * 100:.1f} altında (düşüş trendi)")
        cross = _crossed(scored["sma50"], scored["sma200"], 10)
        if cross > 0:
            reasons.append("Son 10 günde GOLDEN CROSS oluştu (50G ort. 200G ort.'u yukarı kesti)")
        elif cross < 0:
            reasons.append("Son 10 günde DEATH CROSS oluştu (50G ort. 200G ort.'u aşağı kesti)")

    if pd.notna(last["rsi"]):
        r = last["rsi"]
        if r < s["rsi_oversold"]:
            reasons.append(f"RSI {r:.1f} → aşırı satım bölgesi (tepki yükselişi ihtimali)")
        elif r > s["rsi_overbought"]:
            reasons.append(f"RSI {r:.1f} → aşırı alım bölgesi (düzeltme riski)")
        else:
            reasons.append(f"RSI {r:.1f} (nötr)")

    macd_cross = _crossed(scored["macd"], scored["macd_signal"], 3)
    if macd_cross > 0:
        reasons.append("MACD sinyal çizgisini yukarı kesti (al sinyali)")
    elif macd_cross < 0:
        reasons.append("MACD sinyal çizgisini aşağı kesti (sat sinyali)")

    if pd.notna(last["bb_lower"]):
        if last["close"] < last["bb_lower"]:
            reasons.append("Fiyat alt Bollinger bandının altında (aşırı düşüş)")
        elif last["close"] > last["bb_upper"]:
            reasons.append("Fiyat üst Bollinger bandının üstünde (aşırı yükseliş)")

    if pd.notna(last["pct_rank_5y"]):
        pct = last["pct_rank_5y"] * 100
        note = ""
        if last["pct_rank_5y"] < s["cheap_percentile"]:
            note = " → tarihsel olarak UCUZ"
        elif last["pct_rank_5y"] > s["expensive_percentile"]:
            note = " → tarihsel olarak PAHALI"
        reasons.append(f"5 yıllık fiyat yüzdeliği: %{pct:.0f} (0 = 5 yılın en ucuzu, 100 = en pahalısı){note}")
    if pd.notna(last["drawdown_5y"]) and last["drawdown_5y"] < -0.01:
        reasons.append(f"5 yıllık zirvenin %{-last['drawdown_5y'] * 100:.0f} altında")
    return reasons


def market_info(btc: pd.DataFrame) -> dict | None:
    """Piyasanın (Bitcoin'in) güncel rejimi ve bu rejimin ne zamandır sürdüğü."""
    if btc is None or btc.empty:
        return None
    if "sma200" not in btc.columns:
        btc = add_indicators(btc)
    regime = market_regime(btc)
    last = btc.iloc[-1]
    current = regime.iloc[-1]
    changed = regime[regime != current]
    since = regime.index[0] if changed.empty else regime.index[regime.index > changed.index[-1]][0]
    return {
        "regime": current,
        "label": REGIME_LABELS[current],
        "btc_price": float(last["close"]),
        "btc_vs_sma200": None if pd.isna(last["sma200"]) else float(last["close"] / last["sma200"] - 1),
        "since": since,
    }


def analyze(symbol: str, df: pd.DataFrame, cfg: dict, scored: pd.DataFrame | None = None,
            market: pd.DataFrame | None = None) -> Signal:
    """Bir coinin güncel sinyalini, gerekçelerini ve 5 yıllık geçmiş başarısını hesaplar.

    `market`: Bitcoin'in günlük verisi. Verilirse güncel piyasa rejimi eklenir ve sinyalin geçmiş başarısı
    rejimlere bölünür. (Gerçek veriyle yapılan ölçümde AL sinyalleri Bitcoin düşüşteyken daha KÖTÜ sonuç
    vermediği için rejime göre sinyal değiştirilmez; yalnızca bilgi verilir. Bkz. tools/research/regime.py)
    """
    scored = scored if scored is not None else score_frame(df, cfg)
    last = scored.iloc[-1]
    s = cfg["signals"]
    horizon = int(s["horizon_days"])

    signal = Signal(
        symbol=symbol,
        date=scored.index[-1],
        price=float(last["close"]),
        score=int(last["score"]),
        level=str(last["level"]),
        reasons=build_reasons(scored, cfg),
        rsi=_num(last["rsi"]),
        pct_rank_5y=_num(last["pct_rank_5y"]),
        drawdown_5y=_num(last["drawdown_5y"]),
        change_7d=_pct_change(scored["close"], 7),
        change_30d=_pct_change(scored["close"], 30),
    )

    # Teknik puan (eski sistem): sinyal olarak kullanılmaz, yalnızca bilgi ve karşılaştırma için
    window = scored.loc[scored.index >= scored.index[-1] - pd.Timedelta(days=1826)]
    signal.edge = {
        "horizon": horizon,
        "buy": forward_return_stats(window["close"], window["score"] >= s["buy_threshold"], horizon),
        "sell": forward_return_stats(window["close"], window["score"] <= s["sell_threshold"], horizon),
        "all": forward_return_stats(window["close"], pd.Series(True, index=window.index), horizon),
    }
    if market is not None and not market.empty:
        signal.market = market_info(market)

    # Asıl sinyal: trend kırılımı kuralı (bkz. cointracker/trend.py ve tools/research/)
    # Kural günlük KAPANIŞA göre çalışır: bugünün henüz kapanmamış mumu sinyal için kullanılmaz,
    # yalnızca anlık fiyatın alım bölgesinde olup olmadığına bakılır.
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    complete = scored.iloc[:-1] if scored.index[-1] >= today and len(scored) > 1 else scored
    tf = trend_frame(complete, market)
    st = trend_setup(tf, price=float(scored["close"].iloc[-1]))
    signal.setup = st
    signal.level = st["level"]
    signal.stop_loss = st["stop"]
    signal.take_profit = None
    trades = rule_trades(tf, since=tf.index[-1] - pd.Timedelta(days=1826))
    signal.rule = {"stats": rule_stats(trades), "trades": trades[-8:], "trades_all": trades}
    signal.history = {"horizon": horizon, **signal.rule["stats"]}

    # Destek/direnç: son 1 yılın dönüş noktaları (kâr alma ve geri çekilme seviyeleri olarak gösterilir)
    supports, resistances = support_resistance(complete, st["atr"], limit=3) if st["atr"] else ([], [])

    signal.plan = {
        "action": {"BUY": "AL", "ABOVE_ZONE": "TUT", "TREND": "TUT", "EXIT": "SAT", "WAIT": "BEKLE"}[st["state"]],
        "state": st["state"], "summary": st["summary"], "price": st["price"],
        "buy_low": st["buy_low"], "buy_high": st["buy_high"], "in_buy_zone": st["in_buy_zone"],
        "stop": st["stop"], "exit_level": st["exit_level"], "atr": st["atr"],
        "close": st["close"], "as_of": st["as_of"],
        "conditions": st["conditions"], "signal": st["signal"], "market_open": st["market_open"],
        "supports": supports, "resistances": resistances,
    }
    return signal
