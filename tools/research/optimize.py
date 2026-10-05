"""Strateji araştırması: gerçek 5 yıllık veriyle mevcut ve yeni stratejinin başarı oranlarını ölçer.

Aşırı uyumu (geçmişe ezber) önlemek için:
  * Parametreler yalnızca EĞİTİM döneminde (ilk ~3 yıl) seçilir,
  * Seçilenlerin sonucu hiç görülmemiş TEST döneminde (son 2 yıl) raporlanır,
  * Tüm coinler tek bir havuzda değerlendirilir (coin başına ayrı ayar yok).

Kullanım: python tools/research/optimize.py [çıktı.json]
"""

from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cointracker.backtest import backtest  # noqa: E402
from cointracker.config import DEFAULT_CONFIG, _deep_merge  # noqa: E402
from cointracker.data import BinanceClient, DataStore, list_universe  # noqa: E402
from cointracker.signals import score_frame  # noqa: E402
from cointracker.strategy import SetupParams, entry_signals, simulate, stats  # noqa: E402

TOP_N = 40
# 2. tur: 1. turda yüksek başarı oranının testte zarara dönmesi üzerine, başabaş stop, göreceli güç ve
# daha sıkı piyasa filtresi eklendi. Arama alanı aşırı uyumu sınırlamak için küçük tutuldu.
GRID = {
    "rsi_entry": [35, 40, 45],
    "confirm": [True],
    "regime": ["coin", "coin+btc", "coin+btc+rs"],
    "tp_atr": [1.0, 1.5, 2.0],
    "sl_atr": [1.5, 2.0, 3.0],
    "max_hold": [10, 20],
    "breakeven": [None, 0.5],
}
MIN_TRAIN_TRADES = 150


def pct(v):
    return "-" if v is None else f"{v * 100:5.1f}%"


def load(cfg) -> dict[str, pd.DataFrame]:
    client = BinanceClient()
    store = DataStore(cfg["data_dir"])
    symbols = list_universe(_deep_merge(cfg, {"universe": {"mode": "top", "top_n": TOP_N}}), client)
    if "BTCUSDT" not in symbols:
        symbols.insert(0, "BTCUSDT")
    frames = {}
    for sym in symbols:
        try:
            df = store.update(sym, client, 5)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym}: indirilemedi ({exc})")
            continue
        if len(df) >= 400:
            frames[sym] = score_frame(df, cfg)
    return frames


