"""Alım + satış kuralı araştırması: "GÜÇLÜ AL'da al, uygulama ne zaman satacağını söylesin".

Az sayıda, önceden belirlenmiş kural (parametre taraması yok), son 5 yıl, büyük coinler, günlük mumlar.
Karar günün kapanışında verilir, işlem ertesi günün açılışında yapılır; komisyon+kayma her yön %0,15.

Alım kuralları:
  GÜÇLÜ AL          : puan +4'e yeni çıktı (uygulamanın bugünkü sinyali)
  GÜÇLÜ AL + BTC    : aynısı, BTC 200G ortalamanın üstündeyken
  Trend kırılımı    : kapanış son 20 günün en yükseği, coin ve BTC 200G üstünde, 50G > 200G
  Rastgele          : her 5. gün (karşılaştırma; kuralın gerçekten bir şey katıp katmadığını gösterir)
Satış kuralları:
  7 gün             : 7 gün sonra sat
  Plan              : uygulamanın planı (zarar-durdur / hedef 1 / en fazla 60 gün)
  İz süren 3 ATR    : girişten beri en yüksek kapanışın 3 ATR altına kapanınca sat (en fazla 180 gün)
  20G ort. altı     : kapanış 20 günlük ortalamanın altına inince sat; başlangıç stop'u giriş − 2 ATR
  50G ort. altı     : aynısı 50 günlük ortalamayla

Eğitim: ilk 3 yıl, test: son 2 yıl. Bir kural ancak iki dönemde de rastgele girişten iyiyse anlamlıdır.
Kullanım: python tools/research/entryexit.py
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

FEE = 0.0015
MAX_HOLD = 180


def entries(df: pd.DataFrame, btc_up: pd.Series) -> dict[str, pd.Series]:
    up = btc_up.reindex(df.index).fillna(False).astype(bool)
    sb = df["score"] >= 4
    sb_new = sb & ~sb.shift(1, fill_value=False)
    trend = (df["close"] > df["sma200"]) & (df["sma50"] > df["sma200"]) & up
    brk = trend & (df["close"] >= df["close"].rolling(20).max())
    brk_new = brk & ~brk.shift(1, fill_value=False)
    rand = pd.Series(np.arange(len(df)) % 5 == 0, index=df.index) & df["sma200"].notna()
    return {"GÜÇLÜ AL": sb_new, "GÜÇLÜ AL + BTC yukarı": sb_new & up,
            "Trend kırılımı": brk_new, "Rastgele": rand}


def run_exit(df, i, kind, cfg):
    """Giriş i+1 açılışı. (getiri, gün) döner; sonuçlanmadıysa None."""
    n = len(df)
    if i + 2 >= n:
        return None
    entry = df["open"].iat[i + 1]
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = df["atr"].to_numpy()

    def done(px, j):
        return (px * (1 - FEE)) / (entry * (1 + FEE)) - 1, j - i

    if kind == "7 gün":
        return done(c[i + 7], i + 7) if i + 7 < n else None
    if kind == "Plan":
        plan = trade_plan(df.iloc[: i + 1], cfg)
        if not plan or not (plan["stop"] < entry < plan["target1"]):
            return None
        stop, target = plan["stop"], plan["target1"]
        for j in range(i + 1, min(i + 61, n)):
            if l[j] <= stop:
                return done(min(stop, o[j]), j)
            if h[j] >= target:
                return done(max(target, o[j]), j)
        return done(c[i + 60], i + 60) if i + 60 < n else None

    stop0 = entry - 2 * atr[i]
    ma = None
    if kind == "20G ort. altı":
        ma = df["close"].rolling(20).mean().to_numpy()
    elif kind == "50G ort. altı":
        ma = df["sma50"].to_numpy()
    peak = entry
    for j in range(i + 1, min(i + 1 + MAX_HOLD, n)):
        if kind != "İz süren 3 ATR" and l[j] <= stop0:
            return done(min(stop0, o[j]), j)
        peak = max(peak, c[j])
        if kind == "İz süren 3 ATR":
            trigger = c[j] < peak - 3 * atr[j]
        else:
            trigger = c[j] < ma[j]
        if trigger:
            return done(o[j + 1], j + 1) if j + 1 < n else None
    j = i + MAX_HOLD
    return done(c[j], j) if j < n else None


EXITS = ["7 gün", "Plan", "İz süren 3 ATR", "20G ort. altı", "50G ort. altı"]


def stats(r: pd.DataFrame):
    if r.empty:
        return None
    ret = r["ret"]
    gains, losses = ret[ret > 0].sum(), -ret[ret <= 0].sum()
    return {"n": len(r), "win": (ret > 0).mean(), "avg": ret.mean(), "med": ret.median(),
            "pf": gains / losses if losses > 0 else np.inf, "worst": ret.min(), "days": r["days"].mean()}


def fmt(s):
    if not s:
        return f"{'-':>48}"
    p = lambda x: f"{x * 100:+5.1f}%"
    return f"{s['n']:>5} {s['win'] * 100:5.1f}% {p(s['avg']):>7} {p(s['med']):>7} {s['pf']:5.2f} {p(s['worst']):>7} {s['days']:5.0f}g"


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    frames = load(cfg)
    btc = frames["BTCUSDT"]
    btc_up = btc["close"] > btc["sma200"]
    end = max(df.index[-1] for df in frames.values())
    split, start = end - pd.Timedelta(days=730), end - pd.Timedelta(days=1826)
    print(f"{len(frames)} coin, {start:%Y-%m-%d} → {end:%Y-%m-%d}, test başlangıcı {split:%Y-%m-%d}\n")

    rows = []
    for sym, df in frames.items():
        for ename, mask in entries(df, btc_up).items():
            for i in np.flatnonzero(mask.to_numpy()):
                t = df.index[i]
                if t < start or pd.isna(df["atr"].iat[i]):
                    continue
                for x in EXITS:
                    if x == "Plan" and ename == "Rastgele":
                        continue  # plan hesaplaması pahalı; rastgele için atlanır
                    res = run_exit(df, i, x, cfg)
                    if res:
                        rows.append({"entry": ename, "exit": x, "date": t, "sym": sym, "ret": res[0], "days": res[1]})
    data = pd.DataFrame(rows)

    head = f"{'işlem':>5} {'başarı':>6} {'ort.':>7} {'medyan':>7} {'PF':>5} {'en kötü':>7} {'süre':>6}"
    print(f"{'alım':>22} | {'satış':>14} | EĞİTİM: {head} | TEST: {head}")
    for ename in ["GÜÇLÜ AL", "GÜÇLÜ AL + BTC yukarı", "Trend kırılımı", "Rastgele"]:
        for x in EXITS:
            d = data[(data["entry"] == ename) & (data["exit"] == x)]
            if d.empty:
                continue
            tr, te = stats(d[d["date"] < split]), stats(d[d["date"] >= split])
            print(f"{ename:>22} | {x:>14} | {fmt(tr)} | {fmt(te)}")
        print()

    # Yıllara göre kararlılık: en iyi görünen kombinasyonlar
    print("Yıllara göre ortalama getiri (işlem başına):")
    for ename in ["GÜÇLÜ AL", "Trend kırılımı", "Rastgele"]:
        for x in ["İz süren 3 ATR", "50G ort. altı"]:
            d = data[(data["entry"] == ename) & (data["exit"] == x)]
            by = d.groupby(d["date"].dt.year)["ret"].agg(["count", "mean"])
            cells = "  ".join(f"{y}: {m * 100:+.1f}% ({c})" for y, (c, m) in by.iterrows())
            print(f"  {ename} / {x}: {cells}")


if __name__ == "__main__":
    main()
