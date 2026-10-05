"""Uyarılar: sinyal değişimleri ve fiyat alarmları; konsol, Telegram ve webhook (Discord/Slack) bildirimleri."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

import requests

from cointracker.data import normalize_symbol
from cointracker.exchanges import Quote
from cointracker.signals import Signal

log = logging.getLogger(__name__)

EMOJI = {"STRONG_BUY": "🟢🟢", "BUY": "🟢", "HOLD": "⚪", "SELL": "🔴", "STRONG_SELL": "🔴🔴"}
DISCLAIMER = "⚠️ Yatırım tavsiyesi değildir; teknik göstergelere dayalı otomatik analizdir."


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class ConsoleNotifier:
    def send(self, text: str) -> None:
        print(text)
        print("-" * 60)


class TelegramNotifier:
    MAX_LEN = 4000

    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None):
        self.token, self.chat_id = token, chat_id
        self.session = session or requests.Session()

    def send(self, text: str) -> None:
        for start in range(0, len(text), self.MAX_LEN):
            resp = self.session.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text[start : start + self.MAX_LEN],
                      "disable_web_page_preview": True},
                timeout=15,
            )
            resp.raise_for_status()


class WebhookNotifier:
    """Discord ('content') ve Slack ('text') gelen webhook'larıyla uyumlu."""

    def __init__(self, url: str, session: requests.Session | None = None):
        self.url = url
        self.session = session or requests.Session()

    def send(self, text: str) -> None:
        resp = self.session.post(self.url, json={"content": text[:1900], "text": text}, timeout=15)
        resp.raise_for_status()


def build_notifiers(cfg: dict) -> list[Notifier]:
    notifiers: list[Notifier] = [ConsoleNotifier()]
    tg = cfg["alerts"]["telegram"]
    if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
        notifiers.append(TelegramNotifier(tg["bot_token"], str(tg["chat_id"])))
    hook = cfg["alerts"]["webhook"]
    if hook.get("enabled") and hook.get("url"):
        notifiers.append(WebhookNotifier(hook["url"]))
    return notifiers


def broadcast(notifiers: list[Notifier], text: str) -> None:
    for n in notifiers:
        try:
            n.send(text)
        except Exception as exc:
            log.error("%s bildirimi gönderilemedi: %s", type(n).__name__, exc)


def fmt_price(p: float | None) -> str:
    if p is None:
        return "-"
    if p >= 1000:
        return f"{p:,.0f}"
    if p >= 1:
        return f"{p:,.2f}"
    return f"{p:.6g}"


def fmt_pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.1f}%"


def format_signal(sig: Signal, quotes: list[Quote] | None = None, detailed: bool = True) -> str:
    lines = [
        f"{EMOJI[sig.level]} {sig.symbol}: {sig.label}  (puan {sig.score:+d})",
        f"Fiyat: {fmt_price(sig.price)} USDT | 7g {fmt_pct(sig.change_7d)} | 30g {fmt_pct(sig.change_30d)}",
    ]
    if detailed:
        lines += [f"  • {r}" for r in sig.reasons]
        h = sig.history
        if h.get("count"):
            base = h.get("baseline", {})
            lines.append(
                f"Son 5 yılda bu sinyal {h['count']} gün görüldü → {h['horizon']} gün sonra "
                f"ort. {fmt_pct(h['avg_return'])}, yükselme oranı %{h['win_rate'] * 100:.0f}"
                + (f" (tüm günlerin ortalaması {fmt_pct(base.get('avg_return'))})" if base.get("count") else "")
            )
        plan = sig.plan
        if plan:
            rr = f" | risk/ödül 1:{plan['risk_reward']:.1f}" if plan.get("risk_reward") else ""
            lines += [
                f"📍 Plan ({plan['action']}): {plan['summary']}",
                f"   Alım bölgesi {fmt_price(plan['buy_low'])}–{fmt_price(plan['buy_high'])} | "
                f"Hedef 1 {fmt_price(plan['target1'])} | Hedef 2 {fmt_price(plan['target2'])} | "
                f"Zarar-durdur {fmt_price(plan['stop'])}{rr}",
            ]
        elif sig.is_buy and sig.stop_loss:
            lines.append(f"Öneri: zarar-durdur ≈ {fmt_price(sig.stop_loss)}, kâr-al ≈ {fmt_price(sig.take_profit)}")
    if quotes:
        if sig.is_sell:
            best = max(quotes, key=lambda q: q.effective_sell)
            lines.append(f"En iyi satış: {best.exchange} {fmt_price(best.price)} ({best.pair}) → {best.url}")
        else:
            best = quotes[0]
            lines.append(f"En uygun alış: {best.exchange} {fmt_price(best.price)} ({best.pair}) → {best.url}")
    return "\n".join(lines)


class AlertState:
    """Aynı uyarının tekrar tekrar gönderilmemesi için son durumları saklar."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"levels": {}, "price_alerts": {}}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                log.warning("%s okunamadı, sıfırdan başlanıyor", self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")


def signal_changes(signals: list[Signal], state: AlertState, cfg: dict) -> list[Signal]:
    """Seviyesi değişen ve uyarı listesinde olan sinyalleri döndürür; durumu günceller."""
    wanted = set(cfg["alerts"]["levels"])
    changed: list[Signal] = []
    for sig in signals:
        previous = state.data["levels"].get(sig.symbol)
        if previous != sig.level and sig.level in wanted:
            changed.append(sig)
        state.data["levels"][sig.symbol] = sig.level
    return changed


def price_alerts(prices: dict[str, float], state: AlertState, cfg: dict) -> list[str]:
    """config'teki fiyat alarmlarını kontrol eder. Koşul bozulup yeniden oluşursa tekrar uyarır."""
    quote = cfg["universe"]["quote"]
    messages: list[str] = []
    for rule in cfg["alerts"].get("price_alerts", []):
        symbol = normalize_symbol(rule["coin"], quote)
        price = prices.get(symbol)
        if price is None:
            continue
        for kind, op in (("above", lambda p, t: p >= t), ("below", lambda p, t: p <= t)):
            if kind not in rule:
                continue
            key = f"{symbol}:{kind}:{rule[kind]}"
            hit = op(price, float(rule[kind]))
            if hit and not state.data["price_alerts"].get(key):
                word = "üzerine çıktı" if kind == "above" else "altına indi"
                messages.append(f"🔔 {symbol} fiyatı {fmt_price(float(rule[kind]))} {word}: şu an {fmt_price(price)}")
            state.data["price_alerts"][key] = hit
    return messages
