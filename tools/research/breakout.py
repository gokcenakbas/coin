"""Trend kırılımı stratejisinin gerçekçi portföy testi (yeni GÜÇLÜ AL adayı).

Alım: coin 200G ortalamanın üstünde, 50G > 200G, BTC 200G üstünde ve kapanış son 20 günün en yükseği
      (sinyalin ilk günü). İşlem ertesi gün açılışında.
Satış: kapanış 20 günlük ortalamanın altına inerse (ya da iz süren 3 ATR) ertesi açılışta;
       başlangıç zarar-durdur: giriş − 2 ATR (gün içinde değerse o seviyeden).
Portföy: sermaye en fazla N eşit dilime bölünür; dilim boşsa yeni sinyal alınır (aynı gün birden fazla
       sinyal varsa 20 günlük getirisi en yüksek olan önce), boş dilimler nakitte bekler.
Komisyon+kayma her yön %0,15. Eğitim ilk 3 yıl, test son 2 yıl; ayrıca yıllara göre.

Kullanım: python tools/research/breakout.py
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

FEE = 0.0015


def prepare(frames):
    btc = frames["BTCUSDT"]
    btc_up = btc["close"] > btc["sma200"]
    out = {}
    for sym, df in frames.items():
        d = df.copy()
        up = btc_up.reindex(d.index).fillna(False).astype(bool)
        trend = (d["close"] > d["sma200"]) & (d["sma50"] > d["sma200"]) & up
        brk = trend & (d["close"] >= d["close"].rolling(20).max())
        d["entry_sig"] = brk & ~brk.shift(1, fill_value=False)
        d["sma20"] = d["close"].rolling(20).mean()
        d["mom20"] = d["close"] / d["close"].shift(20) - 1
        out[sym] = d
    return out


def portfolio(data, start, end, slots=5, exit_kind="sma20"):
    dates = sorted(set().union(*[d.loc[start:end].index for d in data.values()]))
    cash, equity_curve, trades = 1.0, [], []
    pos = {}  # sym -> dict(qty, entry, stop, peak)
    pending_buys, pending_sells = [], []
    for t in dates:
        # 1) açılışta bekleyen emirler
        for sym in pending_sells:
            if sym in pos and t in data[sym].index:
                p = pos.pop(sym)
                px = data[sym].at[t, "open"] * (1 - FEE)
                cash += p["qty"] * px
                trades.append(px / p["cost_px"] - 1)
        pending_sells = []
        for sym in pending_buys:
            if len(pos) >= slots or sym in pos or t not in data[sym].index:
                continue
            d = data[sym]
            eq = cash + sum(q["qty"] * data[s].at[t, "open"] for s, q in pos.items() if t in data[s].index)
            size = min(cash, eq / slots)
            if size <= 0:
                continue
            px = d.at[t, "open"]
            cost_px = px * (1 + FEE)
            atr = d["atr"].shift(1).at[t]
            pos[sym] = {"qty": size / cost_px, "cost_px": cost_px, "stop": px - 2 * atr, "peak": px}
            cash -= size
        pending_buys = []
        # 2) gün içi stop, kapanışta çıkış kontrolü
        for sym in list(pos):
            d = data[sym]
            if t not in d.index:
                continue
            p, row = pos[sym], d.loc[t]
            if row["low"] <= p["stop"]:
                px = min(p["stop"], row["open"]) * (1 - FEE)
                cash += p["qty"] * px
                trades.append(px / p["cost_px"] - 1)
                pos.pop(sym)
                continue
            p["peak"] = max(p["peak"], row["close"])
            if exit_kind == "sma20":
                hit = row["close"] < row["sma20"]
            else:
                hit = row["close"] < p["peak"] - 3 * row["atr"]
            if hit:
                pending_sells.append(sym)
        # 3) yeni sinyaller (kapanışta), ertesi açılışta alınacak
        cands = [(data[s].at[t, "mom20"], s) for s in data if s != "BTCUSDT" and t in data[s].index
                 and data[s].at[t, "entry_sig"] and s not in pos]
        pending_buys = [s for _, s in sorted(cands, reverse=True)]
        eq = cash + sum(q["qty"] * data[s].at[t, "close"] for s, q in pos.items() if t in data[s].index)
        equity_curve.append((t, eq))
    eq = pd.Series(dict(equity_curve))
    return eq, np.array(trades)


def summarize(eq, trades):
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    win = (trades > 0).mean() if len(trades) else np.nan
    return total, cagr, dd, len(trades), win


def hold(series, start, end, fee=True):
    s = series.loc[start:end]
    return s / s.iloc[0]


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    frames = load(cfg)
    data = prepare(frames)
    btc = frames["BTCUSDT"]
    end = max(df.index[-1] for df in frames.values())
    split, start = end - pd.Timedelta(days=730), end - pd.Timedelta(days=1826)
    periods = {"Eğitim (ilk 3 yıl)": (start, split), "Test (son 2 yıl)": (split, end)}
    for y in range(start.year + 1, end.year + 1):
        periods[str(y)] = (max(start, pd.Timestamp(f"{y}-01-01")), min(end, pd.Timestamp(f"{y}-12-31")))

    print(f"{len(frames)} coin. Satır: toplam getiri / yıllık / en büyük düşüş / işlem / başarı\n")
    p = lambda x: f"{x * 100:+7.1f}%"
    for name, (a, b) in periods.items():
        print(f"== {name}: {a:%Y-%m-%d} → {b:%Y-%m-%d} ==")
        bh = hold(btc["close"], a, b)
        t, c, dd, *_ = summarize(bh, np.array([]))
        print(f"  {'BTC al-tut':>40}: {p(t)} {p(c)} {p(dd)}")
        btc_f = btc.loc[a:b]
        r = btc_f["close"].pct_change().fillna(0)
        posn = (btc["close"] > btc["sma200"]).shift(1).reindex(btc_f.index).fillna(False).astype(float)
        eqf = (1 + posn * r - posn.diff().abs().fillna(0) * FEE).cumprod()
        t, c, dd, *_ = summarize(eqf, np.array([]))
        print(f"  {'BTC 200G filtresi':>40}: {p(t)} {p(c)} {p(dd)}")
        for slots in (3, 5, 10):
            for ek, en in (("sma20", "20G ort. altı"), ("trail", "iz süren 3 ATR")):
                eq, tr = portfolio(data, a, b, slots, ek)
                t, c, dd, n, w = summarize(eq, tr)
                print(f"  {f'Kırılım, {slots} dilim, satış: {en}':>40}: {p(t)} {p(c)} {p(dd)} {n:5d} {w * 100:5.1f}%")
        print()


if __name__ == "__main__":
    main()
