import yaml

from cointracker import cli
from cointracker.data import DataStore
from tests.conftest import make_ohlcv, synthetic_prices


def _setup(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = DataStore(data_dir)
    store.save("AAAUSDT", make_ohlcv(synthetic_prices(seed=1)))
    store.save("BBBUSDT", make_ohlcv(synthetic_prices(seed=2)))
    store.save("NEWUSDT", make_ohlcv(synthetic_prices(days=100)))  # çok kısa, atlanmalı
    store.save("BTCUSDT", make_ohlcv(synthetic_prices(seed=9)))  # piyasa rejimi için (ağa gitmesin)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({"data_dir": str(data_dir)}))
    monkeypatch.setattr(cli, "get_quotes", lambda coin, exchanges=None: [])
    return ["-c", str(cfg_path)]


def test_scan_from_cache(tmp_path, monkeypatch, capsys):
    base = _setup(tmp_path, monkeypatch)
    assert cli.main(base + ["scan", "--no-update", "--backtest", "--coins", "aaa", "bbb", "new"]) == 0
    out = capsys.readouterr().out
    assert "AAAUSDT" in out and "BBBUSDT" in out and "NEWUSDT" not in out


def test_report_written(tmp_path, monkeypatch):
    base = _setup(tmp_path, monkeypatch)
    out = tmp_path / "r.html"
    assert cli.main(base + ["report", "--no-update", "--coins", "AAA", "BBB", "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "AAAUSDT" in html and "<table>" in html


def test_watch_once_alerts_then_stays_quiet(tmp_path, monkeypatch, capsys):
    base = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.Engine, "update", lambda self, symbols, workers=4: {})
    cfg_path = tmp_path / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["alerts"] = {"levels": ["STRONG_BUY", "BUY", "SELL", "STRONG_SELL", "HOLD"]}
    cfg_path.write_text(yaml.safe_dump(cfg))

    assert cli.main(base + ["watch", "--once", "--coins", "AAA", "BBB"]) == 0
    first = capsys.readouterr().out
    assert "Coin uyarıları" in first and "2 uyarı" in first

    assert cli.main(base + ["watch", "--once", "--coins", "AAA", "BBB"]) == 0
    assert "0 uyarı" in capsys.readouterr().out
