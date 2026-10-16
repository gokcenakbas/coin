"""Short (açığa satış) kuralı araştırması: uygulamanın alım kuralının aynası işe yarıyor mu?

Short sinyali (alım kuralının tersi, sinyal günün kapanışında):
  * coin 200 günlük ortalamanın ALTINDA, 50G ortalama 200G'nin altında
  * Bitcoin 200 günlük ortalamanın altında (piyasa düşüşte) — ayrıca "piyasa fark etmez" çeşidi de denenir
  * kapanış son 20 günün EN DÜŞÜĞÜ (aşağı kırılım; ilk gün)
  * coin çok oynak değil (ATR / fiyat < %7,7)
Giriş: ertesi gün açılış. Zarar-durdur: giriş + 2 ATR (gün içinde değerse). Kapanış: günlük kapanış 20G ortalamanın
ÜSTÜNE çıkarsa ertesi gün açılış. Masraf: her yön %0,15 + vadeli fonlama ücreti günde ~%0,03 (varsayım; düşüş
dönemlerinde fonlama çoğu zaman shortun lehine olduğu için temkinli bir tahmin). Getiriler kaldıraçsızdır.
Karşılaştırma: aynı çıkış kuralıyla rastgele günlerde açılan shortlar.
Eğitim ilk 3 yıl, test son 2 yıl. Kullanım: python tools/research/short.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import optimize  # noqa: E402
from optimize import load  # noqa: E402

from cointracker.config import DEFAULT_CONFIG, _deep_merge  # noqa: E402

FEE = 0.0015
FUNDING_PER_DAY = 0.0003
STOP_ATR = 2.0
MAX_ATR_PCT = 0.077


def prepare(df: pd.DataFrame, btc_down: pd.Series) -> pd.DataFrame:
    d = df.copy()
    c = d["close"]
    d["sma20"] = c.rolling(20).mean()
    d["atr_pct"] = d["atr"] / c
    down = btc_down.reindex(d.index).fillna(False).astype(bool)
    downtrend = (c < d["sma200"]) & (d["sma50"] < d["sma200"])
    low20 = c <= c.rolling(20).min()
    calm = d["atr_pct"] < MAX_ATR_PCT
    brk = downtrend & low20
    first = brk & ~brk.shift(1, fill_value=False)
    d["sig_btc"] = first & down & calm
    d["sig_any"] = first & calm
    d["sig_any_vol"] = first  # oynaklık filtresiz
    return d


def short_trades(d: pd.DataFrame, sig: np.ndarray, start) -> list[dict]:
    o, h, c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "close"))
    atr, sma20 = d["atr"].to_numpy(dtype=float), d["sma20"].to_numpy(dtype=float)
    n, i, out = len(d), 0, []
    while i < n - 1:
        if not sig[i] or d.index[i] < start or not np.isfinite(atr[i]):
            i += 1
            continue
        entry = o[i + 1]
        stop = entry + STOP_ATR * atr[i]
        exit_px = exit_j = None
        for j in range(i + 1, n):
            if h[j] >= stop:
                exit_px, exit_j = max(stop, o[j]), j
                break
            if c[j] > sma20[j]:
                if j + 1 < n:
                    exit_px, exit_j = o[j + 1], j + 1
                break
        if exit_px is None:
            break
        days = exit_j - i
        ret = (entry * (1 - FEE) - exit_px * (1 + FEE)) / entry - FUNDING_PER_DAY * days
        out.append({"date": d.index[i], "ret": ret, "days": days, "stopped": exit_px >= stop})
        i = exit_j
    return out


def stats(r: pd.DataFrame) -> str:
    if r.empty:
        return f"{'işlem yok':>48}"
    x = r["ret"]
    gains, losses = x[x > 0].sum(), -x[x <= 0].sum()
    pf = gains / losses if losses > 0 else np.inf
    return (f"{len(x):>4} işl. başarı %{(x > 0).mean() * 100:3.0f} ort. {x.mean() * 100:+5.1f}% "
            f"medyan {x.median() * 100:+5.1f}% PF {pf:4.2f} en kötü {x.min() * 100:+4.0f}% süre {r['days'].mean():3.0f}g")


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    optimize.TOP_N = 80
    frames = load(cfg)
    btc = frames["BTCUSDT"]
    btc_down = btc["close"] < btc["sma200"]
    end = max(df.index[-1] for df in frames.values())
    split, start = end - pd.Timedelta(days=730), end - pd.Timedelta(days=1826)
    print(f"{len(frames)} coin, {start:%Y-%m-%d} → {end:%Y-%m-%d}, test başlangıcı {split:%Y-%m-%d}")
    print(f"BTC 200G altında geçen gün oranı: eğitim %{btc_down.loc[start:split].mean() * 100:.0f}, "
          f"test %{btc_down.loc[split:].mean() * 100:.0f}\n")

    rng = np.random.default_rng(5)
    rows = {"Short — BTC düşüşteyken (ana kural)": [], "Short — piyasa fark etmez": [],
            "Short — oynaklık filtresi yok": [], "Rastgele short (aynı çıkış)": [],
            "Rastgele short, BTC düşüşteyken": []}
    for sym, df in frames.items():
        if sym == "BTCUSDT":
            continue
        d = prepare(df, btc_down)
        rows["Short — BTC düşüşteyken (ana kural)"] += short_trades(d, d["sig_btc"].to_numpy(), start)
        rows["Short — piyasa fark etmez"] += short_trades(d, d["sig_any"].to_numpy(), start)
        rows["Short — oynaklık filtresi yok"] += short_trades(d, d["sig_any_vol"].to_numpy(), start)
        rnd = (rng.random(len(d)) < 0.05) & d["sma200"].notna().to_numpy()
        rows["Rastgele short (aynı çıkış)"] += short_trades(d, rnd, start)
        rnd_down = rnd & btc_down.reindex(d.index).fillna(False).to_numpy()
        rows["Rastgele short, BTC düşüşteyken"] += short_trades(d, rnd_down, start)

    for name, tr in rows.items():
        t = pd.DataFrame(tr)
        if t.empty:
            print(f"{name:>38}: işlem yok")
            continue
        print(f"{name:>38}: EĞİTİM {stats(t[t['date'] < split])}")
        print(f"{'':>38}  TEST   {stats(t[t['date'] >= split])}")
        if name.startswith("Short — BTC"):
            by = t.groupby(t["date"].dt.year)["ret"].agg(["count", "mean"])
            print(f"{'':>38}  yıllar: " + "  ".join(f"{y}: {m * 100:+.1f}% ({int(c)})" for y, (c, m) in by.iterrows()))
    print("\nNot: getiriler kaldıraçsız; 2x kaldıraçta kâr ve zarar yaklaşık iki katı, tasfiye riski ayrıca vardır.")


if __name__ == "__main__":
    main()
