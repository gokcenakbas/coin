"""Canlı panelin sunucusu (hem tarayıcıda hem de Mac uygulamasının penceresinde kullanılır).

Python tarafı 5 yıllık analizleri, gün içi sinyalleri, emir defterini, borsa fiyatlarını,
takip listesini ve fiyat alarmlarını JSON olarak sunar. Anlık fiyatlar arayüz tarafından
doğrudan Binance WebSocket akışından (saniyede bir) alınır.
Yalnızca bu bilgisayardan erişilebilir (127.0.0.1).
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from cointracker.data import base_asset, normalize_symbol
from cointracker.engine import CoinAnalysis, Engine
from cointracker.exchanges import get_quotes
from cointracker.indicators import add_indicators
from cointracker.live import INTRADAY_TIMEFRAMES, orderbook_summary, ticker_summary, timeframe_signal
from cointracker.signals import LABELS_TR

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/vendor/lightweight-charts.js": ("vendor/lightweight-charts.standalone.production.js",
                                      "application/javascript; charset=utf-8"),
}
CHART_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d", "1w"}
DETAIL_TTL_SECONDS = 20
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_BODY = 16_384

Notifier = Callable[[str, str], object]


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


class WatchStore:
    """Takip listesi (favoriler) ve coin bazlı fiyat alarmları: data/watchlist.json"""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data: dict = {"favorites": [], "alarms": [], "settings": {"capital": None, "risk_pct": 1.0}}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                log.warning("%s okunamadı, boş liste ile başlanıyor", path)

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.data))

    def favorites(self) -> set[str]:
        with self.lock:
            return set(self.data["favorites"])

    def apply(self, body: dict, quote: str) -> dict:
        action = body.get("action")
        with self.lock:
            if action == "toggle":
                sym = normalize_symbol(str(body["symbol"]), quote)
                favs = self.data["favorites"]
                favs.remove(sym) if sym in favs else favs.append(sym)
            elif action == "add_alarm":
                kind = body.get("kind")
                price = float(body["price"])
                if kind not in ("above", "below") or not math.isfinite(price) or price <= 0:
                    raise ValueError("geçersiz alarm")
                sym = normalize_symbol(str(body["symbol"]), quote)
                duplicate = any(a["symbol"] == sym and a["kind"] == kind and math.isclose(a["price"], price)
                                for a in self.data["alarms"])
                if not duplicate:
                    self.data["alarms"].append({
                        "id": uuid.uuid4().hex[:12], "symbol": sym, "kind": kind, "price": price,
                        "created": datetime.now(timezone.utc).isoformat(),
                    })
            elif action == "settings":
                capital = body.get("capital")
                capital = None if capital in (None, "") else float(capital)
                risk = float(body.get("risk_pct", 1.0))
                if (capital is not None and (not math.isfinite(capital) or capital < 0)) or not 0.1 <= risk <= 10:
                    raise ValueError("geçersiz ayar")
                self.data["settings"] = {"capital": capital, "risk_pct": risk}
            elif action == "remove_alarm":
                self.data["alarms"] = [a for a in self.data["alarms"] if a["id"] != body.get("id")]
            else:
                raise ValueError("bilinmeyen işlem")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.snapshot()


class DashboardApp:
    def __init__(self, cfg: dict, engine: Engine | None = None, symbols: list[str] | None = None,
                 notifier: Notifier | None = None, workers: int = 4):
        self.cfg = cfg
        self.engine = engine or Engine(cfg)
        self.quote = cfg["universe"]["quote"]
        self.fixed_symbols = [normalize_symbol(s, self.quote) for s in symbols] if symbols else None
        self.notifier = notifier
        self.workers = workers
        self.lock = threading.Lock()
        self.universe: list[str] = []
        self.results: dict[str, CoinAnalysis] = {}
        self.no_data: set[str] = set()
        self.status = {"phase": "starting", "done": 0, "total": 0, "updated": None, "error": None}
        self.watch = WatchStore(Path(cfg["data_dir"]) / "watchlist.json")
        self._detail_cache: dict[str, tuple[float, dict]] = {}
        self._refresh_lock = threading.Lock()

    # ---- arka plan yenileme -------------------------------------------------
    def _set_status(self, **kw) -> None:
        with self.lock:
            self.status.update(kw)

    def notify(self, title: str, body: str) -> bool:
        if not self.notifier:
            return False
        try:
            self.notifier(title, body)
            return True
        except Exception as exc:
            log.warning("Bildirim gönderilemedi: %s", exc)
            return False

    def _process(self, symbol: str, update: bool) -> CoinAnalysis | None:
        if update:
            self.engine.update_one(symbol)
        return self.engine.analyze_symbol(symbol)

    def refresh(self, update: bool = True) -> bool:
        """Coinleri tek tek günceller ve analiz eder; her coin bittiği anda panelde görünür.

        Zaten bir yenileme sürüyorsa hiçbir şey yapmaz ve False döner.
        """
        if not self._refresh_lock.acquire(blocking=False):
            return False
        try:
            self._refresh(update)
        finally:
            self._refresh_lock.release()
        return True

    def refresh_now(self) -> bool:
        """Arayüzdeki "Yenile" düğmesi: yenilemeyi arka planda başlatır (sürüyorsa yeniden başlatmaz)."""
        if self._refresh_lock.locked():
            return False
        self._detail_cache.clear()
        threading.Thread(target=self.refresh, daemon=True, name="dashboard-manual-refresh").start()
        return True

    def _refresh(self, update: bool) -> None:
        try:
            symbols = self.fixed_symbols or self.engine.universe()
            with self.lock:
                self.universe = symbols
                previous = {s: r.signal.level for s, r in self.results.items()}
                self.status.update(phase="downloading" if update else "analyzing", done=0,
                                   total=len(symbols), error=None)
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {pool.submit(self._process, s, update): s for s in symbols}
                for fut in as_completed(futures):
                    sym = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:
                        log.warning("%s analiz edilemedi: %s", sym, exc)
                        result = None
                    with self.lock:
                        if result is not None:
                            self.results[sym] = result
                            self.no_data.discard(sym)
                        elif sym not in self.results:
                            self.no_data.add(sym)
                        self.status["done"] += 1
            with self.lock:
                keep = set(symbols)
                self.results = {s: r for s, r in self.results.items() if s in keep}
                self.no_data &= keep
            self._set_status(phase="ready", updated=datetime.now(timezone.utc).isoformat())
            self._notify_level_changes(previous)
        except Exception as exc:
            log.exception("Panel verisi yenilenemedi")
            self._set_status(phase="ready" if self.results else "error", error=str(exc))

    def _notify_level_changes(self, previous: dict[str, str]) -> None:
        """Takip listesindeki coinlerin sinyali değiştiyse bildirim gönderir."""
        favorites = self.watch.favorites()
        with self.lock:
            changes = [
                r.signal for s, r in self.results.items()
                if s in favorites and s in previous and previous[s] != r.signal.level
            ]
        for sig in changes:
            self.notify(
                f"{base_asset(sig.symbol, self.quote)}: {LABELS_TR[previous[sig.symbol]]} → {sig.label}",
                f"Puan {sig.score:+d} · fiyat {sig.price:g} USDT",
            )

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
    @staticmethod
    def _summary(r: CoinAnalysis, base: str) -> dict:
        s, bt = r.signal, r.backtest
        return {
            "symbol": s.symbol, "base": base, "price": s.price,
            "score": s.score, "level": s.level, "label": s.label, "rsi": s.rsi,
            "pct_rank_5y": s.pct_rank_5y, "drawdown_5y": s.drawdown_5y,
            "change_7d": s.change_7d, "change_30d": s.change_30d,
            "bt_return": bt.total_return if bt else None,
            "bh_return": bt.buy_hold_return if bt else None,
            "win_rate": bt.win_rate if bt else None,
            "plan": {k: s.plan[k] for k in ("action", "buy_low", "buy_high", "target1", "stop", "in_buy_zone")}
            if s.plan else None,
        }

    @staticmethod
    def _reliability(results: dict[str, CoinAnalysis]) -> dict | None:
        """Tüm coinlerde (gün ağırlıklı): AL sonrası yükselme, SAT sonrası düşme ve rastgele gün oranları."""
        totals = {k: [0, 0.0] for k in ("buy", "sell", "all")}
        horizon = None
        for r in results.values():
            edge = r.signal.edge or {}
            horizon = edge.get("horizon", horizon)
            for key in totals:
                st = edge.get(key) or {}
                if st.get("count"):
                    totals[key][0] += st["count"]
                    totals[key][1] += st["count"] * st["win_rate"]
        if not totals["all"][0]:
            return None
        rise = {k: (w / n if n else None) for k, (n, w) in totals.items()}
        return {
            "horizon": horizon,
            "buy_rise": rise["buy"], "buy_days": totals["buy"][0],
            "sell_fall": None if rise["sell"] is None else 1 - rise["sell"], "sell_days": totals["sell"][0],
            "all_rise": rise["all"], "all_fall": 1 - rise["all"], "coins": len(results),
        }

    def signals_payload(self) -> dict:
        with self.lock:
            results = dict(self.results)
            universe = list(self.universe)
            no_data = set(self.no_data)
            status = dict(self.status)
        status["app"] = self.notifier is not None
        status["market"] = next((r.signal.market for r in results.values() if r.signal.market), None)
        status["reliability"] = self._reliability(results)
        items = [self._summary(r, base_asset(s, self.quote)) for s, r in results.items()]
        for sym in universe:
            if sym in results:
                continue
            level, label = ("NODATA", "Yeni coin") if sym in no_data else ("PENDING", "Hazırlanıyor")
            items.append({"symbol": sym, "base": base_asset(sym, self.quote), "price": None, "score": None,
                          "level": level, "label": label})
        items.sort(key=lambda i: (i["score"] is None, -(i["score"] or 0)))
        return clean({"status": status, "items": items, "watch": self.watch.snapshot()})

    def coin_payload(self, symbol: str) -> dict:
        cached = self._detail_cache.get(symbol)
        if cached and time.time() - cached[0] < DETAIL_TTL_SECONDS:
            return cached[1]

        with self.lock:
            analysis = self.results.get(symbol)
        if analysis is None:
            analysis = self._process(symbol, update=True)
            if analysis is not None:
                with self.lock:
                    self.results[symbol] = analysis
                    self.no_data.discard(symbol)
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


def make_handler(app: DashboardApp, loopback_only: bool = True) -> type[BaseHTTPRequestHandler]:
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

        def _host_ok(self) -> bool:
            # DNS rebinding'e karşı: yalnızca localhost adına gelen istekleri kabul et
            if not loopback_only:
                return True
            host = self.headers.get("Host", "")
            host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
            return host in LOOPBACK_HOSTS

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                self._json({"error": "izin yok"}, 403)
                return
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
                elif url.path == "/api/watchlist":
                    self._json(app.watch.snapshot())
                else:
                    self._json({"error": "bulunamadı"}, 404)
            except Exception as exc:
                log.warning("%s isteği başarısız: %s", url.path, exc)
                self._json({"error": str(exc)}, 502)

        def do_POST(self) -> None:  # noqa: N802
            # application/json zorunluluğu, başka sitelerin tarayıcı üzerinden istek atmasını engeller (CORS ön kontrolü)
            if not self._host_ok() or not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json({"error": "izin yok"}, 403)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._json({"error": "istek çok büyük"}, 413)
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/watchlist":
                    self._json(app.watch.apply(body, app.quote))
                elif path == "/api/refresh":
                    self._json({"started": app.refresh_now()})
                elif path == "/api/notify":
                    self._json({"ok": app.notify(str(body.get("title", ""))[:200], str(body.get("body", ""))[:500])})
                else:
                    self._json({"error": "bulunamadı"}, 404)
            except (ValueError, KeyError, TypeError) as exc:
                self._json({"error": str(exc)}, 400)

        def log_message(self, fmt, *args) -> None:
            log.debug(fmt, *args)

    return Handler


def start_server(cfg: dict, host: str = "127.0.0.1", port: int = 0, symbols: list[str] | None = None,
                 refresh_minutes: float = 15, update: bool = True, notifier: Notifier | None = None,
                 ) -> tuple[DashboardApp, ThreadingHTTPServer, str]:
    """Paneli arka planda başlatır; (uygulama, sunucu, adres) döndürür. port=0 boş bir port seçer."""
    app = DashboardApp(cfg, symbols=symbols, notifier=notifier)
    app.start_background(refresh_minutes, update=update)
    server = ThreadingHTTPServer((host, port), make_handler(app, loopback_only=host in LOOPBACK_HOSTS))
    threading.Thread(target=server.serve_forever, daemon=True, name="dashboard-http").start()
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    return app, server, f"http://{shown}:{server.server_address[1]}/"


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8050, open_browser: bool = True,
          symbols: list[str] | None = None, refresh_minutes: float = 15, update: bool = True) -> None:
    _, server, url = start_server(cfg, host, port, symbols, refresh_minutes, update)
    print(f"Canlı panel çalışıyor: {url}  (kapatmak için Ctrl+C)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        while True:
            time.sleep(3600)
    finally:
        server.shutdown()
        server.server_close()
