"""Zaman Makinesi: bir işleme girmeden önce olası sonuçları gösterir (coin sürümü).

Fikir: github.com/Jakeschincariol/day-trader-skill → trader-timemachine (MIT). Oradaki 5 dakikalık gün içi
yeniden oynatma, uygulamanın günlük kuralına uyarlandı:

1. Simülasyon. Coinin son 1 yılındaki günlük mumları havuz olur; her mum önceki kapanışa göre dört orana çevrilir
   (açılış, en yüksek, en düşük, kapanış). Bir senaryo bugünkü fiyattan başlar ve her gün havuzdan rastgele bir mum
   çekip fiyata uygular. Her gün sırayla: önce zarar-durdur, sonra kâr alma hedefi (ikisine aynı gün değerse stop
   sayılır), sonra kapanış 20 günlük ortalamanın altındaysa ertesi gün açılışta çıkış. 10.000 senaryo, en fazla
   60 gün. Varsayılan olarak geçmişin ortalama yönü çıkarılır ("trend varsayımı yok"): bir yılda çok yükselmiş bir
   coin, simülasyonda sırf geçmişte yükseldiği için iyi görünmesin. Sonuç bir tahmin değil, coinin kendi
   oynaklığıyla bu planın nasıl davranacağının ölçüsüdür.
2. Geçmiş. Aynı kural bu coinde geçmişte her oluştuğunda gerçekte ne olduğu (cointracker.trend.rule_trades).
3. Karar. Kuralın bu coindeki gerçek geçmişinden (en az 5 işlem varsa), yoksa genel 5 yıllık testten gelir.
   Simülasyon karar vermez, riski anlatır (bkz. verdict).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cointracker.trend import EXIT_MA_DAYS, FEE, MEASURED

REPLAYS = 10_000
HORIZON = 60
LOOKBACK = 365
SEED = 19


def build_pool(df: pd.DataFrame, lookback: int = LOOKBACK, neutral: bool = True) -> np.ndarray:
    """Son `lookback` günün mumları → (n, 4) oran dizisi: açılış, yüksek, düşük, kapanış / önceki kapanış."""
    d = df[["open", "high", "low", "close"]].astype(float).iloc[-(lookback + 1):]
    prev = d["close"].shift(1)
    ratios = (d[["open", "high", "low", "close"]].div(prev, axis=0)).iloc[1:].dropna()
    ratios = ratios[(ratios > 0).all(axis=1)]
    pool = ratios.to_numpy()
    if neutral and len(pool):
        drift = np.exp(np.log(pool[:, 3]).mean())  # günlük ortalama yön
        pool = pool / drift
    return pool


def simulate(df: pd.DataFrame, entry: float, stop: float, target: float | None = None,
             replays: int = REPLAYS, horizon: int = HORIZON, seed: int = SEED, lookback: int = LOOKBACK,
             neutral: bool = True, fee: float = FEE) -> dict | None:
    """Bugünkü fiyattan (entry) başlayan `replays` senaryo. Getiriler masraf dahil, kaldıraçsız."""
    pool = build_pool(df, lookback, neutral)
    closes = df["close"].astype(float).to_numpy()
    if len(pool) < 60 or len(closes) < EXIT_MA_DAYS or not (0 < stop < entry):
        return None
    rng = np.random.default_rng(seed)
    risk = entry - stop
    n = replays
    picks = rng.integers(0, len(pool), size=(n, horizon + 1))
    # 20G ortalama: son 19 gerçek kapanış + simülasyondaki kapanışlar (bugünkü fiyat girişte kapanış sayılır)
    window = np.tile(np.append(closes[-(EXIT_MA_DAYS - 1):], entry), (n, 1))  # 19 gerçek kapanış + giriş
    wsum = window.sum(axis=1)
    buf = window.copy()  # dairesel tampon
    pos = 0
    price = np.full(n, entry)
    alive = np.ones(n, bool)
    pending = np.zeros(n, bool)  # dün kapanış 20G altındaydı → bugün açılışta çık
    exit_px = np.full(n, np.nan)
    exit_day = np.full(n, horizon)
    reason = np.full(n, 3, dtype=np.int8)  # 0 hedef, 1 stop, 2 20G ort. altı, 3 süre doldu (açık)
    first_hit = np.full(n, -1, dtype=np.int8)  # 0 hedef önce, 1 stop önce (20G çıkışı fark etmeksizin)
    path_close = np.empty((n, horizon))
    for t in range(horizon):
        r = pool[picks[:, t]]
        o, h, lo, c = (price * r[:, k] for k in range(4))
        # dünkü 20G sinyali: bugün açılıştan çık
        go = alive & pending
        exit_px[go], exit_day[go], reason[go] = o[go], t + 1, 2
        alive &= ~go
        # stop (açılışta boşluk dahil), sonra hedef
        hit_stop = alive & (lo <= stop)
        exit_px[hit_stop] = np.minimum(stop, o[hit_stop])
        exit_day[hit_stop], reason[hit_stop] = t + 1, 1
        first_hit[hit_stop & (first_hit < 0)] = 1
        alive &= ~hit_stop
        if target:
            hit_tgt = alive & (h >= target)
            exit_px[hit_tgt] = np.maximum(target, o[hit_tgt])
            exit_day[hit_tgt], reason[hit_tgt] = t + 1, 0
            first_hit[hit_tgt & (first_hit < 0)] = 0
            alive &= ~hit_tgt
        # 20G ortalama ve kapanış kuralı
        wsum += c - buf[:, pos]
        buf[:, pos] = c
        pos = (pos + 1) % EXIT_MA_DAYS
        sma = wsum / EXIT_MA_DAYS
        pending = alive & (c < sma)
        path_close[:, t] = c
        price = c
    still = np.isnan(exit_px)
    exit_px[still] = price[still]
    ret = exit_px * (1 - fee) / (entry * (1 + fee)) - 1
    rmult = (exit_px - entry) / risk
    hits = (first_hit >= 0).sum()
    tgt_first = float((first_hit == 0).sum() / hits) if hits else None
    target_r = (target - entry) / risk if target else None

    def q(a, p):
        return float(np.percentile(a, p))

    fan = [{"day": d + 1, "p10": q(path_close[:, d], 10), "p50": q(path_close[:, d], 50), "p90": q(path_close[:, d], 90)}
           for d in range(0, horizon, 5)]
    return {
        "replays": n, "horizon": horizon, "seed": seed, "lookback": min(lookback, len(pool)), "neutral": neutral,
        "entry": entry, "stop": stop, "target": target, "target_r": target_r,
        "p_target": float((reason == 0).mean()), "p_stop": float((reason == 1).mean()),
        "p_ma_exit": float((reason == 2).mean()), "p_open": float((reason == 3).mean()),
        "p_profit": float((ret > 0).mean()), "p_gain20": float((ret >= 0.20).mean()),
        "target_first_of_hits": tgt_first,
        "ret_p10": q(ret, 10), "ret_p50": q(ret, 50), "ret_p90": q(ret, 90), "ret_mean": float(ret.mean()),
        "avg_r": float(rmult.mean()), "days_median": float(np.median(exit_day)),
        "fan": fan,
    }


def _p(v: float, digits: int = 1) -> str:
    return f"{v * 100:+.{digits}f}%".replace(".", ",")


def verdict(mc: dict, hist: dict, general: dict | None = None) -> dict:
    """Karar: kuralın bu coindeki gerçek geçmişi (yeterliyse), yoksa genel 5 yıllık test; risk satırı simülasyondan.

    Simülasyon tek başına karar vermez: trend varsayımı olmadan, 20G ortalama çıkışı zarar eden senaryoları stopa
    varmadan kapattığı için "hedef önce mi stop önce mi" oranı yapısal olarak iyi görünür; bu yüzden yalnızca riski
    (stop olasılığı, kötü senaryo, süre) anlatmak için kullanılır.
    """
    risk = (f"Simülasyon ({mc['replays']:,} senaryo): zarar-durdur %{mc['p_stop'] * 100:.0f}, kâr alma hedefi "
            f"%{mc['p_target'] * 100:.0f}, 20G ortalamayla çıkış %{mc['p_ma_exit'] * 100:.0f} · en kötü %10: {_p(mc['ret_p10'], 0)} "
            f"veya daha kötü · en iyi %10: {_p(mc['ret_p90'], 0)} veya daha iyi · tipik süre {mc['days_median']:.0f} gün."
            ).replace(",", ".", 1)
    if hist.get("trades", 0) >= 5:
        avg, wr, n = hist["avg"], hist["win_rate"], hist["trades"]
        base = (f"Bu coinde kural geçmişte {n} işlem yaptı: işlem başına ort. {_p(avg)}, kazanan %{wr * 100:.0f} "
                f"(en iyi {_p(hist['best'], 0)}, en kötü {_p(hist['worst'], 0)}).")
        if avg > 0.02:
            tone, head = "up", "Geçmişi olumlu"
        elif avg < 0:
            tone, head = "down", "Geçmişi olumsuz"
        else:
            tone, head = "flat", "Geçmişi başabaş"
        note = " Tek coinin geçmişi şansa çok bağlıdır; genel testle birlikte değerlendirin." if n < 15 else ""
        text = f"{head}. {base}{note}"
    else:
        tone = "flat"
        g = general or {}
        text = ("Bu coinde kuralın yeterli geçmişi yok (5 işlemden az). Genel 5 yıllık testte GÜÇLÜ AL işlem başına "
                f"ort. {_p(g.get('train_avg', 0))} / {_p(g.get('test_avg', 0))} (ilk 3 yıl / son 2 yıl), "
                f"kazanan %{g.get('test_win', 0) * 100:.0f}–%{g.get('train_win', 0) * 100:.0f}.")
    return {"tone": tone, "text": text, "risk": risk}


def history(trades: list[dict]) -> dict:
    """Kuralın bu coindeki gerçek geçmişi (kapanmış işlemler)."""
    closed = [t for t in trades if not t.get("open")]
    if not closed:
        return {"trades": 0}
    r = np.array([t["ret"] for t in closed])
    return {"trades": len(r), "wins": int((r > 0).sum()), "win_rate": float((r > 0).mean()),
            "avg": float(r.mean()), "median": float(np.median(r)), "best": float(r.max()), "worst": float(r.min()),
            "stopped": sum(1 for t in closed if t.get("reason") == "zarar-durdur")}


def time_machine(df: pd.DataFrame, plan: dict, trades: list[dict], **kw) -> dict | None:
    """Uygulamanın planı için: giriş = bugünkü fiyat, stop = plan stopu, hedef = ilk kâr alma yeri (yoksa 2R)."""
    entry, stop = plan.get("price"), plan.get("stop")
    if not entry or not stop or not (0 < stop < entry):
        return None
    atr = plan.get("atr") or 0
    res = [r["price"] for r in plan.get("resistances") or [] if r["price"] >= entry + atr]
    target = res[0] if res else entry + 2 * (entry - stop)
    mc = simulate(df, entry, stop, target, **kw)
    if mc is None:
        return None
    mc["target_source"] = "ilk direnç, kâr alma yeri 1" if res else "2R, riskin 2 katı"
    hist = history(trades)
    return {"mc": mc, "verdict": verdict(mc, hist, MEASURED.get("filters", {}).get("strong")), "history": hist}
