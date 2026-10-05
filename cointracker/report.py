"""Tüm coinlerin sinyal ve 5 yıllık test sonuçlarını tek bir HTML sayfasında toplar."""

from __future__ import annotations

import html
from datetime import datetime, timezone

from cointracker.alerts import DISCLAIMER, fmt_pct, fmt_price
from cointracker.engine import CoinAnalysis

_CSS = """
:root{--bg:#f7f7f8;--card:#fff;--fg:#1d1d1f;--muted:#6b6b76;--line:#e4e4e8;--buy:#0f8a4f;--sell:#c23434}
@media (prefers-color-scheme:dark){:root{--bg:#141417;--card:#1d1d22;--fg:#ececf0;--muted:#9a9aa6;--line:#2c2c33;--buy:#3ccf86;--sell:#ff6b6b}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}.muted{color:var(--muted)}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;margin-top:16px}
table{border-collapse:collapse;width:100%;min-width:900px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{cursor:pointer;position:sticky;top:0;background:var(--card);font-weight:600}
td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){text-align:left}
.BUY,.STRONG_BUY,.pos{color:var(--buy)}.SELL,.STRONG_SELL,.neg{color:var(--sell)}
.STRONG_BUY,.STRONG_SELL{font-weight:700}
details summary{cursor:pointer;color:var(--muted)}details ul{margin:6px 0;padding-left:18px;white-space:normal;text-align:left}
"""

_JS = """
document.querySelectorAll('th').forEach((th,i)=>th.addEventListener('click',()=>{
 const tb=th.closest('table').tBodies[0],rows=[...tb.rows],asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
 rows.sort((a,b)=>{const x=a.cells[i].dataset.v??a.cells[i].innerText,y=b.cells[i].dataset.v??b.cells[i].innerText;
  const nx=parseFloat(x),ny=parseFloat(y);const r=isNaN(nx)||isNaN(ny)?x.localeCompare(y):nx-ny;return asc?r:-r});
 rows.forEach(r=>tb.appendChild(r));}));
"""


def _pct_cell(v: float | None) -> str:
    if v is None:
        return '<td data-v="-1e9">-</td>'
    cls = "pos" if v > 0 else "neg" if v < 0 else ""
    return f'<td class="{cls}" data-v="{v}">{fmt_pct(v)}</td>'


def render_report(results: list[CoinAnalysis], years: int) -> str:
    rows = []
    for r in results:
        s, bt = r.signal, r.backtest
        reasons = "".join(f"<li>{html.escape(x)}</li>" for x in s.reasons)
        h = s.history
        if h.get("count"):
            reasons += (
                f"<li>5 yılda bu sinyal {h['count']} gün görüldü; {h['horizon']} gün sonra ort. "
                f"{fmt_pct(h['avg_return'])}, yükselme oranı %{h['win_rate'] * 100:.0f}</li>"
            )
        rank = "-" if s.pct_rank_5y is None else f"%{s.pct_rank_5y * 100:.0f}"
        rows.append(
            "<tr>"
            f"<td><b>{html.escape(s.symbol)}</b></td>"
            f'<td class="{s.level}" data-v="{s.score}">{s.label}</td>'
            f"<td>{s.score:+d}</td>"
            f'<td data-v="{s.price}">{fmt_price(s.price)}</td>'
            + _pct_cell(s.change_7d)
            + _pct_cell(s.change_30d)
            + f'<td>{"-" if s.rsi is None else f"{s.rsi:.0f}"}</td>'
            f'<td data-v="{s.pct_rank_5y if s.pct_rank_5y is not None else -1}">{rank}</td>'
            + _pct_cell(s.drawdown_5y)
            + _pct_cell(bt.total_return if bt else None)
            + _pct_cell(bt.buy_hold_return if bt else None)
            + f'<td>{bt.trades if bt else "-"}</td>'
            f'<td>{"-" if not bt or bt.win_rate is None else f"%{bt.win_rate * 100:.0f}"}</td>'
            f"<td><details><summary>neden?</summary><ul>{reasons}</ul></details></td>"
            "</tr>"
        )

    buys = sum(r.signal.is_buy for r in results)
    sells = sum(r.signal.is_sell for r in results)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Coin Sinyal Raporu</title>
<style>{_CSS}</style></head><body><main>
<h1>Coin Sinyal Raporu</h1>
<div class="muted">{now} · {len(results)} coin · {buys} AL · {sells} SAT · göstergeler ve testler son {years} yıllık günlük veriye dayanır</div>
<div class="wrap"><table><thead><tr>
<th>Coin</th><th>Sinyal</th><th>Puan</th><th>Fiyat</th><th>7g</th><th>30g</th><th>RSI</th>
<th>5y yüzdelik</th><th>5y zirveden</th><th>Strateji ({years}y)</th><th>Al-tut ({years}y)</th><th>İşlem</th><th>Başarı</th><th>Gerekçe</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="muted">{html.escape(DISCLAIMER)} Geçmiş performans gelecekteki sonuçları garanti etmez.</p>
</main><script>{_JS}</script></body></html>"""
