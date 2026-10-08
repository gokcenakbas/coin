"""Ayarların yüklenmesi. config.yaml yoksa varsayılanlar kullanılır."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

STABLECOINS = {
    "USDT", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "USDP", "PYUSD", "USDD",
    "USDE", "EUR", "EURI", "AEUR", "TRY", "GBP", "BRL", "UST", "USTC", "PAX",
    "SUSD", "GUSD", "FRAX", "LUSD", "XUSD", "USD1", "BFUSD", "RLUSD",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "data_dir": "data",
    "history_years": 5,
    "universe": {
        # "top": 24 saatlik hacme göre en büyük N coin
        # "all": Binance'te işlem gören tüm USDT pariteleri
        # "list": yalnızca aşağıdaki "symbols" listesi
        "mode": "top",
        "top_n": 100,
        "quote": "USDT",
        "symbols": ["BTC", "ETH", "SOL", "BNB", "XRP"],
        "exclude": [],
    },
    "signals": {
        "strong_buy_threshold": 4,
        "buy_threshold": 2,
        "sell_threshold": -2,
        "strong_sell_threshold": -4,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        # 5 yıllık fiyat aralığında bu yüzdeliğin altı "ucuz", üstü "pahalı"
        "cheap_percentile": 0.20,
        "expensive_percentile": 0.90,
        "horizon_days": 30,
    },
    "risk": {
        "stop_atr_mult": 2.0,
        "take_profit_atr_mult": 3.0,
    },
    "backtest": {
        "fee": 0.001,
    },
    "alerts": {
        "levels": ["STRONG_BUY", "SELL"],
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "webhook": {"enabled": False, "url": ""},
        # Örnek: [{"coin": "BTC", "above": 150000}, {"coin": "ETH", "below": 2000}]
        "price_alerts": [],
    },
    "watch": {"interval_minutes": 60},
    "exchanges": ["binance", "okx", "bybit", "kucoin", "gate", "coinbase", "kraken", "btcturk"],
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """config.yaml dosyasını varsayılanlarla birleştirir; gizli bilgileri ortam değişkenlerinden okur."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    candidate = Path(path) if path else Path("config.yaml")
    if candidate.exists():
        with open(candidate, encoding="utf-8") as fh:
            cfg = _deep_merge(cfg, yaml.safe_load(fh) or {})

    tg = cfg["alerts"]["telegram"]
    tg["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", tg.get("bot_token", ""))
    tg["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", tg.get("chat_id", ""))
    if tg["bot_token"] and tg["chat_id"]:
        tg["enabled"] = True

    hook = cfg["alerts"]["webhook"]
    hook["url"] = os.environ.get("ALERT_WEBHOOK_URL", hook.get("url", ""))
    if hook["url"]:
        hook["enabled"] = True
    return cfg
