"""Tarayıcıda açılan canlı panel.

Python tarafı 5 yıllık analizleri, gün içi sinyalleri, emir defterini ve borsa fiyatlarını JSON olarak sunar.
Anlık fiyatlar tarayıcı tarafından doğrudan Binance WebSocket akışından (saniyede bir) alınır.
Yalnızca bu bilgisayardan erişilebilir (127.0.0.1).
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import webbrowser
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from cointracker.data import base_asset, normalize_symbol
from cointracker.engine import CoinAnalysis, Engine
from cointracker.exchanges import get_quotes
from cointracker.indicators import add_indicators
from cointracker.live import INTRADAY_TIMEFRAMES, orderbook_summary, ticker_summary, timeframe_signal

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/vendor/lightweight-charts.js": ("vendor/lightweight-charts.standalone.production.js",
                                      "application/javascript; charset=utf-8"),
}
CHART_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d", "1w"}
DETAIL_TTL_SECONDS = 20


def clean(obj):
    """JSON'a çevrilebilir hale getirir (NaN -> null, numpy/pandas tipleri -> Python)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _epoch_seconds(index: pd.DatetimeIndex) -> np.ndarray:
    return ((index - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy()


def _line(times: np.ndarray, values: pd.Series) -> list[dict]:
    return [{"time": int(t), "value": float(v)} for t, v in zip(times, values.to_numpy()) if np.isfinite(v)]


class DashboardApp:
    def __init__(self, cfg: dict, engine: Engine | None = None, symbols: list[str] | None = None):
        self.cfg = cfg
        self.engine = engine or Engine(cfg)
        self.quote = cfg["universe"]["quote"]
        self.fixed_symbols = [normalize_symbol(s, self.quote) for s in symbols] if symbols else None
        self.lock = threading.Lock()
        self.results: dict[str, CoinAnalysis] = {}
        self.status = {"phase": "starting", "done": 0, "total": 0, "updated": None, "error": None}
        self._detail_cache: dict[str, tuple[float, dict]] = {}

    # ---- arka plan yenileme -------------------------------------------------
    def _set_status(self, **kw) -> None:
        with self.lock:
            self.status.update(kw)

    def refresh(self, update: bool = True) -> None:
        try:
            symbols = self.fixed_symbols or self.engine.universe()
            self._set_status(phase="downloading" if update else "analyzing", done=0, total=len(symbols), error=None)
            if update:
                self.engine.update(symbols, progress=lambda d, t: self._set_status(done=d))
            self._set_status(phase="analyzing")
            results = self.engine.analyze_all(symbols)
            with self.lock:
                self.results = {r.signal.symbol: r for r in results}
            self._set_status(phase="ready", updated=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            log.exception("Panel verisi yenilenemedi")
            self._set_status(phase="ready" if self.results else "error", error=str(exc))

    def start_background(self, refresh_minutes: float, update: bool = True) -> threading.Thread:
        def loop() -> None:
            first = True
            while True:
                self.refresh(update=update or not first)
                first = False
                time.sleep(refresh_minutes * 60)

        thread = threading.Thread(target=loop, daemon=True, name="dashboard-refresh")
        thread.start()
        return thread

    # ---- API ----------------------------------------------------------------
    def signals_payload(self) -> dict:
        with self.lock:
            results = sorted(self.results.values(), key=lambda r: r.signal.score, reverse=True)
            status = dict(self.status)
        items = []
        for r in results:
            s, bt = r.signal, r.backtest
            items.append({
                "symbol": s.symbol, "base": base_asset(s.symbol, self.quote), "price": s.price,
                "score": s.score, "level": s.level, "label": s.label, "rsi": s.rsi,
                "pct_rank_5y": s.pct_rank_5y, "drawdown_5y": s.drawdown_5y,
                "change_7d": s.change_7d, "change_30d": s.change_30d,
                "bt_return": bt.total_return if bt else None,
                "bh_return": bt.buy_hold_return if bt else None,
                "win_rate": bt.win_rate if bt else None,
            })
        return clean({"status": status, "items": items})

    def coin_payload(self, symbol: str) -> dict:
        cached = self._detail_cache.get(symbol)
        if cached and time.time() - cached[0] < DETAIL_TTL_SECONDS:
            return cached[1]

        with self.lock:
            analysis = self.results.get(symbol)
        if analysis is None:
            self.engine.update([symbol])
            analysis = self.engine.analyze_symbol(symbol)
        client = self.engine.client
        errors: list[str] = []

        timeframes = []
        for interval, label in INTRADAY_TIMEFRAMES:
            try:
                timeframes.append(timeframe_signal(client.recent_klines(symbol, interval, 500), self.cfg, interval, label))
            except Exception as exc:
                errors.append(f"{label} verisi alınamadı: {exc}")
        if analysis:
            s = analysis.signal
            timeframes.append({
                "interval": "1d", "label": "1 gün", "score": s.score, "level": s.level, "level_label": s.label,
                "rsi": s.rsi, "trend": None, "macd": None, "change": None,
            })

        orderbook, ticker = {}, {}
        try:
            orderbook = orderbook_summary(client.depth(symbol))
        except Exception as exc:
            errors.append(f"Emir defteri alınamadı: {exc}")
        try:
            ticker = ticker_summary(client.ticker_24h(symbol))
        except Exception as exc:
            errors.append(f"24 saatlik özet alınamadı: {exc}")
        quotes = [
            {**asdict(q), "effective_buy": q.effective_buy, "effective_sell": q.effective_sell}
            for q in get_quotes(base_asset(symbol, self.quote), self.cfg["exchanges"])
        ]

        payload = clean({
            "symbol": symbol,
            "base": base_asset(symbol, self.quote),
            "signal": asdict(analysis.signal) | {"label": analysis.signal.label} if analysis else None,
            "backtest": asdict(analysis.backtest) if analysis and analysis.backtest else None,
            "timeframes": timeframes,
            "orderbook": orderbook,
            "ticker": ticker,
            "quotes": quotes,
            "errors": errors,
        })
        self._detail_cache[symbol] = (time.time(), payload)
        return payload

    def klines_payload(self, symbol: str, interval: str) -> dict:
        if interval == "1d":
            df = self.engine.store.load(symbol)
            if df is None:
                df = self.engine.client.recent_klines(symbol, "1d", 1000)
        else:
            df = self.engine.client.recent_klines(symbol, interval, 1000)
        if df is None or df.empty:
            return {"candles": []}
        ind = add_indicators(df)
        times = _epoch_seconds(ind.index)
        candles = [
            {"time": int(t), "open": o, "high": h, "low": lo, "close": c, "volume": v}
            for t, o, h, lo, c, v in zip(
                times, *(ind[k].to_numpy(dtype=float).tolist() for k in ("open", "high", "low", "close", "volume"))
            )
        ]
        return {
            "candles": candles,
            "sma50": _line(times, ind["sma50"]),
            "sma200": _line(times, ind["sma200"]),
            "bb_upper": _line(times, ind["bb_upper"]),
            "bb_lower": _line(times, ind["bb_lower"]),
        }


def make_handler(app: DashboardApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in STATIC_FILES:
                    name, ctype = STATIC_FILES[url.path]
                    self._send(200, (WEB_DIR / name).read_bytes(), ctype)
                elif url.path == "/api/signals":
                    self._json(app.signals_payload())
                elif url.path == "/api/coin":
                    self._json(app.coin_payload(normalize_symbol(query.get("symbol", "BTC"), app.quote)))
                elif url.path == "/api/klines":
                    interval = query.get("interval", "1d")
                    if interval not in CHART_INTERVALS:
                        self._json({"error": "geçersiz zaman dilimi"}, 400)
                        return
                    self._json(app.klines_payload(normalize_symbol(query.get("symbol", "BTC"), app.quote), interval))
                else:
                    self._json({"error": "bulunamadı"}, 404)
            except Exception as exc:
                log.warning("%s isteği başarısız: %s", url.path, exc)
                self._json({"error": str(exc)}, 502)

        def log_message(self, fmt, *args) -> None:
            log.debug(fmt, *args)

    return Handler


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8050, open_browser: bool = True,
          symbols: list[str] | None = None, refresh_minutes: float = 15, update: bool = True) -> None:
    app = DashboardApp(cfg, symbols=symbols)
    app.start_background(refresh_minutes, update=update)
    server = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{server.server_address[1]}"
    print(f"Canlı panel çalışıyor: {url}  (kapatmak için Ctrl+C)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
