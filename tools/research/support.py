"""Desteğe yaklaşan coinler araştırması: "desteğe yakın" uyarısı işe yarar mı?

Her gün (yalnızca o güne kadarki veriyle) son 1 yılın destekleri bulunur (cointracker.levels.support_resistance;
dönüş noktaları kümelenir, dokunma sayısı = güç). Olay: kapanış en yakın desteğin 0,5–2,5 ATR üstünde ve son
5 günde düşüyor ("desteğe yaklaşıyor"). Aynı coinde 10 gün içinde tekrar sayılmaz.

Ölçülenler:
  * Desteğe 10 gün içinde değme oranı (gün içi dip ≤ destek + 0,25 ATR)
  * Değdiyse tutma oranı: önce destek + 1 ATR'nin üstüne kapanış mı, yoksa destek − 1 ATR'nin altına kapanış mı
  * İşlem: destek + 0,25 ATR'ye limit alış (10 gün geçerli), zarar-durdur destek − 1 ATR,
    satış ilk dirençte (alışın ≥ 1 ATR üstü) ya da en fazla 30 gün
  * Karşılaştırma: rastgele günlerde ertesi açılışta alış, aynı ATR mesafeli stop ve aynı direnç hedefi
Kırılımlar: piyasa (BTC 200G üstü/altı), coin 200G üstü/altı, destek gücü (dokunma sayısı).
Komisyon+kayma her yön %0,15. Eğitim ilk 3 yıl, test son 2 yıl.
Kullanım: python tools/research/support.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from optimize import load  # noqa: E402

from cointracker.config import DEFAULT_CONFIG, _deep_merge  # noqa: E402
from cointracker.levels import support_resistance  # noqa: E402

FEE = 0.0015
NEAR_MIN, NEAR_MAX = 0.5, 2.5  # ATR
WAIT = 10
MAX_HOLD = 30
COOLDOWN = 10


def trade(o, h, low, c, j0, entry, stop, target):
    """j0 gününde entry fiyatından alındı. (getiri, sonuç) döner; sonuçlanmadıysa None."""
    n = len(c)
    for j in range(j0, min(j0 + MAX_HOLD, n)):
        if low[j] <= stop:
            px = min(stop, o[j]) if j > j0 else stop
            return (px * (1 - FEE)) / (entry * (1 + FEE)) - 1, "stop"
        if target and h[j] >= target:
            px = max(target, o[j]) if j > j0 else target
            return (px * (1 - FEE)) / (entry * (1 + FEE)) - 1, "hedef"
    j = j0 + MAX_HOLD - 1
    if j >= n:
        return None
    return (c[j] * (1 - FEE)) / (entry * (1 + FEE)) - 1, "süre"


def scan(sym, df, btc_up, start, rng):
    o, h, low, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    atr = df["atr"].to_numpy(dtype=float)
    sma200 = df["sma200"].to_numpy(dtype=float)
    up = btc_up.reindex(df.index).fillna(False).to_numpy(dtype=bool)
    n = len(df)
    events, rand = [], []
    last_event = -COOLDOWN
    for i in range(max(400, 5), n - 1):
        if df.index[i] < start or not np.isfinite(atr[i]):
            continue
        a = atr[i]
        sup, res = support_resistance(df.iloc[: i + 1], a, limit=5)
        meta = {"date": df.index[i], "sym": sym, "btc_up": bool(up[i]), "above200": bool(c[i] > sma200[i])}
        # rastgele karşılaştırma (günlerin ~%5'i)
        if rng.random() < 0.05:
            entry = o[i + 1]
            tgt = next((r["price"] for r in res if r["price"] >= entry + a), None)
            out = trade(o, h, low, c, i + 1, entry, entry - 1.25 * a, tgt)
            if out:
                rand.append({**meta, "ret": out[0], "how": out[1]})
        if not sup or i - last_event < COOLDOWN:
            continue
        s = sup[0]
        dist = (c[i] - s["price"]) / a
        if not (NEAR_MIN <= dist <= NEAR_MAX and c[i] < c[i - 5]):
            continue
        last_event = i
        limit, stop = s["price"] + 0.25 * a, s["price"] - a
        ev = {**meta, "dist": dist, "touches": s["touches"], "hit": False, "held": None, "ret": None, "how": None}
        for j in range(i + 1, min(i + 1 + WAIT, n)):
            if low[j] <= limit:
                ev["hit"] = True
                entry = min(o[j], limit)
                tgt = next((r["price"] for r in res if r["price"] >= entry + a), None)
                out = trade(o, h, low, c, j, entry, stop, tgt)
                if out:
                    ev["ret"], ev["how"] = out
                for k in range(j, min(j + 30, n)):
                    if c[k] < s["price"] - a:
                        ev["held"] = False
                        break
                    if c[k] > s["price"] + a:
                        ev["held"] = True
                        break
                break
        events.append(ev)
    return events, rand


def stats(r):
    r = r.dropna(subset=["ret"]) if "ret" in r else r
    if r.empty:
        return "-"
    x = r["ret"]
    gains, losses = x[x > 0].sum(), -x[x <= 0].sum()
    pf = gains / losses if losses > 0 else np.inf
    return f"{len(x):>5} işlem, başarı {(x > 0).mean() * 100:4.1f}%, ort. {x.mean() * 100:+5.1f}%, medyan {x.median() * 100:+5.1f}%, PF {pf:4.2f}"


def report(name, ev, rd):
    if ev.empty:
        print(f"  {name}: olay yok")
        return
    hit = ev["hit"].mean()
    held = ev.loc[ev["hit"] & ev["held"].notna(), "held"].astype(bool)
    print(f"  {name}: {len(ev)} olay | 10 günde desteğe değdi {hit * 100:.0f}% | değince tuttu "
          f"{held.mean() * 100:.0f}% (n={len(held)})")
    print(f"      destekte al  : {stats(ev[ev['hit']])}")
    print(f"      rastgele al  : {stats(rd)}")


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    frames = load(cfg)
    btc = frames["BTCUSDT"]
    btc_up = btc["close"] > btc["sma200"]
    end = max(df.index[-1] for df in frames.values())
    split, start = end - pd.Timedelta(days=730), end - pd.Timedelta(days=1826)
    print(f"{len(frames)} coin, {start:%Y-%m-%d} → {end:%Y-%m-%d}, test başlangıcı {split:%Y-%m-%d}\n")
    rng = np.random.default_rng(7)
    evs, rds = [], []
    for sym, df in frames.items():
        e, r = scan(sym, df, btc_up, start, rng)
        evs += e
        rds += r
    ev, rd = pd.DataFrame(evs), pd.DataFrame(rds)

    groups = [
        ("Hepsi", lambda d: d.index == d.index),
        ("BTC 200G üstü", lambda d: d["btc_up"]),
        ("BTC 200G altı", lambda d: ~d["btc_up"]),
        ("BTC ve coin 200G üstü", lambda d: d["btc_up"] & d["above200"]),
        ("Coin 200G altı", lambda d: ~d["above200"]),
    ]
    for period, mask_e, mask_r in [("EĞİTİM", ev["date"] < split, rd["date"] < split),
                                   ("TEST", ev["date"] >= split, rd["date"] >= split)]:
        print(f"=== {period} ===")
        e, r = ev[mask_e], rd[mask_r]
        for name, f in groups:
            report(name, e[f(e)], r[f(r)])
        for lo, hi, name in [(1, 2, "zayıf destek (1-2 dokunma)"), (3, 4, "orta (3-4)"), (5, 999, "güçlü (5+)")]:
            sub = e[(e["touches"] >= lo) & (e["touches"] <= hi)]
            report(name, sub, r.iloc[0:0])
        for lo, hi in [(0.5, 1.0), (1.0, 1.75), (1.75, 2.5)]:
            sub = e[(e["dist"] >= lo) & (e["dist"] < hi)]
            print(f"  uzaklık {lo}-{hi} ATR: {len(sub)} olay, 10 günde değdi {sub['hit'].mean() * 100:.0f}%")
        print()


if __name__ == "__main__":
    main()
