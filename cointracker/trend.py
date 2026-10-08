"""Trend kırılımı stratejisi: uygulamanın GÜÇLÜ AL sinyali ve satış kuralı.

Gerçek veriyle test edildi (tools/research/entryexit.py, breakout.py; 32 büyük coin, son 5 yıl, eğitim
ilk 3 yıl / test son 2 yıl, komisyon+kayma dahil). Eski puan sistemindeki GÜÇLÜ AL rastgele alımdan iyi
değildi; bu kural iki dönemde de rastgele alımı belirgin şekilde geçti.

AL (GÜÇLÜ AL) — dördü birden:
  1. coin 200 günlük ortalamanın üstünde
  2. 50 günlük ortalama 200 günlük ortalamanın üstünde
  3. Bitcoin 200 günlük ortalamanın üstünde (piyasa yükselişte)
  4. günlük kapanış son 20 günün en yükseği (kırılım; sinyal o günün kapanışında oluşur)
  Alım bölgesi: kırılım seviyesi (önceki 19 günün en yüksek kapanışı) … sinyal kapanışı + 0,5 ATR;
  sinyal 3 gün geçerli. Fiyat bölgenin üstündeyse kovalanmaz.
SAT:
  * günlük kapanış 20 günlük ortalamanın altına inerse ertesi gün sat
  * başlangıç zarar-durdur: alış fiyatı − 2 ATR (gün içinde dokunursa sat)

Seviye kodları (arayüz ve bildirimler): STRONG_BUY = GÜÇLÜ AL, TREND = trendde (elindeyse tut, yeni
alım yok), HOLD = BEKLE, SELL = SAT (elindeyse).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cointracker.indicators import add_indicators

BREAKOUT_DAYS = 20
EXIT_MA_DAYS = 20
SIGNAL_VALID_DAYS = 3
ZONE_ATR = 0.5
STOP_ATR = 2.0
FEE = 0.0015  # komisyon + kayma, her yön (araştırmadakiyle aynı)

LEVEL_LABELS = {"STRONG_BUY": "GÜÇLÜ AL", "TREND": "TRENDDE", "HOLD": "BEKLE", "SELL": "SAT"}

# Araştırmada ölçülen sonuçlar (Ekim 2026; tools/research/entryexit.py ve breakout.py çıktıları).
MEASURED = {
    "coins": 32,
    "train": {"period": "Eki 2021 – Eki 2024", "trades": 554, "win_rate": 0.375, "avg": 0.089, "median": -0.035,
              "profit_factor": 2.63, "worst": -0.271, "days": 15},
    "test": {"period": "Eki 2024 – Eki 2026", "trades": 368, "win_rate": 0.293, "avg": 0.138, "median": -0.046,
             "profit_factor": 3.16, "worst": -0.226, "days": 14},
    "random": {"train_avg": 0.011, "test_avg": 0.027, "train_pf": 1.35, "test_pf": 1.84},
    "portfolio10": {"train_return": 2.342, "train_dd": -0.343, "test_return": 3.138, "test_dd": -0.415},
    "btc_hold": {"train_return": 0.152, "train_dd": -0.766, "test_return": 0.335, "test_dd": -0.530},
}


def _fmt_tr(v: float | None) -> str:
    """Arayüzdeki gibi Türkçe sayı: 84.250 · 146,20 · 0,1712"""
    if v is None or not np.isfinite(v):
        return "-"
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", ".")
    if abs(v) >= 1:
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    decimals = 3 - int(np.floor(np.log10(abs(v)))) if v else 2  # 4 anlamlı basamak
    return f"{v:.{decimals}f}".replace(".", ",")


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def trend_frame(df: pd.DataFrame, btc: pd.DataFrame | None) -> pd.DataFrame:
    """Her gün için kural koşulları ve sinyaller (yalnızca o güne kadarki veriyle)."""
    out = df if "sma200" in df.columns else add_indicators(df)
    out = out.copy()
    close = out["close"]
    out["sma20"] = _sma(close, EXIT_MA_DAYS)
    if btc is not None and not btc.empty:
        b = btc if "sma200" in btc.columns else add_indicators(btc)
        btc_up = (b["close"] > b["sma200"]).reindex(out.index).ffill().fillna(False).astype(bool)
    else:
        btc_up = pd.Series(False, index=out.index)
    out["btc_up"] = btc_up
    out["c_above200"] = close > out["sma200"]
    out["c_golden"] = out["sma50"] > out["sma200"]
    out["in_trend"] = out["c_above200"] & out["c_golden"] & out["btc_up"]
    out["brk_level"] = close.shift(1).rolling(BREAKOUT_DAYS - 1, min_periods=BREAKOUT_DAYS - 1).max()
    out["is_high20"] = close >= close.rolling(BREAKOUT_DAYS, min_periods=BREAKOUT_DAYS).max()
    brk = out["in_trend"] & out["is_high20"]
    out["entry_sig"] = brk & ~brk.shift(1, fill_value=False)
    return out


def setup(tf: pd.DataFrame, price: float | None = None) -> dict:
    """Son güne göre durum, alım bölgesi, zarar-durdur ve satış seviyesi."""
    last = tf.iloc[-1]
    price = float(last["close"]) if price is None else float(price)
    atr = float(last["atr"]) if pd.notna(last["atr"]) else None
    sma20 = float(last["sma20"]) if pd.notna(last["sma20"]) else None
    conditions = [
        {"text": "Fiyat 200 günlük ortalamanın üstünde", "ok": bool(last["c_above200"])},
        {"text": "50 günlük ortalama 200 günlük ortalamanın üstünde", "ok": bool(last["c_golden"])},
        {"text": "Bitcoin 200 günlük ortalamanın üstünde (piyasa yükselişte)", "ok": bool(last["btc_up"])},
        {"text": f"Kapanış son {BREAKOUT_DAYS} günün en yükseği (kırılım)", "ok": bool(last["is_high20"])},
    ]

    recent = tf["entry_sig"].iloc[-SIGNAL_VALID_DAYS:]
    sig_dates = recent.index[recent.to_numpy()]
    signal = None
    if len(sig_dates):
        t = sig_dates[-1]
        row = tf.loc[t]
        days_ago = int(len(tf) - 1 - tf.index.get_loc(t))
        zone_low = float(row["brk_level"])
        zone_high = float(row["close"] + ZONE_ATR * row["atr"])
        signal = {"date": t, "days_ago": days_ago, "close": float(row["close"]),
                  "atr": float(row["atr"]), "zone_low": zone_low, "zone_high": zone_high}

    still_ok = bool(last["in_trend"]) and sma20 is not None and last["close"] >= sma20
    below_exit = sma20 is not None and last["close"] < sma20

    if signal and still_ok and signal["zone_low"] <= price <= signal["zone_high"]:
        state = "BUY"
    elif signal and still_ok and price > signal["zone_high"]:
        state = "ABOVE_ZONE"
    elif still_ok:
        state = "TREND"
    elif below_exit:
        state = "EXIT"
    else:
        state = "WAIT"
    level = {"BUY": "STRONG_BUY", "ABOVE_ZONE": "TREND", "TREND": "TREND", "EXIT": "SELL", "WAIT": "HOLD"}[state]

    stop_atr = signal["atr"] if signal else atr
    stop = price - STOP_ATR * stop_atr if stop_atr else None
    buy_low = signal["zone_low"] if signal else None
    buy_high = signal["zone_high"] if signal else None

    fmt = _fmt_tr
    if state == "BUY":
        when = f"{signal['days_ago']} gün önceki kapanışta" if signal["days_ago"] else "son günlük kapanışta"
        summary = (f"GÜÇLÜ AL: {when} kırılım oldu ve fiyat "
                   f"alım bölgesinde ({fmt(buy_low)} – {fmt(buy_high)}). Alırsanız zarar-durdur ≈ {fmt(stop)}; "
                   f"günlük kapanış 20 günlük ortalamanın (şu an {fmt(sma20)}) altına inerse ertesi gün satın.")
    elif state == "ABOVE_ZONE":
        summary = (f"Kırılım oldu ama fiyat alım bölgesinin üstünde ({fmt(buy_high)}); kovalamayın. "
                   f"Elinizdeyse tutun: kapanış {fmt(sma20)} (20G ort.) altına inerse satın.")
    elif state == "TREND":
        summary = (f"Trend sürüyor, yeni alım sinyali yok. Elinizdeyse tutun; günlük kapanış 20 günlük ortalamanın "
                   f"(şu an {fmt(sma20)}) altına inerse ertesi gün satın.")
    elif state == "EXIT":
        summary = (f"SAT: günlük kapanış 20 günlük ortalamanın ({fmt(sma20)}) altında. Elinizdeyse satın; "
                   f"yeni alım için tüm koşulların yeniden oluşmasını bekleyin.")
    else:
        missing = [c["text"] for c in conditions[:3] if not c["ok"]]
        summary = "BEKLE: alım koşulları yok" + (f" ({'; '.join(missing).lower()})." if missing else ".")

    return {
        "state": state, "level": level, "label": LEVEL_LABELS[level] if state != "ABOVE_ZONE" else "TRENDDE",
        "summary": summary, "price": price, "atr": atr,
        "signal": signal, "buy_low": buy_low, "buy_high": buy_high,
        "in_buy_zone": state == "BUY",
        "stop": stop, "exit_level": sma20, "close": float(last["close"]), "as_of": tf.index[-1],
        "conditions": conditions, "market_open": bool(last["btc_up"]),
    }


def rule_trades(tf: pd.DataFrame, since: pd.Timestamp | None = None) -> list[dict]:
    """Kuralın bu coindeki geçmiş işlemleri (sinyalin ertesi günü açılışta alış; araştırmayla aynı)."""
    o, h, low, c = (tf[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    atr, sma20 = tf["atr"].to_numpy(dtype=float), tf["sma20"].to_numpy(dtype=float)
    sig = tf["entry_sig"].to_numpy()
    n, i, trades = len(tf), 0, []
    while i < n - 1:
        if not sig[i] or (since is not None and tf.index[i] < since) or not np.isfinite(atr[i]):
            i += 1
            continue
        entry = o[i + 1]
        stop = entry - STOP_ATR * atr[i]
        exit_px = exit_j = None
        reason = None
        for j in range(i + 1, n):
            if low[j] <= stop:
                exit_px, exit_j, reason = min(stop, o[j]), j, "zarar-durdur"
                break
            if c[j] < sma20[j]:
                if j + 1 < n:
                    exit_px, exit_j, reason = o[j + 1], j + 1, "20G ort. altı"
                break
        if exit_px is None:  # hâlâ açık
            trades.append({"entry_date": tf.index[i + 1], "entry": entry, "exit_date": None, "exit": None,
                           "ret": c[-1] / entry - 1, "open": True, "reason": "açık"})
            break
        ret = (exit_px * (1 - FEE)) / (entry * (1 + FEE)) - 1
        trades.append({"entry_date": tf.index[i + 1], "entry": entry, "exit_date": tf.index[exit_j], "exit": exit_px,
                       "ret": ret, "open": False, "reason": reason})
        i = exit_j  # pozisyon kapanana kadar yeni sinyal alınmaz
    return trades


def rule_stats(trades: list[dict]) -> dict:
    closed = [t["ret"] for t in trades if not t["open"]]
    if not closed:
        return {"trades": 0}
    r = np.array(closed)
    gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
    return {"trades": len(r), "win_rate": float((r > 0).mean()), "avg": float(r.mean()),
            "total": float(np.prod(1 + r) - 1), "profit_factor": float(gains / losses) if losses > 0 else None,
            "best": float(r.max()), "worst": float(r.min())}
