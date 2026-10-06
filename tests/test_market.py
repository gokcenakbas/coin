import numpy as np

from cointracker.signals import analyze, market_info, market_regime
from tests.conftest import make_ohlcv, synthetic_prices

BUY_SEED = 20  # bu sentetik seri günün sonunda AL (puan +2) veriyor
UP = make_ohlcv(np.linspace(100, 300, 1900))
DOWN = make_ohlcv(np.linspace(300, 100, 1900))


def test_market_regime_classification():
    assert market_regime(UP).iloc[-1] == "up"
    assert market_regime(DOWN).iloc[-1] == "down"
    assert market_regime(UP).iloc[:150].eq("mixed").all()  # 200G ortalama oluşmadan karar verilmez
    # yükselişten sert düşüşe geçiş: fiyat ortalamanın altına indi ama 50G hâlâ 200G üstünde → kararsız
    turn = make_ohlcv(np.concatenate([np.linspace(100, 300, 1000), np.linspace(300, 255, 10)]))
    assert market_regime(turn).iloc[-1] == "mixed"


def test_market_info_since_and_distance():
    info = market_info(DOWN)
    assert info["regime"] == "down" and info["label"] == "Düşüş"
    assert info["btc_vs_sma200"] < 0
    assert info["since"] <= DOWN.index[-1]


def test_market_is_informational_only(cfg):
    """Gerçek veride AL sinyalleri BTC düşüşteyken daha kötü çıkmadı; bu yüzden rejim sinyali değiştirmez."""
    coin = make_ohlcv(synthetic_prices(seed=BUY_SEED))
    plain = analyze("X", coin, cfg)
    assert plain.is_buy and plain.market is None and "by_regime" not in plain.history
    for market, name in ((UP, "up"), (DOWN, "down")):
        sig = analyze("X", coin, cfg, market=market)
        assert sig.level == plain.level and sig.score == plain.score and sig.reasons == plain.reasons
        assert sig.market["regime"] == name
        by = sig.history["by_regime"]
        assert sum(by[k]["count"] for k in by) == plain.history["count"]


def test_edge_compares_signals_with_random_days(cfg):
    sig = analyze("X", make_ohlcv(synthetic_prices(seed=BUY_SEED)), cfg)
    e = sig.edge
    assert e["horizon"] == cfg["signals"]["horizon_days"]
    assert e["all"]["count"] > e["buy"]["count"] > 0 and e["sell"]["count"] > 0
    assert e["all"] is sig.history["baseline"]
