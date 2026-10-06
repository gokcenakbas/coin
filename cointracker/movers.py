"""Hızlı hareket öncü izleri (1 saatlik mumlar): sıkışma, hacim patlaması, kırılım.

Aynı hesaplar hem araştırmada (tools/research/fastmoves.py) hem de uygulamadaki
"Patlama adayları" listesinde kullanılır; böylece uygulama tam olarak ölçülen şeyi gösterir.

Gerçek veriyle ölçüm (Ekim 2026, 39 coin, 1 yıl saatlik; olay = sonraki 4 saatte ±%5, normalde ~%6-7):
  * Hacim normalin ≥3 katı: olasılık ~2,5 kat; ≥5 katı: ~3 kat (iki dönemde de tutarlı)
  * Hacimle yukarı kırılım (3 günlük tepe): ~2,4-3,8 kat
  * Sıkışma (dar bant): olasılığı YARIYA düşürdü → aday sayılmaz
  * Hacimle aşağı kırılım: dönemler arasında tutarsız → aday sayılmaz
  * Hiçbiri yönü söylemiyor: yukarı ve aşağı büyük hareket benzer sıklıkta
"""

from __future__ import annotations

import pandas as pd

MOVE_PCT = 0.05      # "hızlı hareket": sonraki MOVE_HOURS saatte ±%5
MOVE_HOURS = 4
SQUEEZE_WINDOW = 720  # 30 gün: bant genişliği bu süre içindeki yerine göre değerlendirilir
VOLUME_WINDOW = 72    # 3 gün: "normal" saatlik hacim
BREAKOUT_WINDOW = 72  # 3 günlük tepe/dip

RVOL_HIGH = 3.0       # saatlik hacim normalin 3 katı → hacim patlaması
RVOL_BREAKOUT = 2.0   # yukarı kırılımın sayılması için gereken hacim


def features(df: pd.DataFrame) -> pd.DataFrame:
    """Her saat için yalnızca o saate kadarki veriyle hesaplanan öncü izler."""
    close = df["close"]
    mid = close.rolling(20, min_periods=20).mean()
    std = close.rolling(20, min_periods=20).std(ddof=0)
    bb_width = 4 * std / mid
    volume = df["quote_volume"]
    normal_volume = volume.rolling(VOLUME_WINDOW, min_periods=24).median().shift(1)
    prior_high = df["high"].rolling(BREAKOUT_WINDOW, min_periods=BREAKOUT_WINDOW).max().shift(1)
    prior_low = df["low"].rolling(BREAKOUT_WINDOW, min_periods=BREAKOUT_WINDOW).min().shift(1)
    return pd.DataFrame({
        "close": close,
        "bb_width": bb_width,
        "squeeze_pct": bb_width.rolling(SQUEEZE_WINDOW, min_periods=240).rank(pct=True),
        "rvol": volume / normal_volume,
        "breakout_up": close > prior_high,
        "breakout_down": close < prior_low,
        "ret_1h": close.pct_change(),
        "range_3d": prior_high / prior_low - 1,
    }, index=df.index)


def candidate(df: pd.DataFrame) -> dict | None:
    """Son kapanmış saate göre coin bir "patlama adayı" mı? Değilse None."""
    if len(df) < 260:
        return None
    f = features(df).iloc[-2]  # son satır henüz kapanmamış saat; kapanmış son saate bak
    if pd.isna(f["squeeze_pct"]) or pd.isna(f["rvol"]):
        return None
    volume = f["rvol"] >= RVOL_HIGH
    breakout = bool(f["breakout_up"]) and f["rvol"] >= RVOL_BREAKOUT
    if not (volume or breakout):
        return None
    tags = (["volume"] if volume else []) + (["breakout_up"] if breakout else [])
    return {
        "tags": tags,
        "squeeze_pct": float(f["squeeze_pct"]),
        "rvol": float(f["rvol"]),
        "breakout": "up" if breakout else None,
        "ret_1h": float(f["ret_1h"]),
        "range_3d": None if pd.isna(f["range_3d"]) else float(f["range_3d"]),
        "hour": df.index[-2],
    }
