"""GÜÇLÜ AL incelemesi: uygulamanın sinyalleriyle alınan coinlerde ne oldu ve ne işe yarardı?

Son 5 yıl, en büyük 40 coin, günlük mumlar. Sinyal günün KAPANIŞINDA belli olur; giriş ertesi günün AÇILIŞI.

1) Seviye bazında ileri getiriler (GÜÇLÜ AL, AL, BEKLE, SAT, GÜÇLÜ SAT ve "rastgele gün"):
   1/7/30/90 gün sonra yükselme oranı, ortalama ve medyan getiri, aynı sürede BTC'ye göre fark.
2) Uygulamadaki plana uyulsaydı (GÜÇLÜ AL'a geçilen gün al, plandaki zarar-durdur / hedef 1 / 60 gün):
   işlem başarısı, ortalama sonuç, en kötü sonuç.
3) GÜÇLÜ AL günlerinin tipik yapısı (hangi bileşenler puan veriyor) ve BTC rejimine göre dağılım.
4) Parametre aranmayan, bilinen basit kurallar (eğitim: ilk 3 yıl, test: son 2 yıl):
   BTC al-tut, coin sepeti al-tut, BTC 200G trend filtresi, coin bazında trend filtresi.

Kullanım: python tools/research/strongbuy.py
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
from cointracker.levels import trade_plan  # noqa: E402
from cointracker.signals import COMPONENTS, market_regime  # noqa: E402

FEE = 0.001 + 0.0005  # komisyon + kayma, her yön
LEVELS = ["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"]
LABEL = {"STRONG_BUY": "GÜÇLÜ AL", "BUY": "AL", "HOLD": "BEKLE", "SELL": "SAT", "STRONG_SELL": "GÜÇLÜ SAT"}


def f(x, signed=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x * 100:+6.1f}%" if signed else f"{x * 100:5.1f}%"


def forward_table(frames, btc, start, end):
    rows = []
    bopen = btc["open"]
    for sym, df in frames.items():
        if sym == "BTCUSDT":
            continue
        part = df.loc[start:end]
        if part.empty:
            continue
        entry = df["open"].shift(-1).reindex(part.index)  # ertesi gün açılış
        row = {"level": part["level"], "sym": sym}
        for h in (1, 7, 30, 90):
            exit_ = df["close"].shift(-h).reindex(part.index)
            ret = exit_ / entry - 1
            b_entry = bopen.shift(-1).reindex(part.index)
            b_ret = btc["close"].shift(-h).reindex(part.index) / b_entry - 1
            row[f"r{h}"] = ret
            row[f"x{h}"] = ret - b_ret
        rows.append(pd.DataFrame(row, index=part.index))
    return pd.concat(rows)


def print_forward(data, title):
    print(f"== {title} ==")
    print(f"{'seviye':>10} {'gün':>7} | " + " | ".join(f"{h:>3}g: yüks. ort. medyan BTC'ye göre" for h in (7, 30, 90)))
    groups = [(LABEL[l], data[data["level"] == l]) for l in LEVELS] + [("rastgele", data)]
    for name, d in groups:
        cells = []
        for h in (7, 30, 90):
            r, x = d[f"r{h}"].dropna(), d[f"x{h}"].dropna()
            if r.empty:
                cells.append(f"{'-':>33}")
                continue
            cells.append(f"{f((r > 0).mean()):>8} {f(r.mean(), True):>7} {f(r.median(), True):>7} {f(x.mean(), True):>9}")
        print(f"{name:>10} {len(d):>7} | " + " | ".join(cells))
    print()


def follow_plan(frames, cfg, start, end, max_days=60):
    """GÜÇLÜ AL'a geçilen gün plan hesaplanır, ertesi gün açılışta alınır; stop/hedef1/60 gün."""
    trades = []
    for sym, df in frames.items():
        if sym == "BTCUSDT":
            continue
        lvl = df["level"]
        events = df.index[(lvl == "STRONG_BUY") & (lvl.shift(1) != "STRONG_BUY")]
        events = [t for t in events if start <= t <= end]
        for t in events:
            i = df.index.get_loc(t)
            if i + 2 >= len(df):
                continue
            plan = trade_plan(df.iloc[: i + 1], cfg)
            if not plan:
                continue
            stop, target = plan["stop"], plan["target1"]
            entry = df["open"].iloc[i + 1]
            if not (stop < entry < target):
                continue
            exit_px, reason = None, "süre"
            for j in range(i + 1, min(i + 1 + max_days, len(df))):
                bar = df.iloc[j]
                if bar["low"] <= stop:  # aynı gün ikisi de olursa önce stop varsayılır
                    exit_px, reason = min(stop, bar["open"]), "stop"
                    break
                if bar["high"] >= target:
                    exit_px, reason = max(target, bar["open"]), "hedef"
                    break
            if exit_px is None:
                if i + max_days >= len(df):
                    continue  # henüz sonuçlanmadı
                exit_px = df["close"].iloc[i + max_days]
            ret = (exit_px * (1 - FEE)) / (entry * (1 + FEE)) - 1
            trades.append({"sym": sym, "date": t, "ret": ret, "reason": reason,
                           "stop_dist": stop / entry - 1, "target_dist": target / entry - 1})
    return pd.DataFrame(trades)


