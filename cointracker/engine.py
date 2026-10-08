"""Veri güncelleme + sinyal + geriye dönük test adımlarını tüm coinler için birlikte çalıştırır."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from cointracker.backtest import BacktestResult, backtest
from cointracker.data import BinanceClient, DataStore, list_universe
from cointracker.indicators import add_indicators
from cointracker.signals import Signal, analyze, market_info, score_frame

log = logging.getLogger(__name__)
MIN_DAYS = 220  # SMA200 için asgari gün sayısı
MARKET_SYMBOL = "BTCUSDT"  # piyasa rejimi Bitcoin'e göre belirlenir


@dataclass
class CoinAnalysis:
    signal: Signal
    backtest: BacktestResult | None


class Engine:
    def __init__(self, cfg: dict, client: BinanceClient | None = None):
        self.cfg = cfg
        self.client = client or BinanceClient()
        self.store = DataStore(cfg["data_dir"])
        self._market_cache: tuple | None = None

    @property
    def state_path(self) -> Path:
        return Path(self.cfg["data_dir"]) / "alert_state.json"

    def universe(self) -> list[str]:
        return list_universe(self.cfg, self.client)

    def update_one(self, symbol: str) -> int:
        """Tek paritenin eksik günlerini indirir; elimizdeki gün sayısını döndürür (hata olursa önbellek)."""
        try:
            return len(self.store.update(
                symbol, self.client, int(self.cfg["history_years"]), self.cfg["universe"]["quote"]
            ))
        except Exception as exc:
            log.warning("%s güncellenemedi: %s", symbol, exc)
            existing = self.store.load(symbol)
            return 0 if existing is None else len(existing)

    def update(self, symbols: list[str], workers: int = 4) -> dict[str, int]:
        """Her parite için eksik verileri indirir; parite -> gün sayısı döndürür."""
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return dict(zip(symbols, pool.map(self.update_one, symbols)))

    def market_frame(self):
        """Bitcoin'in göstergeli günlük verisi (piyasa rejimi için); veri değişmedikçe önbellekten."""
        df = self.store.load(MARKET_SYMBOL)
        if df is None:
            self.update_one(MARKET_SYMBOL)
            df = self.store.load(MARKET_SYMBOL)
        if df is None or len(df) < MIN_DAYS:
            return None
        key = (df.index[-1], len(df), float(df["close"].iloc[-1]))
        if not self._market_cache or self._market_cache[0] != key:
            self._market_cache = (key, add_indicators(df))
        return self._market_cache[1]

    def market(self) -> dict | None:
        frame = self.market_frame()
        return market_info(frame) if frame is not None else None

    def analyze_symbol(self, symbol: str, run_backtest: bool = True) -> CoinAnalysis | None:
        df = self.store.load(symbol)
        if df is None or len(df) < MIN_DAYS:
            log.info("%s: yeterli veri yok (%s gün), atlanıyor", symbol, 0 if df is None else len(df))
            return None
        scored = score_frame(df, self.cfg)
        sig = analyze(symbol, df, self.cfg, scored=scored, market=self.market_frame())
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
        order = {"STRONG_BUY": 0, "BUY": 1, "TREND": 2, "SELL": 3, "HOLD": 4}
        out.sort(key=lambda r: (order.get(r.signal.level, 9), -r.signal.score))
        return out
