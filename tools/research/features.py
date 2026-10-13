"""Ek veriler GÜÇLÜ AL/AL işlemlerinin tutma olasılığını artırıyor mu?

Uygulamanın kuralının (cointracker/trend.py) son 5 yıldaki tüm işlemleri alınır; her işlem için sinyal günü
(yalnızca o güne kadarki veriyle) aşağıdaki ölçüler hesaplanır. Her ölçü için eşikler EĞİTİM döneminde (ilk 3 yıl)
üçe bölünerek bulunur, aynı eşikler TEST dönemine (son 2 yıl) uygulanır. Bir ölçü ancak iki dönemde de aynı yönde
fark yaratıyorsa işe yarıyor sayılır (çok sayıda ölçü denendiği için tek dönemde iyi görünen şans olabilir).

Ölçüler:
  vol_ratio   : kırılım günü işlem hacmi / önceki 20 günün ortalaması (hacimli kırılım mı?)
  rs_btc_30   : coinin 30 günlük getirisi − BTC'nin 30 günlük getirisi (Bitcoin'den güçlü mü?)
  rs_btc_90   : aynısı 90 gün
  breadth     : o gün 200 günlük ortalamanın üstündeki coinlerin oranı (piyasanın geneli)
  close_loc   : kapanışın günün aralığındaki yeri (1 = günün zirvesinde kapandı)
  brk_margin  : kapanışın kırılan seviyenin ne kadar üstünde olduğu (ATR cinsinden)
  squeeze     : kırılımdan önceki 20 günün ortalama oynaklığı / önceki 100 günün (düşük = sıkışmadan kırılım)
  stretch     : fiyatın 50 günlük ortalamaya uzaklığı (çok uzaklaşmış mı?)
  btc_mom     : BTC'nin 30 günlük getirisi
  atr_pct     : günlük oynaklık (ATR / fiyat)
  strong      : kırılımda 1 yıllık direnç geçildi mi (GÜÇLÜ AL = 1, AL = 0)
Kullanım: python tools/research/features.py
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
from cointracker.trend import broken_resistance, rule_trades, trend_frame  # noqa: E402

FEATURES = ["vol_ratio", "rs_btc_30", "rs_btc_90", "breadth", "close_loc", "brk_margin", "squeeze", "stretch",
            "btc_mom", "atr_pct", "strong"]
NAMES = {
    "vol_ratio": "Kırılım günü hacmi / 20G ort.", "rs_btc_30": "BTC'ye göre güç (30g)",
    "rs_btc_90": "BTC'ye göre güç (90g)", "breadth": "Piyasa genişliği (200G üstü oranı)",
    "close_loc": "Kapanışın gün içi yeri", "brk_margin": "Kırılım payı (ATR)", "squeeze": "Önceki sıkışma",
    "stretch": "50G ort.'ya uzaklık", "btc_mom": "BTC 30g getirisi", "atr_pct": "Günlük oynaklık",
    "strong": "1y direnç kırıldı (GÜÇLÜ AL)",
}


def stats(r: pd.Series) -> str:
    if r.empty:
        return f"{'-':>34}"
    gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
    pf = gains / losses if losses > 0 else np.inf
    return f"{len(r):>4} işl. %{(r > 0).mean() * 100:3.0f} {r.mean() * 100:+6.1f}% PF {pf:4.2f}"


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    optimize.TOP_N = 80
    frames = load(cfg)
    btc = frames["BTCUSDT"]
    end = max(df.index[-1] for df in frames.values())
    split, start = end - pd.Timedelta(days=730), end - pd.Timedelta(days=1826)
    print(f"{len(frames)} coin, {start:%Y-%m-%d} → {end:%Y-%m-%d}, test başlangıcı {split:%Y-%m-%d}\n")

    above = pd.DataFrame({s: (df["close"] > df["sma200"]).where(df["sma200"].notna()) for s, df in frames.items()})
    breadth = above.mean(axis=1)
    btc_ret30 = btc["close"].pct_change(30)
    btc_ret90 = btc["close"].pct_change(90)

    rows = []
    for sym, df in frames.items():
        if sym == "BTCUSDT":
            continue
        tf = trend_frame(df, btc)
        atrp = tf["atr"] / tf["close"]
        for t in rule_trades(tf, since=start, signal="brk_sig"):
            if t["open"]:
                continue
            i = tf.index.get_loc(t["entry_date"]) - 1
            d = tf.index[i]
            row = tf.iloc[i]
            vol20 = tf["volume"].iloc[max(0, i - 20):i].mean()
            rng = row["high"] - row["low"]
            prev = atrp.iloc[max(0, i - 20):i].mean()
            base = atrp.iloc[max(0, i - 120):max(1, i - 20)].mean()
            rows.append({
                "sym": sym, "date": d, "ret": t["ret"],
                "vol_ratio": row["volume"] / vol20 if vol20 else np.nan,
                "rs_btc_30": tf["close"].pct_change(30).iloc[i] - btc_ret30.get(d, np.nan),
                "rs_btc_90": tf["close"].pct_change(90).iloc[i] - btc_ret90.get(d, np.nan),
                "breadth": breadth.get(d, np.nan),
                "close_loc": (row["close"] - row["low"]) / rng if rng > 0 else np.nan,
                "brk_margin": (row["close"] - row["brk_level"]) / row["atr"] if row["atr"] else np.nan,
                "squeeze": prev / base if base else np.nan,
                "stretch": row["close"] / row["sma50"] - 1,
                "btc_mom": btc_ret30.get(d, np.nan),
                "atr_pct": atrp.iloc[i],
                "strong": float(broken_resistance(tf, d) is not None),
            })
    data = pd.DataFrame(rows)
    tr, te = data[data["date"] < split], data[data["date"] >= split]
    print(f"Tüm işlemler — eğitim: {stats(tr['ret'])} | test: {stats(te['ret'])}\n")

    verdicts = []
    for f in FEATURES:
        print(f"== {NAMES[f]} ({f}) ==")
        if f == "strong":
            cuts = [0.5]
            labels = ["AL (direnç yok)", "GÜÇLÜ AL"]
        else:
            q = tr[f].dropna().quantile([1 / 3, 2 / 3]).to_numpy()
            if len(q) < 2 or not q[0] < q[1]:
                print("   ölçü ayırt edici değil (eşikler aynı)\n")
                continue
            cuts = list(q)
            labels = [f"düşük (< {q[0]:.3g})", "orta", f"yüksek (≥ {q[1]:.3g})"]
        bins = [-np.inf, *cuts, np.inf]
        res = {}
        for name, part in (("eğitim", tr), ("test", te)):
            g = pd.cut(part[f], bins, labels=labels, right=False)
            res[name] = part.groupby(g, observed=False)["ret"]
        for lab in labels:
            a = res["eğitim"].get_group(lab) if lab in res["eğitim"].groups else pd.Series(dtype=float)
            b = res["test"].get_group(lab) if lab in res["test"].groups else pd.Series(dtype=float)
            print(f"   {lab:>24}: eğitim {stats(a)} | test {stats(b)}")
        lo, hi = labels[0], labels[-1]
        d_tr = res["eğitim"].mean().get(hi, np.nan) - res["eğitim"].mean().get(lo, np.nan)
        d_te = res["test"].mean().get(hi, np.nan) - res["test"].mean().get(lo, np.nan)
        same = np.sign(d_tr) == np.sign(d_te) and min(abs(d_tr), abs(d_te)) >= 0.03
        verdicts.append((f, d_tr, d_te, same))
        print(f"   yüksek − düşük farkı: eğitim {d_tr * 100:+.1f} puan, test {d_te * 100:+.1f} puan → "
              f"{'İKİ DÖNEMDE DE AYNI YÖNDE' if same else 'tutarsız / küçük'}\n")

    print("ÖZET (yüksek − düşük üçte bir, işlem başına ortalama getiri farkı):")
    for f, a, b, ok in sorted(verdicts, key=lambda v: -min(abs(v[1]), abs(v[2])) if v[3] else 0):
        print(f"  {'✓' if ok else '·'} {NAMES[f]:>36}: eğitim {a * 100:+6.1f} | test {b * 100:+6.1f}")

    # Tutarlı çıkan ölçülerin hepsini birlikte uygulamak (yön eğitimden): kaç işlem kalıyor, sonuç ne?
    good = [(f, np.sign(a)) for f, a, b, ok in verdicts if ok and f != "strong"]
    if good:
        print("\nTutarlı ölçülerin kötü üçte birini dışarıda bırakınca:")
        keep_tr, keep_te = pd.Series(True, index=tr.index), pd.Series(True, index=te.index)
        for f, sgn in good:
            q = tr[f].dropna().quantile([1 / 3, 2 / 3]).to_numpy()
            if sgn > 0:
                keep_tr &= tr[f] >= q[0]
                keep_te &= te[f] >= q[0]
            else:
                keep_tr &= tr[f] < q[1]
                keep_te &= te[f] < q[1]
            print(f"  + {NAMES[f]} ({'düşük' if sgn > 0 else 'yüksek'} olanlar çıkarıldı): "
                  f"eğitim {stats(tr.loc[keep_tr, 'ret'])} | test {stats(te.loc[keep_te, 'ret'])}")
    compare_rules(tr, te)


def compare_rules(tr: pd.DataFrame, te: pd.DataFrame) -> None:
    """Uygulamaya konabilecek seviye tanımları (sabit, yuvarlak eşikler: eğitimin üçte bir sınırları)."""
    calm = lambda d: d["atr_pct"] < 0.077  # noqa: E731
    fresh = lambda d: (d["stretch"] < 0.18) & (d["rs_btc_30"] < 0.08)  # noqa: E731
    rules = {
        "Bugünkü kural — tümü": lambda d: d.index == d.index,
        "Bugünkü GÜÇLÜ AL (1y direnç)": lambda d: d["strong"] > 0,
        "Bugünkü AL (direnç yok)": lambda d: d["strong"] == 0,
        "Yeni: oynak olmayanlar (ATR < %7,7)": calm,
        "Yeni GÜÇLÜ AL: sakin + aşırı uzamamış": lambda d: calm(d) & fresh(d),
        "Yeni AL: sakin ama uzamış": lambda d: calm(d) & ~fresh(d),
        "Elenen: çok oynak": lambda d: ~calm(d),
    }
    print("\nSEVİYE TANIMLARI (eğitim | test):")
    for name, f in rules.items():
        print(f"  {name:>40}: eğitim {stats(tr.loc[f(tr), 'ret'])} | test {stats(te.loc[f(te), 'ret'])}")
    for y, part in pd.concat([tr, te]).groupby(lambda i: pd.concat([tr, te]).at[i, "date"].year):
        new = part[calm(part) & fresh(part)]
        print(f"  {y}: bugünkü kural {stats(part['ret'])} | yeni GÜÇLÜ AL {stats(new['ret'])}")


if __name__ == "__main__":
    main()
