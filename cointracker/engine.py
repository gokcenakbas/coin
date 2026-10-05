"""Veri güncelleme + sinyal + geriye dönük test adımlarını tüm coinler için birlikte çalıştırır."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cointracker.backtest import BacktestResult, backtest
from cointracker.data import BinanceClient, DataStore, list_universe
from cointracker.signals import Signal, analyze, score_frame

log = logging.getLogger(__name__)
MIN_DAYS = 220  # SMA200 için asgari gün sayısı


@dataclass
class CoinAnalysis:
    signal: Signal
    backtest: BacktestResult | None


class Engine:
    def __init__(self, cfg: dict, client: BinanceClient | None = None):
        self.cfg = cfg
        self.client = client or BinanceClient()
        self.store = DataStore(cfg["data_dir"])

    @property
    def state_path(self) -> Path:
        return Path(self.cfg["data_dir"]) / "alert_state.json"

    def universe(self) -> list[str]:
        return list_universe(self.cfg, self.client)

    def update(
        self, symbols: list[str], workers: int = 4, progress: Callable[[int, int], None] | None = None
    ) -> dict[str, int]:
        """Her parite için eksik verileri indirir; parite -> gün sayısı döndürür.

        `progress(tamamlanan, toplam)` verilirse her coin bittiğinde çağrılır.
        """
        years = int(self.cfg["history_years"])
        quote = self.cfg["universe"]["quote"]
        done = 0
        lock = threading.Lock()

        def one(sym: str) -> tuple[str, int]:
            nonlocal done
            try:
                result = sym, len(self.store.update(sym, self.client, years, quote))
            except Exception as exc:
                log.warning("%s güncellenemedi: %s", sym, exc)
                existing = self.store.load(sym)
                result = sym, 0 if existing is None else len(existing)
            if progress:
                with lock:
                    done += 1
                    progress(done, len(symbols))
            return result

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return dict(pool.map(one, symbols))

    def analyze_symbol(self, symbol: str, run_backtest: bool = True) -> CoinAnalysis | None:
        df = self.store.load(symbol)
        if df is None or len(df) < MIN_DAYS:
            log.info("%s: yeterli veri yok (%s gün), atlanıyor", symbol, 0 if df is None else len(df))
            return None
        scored = score_frame(df, self.cfg)
        sig = analyze(symbol, df, self.cfg, scored=scored)
        bt = None
        if run_backtest:
            try:
                bt = backtest(scored, self.cfg, symbol)
            except ValueError as exc:
                log.info("%s", exc)
        return CoinAnalysis(sig, bt)

    def analyze_all(self, symbols: list[str], run_backtest: bool = True) -> list[CoinAnalysis]:
        results = [self.analyze_symbol(s, run_backtest) for s in symbols]
        out = [r for r in results if r is not None]
        out.sort(key=lambda r: r.signal.score, reverse=True)
        return out
