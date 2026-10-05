"""İkinci nesil alım stratejisi: "yükseliş trendinde geri çekilmeden alım" + hedef/stop/süre çıkışı.

Fikir: Kripto paralarda en güvenilir alımlar, ana trend yukarıyken gelen kısa süreli düşüşlerdir.
  Giriş (gün sonu, ertesi gün açılışta işlem):
    * Rejim filtresi: coin 200 günlük ortalamanın üstünde ve 50G ort. > 200G ort. (yükseliş trendi);
      isteğe bağlı olarak Bitcoin de yükseliş trendinde (piyasa genel olarak sağlıklı) ve
      coin son 90 günde Bitcoin'den daha iyi performans göstermiş (göreceli güç).
    * Geri çekilme: RSI son `lookback` günde `rsi_entry` eşiğinin altına inmiş.
    * Dönüş onayı: RSI ve kapanış bir önceki güne göre yükselmiş (düşüş durmuş).
  Çıkış (hangisi önce olursa):
    * Kâr al: giriş + `tp_atr` × ATR
    * Zarar-durdur: giriş − `sl_atr` × ATR (aynı gün ikisi de görülürse kötümser varsayımla stop)
    * Başabaş: fiyat hedefe giden yolun `breakeven` kadarını katederse, ertesi günden itibaren stop
      giriş fiyatına (masraflar dahil) çekilir.
    * Süre: `max_hold` gün sonunda kapanıştan çık.
Komisyon ve kayma (slippage) her işlemden düşülür.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SetupParams:
    rsi_entry: float = 45.0
    lookback: int = 3
    confirm: bool = True
    regime: str = "coin+btc"  # "none" | "coin" | "coin+btc" | "coin+btc+rs"
    tp_atr: float = 1.5
    sl_atr: float = 2.0
    max_hold: int = 20
    breakeven: float | None = None  # hedef yolunun bu oranı katedilince stop girişe çekilir (ör. 0.6)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry: float
    exit: float
    ret: float
    reason: str  # "tp" | "sl" | "be" (başabaş) | "time"
    days: int


def uptrend(ind: pd.DataFrame) -> pd.Series:
    return (ind["close"] > ind["sma200"]) & (ind["sma50"] > ind["sma200"])


def market_filters(btc: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(Bitcoin yükseliş trendinde mi, Bitcoin'in 90 günlük getirisi)"""
    return uptrend(btc), btc["close"] / btc["close"].shift(90) - 1


def entry_signals(ind: pd.DataFrame, p: SetupParams, btc: pd.DataFrame | None = None) -> pd.Series:
    """Gün sonunda kurulum oluştu mu? (True olan günün ertesi açılışında alınır)"""
    rsi = ind["rsi"]
    dipped = (rsi < p.rsi_entry).astype(int).rolling(p.lookback, min_periods=1).max().astype(bool)
    sig = dipped
    if p.confirm:
        sig = sig & (rsi > rsi.shift(1)) & (ind["close"] > ind["close"].shift(1))
    if p.regime != "none":
        sig = sig & uptrend(ind)
    if p.regime in ("coin+btc", "coin+btc+rs") and btc is not None:
        btc_up, btc_ret90 = market_filters(btc)
        sig = sig & btc_up.reindex(ind.index).fillna(False).astype(bool)
        if p.regime == "coin+btc+rs":
            coin_ret90 = ind["close"] / ind["close"].shift(90) - 1
            sig = sig & (coin_ret90 > btc_ret90.reindex(ind.index)).fillna(False)
    return (sig & ind["atr"].notna() & ind["sma200"].notna()).fillna(False)


def simulate(ind: pd.DataFrame, entries: pd.Series, p: SetupParams, fee: float = 0.001,
             slippage: float = 0.0005, start: pd.Timestamp | None = None,
             end: pd.Timestamp | None = None) -> list[Trade]:
    """İşlemleri sırayla simüle eder (aynı anda tek pozisyon). Yalnızca girişi [start, end) içindekiler sayılır."""
    o, h, l, c = (ind[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = ind["atr"].to_numpy(float)
    idx = ind.index
    sig = entries.to_numpy(bool)
    n = len(ind)
    trades: list[Trade] = []
    i = 1
    while i < n:
        if not sig[i - 1]:
            i += 1
            continue
        if (start is not None and idx[i] < start) or (end is not None and idx[i] >= end):
            i += 1
            continue
        entry = o[i] * (1 + slippage)
        tp = entry + p.tp_atr * atr[i - 1]
        sl = entry - p.sl_atr * atr[i - 1]
        last = min(i + p.max_hold - 1, n - 1)
        lo, hi = l[i:last + 1], h[i:last + 1]
        hit_sl = np.flatnonzero(lo <= sl)
        hit_tp = np.flatnonzero(hi >= tp)
        k_sl = hit_sl[0] if hit_sl.size else None
        k_tp = hit_tp[0] if hit_tp.size else None
        stop_px = sl
        if p.breakeven:
            hit_be = np.flatnonzero(hi >= entry + p.breakeven * (tp - entry))
            if hit_be.size:
                kb = hit_be[0]  # başabaş stop'u ertesi günden itibaren geçerli
                be_px = entry * (1 + 2 * fee)
                later = np.flatnonzero(lo[kb + 1:] <= be_px)
                if later.size and (k_sl is None or kb + 1 + later[0] < k_sl):
                    k_sl, stop_px = kb + 1 + later[0], be_px
        if k_sl is not None and (k_tp is None or k_sl <= k_tp):
            k, reason = k_sl, "sl" if stop_px == sl else "be"
            px = min(o[i + k], stop_px) if k > 0 else stop_px
        elif k_tp is not None:
            k, reason = k_tp, "tp"
            px = max(o[i + k], tp) if k > 0 else tp
        else:
            if last - i + 1 < p.max_hold:  # veri bitti, işlem henüz kapanmadı
                break
            k, reason, px = last - i, "time", c[last]
        exit_px = px * (1 - slippage)
        ret = exit_px * (1 - fee) / (entry * (1 + fee)) - 1
        trades.append(Trade(idx[i], idx[i + k], entry, exit_px, ret, reason, int(k + 1)))
        i = i + k + 1
    return trades


def stats(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": None, "avg_ret": None, "profit_factor": None,
                "avg_win": None, "avg_loss": None, "avg_days": None}
    r = np.array([t.ret for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    return {
        "trades": int(r.size),
        "win_rate": float((r > 0).mean()),
        "avg_ret": float(r.mean()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.size and losses.sum() < 0 else None,
        "avg_win": float(wins.mean()) if wins.size else None,
        "avg_loss": float(losses.mean()) if losses.size else None,
        "avg_days": float(np.mean([t.days for t in trades])),
    }