def print_plan(tr, title):
    print(f"== {title} ==")
    if tr.empty:
        print("  işlem yok\n")
        return
    wins = tr["ret"] > 0
    print(f"  {len(tr)} işlem | başarı {f(wins.mean())} | ortalama {f(tr['ret'].mean(), True)} | "
          f"medyan {f(tr['ret'].median(), True)} | en kötü {f(tr['ret'].min(), True)} | en iyi {f(tr['ret'].max(), True)}")
    print(f"  çıkış: {tr['reason'].value_counts().to_dict()} | plandaki ort. stop mesafesi "
          f"{f(tr['stop_dist'].mean(), True)}, hedef mesafesi {f(tr['target_dist'].mean(), True)}")
    print(f"  işlem başına 100 $ yatırılsaydı toplam: {tr['ret'].sum() * 100:+.0f} $\n")


def composition(frames, regime, start, end):
    rows = []
    for sym, df in frames.items():
        if sym == "BTCUSDT":
            continue
        part = df.loc[start:end]
        sb = part[part["level"] == "STRONG_BUY"]
        if not sb.empty:
            rows.append(sb[COMPONENTS].assign(reg=regime.reindex(sb.index).values))
    if not rows:
        return
    d = pd.concat(rows)
    print("== GÜÇLÜ AL günlerinin yapısı (bileşen ortalamaları, 5 yıl) ==")
    print("  " + "  ".join(f"{c[2:]}:{d[c].mean():+.2f}" for c in COMPONENTS))
    below = (d["s_trend"] < 0).mean()
    print(f"  fiyat 200G ortalamanın ALTINDA olan GÜÇLÜ AL günleri: {f(below)}")
    print(f"  BTC rejimi: {d['reg'].value_counts(normalize=True).round(3).to_dict()}\n")


def equity_stats(daily_ret: pd.Series):
    daily_ret = daily_ret.fillna(0)
    eq = (1 + daily_ret).cumprod()
    years = len(daily_ret) / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    return eq.iloc[-1] - 1, cagr, dd


