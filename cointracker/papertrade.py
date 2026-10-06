"""Kâğıt üzerinde (sanal parayla) işlem botu.

Uygulamanın sinyal puanını 15 dakikalık mumlarda kullanarak agresif gün içi alım-satım yapar.
Kurallar deney başında sabitlenir ve state dosyasına yazılır; sonradan değiştirilemez.

  Evren      : deney başında 24 saatlik hacme göre en büyük `top_n` coin (stablecoin hariç)
  Karar anı  : her 15 dakikalık mum kapanışı (yalnızca kapanmış mumlar kullanılır, geleceğe bakılmaz)
  Alım       : puan ≥ `entry_score`; en yüksek puanlılar önce; en fazla `max_positions` pozisyon,
               her biri o anki toplam değerin 1/max_positions'ı; bir sonraki dakikanın açılışından alınır
  Satım      : 1 dakikalık mumlarla kontrol: zarar-durdur (giriş − sl_atr×ATR) ya da kâr-al (giriş + tp_atr×ATR);
               karar anında puan ≤ `exit_score` ya da `max_hold_min` dakika dolduysa piyasa fiyatından çıkış
  Masraflar  : her alım ve satımda komisyon + kayma (slippage). Kaldıraç yok, açığa satış yok.

Kullanım:
  python -m cointracker.papertrade start  --hours 24 --state paper/state.json   # deneyi başlat (bir sonraki 15dk'dan)
  python -m cointracker.papertrade step   --state paper/state.json              # şimdiye kadar ilerlet
  python -m cointracker.papertrade replay --hours 24 --state paper/replay.json  # geçmiş 24 saati aynı kurallarla prova et
  python -m cointracker.papertrade report --state paper/state.json --out paper/rapor.md
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
from cointracker.data import BinanceClient, base_asset, list_universe
from cointracker.indicators import add_indicators
from cointracker.signals import score_frame

log = logging.getLogger(__name__)
STEP = pd.Timedelta(minutes=15)
MINUTE = pd.Timedelta(minutes=1)
BENCHMARK = "BTCUSDT"


@dataclass(frozen=True)
class PaperConfig:
    start_cash: float = 1000.0
    top_n: int = 15
    max_positions: int = 4
    entry_score: int = 2
    exit_score: int = -1
    tp_atr: float = 1.5
    sl_atr: float = 1.0
    max_hold_min: int = 120
    fee: float = 0.001
    slippage: float = 0.0005
    lookback: int = 300  # gösterge hesabı için kullanılan 15 dakikalık mum sayısı
    min_order: float = 10.0


def _ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_convert(None) if ts.tzinfo else ts


def _utc(ts: pd.Timestamp) -> datetime:
    return ts.tz_localize("UTC").to_pydatetime()


def new_state(cfg: PaperConfig, start: pd.Timestamp, hours: float, universe: list[str]) -> dict:
    start = _ts(start).floor("15min")
    return {
        "version": 1,
        "config": asdict(cfg),
        "start": start.isoformat(),
        "end": (start + pd.Timedelta(hours=hours)).isoformat(),
        "cursor": start.isoformat(),
        "cash": cfg.start_cash,
        "fees_paid": 0.0,
        "positions": {},
        "trades": [],
        "equity": [[start.isoformat(), cfg.start_cash]],
        "universe": universe,
        "benchmark": {},
        "finished": False,
    }


def scores(df15: pd.DataFrame, app_cfg: dict) -> pd.DataFrame:
    """15 dakikalık mumlara uygulamanın puanını uygular (5 yıllık değer bileşeni gün içinde kapalı)."""
    ind = add_indicators(df15)
    ind["pct_rank_5y"] = np.nan
    return score_frame(ind, app_cfg)[["close", "score", "atr"]]


class PaperTrader:
    def __init__(self, state: dict, app_cfg: dict | None = None):
        self.s = state
        self.cfg = PaperConfig(**state["config"])
        self.app_cfg = app_cfg or DEFAULT_CONFIG

    # ---- muhasebe ----------------------------------------------------------
    def _buy(self, sym: str, t: pd.Timestamp, price: float, amount: float, atr: float, score: int) -> None:
        c = self.cfg
        entry = price * (1 + c.slippage)
        fee = amount * c.fee
        self.s["cash"] -= amount
        self.s["fees_paid"] += fee
        self.s["positions"][sym] = {
            "entry_time": t.isoformat(), "entry": entry, "qty": (amount - fee) / entry, "cost": amount,
            "stop": entry - c.sl_atr * atr, "target": entry + c.tp_atr * atr, "score": score,
        }

    def _sell(self, sym: str, t: pd.Timestamp, price: float, reason: str) -> None:
        c = self.cfg
        pos = self.s["positions"].pop(sym)
        gross = pos["qty"] * price * (1 - c.slippage)
        fee = gross * c.fee
        proceeds = gross - fee
        self.s["cash"] += proceeds
        self.s["fees_paid"] += fee
        self.s["trades"].append({
            "symbol": sym, "entry_time": pos["entry_time"], "entry": pos["entry"], "exit_time": t.isoformat(),
            "exit": price * (1 - c.slippage), "qty": pos["qty"], "cost": pos["cost"], "proceeds": proceeds,
            "pnl": proceeds - pos["cost"], "pnl_pct": proceeds / pos["cost"] - 1, "reason": reason,
            "score": pos["score"],
        })

    def equity(self, prices: dict[str, float]) -> float:
        return self.s["cash"] + sum(p["qty"] * prices.get(sym, p["entry"]) for sym, p in self.s["positions"].items())

    # ---- simülasyon ----------------------------------------------------------
    def advance(self, client, now: pd.Timestamp) -> bool:
        """Deneyi `now` anına kadar ilerletir. İlerleme olduysa True döner."""
        if self.s["finished"]:
            return False
        c = self.cfg
        cursor, end = _ts(self.s["cursor"]), _ts(self.s["end"])
        limit = min(_ts(now).floor("1min"), end)  # bu andan önce açılan 1dk mumlar kapanmıştır
        if cursor + STEP > limit:
            return False

        symbols = list(dict.fromkeys(self.s["universe"] + [BENCHMARK]))
        d15, d1 = {}, {}
        for sym in symbols:
            try:
                # Göstergeler her turda deney başlangıcına göre sabit bir noktadan hesaplanır; böylece deneyi
                # tek seferde ya da düzensiz aralıklarla parça parça ilerletmek birebir aynı sonucu verir.
                anchor = _ts(self.s["start"]) - c.lookback * STEP
                d15[sym] = scores(client.klines(sym, _utc(anchor), _utc(limit), "15m"), self.app_cfg)
                d1[sym] = client.klines(sym, _utc(cursor), _utc(limit - pd.Timedelta(milliseconds=1)), "1m")
            except Exception as exc:  # tek coinin verisi gelmezse o coin bu turda atlanır
                log.warning("%s verisi alınamadı: %s", sym, exc)
        bench = d1.get(BENCHMARK)
        if "btc_start" not in self.s["benchmark"] and bench is not None and cursor in bench.index:
            self.s["benchmark"]["btc_start"] = float(bench.loc[cursor, "open"])

        last_price: dict[str, float] = {}
        t = cursor
        while t + STEP <= limit:
            self._decide(t, d15, d1)
            for sym in list(self.s["positions"]):
                self._watch(sym, t, d1)
            t += STEP
            for sym, df in d1.items():
                done = df.loc[:t - MINUTE]
                if not done.empty:
                    last_price[sym] = float(done["close"].iloc[-1])
            self.s["cursor"] = t.isoformat()
            self.s["equity"].append([t.isoformat(), self.equity(last_price)])

        if t >= end:
            for sym in list(self.s["positions"]):
                self._sell(sym, end, last_price.get(sym, self.s["positions"][sym]["entry"]), "deney sonu")
            if BENCHMARK in last_price:
                self.s["benchmark"]["btc_end"] = last_price[BENCHMARK]
            self.s["equity"][-1][1] = self.s["cash"]
            self.s["finished"] = True
        return True

    def _decide(self, t: pd.Timestamp, d15: dict, d1: dict) -> None:
        c = self.cfg
        bar = t - STEP  # t anında kapanan 15dk mum

        def row(sym):
            df = d15.get(sym)
            return df.loc[bar] if df is not None and bar in df.index else None

        def open_at(sym):
            df = d1.get(sym)
            return float(df.loc[t, "open"]) if df is not None and t in df.index else None

        # 1) Sinyal bozulan ya da süresi dolan pozisyonlardan çık
        for sym in list(self.s["positions"]):
            r, px = row(sym), open_at(sym)
            if px is None:
                continue
            held = t - _ts(self.s["positions"][sym]["entry_time"])
            if held >= pd.Timedelta(minutes=c.max_hold_min):
                self._sell(sym, t, px, "süre doldu")
            elif r is not None and r["score"] <= c.exit_score:
                self._sell(sym, t, px, "sinyal döndü")

        # 2) Yeni alımlar: puanı en yüksek olanlar önce
        free = c.max_positions - len(self.s["positions"])
        if free <= 0:
            return
        candidates = []
        for sym in self.s["universe"]:
            r = row(sym)
            if sym in self.s["positions"] or r is None or pd.isna(r["atr"]) or r["score"] < c.entry_score:
                continue
            candidates.append((int(r["score"]), sym, float(r["atr"])))
        prices = {s: open_at(s) for s in self.s["positions"]}
        equity = self.equity({k: v for k, v in prices.items() if v})
        for score, sym, atr in sorted(candidates, reverse=True)[:free]:
            px = open_at(sym)
            amount = min(equity / c.max_positions, self.s["cash"])
            if px is None or amount < c.min_order:
                continue
            self._buy(sym, t, px, amount, atr, score)

    def _watch(self, sym: str, t: pd.Timestamp, d1: dict) -> None:
        """t ile t+15dk arasındaki 1dk mumlarda zarar-durdur / kâr-al kontrolü (aynı dakikada ikisi: önce stop)."""
        df = d1.get(sym)
        if df is None:
            return
        pos = self.s["positions"][sym]
        for ts, b in df.loc[t:t + STEP - MINUTE].iterrows():
            if b["low"] <= pos["stop"]:
                self._sell(sym, ts, min(b["open"], pos["stop"]), "zarar-durdur")
                return
            if b["high"] >= pos["target"]:
                self._sell(sym, ts, max(b["open"], pos["target"]), "kâr-al")
                return


def summary(state: dict) -> dict:
    trades = state["trades"]
    start_cash = state["config"]["start_cash"]
    final = state["equity"][-1][1]
    eq = np.array([e for _, e in state["equity"]])
    peaks = np.maximum.accumulate(eq) if eq.size else eq
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    bench = state.get("benchmark", {})
    btc = bench["btc_end"] / bench["btc_start"] - 1 if {"btc_start", "btc_end"} <= bench.keys() else None
    return {
        "start": state["start"], "end": state["end"], "finished": state["finished"], "cursor": state["cursor"],
        "start_cash": start_cash, "equity": final, "pnl": final - start_cash, "pnl_pct": final / start_cash - 1,
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else None,
        "avg_win": float(np.mean([t["pnl"] for t in wins])) if wins else None,
        "avg_loss": float(np.mean([t["pnl"] for t in losses])) if losses else None,
        "best": max(trades, key=lambda t: t["pnl"]) if trades else None,
        "worst": min(trades, key=lambda t: t["pnl"]) if trades else None,
        "fees": state["fees_paid"], "max_drawdown": float((eq / peaks - 1).min()) if eq.size else 0.0,
        "open_positions": len(state["positions"]), "btc_return": btc,
        "reasons": pd.Series([t["reason"] for t in trades]).value_counts().to_dict() if trades else {},
    }


def _money(v: float) -> str:
    return f"{v:+,.2f} $".replace(",", "X").replace(".", ",").replace("X", ".")


def _price(p: float) -> str:
    return f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if p >= 1 else f"{p:.6g}".replace(".", ",")


def report(state: dict, title: str = "Kâğıt üzerinde işlem raporu") -> str:
    s = summary(state)
    c = state["config"]
    tr = lambda iso: _ts(iso).strftime("%d.%m %H:%M")
    pct = lambda v: "-" if v is None else f"%{v * 100:+.2f}".replace(".", ",")
    status = "" if s["finished"] else f" — devam ediyor, son güncelleme {tr(s['cursor'])}"
    win = "-" if s["win_rate"] is None else f"%{s['win_rate'] * 100:.0f}"
    lines = [
        f"# {title}",
        "",
        f"**Dönem:** {tr(s['start'])} → {tr(s['end'])} (UTC){status}",
        f"**Başlangıç:** {s['start_cash']:.2f} $ · **{'Bitiş' if s['finished'] else 'Şu an'}:** {s['equity']:.2f} $ · "
        f"**Sonuç:** {_money(s['pnl'])} ({pct(s['pnl_pct'])})",
        f"**İşlem:** {s['trades']} (kazanan {s['wins']}, kaybeden {s['losses']}, başarı {win}) · "
        f"**Ödenen komisyon:** {s['fees']:.2f} $ · **En büyük düşüş:** {pct(s['max_drawdown'])}",
        f"**Karşılaştırma:** Aynı sürede Bitcoin'i alıp tutmak: {pct(s['btc_return'])}",
        "",
        f"Kurallar: en hacimli {c['top_n']} coin, 15dk puan ≥ +{c['entry_score']} alım, en fazla {c['max_positions']} pozisyon "
        f"(her biri değerin 1/{c['max_positions']}'ü), kâr-al {c['tp_atr']}×ATR, zarar-durdur {c['sl_atr']}×ATR, "
        f"puan ≤ {c['exit_score']} ya da {c['max_hold_min']} dk sonunda çıkış, komisyon %{c['fee'] * 100:.2f} + kayma "
        f"%{c['slippage'] * 100:.2f}, kaldıraç yok.",
        "",
        "| # | Coin | Alış (UTC) | Alış fiyatı | Satış (UTC) | Satış fiyatı | Neden | Tutar | Sonuç |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, t in enumerate(state["trades"], 1):
        lines.append(
            f"| {i} | {base_asset(t['symbol'])} | {tr(t['entry_time'])} | {_price(t['entry'])} | {tr(t['exit_time'])} | "
            f"{_price(t['exit'])} | {t['reason']} | {t['cost']:.0f} $ | {_money(t['pnl'])} ({pct(t['pnl_pct'])}) |"
        )
    if state["positions"]:
        lines += ["", "**Açık pozisyonlar:** " + ", ".join(
            f"{base_asset(sym)} ({_price(p['entry'])}'den, {tr(p['entry_time'])})" for sym, p in state["positions"].items())]
    return "\n".join(lines) + "\n"


def load_state(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="papertrade")
    ap.add_argument("command", choices=["start", "step", "replay", "report"])
    ap.add_argument("--state", default="paper/state.json")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--out", help="raporu bu dosyaya yaz")
    ap.add_argument("--summary", help="raporu bu dosyanın sonuna ekle (GitHub iş özeti)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    path = Path(args.state)
    state = load_state(path)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    client = BinanceClient()
    title = "Kâğıt üzerinde işlem raporu"

    if args.command in ("start", "replay"):
        if state and not state["finished"] and args.command == "start":
            print(f"Zaten süren bir deney var ({state['start']} → {state['end']}); yeni deney başlatılmadı.")
        else:
            cfg = PaperConfig()
            universe = list_universe(_deep_merge(DEFAULT_CONFIG, {"universe": {"mode": "top", "top_n": cfg.top_n}}), client)
            start = (now.floor("15min") - pd.Timedelta(hours=args.hours)) if args.command == "replay" \
                else now.floor("15min") + STEP
            state = new_state(cfg, start, args.hours, universe)
            print(f"Deney: {state['start']} → {state['end']} UTC, coinler: {', '.join(base_asset(s) for s in universe)}")
    if args.command == "replay":
        title = "Prova: geçmiş 24 saat (aynı kurallar)"
    if state is None:
        print("Kayıtlı deney yok.")
        return 0
    if args.command in ("step", "replay", "start"):
        if PaperTrader(state).advance(client, now):
            print(f"İlerletildi: {state['cursor']} (işlem sayısı {len(state['trades'])}, nakit {state['cash']:.2f} $)")
        save_state(path, state)

    text = report(state, title)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
