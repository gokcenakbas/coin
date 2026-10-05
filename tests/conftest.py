import numpy as np
import pandas as pd
import pytest

from cointracker.config import DEFAULT_CONFIG, _deep_merge


def make_ohlcv(closes, start="2021-01-01") -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq="D", name="date")
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) * 1.01,
            "low": np.minimum(opens, closes) * 0.99,
            "close": closes,
            "volume": 1000.0,
            "quote_volume": closes * 1000.0,
        },
        index=idx,
    )


def synthetic_prices(days: int = 1900, seed: int = 7) -> np.ndarray:
    """Trendli, döngüsel ve gürültülü 5+ yıllık fiyat serisi."""
    rng = np.random.default_rng(seed)
    t = np.arange(days)
    log_price = np.log(100) + 0.0008 * t + 0.6 * np.sin(t / 120) + np.cumsum(rng.normal(0, 0.02, days))
    return np.exp(log_price)


@pytest.fixture
def cfg(tmp_path):
    return _deep_merge(DEFAULT_CONFIG, {"data_dir": str(tmp_path / "data")})


@pytest.fixture
def ohlcv():
    return make_ohlcv(synthetic_prices())
