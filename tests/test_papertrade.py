import copy

import numpy as np
import pandas as pd

from cointracker.papertrade import STEP, PaperConfig, PaperTrader, new_state, report, summary

T0 = pd.Timestamp("2026-10-01 00:00")


class FakeMinuteClient:
    """Rastgele yürüyüşle üretilmiş 1dk veriden istenen aralıkta 1m/15m mum döndürür."""

    def __init__(self, symbols, minutes=6000, seed=1, cutoff=None):
        self.cutoff = cutoff
        self.data = {}
        rng = np.random.default_rng(seed)
        idx = pd.date_range(T0 - 300 * STEP, periods=minutes, freq="1min")
        for i, sym in enumerate(symbols):
            close = 100 * np.exp(np.cumsum(rng.normal(0.00002 * (i - 2), 0.0025, minutes)))
            open_ = np.concatenate([[close[0]], close[:-1]])
            wig = np.abs(rng.normal(0, 0.0012, minutes))
            self.data[sym] = pd.DataFrame({
                "open": open_, "high": np.maximum(open_, close) * (1 + wig), "low": np.minimum(open_, close) * (1 - wig),
                "close": close, "volume": 1.0, "quote_volume": close,
            }, index=idx)

    def klines(self, symbol, start, end, interval="1d"):
        df = self.data[symbol]
        if self.cutoff is not None:  # "gelecek" verisi bozulsa bile geçmiş kararlar değişmemeli
            df = df.copy()
            df.loc[df.index >= self.cutoff, ["open", "high", "low", "close"]] *= 3
        if interval == "15m":
            df = df.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                           "volume": "sum", "quote_volume": "sum"})
        s, e = pd.Timestamp(start).tz_convert(None), pd.Timestamp(end).tz_convert(None)
        if interval == "15m":
            # yalnızca açılış zamanı aralıkta olan mumlar (Binance davranışı)
            return df.loc[(df.index >= s.floor("15min")) & (df.index <= e)]
        return df.loc[(df.index >= s) & (df.index <= e)]


SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "BTCUSDT"]


def run(chunks, client=None, hours=24):
    client = client or FakeMinuteClient(SYMS)
    state = new_state(PaperConfig(), T0, hours, SYMS[:-1])
    trader = PaperTrader(state)
    for now in chunks:
        trader.advance(client, now)
    return state


def test_one_shot_replay_accounting_and_rules():
    state = run([T0 + pd.Timedelta(hours=25)])
    s = summary(state)
    assert state["finished"] and not state["positions"]
    assert s["trades"] >= 20
    assert np.isclose(state["cash"], 1000 + sum(t["pnl"] for t in state["trades"]))
    assert np.isclose(s["equity"], state["cash"])
    for t in state["trades"]:
        entry, exit_ = pd.Timestamp(t["entry_time"]), pd.Timestamp(t["exit_time"])
        assert entry.minute % 15 == 0 and entry.second == 0  # alımlar yalnızca karar anlarında
        assert exit_ >= entry and t["cost"] > 0
        assert exit_ - entry <= pd.Timedelta(minutes=PaperConfig().max_hold_min)
    # aynı anda en fazla 4 pozisyon
    events = sorted([(pd.Timestamp(t["entry_time"]), 1) for t in state["trades"]] +
                    [(pd.Timestamp(t["exit_time"]), -1) for t in state["trades"]], key=lambda e: (e[0], e[1]))
    assert max(np.cumsum([d for _, d in events])) <= 4
    assert s["btc_return"] is not None


def test_incremental_steps_match_one_shot():
    one = run([T0 + pd.Timedelta(hours=25)])
    rng = np.random.default_rng(3)
    nows = sorted(T0 + pd.to_timedelta(rng.integers(1, 25 * 60, 40), unit="min"))  # düzensiz cron gecikmeleri
    many = run(list(nows) + [T0 + pd.Timedelta(hours=25)])
    assert many["trades"] == one["trades"]
    assert np.isclose(many["cash"], one["cash"])


def test_no_lookahead():
    cutoff = T0 + pd.Timedelta(hours=10)
    clean = run([T0 + pd.Timedelta(hours=25)])
    poisoned = run([T0 + pd.Timedelta(hours=25)], client=FakeMinuteClient(SYMS, cutoff=cutoff))
    before = lambda st: [t for t in st["trades"] if pd.Timestamp(t["exit_time"]) < cutoff]
    assert before(clean) and before(clean) == before(poisoned)


def test_nothing_happens_before_first_bar_closes():
    state = new_state(PaperConfig(), T0, 24, SYMS[:-1])
    assert not PaperTrader(state).advance(FakeMinuteClient(SYMS), T0 + pd.Timedelta(minutes=10))
    assert state["cursor"] == T0.isoformat()


def test_stop_has_priority_and_report_renders():
    state = run([T0 + pd.Timedelta(hours=25)])
    reasons = {t["reason"] for t in state["trades"]}
    assert reasons <= {"zarar-durdur", "kâr-al", "sinyal döndü", "süre doldu", "deney sonu"}
    text = report(copy.deepcopy(state))
    assert "Kâğıt üzerinde işlem raporu" in text and text.count("\n| ") >= len(state["trades"])