def main() -> None:
    out_path = Path(sys.argv[1] if len(sys.argv) > 1 else "research.json")
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    t0 = time.time()
    frames = load(cfg)
    end = max(df.index[-1] for df in frames.values())
    test_start = end - pd.Timedelta(days=730)
    train_start = end - pd.Timedelta(days=1826)
    print(f"{len(frames)} coin yüklendi ({time.time() - t0:.0f} sn). Eğitim: {train_start:%Y-%m-%d} → "
          f"{test_start:%Y-%m-%d}, Test: {test_start:%Y-%m-%d} → {end:%Y-%m-%d}\n")

    btc = frames["BTCUSDT"]

    # ---- Mevcut sistemin ölçümü ----
    def baseline(period_end, years):
        tr = wins = 0
        hits = []
        for df in frames.values():
            part = df.loc[:period_end]
            if len(part) < 300:
                continue
            try:
                bt = backtest(part, cfg, years=years)
            except ValueError:
                continue
            tr += bt.trades
            wins += round((bt.win_rate or 0) * bt.trades)
            window = part.loc[part.index >= period_end - pd.Timedelta(days=int(365.25 * years))]
            fwd = window["close"].shift(-30) / window["close"] - 1
            sel = fwd[(window["score"] >= cfg["signals"]["buy_threshold"]) & fwd.notna()]
            hits.extend((sel > 0).tolist())
        return {"trades": tr, "win_rate": wins / tr if tr else None,
                "al_30d_hit": float(np.mean(hits)) if hits else None, "al_days": len(hits)}

    base_train = baseline(test_start, 3)
    base_test = baseline(end, 2)
    print("MEVCUT SİSTEM (puan ≥ +2 ile al, puan ≤ −2 ya da iz süren stop ile sat)")
    print(f"  Eğitim: {base_train['trades']} işlem, başarı {pct(base_train['win_rate'])} | "
          f"AL sinyali sonrası 30 günde yükselme oranı {pct(base_train['al_30d_hit'])} ({base_train['al_days']} gün)")
    print(f"  Test  : {base_test['trades']} işlem, başarı {pct(base_test['win_rate'])} | "
          f"AL sinyali sonrası 30 günde yükselme oranı {pct(base_test['al_30d_hit'])} ({base_test['al_days']} gün)\n")

    # ---- Yeni strateji: parametre taraması ----
    keys = list(GRID)
    rows = []
    entry_cache: dict[tuple, dict[str, pd.Series]] = {}
    for values in itertools.product(*GRID.values()):
        p = SetupParams(**dict(zip(keys, values)))
        ekey = (p.rsi_entry, p.confirm, p.regime)
        if ekey not in entry_cache:
            entry_cache[ekey] = {s: entry_signals(df, p, btc) for s, df in frames.items()}
        train, test = [], []
        for sym, df in frames.items():
            ent = entry_cache[ekey][sym]
            train += simulate(df, ent, p, start=train_start, end=test_start)
            test += simulate(df, ent, p, start=test_start)
        rows.append({"params": p.as_dict(), "train": stats(train), "test": stats(test),
                     "test_by_year": {str(y): stats([t for t in test if t.entry_date.year == y])["win_rate"]
                                      for y in sorted({t.entry_date.year for t in test})}})
    print(f"{len(rows)} parametre kombinasyonu denendi ({time.time() - t0:.0f} sn)\n")

    def ok(s):
        return (s["trades"] >= MIN_TRAIN_TRADES and s["avg_ret"] is not None and s["avg_ret"] > 0.003
                and (s["profit_factor"] or 0) > 1.2)

    eligible = [r for r in rows if ok(r["train"])]
    eligible.sort(key=lambda r: (r["train"]["win_rate"], r["train"]["avg_ret"]), reverse=True)
    header = (f"{'rsi':>4} {'onay':>5} {'rejim':>9} {'tp':>5} {'sl':>4} {'gün':>4} | "
              f"{'EĞT işlem':>9} {'başarı':>7} {'ort.':>7} {'PF':>5} | {'TEST işlem':>10} {'başarı':>7} {'ort.':>7} {'PF':>5}")

    def line(r):
        p, a, b = r["params"], r["train"], r["test"]
        pf = lambda s: "-" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}"
        return (f"{p['rsi_entry']:>4} {str(p['confirm']):>5} {p['regime']:>9} {p['tp_atr']:>5} {p['sl_atr']:>4} "
                f"{p['max_hold']:>4} | {a['trades']:>9} {pct(a['win_rate']):>7} {pct(a['avg_ret']):>7} {pf(a):>5} | "
                f"{b['trades']:>10} {pct(b['win_rate']):>7} {pct(b['avg_ret']):>7} {pf(b):>5}")

    print("EĞİTİMDE EN YÜKSEK BAŞARI ORANLI 25 AYAR (kârlılık şartı: ort. işlem > %0,3, kâr faktörü > 1,2)")
    print(header)
    for r in eligible[:25]:
        print(line(r))

    target = [r for r in eligible if r["train"]["win_rate"] >= 0.60]
    target.sort(key=lambda r: r["train"]["avg_ret"] * r["train"]["trades"], reverse=True)
    print("\nEĞİTİMDE ≥ %60 BAŞARI + EN YÜKSEK TOPLAM KÂR (ilk 15)")
    print(header)
    for r in target[:15]:
        print(line(r))
        print(f"      test yıllara göre başarı: {r['test_by_year']}")

    both = [r for r in eligible if r["test"]["trades"] and r["test"]["avg_ret"] > 0]
    print(f"\nEğitimde uygun {len(eligible)} ayarın {len(both)} tanesi testte de kârlı kaldı.")
    if both:
        best = max(both, key=lambda r: r["test"]["win_rate"])
        print("Testte kârlı kalanlar içinde başarı oranı en yüksek olan (bilgi amaçlı, seçim için kullanılmaz):")
        print(header)
        print(line(best))

    out_path.write_text(json.dumps({"baseline": {"train": base_train, "test": base_test},
                                    "periods": {"train_start": str(train_start), "test_start": str(test_start),
                                                "end": str(end)},
                                    "coins": list(frames), "rows": rows}, indent=1))
    print(f"\nSonuçlar: {out_path} ({time.time() - t0:.0f} sn)")


if __name__ == "__main__":
    main()
