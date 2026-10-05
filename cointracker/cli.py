"""Komut satırı arayüzü.

Örnekler:
  python -m cointracker update                 # takip listesindeki coinlerin 5 yıllık verisini indir
  python -m cointracker scan                   # tüm coinlerin güncel AL/SAT sinyalleri
  python -m cointracker analyze BTC            # tek coin için ayrıntılı analiz + nereden alınır
  python -m cointracker where SOL              # borsalar arası fiyat karşılaştırması
  python -m cointracker backtest               # stratejinin 5 yıllık geriye dönük testi
  python -m cointracker watch                  # sürekli takip ve uyarı (Telegram/Discord/Slack)
  python -m cointracker report --out rapor.html
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime

from cointracker.alerts import (
    DISCLAIMER, AlertState, broadcast, build_notifiers, fmt_pct, fmt_price, format_signal, price_alerts,
    signal_changes,
)
from cointracker.config import load_config
from cointracker.data import normalize_symbol
from cointracker.engine import CoinAnalysis, Engine
from cointracker.exchanges import get_quotes
from cointracker.report import render_report

log = logging.getLogger("cointracker")
MAX_QUOTES_PER_DIGEST = 10


def _symbols(engine: Engine, coins: list[str] | None) -> list[str]:
    quote = engine.cfg["universe"]["quote"]
    if coins:
        return [normalize_symbol(c, quote) for c in coins]
    return engine.universe()


def _prepare(engine: Engine, args) -> list[str]:
    symbols = _symbols(engine, args.coins)
    if not args.no_update:
        print(f"{len(symbols)} coin için veri güncelleniyor...")
        engine.update(symbols)
    return symbols


def _base(symbol: str, engine: Engine) -> str:
    quote = engine.cfg["universe"]["quote"]
    return symbol[: -len(quote)] if symbol.endswith(quote) else symbol


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    fmt = lambda cells: "  ".join(c.ljust(w) if i < 2 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths)))
    return "\n".join([fmt(headers), "  ".join("-" * w for w in widths), *(fmt(r) for r in rows)])


def cmd_update(engine: Engine, args) -> int:
    symbols = _symbols(engine, args.coins)
    print(f"{len(symbols)} coin için veri indiriliyor (son {engine.cfg['history_years']} yıl)...")
    counts = engine.update(symbols)
    ok = sum(1 for n in counts.values() if n)
    print(f"Tamamlandı: {ok}/{len(symbols)} coin, veri klasörü: {engine.store.dir}")
    return 0


def _signal_rows(results: list[CoinAnalysis]) -> list[list[str]]:
    rows = []
    for r in results:
        s, bt = r.signal, r.backtest
        rows.append([
            s.symbol, s.label, f"{s.score:+d}", fmt_price(s.price), fmt_pct(s.change_7d), fmt_pct(s.change_30d),
            "-" if s.rsi is None else f"{s.rsi:.0f}",
            "-" if s.pct_rank_5y is None else f"%{s.pct_rank_5y * 100:.0f}",
            fmt_pct(s.drawdown_5y),
            fmt_pct(bt.total_return) if bt else "-",
            fmt_pct(bt.buy_hold_return) if bt else "-",
        ])
    return rows


def cmd_scan(engine: Engine, args) -> int:
    symbols = _prepare(engine, args)
    results = engine.analyze_all(symbols, run_backtest=args.backtest)
    if args.only == "buy":
        results = [r for r in results if r.signal.is_buy]
    elif args.only == "sell":
        results = [r for r in results if r.signal.is_sell]
    if args.limit:
        results = results[: args.limit]
    headers = ["Coin", "Sinyal", "Puan", "Fiyat", "7g", "30g", "RSI", "5y%", "Zirveden", "Strateji", "Al-tut"]
    print(_table(headers, _signal_rows(results)))
    print(f"\n{DISCLAIMER}")
    return 0


def cmd_analyze(engine: Engine, args) -> int:
    args.coins = [args.coin]
    symbol = _prepare(engine, args)[0]
    result = engine.analyze_symbol(symbol)
    if result is None:
        print(f"{symbol} için yeterli veri bulunamadı.")
        return 1
    quotes = get_quotes(_base(symbol, engine), engine.cfg["exchanges"])
    print(format_signal(result.signal, quotes))
    bt = result.backtest
    if bt:
        print(
            f"\nGeriye dönük test ({bt.start:%Y-%m-%d} → {bt.end:%Y-%m-%d}):\n"
            f"  Strateji getirisi : {fmt_pct(bt.total_return)} (en büyük düşüş {fmt_pct(bt.max_drawdown)})\n"
            f"  Al-tut getirisi   : {fmt_pct(bt.buy_hold_return)} (en büyük düşüş {fmt_pct(bt.buy_hold_max_drawdown)})\n"
            f"  İşlem sayısı      : {bt.trades}, başarı oranı "
            f"{'-' if bt.win_rate is None else f'%{bt.win_rate * 100:.0f}'}, "
            f"piyasada kalma %{bt.exposure * 100:.0f}"
        )
    if quotes:
        print("\nBorsa fiyatları (komisyon dahil alış maliyetine göre):")
        _print_quotes(quotes)
    print(f"\n{DISCLAIMER}")
    return 0


def _print_quotes(quotes) -> None:
    rows = [
        [q.exchange, q.pair, fmt_price(q.price), f"%{q.fee * 100:.2f}", fmt_price(q.effective_buy), q.url]
        for q in quotes
    ]
    print(_table(["Borsa", "Parite", "Fiyat", "Komisyon", "Alış maliyeti", "Bağlantı"], rows))


def cmd_where(engine: Engine, args) -> int:
    coin = args.coin.upper()
    quotes = get_quotes(coin, engine.cfg["exchanges"])
    if not quotes:
        print(f"{coin} için hiçbir borsadan fiyat alınamadı.")
        return 1
    _print_quotes(quotes)
    cheapest, best_sell = quotes[0], max(quotes, key=lambda q: q.effective_sell)
    spread = (quotes[-1].effective_buy / cheapest.effective_buy - 1) * 100
    print(f"\nEn uygun alış : {cheapest.exchange} ({fmt_price(cheapest.price)})")
    print(f"En iyi satış  : {best_sell.exchange} ({fmt_price(best_sell.price)})")
    print(f"Borsalar arası fark: %{spread:.2f}")
    print("Not: USD ve USDT pariteleri yaklaşık eşdeğer kabul edilir; komisyonlar yaklaşık değerlerdir.")
    return 0


def cmd_backtest(engine: Engine, args) -> int:
    symbols = _prepare(engine, args)
    results = [r for r in engine.analyze_all(symbols) if r.backtest]
    results.sort(key=lambda r: r.backtest.total_return, reverse=True)
    rows = [
        [r.signal.symbol, f"{r.backtest.start:%Y-%m-%d}", fmt_pct(r.backtest.total_return),
         fmt_pct(r.backtest.buy_hold_return), fmt_pct(r.backtest.max_drawdown),
         fmt_pct(r.backtest.buy_hold_max_drawdown), str(r.backtest.trades),
         "-" if r.backtest.win_rate is None else f"%{r.backtest.win_rate * 100:.0f}"]
        for r in results
    ]
    print(_table(["Coin", "Başlangıç", "Strateji", "Al-tut", "Strat. MaxDD", "Al-tut MaxDD", "İşlem", "Başarı"], rows))
    if results:
        beats = sum(r.backtest.beats_buy_hold for r in results)
        safer = sum(r.backtest.max_drawdown > r.backtest.buy_hold_max_drawdown for r in results)
        print(f"\nStrateji {beats}/{len(results)} coinde al-tut'tan fazla kazandırdı, "
              f"{safer}/{len(results)} coinde daha küçük düşüş yaşattı.")
    return 0


def run_watch_cycle(engine: Engine, symbols: list[str], notifiers, state: AlertState) -> int:
    engine.update(symbols)
    results = engine.analyze_all(symbols, run_backtest=False)
    signals = [r.signal for r in results]
    changed = signal_changes(signals, state, engine.cfg)
    price_msgs = price_alerts({s.symbol: s.price for s in signals}, state, engine.cfg)
    state.save()

    if not changed and not price_msgs:
        return 0
    parts = [f"📊 Coin uyarıları — {datetime.now():%Y-%m-%d %H:%M}"]
    parts += price_msgs
    for i, sig in enumerate(sorted(changed, key=lambda s: abs(s.score), reverse=True)):
        quotes = get_quotes(_base(sig.symbol, engine), engine.cfg["exchanges"]) if i < MAX_QUOTES_PER_DIGEST else None
        parts.append(format_signal(sig, quotes))
    parts.append(DISCLAIMER)
    broadcast(notifiers, "\n\n".join(parts))
    return len(changed) + len(price_msgs)


def cmd_watch(engine: Engine, args) -> int:
    symbols = _symbols(engine, args.coins)
    notifiers = build_notifiers(engine.cfg)
    state = AlertState(engine.state_path)
    interval = args.interval or engine.cfg["watch"]["interval_minutes"]
    channels = ", ".join(type(n).__name__.replace("Notifier", "") for n in notifiers)
    print(f"{len(symbols)} coin takip ediliyor, her {interval} dakikada bir kontrol. Bildirim: {channels}")
    while True:
        try:
            n = run_watch_cycle(engine, symbols, notifiers, state)
            print(f"[{datetime.now():%H:%M}] kontrol tamamlandı, {n} uyarı gönderildi")
        except Exception as exc:  # izleme döngüsü tek bir hatada durmasın
            log.exception("İzleme turu başarısız: %s", exc)
        if args.once:
            return 0
        time.sleep(interval * 60)


def cmd_report(engine: Engine, args) -> int:
    symbols = _prepare(engine, args)
    results = engine.analyze_all(symbols)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render_report(results, engine.cfg["history_years"]))
    print(f"Rapor yazıldı: {args.out} ({len(results)} coin)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cointracker", description="5 yıllık veriye dayalı kripto takip ve uyarı sistemi")
    p.add_argument("-c", "--config", help="ayar dosyası (varsayılan: config.yaml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def with_coins(sp, update_flag: bool = True):
        sp.add_argument("--coins", nargs="+", help="yalnızca bu coinler (ör. BTC ETH SOL)")
        if update_flag:
            sp.add_argument("--no-update", action="store_true", help="veriyi indirmeden önbellekten çalış")
        return sp

    with_coins(sub.add_parser("update", help="geçmiş veriyi indir/güncelle"), update_flag=False)
    sp = with_coins(sub.add_parser("scan", help="tüm coinlerin güncel sinyalleri"))
    sp.add_argument("--only", choices=["buy", "sell"], help="yalnızca AL veya SAT sinyalleri")
    sp.add_argument("--limit", type=int, help="en fazla N satır")
    sp.add_argument("--backtest", action="store_true", help="5 yıllık strateji getirisini de göster")

    sp = sub.add_parser("analyze", help="tek coin için ayrıntılı analiz")
    sp.add_argument("coin")
    sp.add_argument("--no-update", action="store_true")
    sp = sub.add_parser("where", help="coini en uygun hangi borsadan alırım?")
    sp.add_argument("coin")
    with_coins(sub.add_parser("backtest", help="stratejinin geriye dönük testi"))

    sp = with_coins(sub.add_parser("watch", help="sürekli takip ve uyarı"), update_flag=False)
    sp.add_argument("--interval", type=int, help="dakika cinsinden kontrol aralığı")
    sp.add_argument("--once", action="store_true", help="tek tur çalış ve çık (cron için)")

    sp = with_coins(sub.add_parser("report", help="HTML rapor oluştur"))
    sp.add_argument("--out", default="report.html")
    return p


COMMANDS = {
    "update": cmd_update, "scan": cmd_scan, "analyze": cmd_analyze, "where": cmd_where,
    "backtest": cmd_backtest, "watch": cmd_watch, "report": cmd_report,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    engine = Engine(load_config(args.config))
    try:
        return COMMANDS[args.command](engine, args)
    except KeyboardInterrupt:
        return 130