def simple_rules(frames, start, end):
    """Parametre aranmayan kurallar. Pozisyon kararı dünkü kapanışa göre, getiri bugün (bakmadan)."""
    btc = frames["BTCUSDT"].loc[:end]
    alts = {s: df.loc[:end] for s, df in frames.items() if s != "BTCUSDT"}
    idx = btc.loc[start:end].index

    def strat(df, hold, with_pos=False):
        r = df["close"].pct_change()
        pos = hold.shift(1).fillna(False).astype(float)
        cost = pos.diff().abs().fillna(pos) * FEE
        net = (pos * r - cost).reindex(idx)
        return (net, pos.reindex(idx).fillna(0)) if with_pos else net

    def basket(hold_fn):
        """Para, o gün tutulan coinler arasında eşit bölünür; hiçbiri yoksa nakitte bekler."""
        nets, poss = zip(*(strat(df, hold_fn(df), True) for df in alts.values()))
        net, pos = pd.concat(nets, axis=1).fillna(0), pd.concat(poss, axis=1)
        held = pos.sum(axis=1)
        return net.sum(axis=1) / held.where(held > 0, 1)

    out = {}
    out["BTC al-tut"] = btc["close"].pct_change().reindex(idx)
    out["BTC: 200G ortalama üstündeyken tut"] = strat(btc, btc["close"] > btc["sma200"])
    basket_ret = pd.concat([df["close"].pct_change().reindex(idx) for df in alts.values()], axis=1)
    out["Coin sepeti al-tut (eşit ağırlık)"] = basket_ret.mean(axis=1)
    out["Coin sepeti: her coin kendi trendindeyken tut"] = basket(
        lambda df: (df["close"] > df["sma200"]) & (df["sma50"] > df["sma200"]))
    btc_up = btc["close"] > btc["sma200"]
    out["Coin sepeti: coin ve BTC 200G üstündeyken tut"] = basket(
        lambda df: (df["close"] > df["sma200"]) & btc_up.reindex(df.index).fillna(False))
    out["Uygulama: GÜÇLÜ AL'dayken tut"] = basket(lambda df: df["score"] >= 4)
    out["Uygulama: AL/GÜÇLÜ AL'dayken tut"] = basket(lambda df: df["score"] >= 2)
    return out


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    frames = load(cfg)
    btc = frames["BTCUSDT"]
    regime = market_regime(btc)
    end = max(df.index[-1] for df in frames.values())
    p5, p2, p1 = end - pd.Timedelta(days=1826), end - pd.Timedelta(days=730), end - pd.Timedelta(days=365)
    print(f"{len(frames)} coin, veri sonu {end:%Y-%m-%d}. Komisyon+kayma her yön %{FEE * 100:.2f}.\n")

    data = forward_table(frames, btc, p5, end)
    print_forward(data, "1) Seviyeye göre ileri getiriler — 5 yıl")
    print_forward(data[data.index >= p2], "1) Seviyeye göre ileri getiriler — son 2 yıl")
    print_forward(data[data.index >= p1], "1) Seviyeye göre ileri getiriler — son 1 yıl")

    tr = follow_plan(frames, cfg, p5, end)
    print_plan(tr, "2) GÜÇLÜ AL + uygulamanın planı (stop / hedef 1 / 60 gün) — 5 yıl")
    if not tr.empty:
        print_plan(tr[tr["date"] >= p2], "2) aynısı — son 2 yıl")
        print_plan(tr[tr["date"] >= p1], "2) aynısı — son 1 yıl")

    composition(frames, regime, p5, end)

    print("== 4) Basit kurallar (eğitim = ilk 3 yıl, test = son 2 yıl; parametre aranmadı) ==")
    print(f"{'kural':>48} | {'eğitim toplam':>13} {'yıllık':>7} {'max düşüş':>9} | {'test toplam':>11} {'yıllık':>7} {'max düşüş':>9}")
    train, test = simple_rules(frames, p5, p2), simple_rules(frames, p2, end)
    for name in train:
        a, b = equity_stats(train[name]), equity_stats(test[name])
        print(f"{name:>48} | {f(a[0], True):>13} {f(a[1], True):>7} {f(a[2], True):>9} | "
              f"{f(b[0], True):>11} {f(b[1], True):>7} {f(b[2], True):>9}")


if __name__ == "__main__":
    main()
