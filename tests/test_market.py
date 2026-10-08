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


def test_bitcoin_trend_gates_new_buys(cfg):
    """Yeni alım yalnızca Bitcoin 200 günlük ortalamanın üstündeyken önerilir (trend kuralı)."""
    coin = make_ohlcv(synthetic_prices(seed=BUY_SEED))
    for market, name in ((UP, "up"), (DOWN, "down")):
        sig = analyze("X", coin, cfg, market=market)
        assert sig.market["regime"] == name
        assert sig.plan["conditions"][2]["ok"] == (name == "up")
        if name == "down":
            assert sig.level != "STRONG_BUY"


def test_edge_compares_old_score_with_random_days(cfg):
    sig = analyze("X", make_ohlcv(synthetic_prices(seed=BUY_SEED)), cfg)
    e = sig.edge
    assert e["horizon"] == cfg["signals"]["horizon_days"]
    assert e["all"]["count"] > e["buy"]["count"] > 0 and e["sell"]["count"] > 0
