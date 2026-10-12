"""Piyasa değeri ve arz bilgisi (CoinGecko, ücretsiz, anahtar gerekmez) ve coin kalitesi.

"Sakin liste" için kullanılır:
  * günlük oynaklık (ATR / fiyat, son 90 gün) — fiyat verisinden, her zaman hesaplanır
  * veri yaşı (destek/direnç son 1 yılın dönüş noktalarından bulunduğu için en az 1 yıl)
  * dolaşımdaki arz / toplam arz — düşükse arzın büyük kısmı ekip, yatırımcı ya da hazine cüzdanlarında
    (az sayıda cüzdanda yoğunlaşma ve ileride kilit açılımı riski için yaklaşık ölçü)
  * piyasa değeri sırası
CoinGecko'ya ulaşılamazsa son 1 haftaya kadarki önbellek kullanılır; o da yoksa yalnızca fiyat verisine dayalı
ölçüler değerlendirilir (bilinmeyen ölçü listeden çıkarmaz).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
CACHE_TTL = 6 * 3600
CACHE_MAX_AGE = 7 * 24 * 3600

# Oynaklık sınırı: tools/research/quality.py'de düşük oynaklıklı üçte bir (eğitim dönemi) ≤ %7,6 idi; Ekim 2026'nın
# daha sakin piyasasında %7,5 memecoinleri de içeri aldığı için %6 seçildi (BTC ≈ %2,7, ETH ≈ %3,6, PEPE ≈ %6,8).
DEFAULT_QUALITY = {"max_atr_pct": 0.06, "min_age_days": 365, "min_float": 0.5, "max_rank": 200}


def fetch_markets(pages: int = 2, session: requests.Session | None = None) -> dict[str, dict]:
    """Piyasa değerine göre ilk pages×250 coin: SEMBOL → {rank, market_cap, circulating, total, max, name}.

    Aynı sembolü taşıyan birden çok coin varsa piyasa değeri en büyük olan alınır.
    """
    s = session or requests.Session()
    out: dict[str, dict] = {}
    for page in range(1, pages + 1):
        resp = s.get(COINGECKO_MARKETS, params={"vs_currency": "usd", "order": "market_cap_desc",
                                                "per_page": 250, "page": page}, timeout=20)
        resp.raise_for_status()
        for c in resp.json():
            sym = str(c.get("symbol", "")).upper()
            if not sym or sym in out:
                continue
            out[sym] = {
                "name": c.get("name"), "rank": c.get("market_cap_rank"), "market_cap": c.get("market_cap"),
                "circulating": c.get("circulating_supply"), "total": c.get("total_supply"),
                "max": c.get("max_supply"),
            }
        if page < pages:
            time.sleep(1.5)  # ücretsiz API'nin dakika sınırına takılmamak için
    return out


class MarketCapStore:
    """CoinGecko verisini data/marketcap.json içinde önbellekler."""

    def __init__(self, data_dir: str | Path, fetch=None):
        self.path = Path(data_dir) / "marketcap.json"
        self.fetch = fetch
        self.data: dict[str, dict] = {}
        self.updated: float | None = None
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.data, self.updated = raw.get("coins", {}), raw.get("updated")
            except (json.JSONDecodeError, OSError):
                log.warning("%s okunamadı", self.path)

    def refresh(self, force: bool = False) -> dict[str, dict]:
        now = time.time()
        if not force and self.updated and now - self.updated < CACHE_TTL and self.data:
            return self.data
        try:
            fresh = (self.fetch or fetch_markets)()
        except Exception as exc:  # noqa: BLE001 — ağ hatası uygulamayı durdurmamalı
            log.warning("Piyasa değeri verisi alınamadı: %s", exc)
            if self.updated and now - self.updated > CACHE_MAX_AGE:
                self.data = {}
            return self.data
        if fresh:
            self.data, self.updated = fresh, now
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps({"updated": now, "coins": fresh}), encoding="utf-8")
            except OSError:
                pass
        return self.data

    def get(self, base: str) -> dict | None:
        return self.data.get(base.upper())


def price_quality(df: pd.DataFrame) -> dict:
    """Fiyat verisinden: son 90 günün ortalama günlük oynaklığı (ATR / kapanış) ve veri yaşı (gün)."""
    if df is None or df.empty or "atr" not in df:
        return {"atr_pct": None, "age_days": 0}
    recent = df.iloc[-90:]
    atr_pct = (recent["atr"] / recent["close"]).mean()
    return {"atr_pct": float(atr_pct) if pd.notna(atr_pct) else None, "age_days": int(len(df))}


def evaluate(pq: dict, mc: dict | None, rules: dict | None = None, market_known: bool = False) -> dict:
    """Sakin listeye girer mi? Her ölçü için ok (True/False/None = bilinmiyor) ve nedenler.

    market_known: CoinGecko listesi elimizde mi. Elimizdeyse ama coin içinde yoksa piyasa değeri ilk ~500'ün
    dışındadır (küçük coin) ve sıra ölçüsünden kalır; liste hiç alınamadıysa ölçü bilinmiyor sayılır.
    """
    r = {**DEFAULT_QUALITY, **(rules or {})}
    float_ratio = None
    if mc and mc.get("circulating") and (mc.get("total") or mc.get("max")):
        denom = mc.get("total") or mc.get("max")
        float_ratio = min(1.0, mc["circulating"] / denom) if denom else None
    rank = mc.get("rank") if mc else None
    checks = {
        "calm": None if pq.get("atr_pct") is None else pq["atr_pct"] <= r["max_atr_pct"],
        "mature": pq.get("age_days", 0) >= r["min_age_days"],
        "float": None if float_ratio is None else float_ratio >= r["min_float"],
        "rank": (False if market_known else None) if rank is None else rank <= r["max_rank"],
    }
    reasons = []
    if checks["calm"] is False:
        reasons.append(f"çok oynak (günlük ort. %{pq['atr_pct'] * 100:.1f}, sınır %{r['max_atr_pct'] * 100:.1f})")
    if checks["mature"] is False:
        reasons.append(f"verisi {pq.get('age_days', 0)} gün (destek/direnç için en az {r['min_age_days']} gün)")
    if checks["float"] is False:
        reasons.append(f"arzın yalnızca %{float_ratio * 100:.0f}'ı dolaşımda (gerisi ekip/yatırımcı/hazine cüzdanlarında)")
    if checks["rank"] is False:
        reasons.append(f"piyasa değeri sırası {rank} (sınır {r['max_rank']})" if rank is not None
                       else "piyasa değeri ilk 500'de değil (çok küçük coin)")
    return {
        "calm_list": all(v is not False for v in checks.values()),
        "checks": checks, "reasons": reasons,
        "atr_pct": pq.get("atr_pct"), "age_days": pq.get("age_days"),
        "float": float_ratio, "rank": rank, "market_cap": mc.get("market_cap") if mc else None,
    }
