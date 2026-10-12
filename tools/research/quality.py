"""Coin kalitesi araştırması: "sakin, grafiğe sadık" coinlerden oluşan bir liste işe yarar mı?

Sorular (eğitim ilk 3 yıl / test son 2 yıl, aynı coinler):
  1. Bir coinde destekler geçmişte iyi tuttuysa, sonraki dönemde de iyi tutuyor mu? (coin bazında kalıcılık)
     Ölçü: support.py'deki olaylar — destek değildiğinde önce destek + 1 ATR üstüne mi, destek − 1 ATR altına mı
     kapanış geldi ("tuttu" oranı).
  2. Düşük volatiliteli coinlerde destekler daha mı iyi tutuyor?
  3. Trend kırılımı kuralı (GÜÇLÜ AL/AL) düşük / orta / yüksek volatiliteli coinlerde nasıl sonuç veriyor?
     (Sakin coinlere kısıtlamak kuralın kârını düşürür mü?)
Volatilite: eğitim dönemindeki günlük ATR / fiyat ortalaması (coin başına tek sayı).
Kullanım: python tools/research/quality.py
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
from support import scan  # noqa: E402

from cointracker.config import DEFAULT_CONFIG, _deep_merge  # noqa: E402
from cointracker.data import base_asset  # noqa: E402
from cointracker.marketcap import evaluate, fetch_markets, price_quality  # noqa: E402
from cointracker.trend import rule_trades, trend_frame  # noqa: E402

MIN_EVENTS = 12


def held_rate(ev: pd.DataFrame) -> tuple[float, int]:
    h = ev.loc[ev["hit"] & ev["held"].notna(), "held"].astype(bool)
    return (float(h.mean()) if len(h) else np.nan), len(h)


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    optimize.TOP_N = 80  # coin bazında karşılaştırma için daha geniş havuz
    frames = load(cfg)
    btc = frames["BTCUSDT"]
    btc_up = btc["close"] > btc["sma200"]
    end = max(df.index[-1] for df in frames.values())
    split, start = end - pd.Timedelta(days=730), end - pd.Timedelta(days=1826)
    print(f"{len(frames)} coin, {start:%Y-%m-%d} → {end:%Y-%m-%d}, test başlangıcı {split:%Y-%m-%d}\n")

    rng = np.random.default_rng(7)
    rows, trades = [], []
    for sym, df in frames.items():
        ev, _ = scan(sym, df, btc_up, start, rng)
        ev = pd.DataFrame(ev)
        tr_win = df.loc[(df.index >= start) & (df.index < split)]
        vol = float((tr_win["atr"] / tr_win["close"]).mean()) if len(tr_win) > 100 else np.nan
        if ev.empty:
            continue
        h_tr, n_tr = held_rate(ev[ev["date"] < split])
        h_te, n_te = held_rate(ev[ev["date"] >= split])
        rows.append({"sym": sym, "vol": vol, "held_train": h_tr, "n_train": n_tr, "held_test": h_te, "n_test": n_te})
        tf = trend_frame(df, btc)
        for t in rule_trades(tf, since=start):
            if not t["open"]:
                trades.append({"sym": sym, "vol": vol, "date": t["entry_date"], "ret": t["ret"]})

    c = pd.DataFrame(rows).dropna(subset=["vol"])
    ok = c[(c["n_train"] >= MIN_EVENTS) & (c["n_test"] >= MIN_EVENTS)]
    print(f"1) Coin bazında destek tutma oranı kalıcı mı? ({len(ok)} coin, her dönemde ≥{MIN_EVENTS} olay)")
    if len(ok) >= 5:
        rho = ok["held_train"].rank().corr(ok["held_test"].rank())
        print(f"   Eğitim ↔ test sıra korelasyonu: {rho:+.2f}  (0 = kalıcılık yok, 1 = tamamen kalıcı)")
        ok = ok.sort_values("held_train")
        k = max(1, len(ok) // 3)
        lo, hi = ok.head(k), ok.tail(k)
        print(f"   Eğitimde en kötü 1/3 ({k} coin): eğitim %{lo['held_train'].mean() * 100:.0f} → test "
              f"%{lo['held_test'].mean() * 100:.0f}")
        print(f"   Eğitimde en iyi 1/3  ({k} coin): eğitim %{hi['held_train'].mean() * 100:.0f} → test "
              f"%{hi['held_test'].mean() * 100:.0f}")
    print()

    c["vol_group"] = pd.qcut(c["vol"], 3, labels=["düşük", "orta", "yüksek"])
    print("2) Volatiliteye göre destek tutma oranı (coin ortalaması):")
    for g, d in c.groupby("vol_group", observed=True):
        print(f"   {g:>6} volatilite (günlük ATR %{d['vol'].min() * 100:.1f}–%{d['vol'].max() * 100:.1f}, "
              f"{len(d)} coin): eğitim %{d['held_train'].mean() * 100:.0f}, test %{d['held_test'].mean() * 100:.0f}"
              f"  [{', '.join(s.replace('USDT', '') for s in d['sym'])}]")
    print()

    t = pd.DataFrame(trades).merge(c[["sym", "vol_group"]], on="sym")
    print("3) Trend kırılımı kuralı volatiliteye göre (işlem başına):")
    for g, d in t.groupby("vol_group", observed=True):
        for name, part in (("eğitim", d[d["date"] < split]), ("test", d[d["date"] >= split])):
            r = part["ret"]
            if r.empty:
                continue
            gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
            pf = gains / losses if losses > 0 else np.inf
            print(f"   {g:>6} / {name:>6}: {len(r):>4} işlem, başarı %{(r > 0).mean() * 100:.0f}, "
                  f"ort. {r.mean() * 100:+.1f}%, medyan {r.median() * 100:+.1f}%, PF {pf:.2f}, en kötü {r.min() * 100:+.0f}%")

    # 4) Bugünkü sakin liste (uygulamadaki 🛡 düğmesiyle aynı ölçütler)
    print()
    try:
        mc = fetch_markets()
        print(f"4) Bugünkü sakin liste (CoinGecko: {len(mc)} coin)")
    except Exception as exc:  # noqa: BLE001
        mc = {}
        print(f"4) Bugünkü sakin liste (CoinGecko'ya ulaşılamadı: {exc})")
    calm, out = [], []
    for sym, df in frames.items():
        base = base_asset(sym, "USDT")
        q = evaluate(price_quality(df), mc.get(base))
        (calm if q["calm_list"] else out).append((base, q))
    print(f"   Listede ({len(calm)}): " + ", ".join(
        f"{b} (%{q['atr_pct'] * 100:.1f}, arz %{(q['float'] or 0) * 100:.0f}, #{q['rank']})" for b, q in calm))
    print(f"   Dışarıda ({len(out)}):")
    for b, q in out:
        print(f"     {b}: {'; '.join(q['reasons'])}")


if __name__ == "__main__":
    main()
