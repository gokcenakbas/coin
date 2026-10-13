"""Şu anki GÜÇLÜ AL / AL / trend durumu (uygulamadaki listeyle aynı hesap), Markdown tablo olarak.

Kullanım: python tools/scan_now.py [çıktı.md]   — GitHub Actions'ta .github/workflows/scan.yml ile çalışır.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cointracker.config import DEFAULT_CONFIG, _deep_merge  # noqa: E402
from cointracker.data import base_asset  # noqa: E402
from cointracker.engine import Engine  # noqa: E402
from cointracker.marketcap import evaluate, fetch_markets  # noqa: E402
from cointracker.trend import _fmt_tr as f  # noqa: E402


def pct(v):
    return "-" if v is None else f"{v * 100:+.1f}%".replace(".", ",")


def main() -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, {"data_dir": "scan-data"})
    eng = Engine(cfg)
    symbols = eng.universe()
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(eng.update_one, symbols + ["BTCUSDT"]))
    results = eng.analyze_all(symbols, run_backtest=False)
    try:
        mc = fetch_markets()
    except Exception:  # noqa: BLE001
        mc = {}
    rows, trend = [], []
    market = None
    for r in results:
        s, p = r.signal, r.signal.plan or {}
        st = s.setup or {}
        market = st.get("market_open", market)
        q = evaluate(s.price_quality, mc.get(base_asset(s.symbol, "USDT")), market_known=bool(mc))
        sg = st.get("signal") or {}
        if s.level in ("STRONG_BUY", "BUY") or (sg and st.get("state") == "ABOVE_ZONE"):
            rows.append((s, p, sg, q, st))
        elif s.level == "TREND":
            trend.append(s)
    out = [f"# Şu anki sinyaller — {results[0].signal.date:%d.%m.%Y} günlük kapanışına göre" if results else "# Sinyal yok",
           "", f"Piyasa (BTC 200G üstü): **{'yeni alım AÇIK' if market else 'yeni alım KAPALI'}** · {len(symbols)} coin tarandı", ""]
    if rows:
        out += ["| Coin | Sinyal | Kırılım | Fiyat | Alım bölgesi | Durum | Zarar-durdur | Satış seviyesi (20G) | Oynaklık | 50G'ye uzaklık | BTC'ye göre 30g | 🛡 |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for s, p, sg, q, st in rows:
            state = {"BUY": "✓ bölgede", "ABOVE_ZONE": "üstünde — kovalama"}.get(st.get("state"), st.get("state"))
            lab = s.label if s.level in ("STRONG_BUY", "BUY") else "(sinyal var, fiyat kaçtı)"
            out.append(f"| {base_asset(s.symbol, 'USDT')} | **{lab}** | {'yeni' if not sg.get('days_ago') else str(sg['days_ago']) + ' g önce'} | "
                       f"{f(p.get('price'))} | {f(p.get('buy_low'))} – {f(p.get('buy_high'))} | {state} | {f(p.get('stop'))} | "
                       f"{f(p.get('exit_level'))} | %{(q['atr_pct'] or 0) * 100:.1f} | {pct(sg.get('stretch'))} | "
                       f"{pct(sg.get('rs_btc_30'))} | {'🛡' if q['calm_list'] else ''} |".replace("%0.", "%0,"))
    else:
        out.append("Şu an GÜÇLÜ AL ya da AL veren coin yok.")
    out += ["", f"TRENDDE (elindeyse tut, yeni alım yok): {', '.join(base_asset(s.symbol, 'USDT') for s in trend) or '-'}"]
    text = "\n".join(out) + "\n"
    print(text)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
