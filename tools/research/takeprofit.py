"""Destek/direnç ile giriş ve kâr alma araştırması (trend kırılımı kuralı üzerine).

Soru: "Giriş bölgesi destek/dirence göre olsun, kâr alabileceğim yerleri göstersin" — bu işe yarıyor mu?
Hepsi aynı kırılım sinyalini kullanır (coin ve BTC 200G üstünde, 50G > 200G, kapanış 20 günün zirvesi).

Girişler:
  Ertesi açılış        : sinyalden sonraki gün açılışta (uygulamanın bugünkü kuralı)
  Destekte (geri test) : kırılan seviyeye (önceki 19 günün zirvesi) + 0,25 ATR'ye limit emir, 5 gün geçerli
  Gerçek direnç kırıldı: yalnızca kırılan seviyenin yakınında (±1 ATR) son 1 yılın bir direnci varsa
Satışlar (hepsinde başlangıç stop'u giriş − 2 ATR, kalan kısım kapanış 20G ortalamanın altına inince):
  20G ort. altı        : tamamı kuralla (bugünkü)
  Yarısı direnç 1      : yarısı girişin en az 1 ATR üstündeki ilk dirençte, kalanı kuralla
  Yarısı direnç 1, BE  : aynısı; yarısı satılınca kalanın stop'u giriş fiyatına çekilir
  Üçte bir D1 + D2     : 1/3 ilk dirençte, 1/3 ikinci dirençte, kalanı kuralla
  Yarısı +2R           : yarısı giriş + 4 ATR'de (riskin 2 katı), kalanı kuralla
  Yarısı +%25          : yarısı %25 kârda, kalanı kuralla
  Tamamı direnç 1      : tamamı ilk dirençte (karşılaştırma için)

Dirençler yalnızca giriş gününe kadarki veriyle (cointracker.levels.support_resistance) hesaplanır.
Komisyon+kayma her satış parçası ve alış için %0,15. Eğitim ilk 3 yıl, test son 2 yıl.
Kullanım: python tools/research/takeprofit.py
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
MAX_HOLD = 365

EXITS = ["20G ort. altı", "Yarısı direnç 1", "Yarısı direnç 1, BE", "Üçte bir D1 + D2", "Yarısı +2R",
         "Yarısı +%25", "Tamamı direnç 1"]
ENTRIES = ["Ertesi açılış", "Destekte (geri test)", "Gerçek direnç kırıldı"]


def prepare(df: pd.DataFrame, btc_up: pd.Series) -> pd.DataFrame:
    d = df.copy()
    up = btc_up.reindex(d.index).fillna(False).astype(bool)
    d["in_trend"] = (d["close"] > d["sma200"]) & (d["sma50"] > d["sma200"]) & up
    brk = d["in_trend"] & (d["close"] >= d["close"].rolling(20).max())
    d["entry_sig"] = brk & ~brk.shift(1, fill_value=False)
    d["brk_level"] = d["close"].shift(1).rolling(19).max()
    d["sma20"] = d["close"].rolling(20).mean()
    return d


def targets(df: pd.DataFrame, i: int, entry: float, atr: float) -> list[float]:
    _, res = support_resistance(df.iloc[: i + 1], atr, limit=5)
    return [r["price"] for r in res if r["price"] >= entry + atr]


def simulate(d: pd.DataFrame, i: int, j0: int, entry: float, kind: str, res: list[float]):
    """i: sinyal günü, j0: alış günü (entry o gün). (getiri, gün, kısmi satış oldu mu) döner."""
    o, h, low, c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    sma20 = d["sma20"].to_numpy(dtype=float)
    atr = float(d["atr"].iat[i])
    stop = entry - 2 * atr
    n = len(d)

    if kind == "20G ort. altı":
        parts = []
    elif kind == "Yarısı direnç 1" or kind == "Yarısı direnç 1, BE":
        parts = [(res[0], 0.5)] if res else []
    elif kind == "Üçte bir D1 + D2":
        parts = [(p, 1 / 3) for p in res[:2]]
    elif kind == "Yarısı +2R":
        parts = [(entry + 4 * atr, 0.5)]
    elif kind == "Yarısı +%25":
        parts = [(entry * 1.25, 0.5)]
    elif kind == "Tamamı direnç 1":
        parts = [(res[0], 1.0)] if res else []
    else:
        raise ValueError(kind)
    if kind == "Tamamı direnç 1" and not parts:
        return None

    left, value, partial = 1.0, 0.0, False
    for j in range(j0, min(j0 + MAX_HOLD, n)):
        # alış günü açılışta alındıysa o günün aralığı geçerli; limitle alındıysa da (gün içi) aynı
        if low[j] <= stop:
            px = min(stop, o[j]) if j > j0 else stop
            return value + left * px * (1 - FEE), j - i, partial
        while parts and h[j] >= parts[0][0]:
            px, frac = parts.pop(0)
            px = max(px, o[j]) if j > j0 else px
            value += frac * px * (1 - FEE)
            left -= frac
            partial = True
            if kind == "Yarısı direnç 1, BE":
                stop = max(stop, entry)
        if left <= 1e-9:
            return value, j - i, partial
        if c[j] < sma20[j]:
            if j + 1 >= n:
                return None
            return value + left * o[j + 1] * (1 - FEE), j + 1 - i, partial
    return None  # sonuçlanmadı


def entry_for(d: pd.DataFrame, i: int, kind: str) -> tuple[int, float] | None:
    o, low = d["open"].to_numpy(dtype=float), d["low"].to_numpy(dtype=float)
    n = len(d)
    if i + 1 >= n:
        return None
    if kind == "Ertesi açılış":
        return i + 1, o[i + 1]
    atr, level = float(d["atr"].iat[i]), float(d["brk_level"].iat[i])
    if kind == "Destekte (geri test)":
        limit = level + 0.25 * atr
        for j in range(i + 1, min(i + 6, n)):
            if low[j] <= limit:
                return j, min(o[j], limit)
        return None
    if kind == "Gerçek direnç kırıldı":
        # kırılımdan önceki güne kadarki veriyle dirençler (o gün fiyatın üstündekiler)
        _, res = support_resistance(d.iloc[:i], atr, limit=5)
        if any(level - atr <= r["price"] <= d["close"].iat[i] for r in res):
            return i + 1, o[i + 1]
        return None
    raise ValueError(kind)


def stats(r: pd.DataFrame):
    if r.empty:
        return None
    ret = r["ret"]
    gains, losses = ret[ret > 0].sum(), -ret[ret <= 0].sum()
    return {"n": len(r), "win": (ret > 0).mean(), "avg": ret.mean(), "med": ret.median(),
            "pf": gains / losses if losses > 0 else np.inf, "worst": ret.min(), "days": r["days"].mean(),
            "hit": r["partial"].mean()}


def fmt(s):
    if not s:
        return f"{'-':>56}"
    p = lambda x: f"{x * 100:+5.1f}%"  # noqa: E731
    return (f"{s['n']:>5} {s['win'] * 100:5.1f}% {p(s['avg']):>7} {p(s['med']):>7} {s['pf']:5.2f} "
            f"{p(s['worst']):>7} {s['days']:4.0f}g {s['hit'] * 100:4.0f}%")


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    frames = load(cfg)
    btc = frames["BTCUSDT"]
    btc_up = btc["close"] > btc["sma200"]
    end = max(df.index[-1] for df in frames.values())
    split, start = end - pd.Timedelta(days=730), end - pd.Timedelta(days=1826)
    print(f"{len(frames)} coin, {start:%Y-%m-%d} → {end:%Y-%m-%d}, test başlangıcı {split:%Y-%m-%d}\n")

    rows, dist = [], []
    for sym, df in frames.items():
        d = prepare(df, btc_up)
        for i in np.flatnonzero(d["entry_sig"].to_numpy()):
            t = d.index[i]
            if t < start or pd.isna(d["atr"].iat[i]) or pd.isna(d["brk_level"].iat[i]):
                continue
            for ename in ENTRIES:
                got = entry_for(d, i, ename)
                if not got:
                    continue
                j0, entry = got
                res = targets(d, j0 - 1 if j0 - 1 >= i else i, entry, float(d["atr"].iat[i]))
                if ename == "Ertesi açılış":
                    dist.append({"date": t, "d1": res[0] / entry - 1 if res else np.nan,
                                 "d1_atr": (res[0] - entry) / d["atr"].iat[i] if res else np.nan})
                for x in EXITS:
                    out = simulate(d, i, j0, entry, x, res)
                    if out is None:
                        continue
                    val, days, partial = out
                    ret = val / (entry * (1 + FEE)) - 1
                    rows.append({"entry": ename, "exit": x, "date": t, "sym": sym, "ret": ret,
                                 "days": days, "partial": partial})
    data = pd.DataFrame(rows)
    dd = pd.DataFrame(dist)
    print(f"Ertesi açılış girişinde ilk direnç (giriş + ≥1 ATR): bulunan {dd['d1'].notna().mean() * 100:.0f}%, "
          f"medyan uzaklık {dd['d1'].median() * 100:+.1f}% ({dd['d1_atr'].median():.1f} ATR)\n")

    head = (f"{'işlem':>5} {'başarı':>6} {'ort.':>7} {'medyan':>7} {'PF':>5} {'en kötü':>7} {'süre':>5} "
            f"{'kısmi':>5}")
    print(f"{'giriş':>22} | {'satış':>20} | EĞİTİM: {head} | TEST: {head}")
    for ename in ENTRIES:
        for x in EXITS:
            d = data[(data["entry"] == ename) & (data["exit"] == x)]
            if d.empty:
                continue
            tr, te = stats(d[d["date"] < split]), stats(d[d["date"] >= split])
            print(f"{ename:>22} | {x:>20} | {fmt(tr)} | {fmt(te)}")
        print()

    print("Yıllara göre ortalama getiri (ertesi açılış girişi):")
    for x in ["20G ort. altı", "Yarısı direnç 1", "Yarısı direnç 1, BE", "Yarısı +2R"]:
        d = data[(data["entry"] == "Ertesi açılış") & (data["exit"] == x)]
        by = d.groupby(d["date"].dt.year)["ret"].agg(["count", "mean"])
        cells = "  ".join(f"{y}: {m * 100:+.1f}% ({int(c)})" for y, (c, m) in by.iterrows())
        print(f"  {x}: {cells}")


if __name__ == "__main__":
    main()
