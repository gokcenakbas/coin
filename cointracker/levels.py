"""Alım-satım seviyeleri: destek/direnç, alım bölgesi, hedefler, zarar-durdur.

Seviyeler günlük mumlardan hesaplanır:
  * Destek/direnç: son 1 yıldaki dönüş noktaları (yerel dip ve tepeler) birbirine yakın olanlar
    birleştirilerek bulunur; bir seviyeye ne kadar çok dokunulduysa o kadar güçlüdür.
  * Alım bölgesi: AL sinyalinde en yakın destek ile güncel fiyat arası; diğer durumlarda en yakın
    desteğin çevresi (fiyatın oraya inmesi beklenir).
  * Hedefler: üstteki ilk iki direnç (yoksa ATR'ye göre hesaplanan uzaklıklar).
  * Zarar-durdur: alım bölgesinin altındaki destek kırılırsa çıkış (destek - 1 ATR).
  * 5 yıllık ucuz/pahalı bölge: son 5 yılın kapanış fiyatlarının yüzdelik dilimleri.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FIVE_YEARS_DAYS = 1826


def swing_points(df: pd.DataFrame, window: int = 5) -> tuple[pd.Series, pd.Series]:
    """Kendinden önceki ve sonraki `window` günün en yükseği/en düşüğü olan günler."""
    span = 2 * window + 1
    highs = df["high"][df["high"] == df["high"].rolling(span, center=True).max()]
    lows = df["low"][df["low"] == df["low"].rolling(span, center=True).min()]
    return highs, lows


def cluster_levels(prices: list[float], tolerance: float) -> list[dict]:
    """Birbirine `tolerance` (oransal) kadar yakın fiyatları tek seviyede birleştirir.

    Her küme ilk fiyatından en fazla `tolerance` genişliğe kadar büyür; böylece geniş bir
    fiyat aralığı zincirleme tek bir seviyeye dönüşmez.
    """
    levels: list[dict] = []
    for p in sorted(prices):
        if levels and p <= levels[-1]["_first"] * (1 + tolerance):
            lvl = levels[-1]
            lvl["_sum"] += p
            lvl["touches"] += 1
            lvl["price"] = lvl["_sum"] / lvl["touches"]
        else:
            levels.append({"price": p, "touches": 1, "_sum": p, "_first": p})
    return [{"price": float(lvl["price"]), "touches": lvl["touches"]} for lvl in levels]


def support_resistance(df: pd.DataFrame, atr: float, lookback: int = 365, window: int = 5,
                       limit: int = 3) -> tuple[list[dict], list[dict]]:
    """(destekler, dirençler) — en yakından uzağa, her biri en fazla `limit` adet.

    Son 1 yılda fiyatın altında (veya üstünde) hiç seviye yoksa — ör. fiyat yıllık dipteyse —
    o taraf için son 5 yıla bakılır.
    """
    supports, resistances = _levels(df, atr, lookback, window)
    if (not supports or not resistances) and len(df) > lookback:
        long_s, long_r = _levels(df, atr, FIVE_YEARS_DAYS, window)
        supports = supports or long_s
        resistances = resistances or long_r
    return supports[:limit], resistances[:limit]


def _levels(df: pd.DataFrame, atr: float, lookback: int, window: int) -> tuple[list[dict], list[dict]]:
    recent = df.iloc[-lookback:]
    price = float(recent["close"].iloc[-1])
    highs, lows = swing_points(recent, window)
    tolerance = max(0.01, 0.6 * atr / price) if atr and price else 0.01
    levels = cluster_levels(list(highs) + list(lows), tolerance)
    # Fiyata yarım ATR'den yakın seviyeler "şu anki fiyat" sayılır, destek/direnç listesine girmez
    gap = 0.5 * atr if atr else price * 0.005
    supports = sorted((lv for lv in levels if lv["price"] < price - gap), key=lambda lv: -lv["price"])
    resistances = sorted((lv for lv in levels if lv["price"] > price + gap), key=lambda lv: lv["price"])
    return supports, resistances


def _pct(a: float, b: float) -> float:
    return a / b - 1


def trade_plan(scored: pd.DataFrame, cfg: dict) -> dict | None:
    """Son güne göre alım bölgesi, hedefler ve zarar-durdur seviyelerini hesaplar."""
    last = scored.iloc[-1]
    atr = float(last["atr"]) if pd.notna(last["atr"]) else None
    if not atr or len(scored) < 30:
        return None
    s = cfg["signals"]
    price = float(last["close"])
    score = int(last["score"])
    supports, resistances = support_resistance(scored, atr)

    s1 = supports[0]["price"] if supports else price - 1.5 * atr
    r_prices = [r["price"] for r in resistances]
    # Hedef 1 alım bölgesine çok yakınsa (1 ATR'den az) anlamsız; bir sonrakine geç
    r_prices = [r for r in r_prices if r > price + atr] or r_prices
    target1 = r_prices[0] if r_prices else price + 2 * atr
    target2 = r_prices[1] if len(r_prices) > 1 else max(target1 + 1.5 * atr, price + 4 * atr)

    if score >= s["buy_threshold"]:
        buy_high = price
        buy_low = max(s1, price - atr)
    else:
        buy_high = min(s1 + 0.3 * atr, price)
        buy_low = s1 - 0.3 * atr
    stop = min(buy_low, s1) - atr
    entry = (buy_low + buy_high) / 2
    risk_reward = (target1 - entry) / (entry - stop) if entry > stop else None

    sell_low, sell_high = target1 - 0.3 * atr, target1 + 0.3 * atr
    if score <= s["sell_threshold"]:
        # Hemen sat ya da en yakın dirence kadar olan tepki yükselişini değerlendir
        sell_low, sell_high = price, resistances[0]["price"] if resistances else price + atr

    window = scored["close"].iloc[-FIVE_YEARS_DAYS:]
    cheap_5y = float(np.quantile(window, s["cheap_percentile"])) if len(window) >= 365 else None
    expensive_5y = float(np.quantile(window, s["expensive_percentile"])) if len(window) >= 365 else None

    if score >= s["buy_threshold"]:
        action = "AL"
        summary = (f"Şu anki fiyattan veya {_fmt(buy_low)} seviyesine kadar geri çekilmelerde alınabilir. "
                   f"Hedef 1: {_fmt(target1)} ({_pct_str(target1, price)}), "
                   f"zarar-durdur: {_fmt(stop)} ({_pct_str(stop, price)}).")
    elif score <= s["sell_threshold"]:
        action = "SAT"
        summary = (f"Elinizdeyse şu anki fiyattan ({_fmt(price)}) satılabilir veya tepki yükselişinde "
                   f"{_fmt(sell_high)} direncine kadar. Yeniden alım için {_fmt(buy_low)}–{_fmt(buy_high)} "
                   f"desteği izlenebilir.")
    else:
        action = "BEKLE"
        summary = (f"Alım için fiyatın {_fmt(buy_low)}–{_fmt(buy_high)} desteğine inmesi beklenebilir "
                   f"({_pct_str(buy_high, price)}). Elinizdeyse {_fmt(sell_low)}–{_fmt(sell_high)} "
                   f"direncinde satış düşünülebilir.")

    return {
        "action": action,
        "summary": summary,
        "price": price,
        "atr": atr,
        "buy_low": buy_low,
        "buy_high": buy_high,
        "in_buy_zone": buy_low <= price <= buy_high * 1.001,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "sell_low": sell_low,
        "sell_high": sell_high,
        "risk_reward": risk_reward,
        "supports": supports,
        "resistances": resistances,
        "cheap_5y": cheap_5y,
        "expensive_5y": expensive_5y,
    }


def _pct_str(a: float, b: float) -> str:
    return f"{_pct(a, b) * 100:+.1f}%".replace(".", ",")


def _fmt(p: float) -> str:
    if p >= 1000:
        return f"{p:,.0f}".replace(",", ".")
    if p >= 1:
        return f"{p:.2f}".replace(".", ",")
    return f"{p:.6g}".replace(".", ",")
