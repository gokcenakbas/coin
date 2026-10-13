"""4 saatlik (ve günlük) destekler rastgele bir seviyeden daha iyi tutuyor mu?

Aynı algoritma (cointracker/levels.support_resistance: dönüş noktaları, yakın olanlar birleştirilir) iki zaman diliminde:
  4 saatlik : son 90 günün 4 saatlik mumları (540 mum), son 2 yıl test edilir (ilk yıl / ikinci yıl)
  günlük    : son 1 yılın günlük mumları (uygulamanın bugünkü hali), son 2 yıl test edilir

Olay: kapanış en yakın desteğin 0,5–2,5 ATR üstünde ve son 5 mumda düşüyor ("desteğe yaklaşıyor").
Karşılaştırma ("rastgele seviye"): yine düşen mumlarda, fiyatın altına gerçek desteklerle aynı uzaklık dağılımından
rastgele bir seviye konur (yakınında gerçek bir destek olmamak şartıyla). Destekler gerçekten bir şey ifade ediyorsa, gerçek desteklerin
tutma oranı rastgele seviyeninkinden belirgin şekilde yüksek olmalı.

Ölçüler (10 mum içinde seviyeye + 0,25 ATR'ye değerse):
  tuttu    : önce seviye + 1 ATR üstünde kapanış (seviye − 1 ATR altında kapanıştan önce)
  işlem    : seviye + 0,25 ATR'den limit alış, stop seviye − 1 ATR, hedef ilk direnç (alışın ≥ 1 ATR üstü),
             en fazla 30 mum; komisyon+kayma her yön %0,15
Kullanım: python tools/research/sr4h.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import optimize  # noqa: E402
from optimize import load  # noqa: E402

from cointracker.config import DEFAULT_CONFIG, _deep_merge  # noqa: E402
from cointracker.data import BinanceClient  # noqa: E402
from cointracker.levels import support_resistance  # noqa: E402

FEE = 0.0015
WAIT, HOLD_BARS, MAX_HOLD, COOLDOWN = 10, 30, 30, 10
COINS = 40


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def outcome(o, h, low, c, i, level, a, res_prices):
    """Seviyeye 10 mum içinde değdi mi, tuttu mu, işlem sonucu."""
    n = len(c)
    limit, stop = level + 0.25 * a, level - a
    for j in range(i + 1, min(i + 1 + WAIT, n)):
        if low[j] > limit:
            continue
        held = None
        for k in range(j, min(j + HOLD_BARS, n)):
            if c[k] < level - a:
                held = False
                break
            if c[k] > level + a:
                held = True
                break
        entry = min(o[j], limit)
        tgt = next((r for r in res_prices if r >= entry + a), None)
        ret = None
        for k in range(j, min(j + MAX_HOLD, n)):
            if low[k] <= stop:
                px = stop if k == j else min(stop, o[k])
                ret = px * (1 - FEE) / (entry * (1 + FEE)) - 1
                break
            if tgt and h[k] >= tgt:
                px = tgt if k == j else max(tgt, o[k])
                ret = px * (1 - FEE) / (entry * (1 + FEE)) - 1
                break
        else:
            k = min(j + MAX_HOLD - 1, n - 1)
            ret = c[k] * (1 - FEE) / (entry * (1 + FEE)) - 1
        return True, held, ret
    return False, None, None


def scan(df: pd.DataFrame, lookback: int, start, rng, pool: list) -> tuple[list, list]:
    """pool: şimdiye kadarki gerçek olayların uzaklıkları; rastgele seviye aynı dağılımdan seçilir."""
    o, h, low, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    at = atr(df).to_numpy()
    real, fake = [], []
    last_real = last_fake = -COOLDOWN
    for i in range(max(lookback // 3, 60), len(df) - 1):
        if df.index[i] < start or not np.isfinite(at[i]) or c[i] >= c[i - 5]:
            continue
        a = at[i]
        sup, res = support_resistance(df.iloc[: i + 1], a, lookback=lookback, limit=5, gap_atr=0)
        res_prices = [r["price"] for r in res]
        sup_prices = [s["price"] for s in sup]
        meta = {"date": df.index[i]}
        if sup and i - last_real >= COOLDOWN:
            d = (c[i] - sup[0]["price"]) / a
            if 0.5 <= d <= 2.5:
                last_real = i
                pool.append(d)
                hit, held, ret = outcome(o, h, low, c, i, sup[0]["price"], a, res_prices)
                real.append({**meta, "dist": d, "touches": sup[0]["touches"], "hit": hit, "held": held, "ret": ret})
        if i - last_fake >= COOLDOWN and rng.random() < 0.5:
            d = float(rng.choice(pool)) if pool else rng.uniform(0.5, 2.5)
            level = c[i] - d * a
            if all(abs(level - s) > 0.75 * a for s in sup_prices):
                last_fake = i
                hit, held, ret = outcome(o, h, low, c, i, level, a, res_prices)
                fake.append({**meta, "dist": d, "touches": 0, "hit": hit, "held": held, "ret": ret})
    return real, fake


def summary(ev: pd.DataFrame) -> str:
    if ev.empty:
        return "olay yok"
    hit = ev[ev["hit"]]
    held = hit["held"].dropna().astype(bool)
    r = hit["ret"].dropna()
    gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
    pf = gains / losses if losses > 0 else np.inf
    return (f"{len(ev):>5} olay, değdi %{ev['hit'].mean() * 100:3.0f}, tuttu %{held.mean() * 100:3.0f} (n={len(held)}) | "
            f"işlem: başarı %{(r > 0).mean() * 100:3.0f}, ort. {r.mean() * 100:+5.2f}%, PF {pf:4.2f}")


def report(name: str, real: pd.DataFrame, fake: pd.DataFrame, split) -> None:
    print(f"== {name} ==")
    for label, part in (("1. dönem", lambda d: d[d["date"] < split]), ("2. dönem", lambda d: d[d["date"] >= split])):
        print(f"  {label}  gerçek destek : {summary(part(real))}")
        print(f"  {label}  rastgele seviye: {summary(part(fake))}")
    strong = real[real["touches"] >= 3]
    print(f"  Güçlü destekler (≥3 dokunma), tüm dönem: {summary(strong)}")
    print()


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    optimize.TOP_N = COINS
    frames = load(cfg)
    client = BinanceClient()
    now = datetime.now(timezone.utc)
    start2y = pd.Timestamp(now - timedelta(days=730)).tz_localize(None)
    split = pd.Timestamp(now - timedelta(days=365)).tz_localize(None)
    rng = np.random.default_rng(11)
    r4, f4, rd, fd = [], [], [], []
    pool4: list[float] = []
    poold: list[float] = []
    for sym, daily in frames.items():
        try:
            h4 = client.klines(sym, now - timedelta(days=730 + 100), now, interval="4h")
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym}: 4 saatlik veri alınamadı ({exc})")
            continue
        if len(h4) < 1000:
            continue
        a, b = scan(h4.iloc[:-1], 540, start2y, rng, pool4)
        r4 += a
        f4 += b
        a, b = scan(daily, 365, start2y, rng, poold)
        rd += a
        fd += b
    print(f"{len(frames)} coin, son 2 yıl ({start2y:%Y-%m-%d} → bugün), 2. dönem başlangıcı {split:%Y-%m-%d}\n")
    report("4 saatlik destekler (son 90 gün)", pd.DataFrame(r4), pd.DataFrame(f4), split)
    report("Günlük destekler (son 1 yıl, uygulamadaki)", pd.DataFrame(rd), pd.DataFrame(fd), split)


if __name__ == "__main__":
    main()
