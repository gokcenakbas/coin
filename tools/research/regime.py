"""Piyasa rejimi analizi: uygulamanın AL/SAT sinyalleri, Bitcoin'in trendine göre ne kadar işe yaramış?

Bitcoin rejimi:
  yükseliş : BTC > 200G ort. ve 50G ort. > 200G ort.
  düşüş    : BTC < 200G ort. ve 50G ort. < 200G ort.
  kararsız : diğer durumlar
Her rejimde, AL (puan ≥ +2) sinyali verilen günlerden 7 ve 30 gün sonra fiyatın yükselme oranı ve
ortalama getirisi; SAT (puan ≤ −2) için düşme oranı ölçülür. Son 5 yıl, en büyük 40 coin.

Kullanım: python tools/research/regime.py
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
from cointracker.signals import market_regime  # noqa: E402


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "research-data"})
    frames = load(cfg)
    btc = frames["BTCUSDT"]
    regime = market_regime(btc)
    end = max(df.index[-1] for df in frames.values())
    periods = {"5 yıl": end - pd.Timedelta(days=1826), "son 2 yıl": end - pd.Timedelta(days=730)}
    buy_th, sell_th = cfg["signals"]["buy_threshold"], cfg["signals"]["sell_threshold"]

    print(f"{len(frames)} coin. Bitcoin rejimi gün dağılımı (5 yıl): "
          f"{regime.loc[periods['5 yıl']:].value_counts().to_dict()}\n")
    for pname, start in periods.items():
        for horizon in (7, 30):
            rows = []
            for sym, df in frames.items():
                if sym == "BTCUSDT":
                    continue
                part = df.loc[start:]
                fwd = part["close"].shift(-horizon) / part["close"] - 1
                reg = regime.reindex(part.index).ffill()
                rows.append(pd.DataFrame({"fwd": fwd, "score": part["score"], "reg": reg}))
            data = pd.concat(rows).dropna()
            print(f"== {pname}, {horizon} gün sonrası ==")
            print(f"{'rejim':>9} | {'AL gün':>7} {'yükselme':>9} {'ort.':>7} | {'SAT gün':>7} {'düşme':>7} {'ort.':>7} | "
                  f"{'tüm gün':>7} {'yükselme':>9}")
            for reg in ("up", "mixed", "down", "tümü"):
                d = data if reg == "tümü" else data[data["reg"] == reg]
                buy, sell = d[d["score"] >= buy_th], d[d["score"] <= sell_th]
                f = lambda x: "-" if np.isnan(x) else f"{x * 100:5.1f}%"
                print(f"{reg:>9} | {len(buy):>7} {f((buy['fwd'] > 0).mean() if len(buy) else np.nan):>9} "
                      f"{f(buy['fwd'].mean() if len(buy) else np.nan):>7} | {len(sell):>7} "
                      f"{f((sell['fwd'] < 0).mean() if len(sell) else np.nan):>7} {f(sell['fwd'].mean() if len(sell) else np.nan):>7} | "
                      f"{len(d):>7} {f((d['fwd'] > 0).mean()):>9}")
            print()


if __name__ == "__main__":
    main()
