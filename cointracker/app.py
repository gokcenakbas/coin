"""Masaüstü uygulaması: canlı paneli kendi penceresinde açar (Mac'te 'Coin Takip.app').

Veriler, ayarlar ve takip listesi şurada saklanır:
  macOS : ~/Library/Application Support/CoinTakip/
  diğer : ~/.local/share/cointakip/   (Windows: %APPDATA%/CoinTakip/)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

from cointracker.config import load_config
from cointracker.dashboard import start_server

APP_NAME = "Coin Takip"
log = logging.getLogger("cointracker.app")


def support_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "CoinTakip"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home())) / "CoinTakip"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "cointakip"
    base.mkdir(parents=True, exist_ok=True)
    return base


def mac_notify(title: str, body: str) -> None:
    """macOS bildirim merkezine bildirim gönderir (metin argüman olarak geçer, betiğe gömülmez)."""
    if sys.platform != "darwin":
        return
    subprocess.Popen(
        ["osascript", "-e", "on run argv", "-e",
         "display notification (item 2 of argv) with title (item 1 of argv) sound name \"Glass\"",
         "-e", "end run", title, body],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _get(url: str, timeout: float = 60) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as resp:
        return resp.read()


def smoke_test(timeout: float = 240) -> int:
    """Paketlenmiş uygulamanın pencere açmadan uçtan uca çalıştığını doğrular (CI için)."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = load_config(Path(tmp) / "yok.yaml")
        cfg["data_dir"] = str(Path(tmp) / "data")
        app, server, url = start_server(cfg, symbols=["BTC", "ETH"])
        try:
            assert b"Coin Takip" in _get(url), "ana sayfa yüklenemedi"
            assert _get(url + "vendor/lightweight-charts.js").startswith(b"/*!"), "grafik kütüphanesi yok"
            deadline = time.time() + timeout
            while True:
                payload = json.loads(_get(url + "api/signals"))
                if payload["status"]["phase"] in ("ready", "error") or time.time() > deadline:
                    break
                time.sleep(2)
            ready = [i for i in payload["items"] if i["score"] is not None]
            print(f"durum: {payload['status']}")
            print("sinyaller:", [(i["base"], i["label"], i["price"]) for i in ready])
            assert ready, "hiç coin analiz edilemedi (Binance verisine erişilemedi mi?)"
            coin = json.loads(_get(url + "api/coin?symbol=BTC"))
            print("BTC zaman dilimleri:", [(t["label"], t["level_label"]) for t in coin["timeframes"]])
            print("BTC emir defteri:", coin["orderbook"].get("verdict"), "| borsalar:", [q["exchange"] for q in coin["quotes"]])
            print("hatalar:", coin["errors"])
            candles = json.loads(_get(url + "api/klines?symbol=BTC&interval=1h"))["candles"]
            assert candles, "saatlik grafik verisi yok"
            try:
                import webview  # noqa: F401  pencere kütüphanesinin pakette olduğunu doğrula
                print("pywebview: hazır")
            except ImportError:
                print("pywebview: YOK (uygulama tarayıcıda açılır)")
            print("SMOKE TEST BAŞARILI")
            return 0
        except AssertionError as exc:
            print(f"SMOKE TEST BAŞARISIZ: {exc}")
            return 1
        finally:
            server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--smoke-test", action="store_true", help="pencere açmadan kendini test et")
    parser.add_argument("--browser", action="store_true", help="kendi penceresi yerine tarayıcıda aç")
    args, _ = parser.parse_known_args(argv)  # macOS'un eklediği -psn_* argümanlarını yoksay

    # Pencereli uygulamada stdout/stderr olmayabilir
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    if args.smoke_test:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
        return smoke_test()

    home = support_dir()
    logging.basicConfig(filename=home / "cointakip.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config(home / "config.yaml")
    cfg["data_dir"] = str(home / "data")
    refresh = float(cfg.get("watch", {}).get("interval_minutes", 15))
    _, server, url = start_server(cfg, refresh_minutes=min(refresh, 15), notifier=mac_notify)
    log.info("Panel adresi: %s", url)

    try:
        import webview
    except ImportError:
        webview = None
    if webview is None or args.browser:
        webbrowser.open(url)
        print(f"{APP_NAME} çalışıyor: {url}  (kapatmak için Ctrl+C)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    webview.create_window(APP_NAME, url, width=1440, height=920, min_size=(960, 640))
    webview.start(private_mode=False, storage_path=str(home / "webview"))
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
