"""Hızlı hareketler önceden sezilebilir mi? Gerçek 1 saatlik veriyle ölçüm.

Olay ("hızlı hareket"): sonraki 4 saat içinde fiyatın bir noktada %5 yukarı ya da %5 aşağı gitmesi.
Her öncü iz için: iz görüldüğünde olay ne sıklıkla geldi, iz yokken ne sıklıkla geldi?
Oynak coinler (memecoin vb.) sonucu şişirmesin diye "coin'e göre düzeltilmiş kat" de raporlanır:
iz görülen saatlerdeki olay sayısı ÷ o coinlerin kendi normal olay oranından beklenen sayı.

Eşikler önceden sabittir (tarama yok). Dönem ikiye bölünür: ilk 8 ay ve son 4 ay; bir iz ancak
iki dönemde de tutarlıysa anlamlı kabul edilir.

Kullanım: python tools/research/fastmoves.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cointracker.config import DEFAULT_CONFIG, _deep_merge  # noqa: E402
from cointracker.data import BinanceClient, base_asset, list_universe  # noqa: E402
from cointracker.movers import MOVE_HOURS, MOVE_PCT, features  # noqa: E402

TOP_N = 40
DAYS = 365


def label_events(df: pd.DataFrame) -> pd.DataFrame:
    fwd_high = df["high"][::-1].rolling(MOVE_HOURS, min_periods=MOVE_HOURS).max()[::-1].shift(-1)
    fwd_low = df["low"][::-1].rolling(MOVE_HOURS, min_periods=MOVE_HOURS).min()[::-1].shift(-1)
    up = fwd_high / df["close"] - 1 >= MOVE_PCT
    down = 1 - fwd_low / df["close"] >= MOVE_PCT
    known = fwd_high.notna()
    return pd.DataFrame({"up": up & known, "down": down & known, "event": (up | down) & known, "known": known})


def main() -> None:
    t0 = time.time()
    client = BinanceClient()
    symbols = list_universe(_deep_merge(DEFAULT_CONFIG, {"universe": {"mode": "top", "top_n": TOP_N}}), client)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAYS)
    frames = []
    for sym in symbols:
        try:
            df = client.klines(sym, start, end, "1h")
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym}: alınamadı ({exc})")
            continue
        if len(df) < 2000:
            continue
        f = features(df).join(label_events(df))
        f["coin"] = base_asset(sym)
        frames.append(f[f["known"]])
    data = pd.concat(frames)
    split = data.index.max() - pd.Timedelta(days=120)
    print(f"{len(frames)} coin, {len(data):,} saat ({time.time() - t0:.0f} sn). "
          f"Olay: sonraki {MOVE_HOURS} saatte ±%{MOVE_PCT * 100:.0f}. Bölünme: {split:%Y-%m-%d}\n")

    sq, rv = data["squeeze_pct"], data["rvol"]
    predictors = {
        "Sıkışma: bant genişliği 30 günün en dar %10'u": sq <= 0.10,
        "Sıkışma: en dar %5": sq <= 0.05,
        "Hacim normalin ≥3 katı": rv >= 3,
        "Hacim normalin ≥5 katı": rv >= 5,
        "Sıkışma (%20) + hacim ≥2 kat": (sq <= 0.20) & (rv >= 2),
        "Yukarı kırılım (3 günlük tepe) + hacim ≥2 kat": data["breakout_up"] & (rv >= 2),
        "Aşağı kırılım (3 günlük dip) + hacim ≥2 kat": data["breakout_down"] & (rv >= 2),
        "Son 1 saatte ≥ %3 yükseliş (başlamış hareket)": data["ret_1h"] >= 0.03,
        "Son 1 saatte ≥ %3 düşüş (başlamış hareket)": data["ret_1h"] <= -0.03,
    }

    for pname, mask_period in (("İLK 8 AY", data.index < split), ("SON 4 AY", data.index >= split)):
        d = data[mask_period]
        coin_rate = d.groupby("coin")["event"].mean()
        coin_up = d.groupby("coin")["up"].mean()
        coin_down = d.groupby("coin")["down"].mean()
        print(f"== {pname}: {len(d):,} saat, herhangi bir saatte olay oranı %{d['event'].mean() * 100:.1f} ==")
        print(f"{'öncü iz':<48} {'saat':>7} {'olay':>7} {'kat':>5} {'düz.kat':>7} | {'↑ olay':>7} {'↑kat':>5} | {'↓ olay':>7} {'↓kat':>5}")
        for name, mask in predictors.items():
            sel = d[mask[mask_period]]
            if sel.empty:
                print(f"{name:<48} {0:>7}")
                continue
            rate = sel["event"].mean()
            expected = sel["coin"].map(coin_rate).mean()
            up_rate, dn_rate = sel["up"].mean(), sel["down"].mean()
            up_adj = up_rate / sel["coin"].map(coin_up).mean()
            dn_adj = dn_rate / sel["coin"].map(coin_down).mean()
            print(f"{name:<48} {len(sel):>7} {rate * 100:>6.1f}% {rate / d['event'].mean():>5.1f} {rate / expected:>7.1f} | "
                  f"{up_rate * 100:>6.1f}% {up_adj:>5.1f} | {dn_rate * 100:>6.1f}% {dn_adj:>5.1f}")
        print()
    print("kat = iz görüldüğünde olay oranı ÷ genel oran; düz.kat = aynı coinlerin kendi normaline göre;"
          " ↑/↓ = yalnızca yukarı/aşağı yönlü olaylar (coine göre düzeltilmiş)")


if __name__ == "__main__":
    main()
