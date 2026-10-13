import json

import numpy as np
import pandas as pd
import pytest

from cointracker import forwardtest as ft
from cointracker.forwardtest import ForwardConfig, prepare, simulate, stats
from cointracker.signals import score_frame
from cointracker.trend import rule_trades
from tests.conftest import make_ohlcv

START = pd.Timestamp("2025-01-01")


def series(n_trend=640, tail=()):
    rise = np.linspace(50, 100, n_trend)
    side = 100 + np.sin(np.arange(40)) * 0.8
    return np.concatenate([rise, side, np.asarray(tail, dtype=float)])


def frames(cfg, coins):
    n = len(next(iter(coins.values())))
    start = START - pd.Timedelta(days=n - 30)  # son 30 gün testin içinde
    out = {"BTCUSDT": score_frame(make_ohlcv(np.linspace(100, 300, n), start=start), cfg)}
    for sym, prices in coins.items():
        out[sym] = score_frame(make_ohlcv(prices, start=start), cfg)
    return out


TAIL = [104, 107, 110, 113, 116, 118, 119, 120, 121, 122, 123, 124, 110, 104, 103, 102, 101, 100, 99, 98,
        97, 96, 95, 94, 93, 92, 91, 90, 89, 88]


def test_portfolio_trade_matches_the_rule(cfg):
    fr = frames(cfg, {"AAAUSDT": series(tail=TAIL)})
    last = fr["AAAUSDT"].index[-1]
    data = prepare(fr, START, last)
    res = simulate(data, START, last, ForwardConfig())
    expected = [t for t in rule_trades(data["AAAUSDT"], since=START) if not t["open"]]
    assert len(res["trades"]) == len(expected) == 1
    got, exp = res["trades"][0], expected[0]
    assert got["entry_day"] == exp["entry_date"] and got["entry"] == exp["entry"]
    assert got["exit_day"] == exp["exit_date"] and got["exit"] == pytest.approx(exp["exit"])
    # masraf: alış ve satışta %0,15 (kuralın araştırmadaki hesabıyla aynı sonuç)
    assert got["ret"] == pytest.approx(exp["ret"], abs=1e-4)
    # 1/10 dilimle işlem yapıldı: portföy getirisi ≈ işlem getirisi / 10
    s = stats(res, ForwardConfig())
    assert s["closed"] == 1 and s["ret"] == pytest.approx(got["ret"] / 10, abs=1e-3)


def test_no_lookahead_running_later_keeps_the_past(cfg):
    fr = frames(cfg, {"AAAUSDT": series(tail=TAIL), "BBBUSDT": series(tail=[t * 1.01 for t in TAIL])})
    last = fr["AAAUSDT"].index[-1]
    full = simulate(prepare(fr, START, last), START, last, ForwardConfig())
    mid = last - pd.Timedelta(days=12)
    part = simulate(prepare(fr, START, mid), START, mid, ForwardConfig())
    full_curve = dict(full["curve"])
    for t, v in part["curve"]:
        assert full_curve[t] == pytest.approx(v)


def test_slots_limit_and_momentum_priority(cfg):
    coins = {f"C{i}USDT": series(tail=[104 + i, 106 + i, 108 + i]) for i in range(4)}
    fr = frames(cfg, coins)
    last = fr["C0USDT"].index[-1]
    res = simulate(prepare(fr, START, last), START, last, ForwardConfig(slots=2))
    assert len(res["open"]) == 2
    held = {o["sym"] for o in res["open"]}
    assert held == {"C3USDT", "C2USDT"}  # son 20 günde en çok yükselenler önce


def test_run_freezes_universe_and_writes_report(cfg, tmp_path, monkeypatch):
    fr = frames(cfg, {"AAAUSDT": series(tail=TAIL)})
    monkeypatch.setattr(ft, "list_universe", lambda cfg, client: ["AAAUSDT"])
    monkeypatch.setattr(ft, "BinanceClient", lambda: None)
    monkeypatch.setattr(ft, "load_frames", lambda syms, cfg: {s: fr[s] for s in ["BTCUSDT", *syms] if s in fr})
    monkeypatch.setattr(ft, "fetch_markets", lambda: {"AAA": {"rank": 50, "circulating": 90, "total": 100}})
    last = fr["AAAUSDT"].index[-1]
    monkeypatch.setattr(ft, "today_utc", lambda: last + pd.Timedelta(days=1))
    state, out = tmp_path / "state.json", tmp_path / "RAPOR.md"
    ft.run(state, out, str(tmp_path / "data"))
    st = json.loads(state.read_text())
    assert st["universe"] == ["AAAUSDT"] and st["start_day"] == (last + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert "Henüz işlem yok" in out.read_text() or "Canlı kâğıt testi" in out.read_text()

    # başlangıcı geriye alıp (testin daha önce başladığını varsayarak) yeniden çalıştır: liste değişmez
    st["start_day"] = START.strftime("%Y-%m-%d")
    state.write_text(json.dumps(st))
    monkeypatch.setattr(ft, "list_universe", lambda cfg, client: pytest.fail("evren yeniden seçilmemeli"))
    res = ft.run(state, out, str(tmp_path / "data"))
    text = out.read_text()
    assert res["summary"]["all"]["closed"] == 1
    assert "Kapanan işlemler" in text and "AAA" in text and "BTC al-tut" in text and "GÜÇLÜ AL + AL" in text
