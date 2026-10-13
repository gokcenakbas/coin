"""Canlı kâğıt testi: uygulamanın GÜÇLÜ AL/AL kuralı, sanal parayla, bugünden itibaren.

Geçmiş testin (tools/research/) gerçek zamanda da tutup tutmadığını görmek için. Kurallar başlangıçta sabitlenir:

  Evren      : başlangıç günü Binance'te hacme göre ilk 100 USDT paritesi (sonra değişmez)
  Sinyal     : günlük kapanışta trend kırılımı (cointracker/trend.py; coin ve BTC 200G üstü, 50G > 200G,
               kapanış 20 günün zirvesi). Kırılımda 1 yıllık direnç geçildiyse GÜÇLÜ AL, değilse AL.
  Alış       : sinyalin ertesi günü açılış fiyatından
  Satış      : gün içinde giriş − 2 ATR'ye (sinyal günü ATR'si) değerse o fiyattan; günlük kapanış 20 günlük
               ortalamanın altına inerse ertesi gün açılıştan
  Portföy    : sanal 1000 $, en fazla 10 pozisyon; her biri toplam değerin 1/10'u. Aynı gün birden çok sinyal
               varsa son 20 günde en çok yükselen önce. Kaldıraç yok.
  Masraf     : her alım ve satımda %0,15 (komisyon + kayma)
  Karşılaştırma portföyleri (aynı kurallar, ayrı 1000 $): yalnızca GÜÇLÜ AL · yalnızca sakin liste coinleri ·
               BTC al-tut

Her çalıştırmada başlangıçtan bugüne kadar tamamlanmış günlük mumlarla baştan hesaplanır; yalnızca o güne kadarki
veri kullanıldığı için sonuç, testin her gün canlı yürütülmesiyle aynıdır ve gün kaçırılsa bile eksik kalmaz.

Kullanım:
  python -m cointracker.forwardtest run --state paper/canli/state.json --out paper/canli/RAPOR.md
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from cointracker.config import DEFAULT_CONFIG, _deep_merge
from cointracker.data import BinanceClient, DataStore, base_asset, list_universe
from cointracker.marketcap import evaluate, fetch_markets, price_quality
from cointracker.signals import score_frame
from cointracker.trend import FEE, MEASURED, STOP_ATR, broken_resistance, trend_frame

log = logging.getLogger(__name__)
BTC = "BTCUSDT"
RULE_VERSION = "trend-kirilim-v1 (20G zirve, 1y direnç = GÜÇLÜ AL, stop 2 ATR, çıkış 20G ort.)"


@dataclass(frozen=True)
class ForwardConfig:
    start_cash: float = 1000.0
    slots: int = 10
    top_n: int = 100
    fee: float = FEE


PORTFOLIOS = {
    "all": "GÜÇLÜ AL + AL (uygulamanın kuralı)",
    "strong": "Yalnızca GÜÇLÜ AL",
    "calm": "Yalnızca 🛡 sakin liste coinleri",
}


def today_utc() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None).normalize()


def new_state(universe: list[str], start_day: pd.Timestamp, calm: dict[str, bool], cfg: ForwardConfig) -> dict:
    return {
        "version": 1, "rule": RULE_VERSION, "config": asdict(cfg),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "start_day": start_day.strftime("%Y-%m-%d"),
        "universe": universe, "calm_at_start": calm,
    }


def prepare(frames: dict[str, pd.DataFrame], start_day: pd.Timestamp, last_day: pd.Timestamp) -> dict:
    """Her coin için kural çerçevesi; yalnızca son tamamlanmış güne kadar."""
    btc = frames[BTC].loc[:last_day]
    out = {}
    for sym, df in frames.items():
        df = df.loc[:last_day]
        if len(df) < 60:
            continue
        tf = trend_frame(df, btc)
        tf["mom20"] = tf["close"] / tf["close"].shift(20) - 1
        # GÜÇLÜ AL: kırılımda 1 yıllık direnç geçildi mi (yalnızca başlangıçtan sonraki sinyaller için hesaplanır)
        tf["strong"] = False
        for t in tf.index[(tf.index >= start_day) & tf["entry_sig"].to_numpy()]:
            tf.at[t, "strong"] = broken_resistance(tf, t) is not None
        out[sym] = tf
    return out


def simulate(data: dict[str, pd.DataFrame], start_day: pd.Timestamp, last_day: pd.Timestamp, cfg: ForwardConfig,
             allow=lambda sym, row: True) -> dict:
    """Bir portföyü start_day'den last_day'e kadar gün gün yürütür."""
    days = pd.date_range(start_day, last_day, freq="D")
    cash, pos, trades, curve = cfg.start_cash, {}, [], []
    pending_buys: list[str] = []
    pending_sells: list[str] = []

    def value(t, field):
        return cash + sum(p["qty"] * data[s].at[t, field] if t in data[s].index else p["qty"] * p["last"]
                          for s, p in pos.items())

    for t in days:
        # 1) Açılışta bekleyen satış ve alımlar
        for sym in pending_sells:
            if sym in pos and t in data[sym].index:
                p = pos.pop(sym)
                px = data[sym].at[t, "open"]
                cash += p["qty"] * px * (1 - cfg.fee)
                trades.append(_closed(sym, p, t, px, "kapanış 20G ort. altı", cfg))
        pending_sells = []
        eq_open = value(t, "open")
        for sym in pending_buys:
            if len(pos) >= cfg.slots or sym in pos or t not in data[sym].index:
                continue
            size = min(cash, eq_open / cfg.slots)
            if size < 1:
                continue
            d = data[sym]
            px = d.at[t, "open"]
            sig_day = d.index[d.index.get_loc(t) - 1]
            atr = d.at[sig_day, "atr"]
            pos[sym] = {"qty": size * (1 - cfg.fee) / px, "entry": px, "cost": size, "stop": px - STOP_ATR * atr,
                        "entry_day": t, "signal_day": sig_day, "strong": bool(d.at[sig_day, "strong"]), "last": px}
            cash -= size
        pending_buys = []

        # 2) Gün içi zarar-durdur, kapanışta satış kuralı
        for sym in list(pos):
            d = data[sym]
            if t not in d.index:
                continue
            p, row = pos[sym], d.loc[t]
            p["last"] = row["close"]
            if row["low"] <= p["stop"]:
                px = p["stop"] if t == p["entry_day"] else min(p["stop"], row["open"])
                cash += p["qty"] * px * (1 - cfg.fee)
                trades.append(_closed(sym, pos.pop(sym), t, px, "zarar-durdur", cfg))
                continue
            if row["close"] < row["sma20"]:
                pending_sells.append(sym)

        # 3) Kapanışta yeni sinyaller → ertesi gün açılışta alınır
        cands = []
        for sym, d in data.items():
            if sym == BTC or sym in pos or t not in d.index or not d.at[t, "entry_sig"]:
                continue
            if allow(sym, d.loc[t]):
                cands.append((d.at[t, "mom20"] if pd.notna(d.at[t, "mom20"]) else 0.0, sym))
        pending_buys = [s for _, s in sorted(cands, reverse=True)]
        curve.append((t, value(t, "close")))

    last = days[-1] if len(days) else start_day
    open_pos = [{"sym": s, "entry_day": p["entry_day"], "entry": p["entry"], "price": p["last"],
                 "ret": p["qty"] * p["last"] * (1 - cfg.fee) / p["cost"] - 1, "stop": p["stop"],
                 "exit_level": data[s].at[last, "sma20"] if last in data[s].index else None,
                 "exit_pending": s in pending_sells, "strong": p["strong"]} for s, p in pos.items()]
    return {"curve": curve, "trades": trades, "open": open_pos, "cash": cash, "next_buys": pending_buys,
            "next_sells": pending_sells}


def _closed(sym, p, t, px, reason, cfg) -> dict:
    ret = p["qty"] * px * (1 - cfg.fee) / p["cost"] - 1  # alış ve satış masrafları dahil
    return {"sym": sym, "entry_day": p["entry_day"], "exit_day": t, "entry": p["entry"], "exit": px,
            "ret": ret, "reason": reason, "strong": p["strong"]}


def stats(result: dict, cfg: ForwardConfig) -> dict:
    curve = pd.Series(dict(result["curve"])) if result["curve"] else pd.Series([cfg.start_cash])
    closed = np.array([t["ret"] for t in result["trades"]])
    return {
        "value": float(curve.iloc[-1]), "ret": float(curve.iloc[-1] / cfg.start_cash - 1),
        "max_dd": float((curve / curve.cummax().clip(lower=cfg.start_cash) - 1).min()) if len(curve) else 0.0,
        "closed": int(len(closed)), "open": len(result["open"]),
        "win_rate": float((closed > 0).mean()) if len(closed) else None,
        "avg": float(closed.mean()) if len(closed) else None,
    }


def btc_hold(frames, start_day, last_day, cfg) -> dict:
    b = frames[BTC].loc[start_day:last_day]
    if b.empty:
        return {"value": cfg.start_cash, "ret": 0.0, "max_dd": 0.0, "curve": pd.Series(dtype=float)}
    curve = b["close"] / b["open"].iloc[0] * cfg.start_cash * (1 - cfg.fee)
    return {"value": float(curve.iloc[-1]), "ret": float(curve.iloc[-1] / cfg.start_cash - 1),
            "max_dd": float((curve / curve.cummax().clip(lower=cfg.start_cash) - 1).min()), "curve": curve}


# ---------------------------------------------------------------- rapor
def _p(v, digits=1):
    return "-" if v is None else f"{v * 100:+.{digits}f}%".replace(".", ",")


def _px(v):
    if v is None or not np.isfinite(v):
        return "-"
    if v >= 1000:
        return f"{v:,.0f}".replace(",", ".")
    if v >= 1:
        return f"{v:.2f}".replace(".", ",")
    return f"{v:.4g}".replace(".", ",")


def report(state: dict, results: dict, summary: dict, btc: dict, last_day: pd.Timestamp) -> str:
    cfg = ForwardConfig(**state["config"])
    start = pd.Timestamp(state["start_day"])
    n_days = max(0, (last_day - start).days + 1)
    m = MEASURED
    lines = [
        "# Canlı kâğıt testi — GÜÇLÜ AL / AL kuralı",
        "",
        f"Başlangıç: **{start:%d.%m.%Y}** · son tamamlanan gün: **{last_day:%d.%m.%Y}** · **{n_days} gün** · "
        f"sanal {cfg.start_cash:,.0f} $ ile, {cfg.slots} pozisyona kadar, komisyon+kayma %{cfg.fee * 100:.2f} her yön."
        .replace(",", "."),
        "",
        f"Kural: {state['rule']}. Evren: başlangıçta hacme göre ilk {len(state['universe'])} coin (sabit).",
        "",
        "| Portföy | Değer | Getiri | En büyük düşüş | Kapanan işlem | Başarı | İşlem başına ort. | Açık |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key, name in PORTFOLIOS.items():
        s = summary[key]
        win = "-" if s["win_rate"] is None else f"%{s['win_rate'] * 100:.0f}"
        lines.append(f"| {name} | {_px(s['value'])} $ | **{_p(s['ret'])}** | {_p(s['max_dd'])} | {s['closed']} | "
                     f"{win} | {_p(s['avg'])} | {s['open']} |")
    lines.append(f"| BTC al-tut | {_px(btc['value'])} $ | **{_p(btc['ret'])}** | {_p(btc['max_dd'])} | - | - | - | - |")
    lines += [
        "",
        f"**Geçmiş testte beklenen** (32 coin, 5 yıl): kazanan işlem %{m['test']['win_rate'] * 100:.0f}–"
        f"%{m['train']['win_rate'] * 100:.0f}, işlem başına ort. {_p(m['train']['avg'])} / {_p(m['test']['avg'])}, "
        f"10 pozisyonlu portföyde en büyük düşüş {_p(m['portfolio10']['train_dd'], 0)} / {_p(m['portfolio10']['test_dd'], 0)}. "
        "İşlemlerin çoğu küçük zararla kapanır; kâr az sayıdaki büyük yükselişten gelir. Bu yüzden ilk haftalarda zarar "
        "görmek normaldir; anlamlı bir karşılaştırma için en az 30–50 kapanan işlem gerekir.",
        "",
    ]
    main = results["all"]
    if n_days and summary["all"]["closed"] + summary["all"]["open"] == 0:
        lines += ["Henüz işlem yok. Kural yalnızca Bitcoin 200 günlük ortalamanın üstündeyken ve bir coin 20 günlük "
                  "zirvesini kırdığında alım yapar; böyle bir gün gelene kadar beklemek de kuralın parçasıdır.", ""]
    if main["open"]:
        lines += ["## Açık pozisyonlar (ana portföy)", "",
                  "| Coin | Sinyal | Alış günü | Alış | Son kapanış | Sonuç | Zarar-durdur | Satış seviyesi (20G) |",
                  "|---|---|---|---|---|---|---|---|"]
        for o in sorted(main["open"], key=lambda o: o["entry_day"]):
            lines.append(f"| {base_asset(o['sym'], 'USDT')} | {'GÜÇLÜ AL' if o['strong'] else 'AL'} | "
                         f"{o['entry_day']:%d.%m} | {_px(o['entry'])} | {_px(o['price'])} | {_p(o['ret'])} | "
                         f"{_px(o['stop'])} | {_px(o['exit_level'])}{' — yarın satılacak' if o['exit_pending'] else ''} |")
        lines.append("")
    if main["next_buys"] or main["next_sells"]:
        free = cfg.slots - len(main["open"]) + len(main["next_sells"])
        buys = [base_asset(s, "USDT") for s in main["next_buys"][:max(0, free)]]
        lines += [f"**Bugün açılışta:** alınacak: {', '.join(buys) or '-'} · satılacak: "
                  f"{', '.join(base_asset(s, 'USDT') for s in main['next_sells']) or '-'}", ""]
    if main["trades"]:
        lines += ["## Kapanan işlemler (ana portföy, son 30)", "",
                  "| Coin | Sinyal | Alış | Satış | Sonuç | Neden |", "|---|---|---|---|---|---|"]
        for t in main["trades"][-30:][::-1]:
            lines.append(f"| {base_asset(t['sym'], 'USDT')} | {'GÜÇLÜ AL' if t['strong'] else 'AL'} | "
                         f"{t['entry_day']:%d.%m} · {_px(t['entry'])} | {t['exit_day']:%d.%m} · {_px(t['exit'])} | "
                         f"{_p(t['ret'])} | {t['reason']} |")
        lines.append("")
    curve = pd.Series(dict(main["curve"]))
    if len(curve) > 1:
        lines += ["## Günlük değer (ana portföy, son 14 gün)", "", "| Gün | Değer | BTC al-tut |", "|---|---|---|"]
        bc = btc.get("curve", pd.Series(dtype=float))
        lines += [f"| {t:%d.%m} | {_px(v)} $ | {_px(bc.get(t)) + ' $' if t in bc.index else '-'} |"
                  for t, v in curve.iloc[-14:].items()]
        lines.append("")
    lines += ["---", "Sanal paradır; gerçek alım-satım yapılmaz. Sonuçlar her gün GitHub Actions ile güncellenir "
              "(`.github/workflows/forwardtest.yml`). Yatırım tavsiyesi değildir."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- çalıştırma
def load_frames(symbols: list[str], cfg: dict) -> dict[str, pd.DataFrame]:
    client, store = BinanceClient(), DataStore(cfg["data_dir"])
    frames = {}
    for sym in dict.fromkeys([BTC, *symbols]):
        try:
            df = store.update(sym, client, 2)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s indirilemedi: %s", sym, exc)
            continue
        if df is not None and len(df) >= 60:
            frames[sym] = score_frame(df, cfg)
    return frames


def run(state_path: Path, out: Path | None, data_dir: str = "paper/canli/data", summary_path: str | None = None) -> dict:
    cfg_app = _deep_merge(DEFAULT_CONFIG, {"data_dir": data_dir})
    fcfg = ForwardConfig()
    today = today_utc()
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        fcfg = ForwardConfig(**state["config"])
    else:
        universe = list_universe(_deep_merge(cfg_app, {"universe": {"mode": "top", "top_n": fcfg.top_n}}),
                                 BinanceClient())
        frames = load_frames(universe, cfg_app)
        try:
            mc = fetch_markets()
        except Exception as exc:  # noqa: BLE001
            log.warning("CoinGecko alınamadı: %s", exc)
            mc = {}
        calm = {s: evaluate(price_quality(df), mc.get(base_asset(s, "USDT")), market_known=bool(mc))["calm_list"]
                for s, df in frames.items()}
        # Bugünün mumu henüz kapanmadı: ilk sinyaller bugünün kapanışında değerlendirilir
        state = new_state(universe, today, calm, fcfg)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    frames = load_frames(state["universe"], cfg_app)
    start = pd.Timestamp(state["start_day"])
    last_day = today - pd.Timedelta(days=1)
    data = prepare(frames, start, last_day)
    calm = state.get("calm_at_start", {})
    results = {
        "all": simulate(data, start, last_day, fcfg),
        "strong": simulate(data, start, last_day, fcfg, allow=lambda s, row: bool(row["strong"])),
        "calm": simulate(data, start, last_day, fcfg, allow=lambda s, row: bool(calm.get(s))),
    }
    summary = {k: stats(r, fcfg) for k, r in results.items()}
    btc = btc_hold(frames, start, last_day, fcfg)
    text = report(state, results, summary, btc, last_day)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(text)
    return {"summary": summary, "btc": {k: v for k, v in btc.items() if k != "curve"}, "last_day": last_day,
            "results": results}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run"])
    ap.add_argument("--state", default="paper/canli/state.json")
    ap.add_argument("--out", default="paper/canli/RAPOR.md")
    ap.add_argument("--data-dir", default="paper/canli/data")
    ap.add_argument("--summary")
    a = ap.parse_args()
    res = run(Path(a.state), Path(a.out), a.data_dir, a.summary)
    print(json.dumps({k: v for k, v in res["summary"]["all"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
