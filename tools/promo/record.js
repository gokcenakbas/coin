// Tanıtım videosunun ana bölümünü kaydeder: demo sunucusunu başlatır, Chromium'u senaryoya göre yönetir,
// altyazı ve imleç ekler, ekran karelerini (CDP screencast) <çıktı>/frames altına yazar.
// Kullanım: node record.js <çıktı_klasörü>   (FONT_DIR: @fontsource/inter/files, CHROME: chromium yolu)
const { chromium } = require("playwright-core");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const OUT = path.resolve(process.argv[2] || "promo-out");
const ROOT = path.resolve(__dirname, "../..");
const PORT = 8790;
const W = 1600, H = 900;
const COINS = {
  BTCUSDT: 84250, ETHUSDT: 2650, SOLUSDT: 146.2, BNBUSDT: 592, XRPUSDT: 2.14, DOGEUSDT: 0.1712, ADAUSDT: 0.684,
  AVAXUSDT: 24.3, LINKUSDT: 14.62, DOTUSDT: 4.12, LTCUSDT: 88.4, SUIUSDT: 3.41, TRXUSDT: 0.2431,
  PEPEUSDT: 0.0000088, NEARUSDT: 2.62, TONUSDT: 3.18,
};
const wait = ms => new Promise(r => setTimeout(r, ms));

function fontCss() {
  const dir = process.env.FONT_DIR;
  if (!dir) return "";
  const LATIN = "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD";
  const EXT = "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF";
  let css = "";
  for (const w of [400, 500, 600, 700, 800]) {
    for (const [sub, range] of [["latin", LATIN], ["latin-ext", EXT]]) {
      const b64 = fs.readFileSync(path.join(dir, `inter-${sub}-${w}-normal.woff2`)).toString("base64");
      css += `@font-face{font-family:Inter;font-weight:${w};font-display:block;src:url(data:font/woff2;base64,${b64}) format('woff2');unicode-range:${range}}\n`;
    }
  }
  return css + "body,button,input{font-family:Inter,-apple-system,sans-serif!important;letter-spacing:-.005em}";
}

// Sayfaya enjekte edilen: kontrol edilebilir sahte Binance akışı, altyazı, imleç ve vurgulama
function pageInit({ prices, fonts }) {
  /* ---- sahte canlı fiyat akışı ---- */
  window.__px = { ...prices };
  const open = {}, vol = {};
  let k = 0;
  for (const s of Object.keys(prices)) {
    k++;
    open[s] = prices[s] / (1 + Math.sin(k * 2.3) * 0.05 + 0.012);
    vol[s] = 2e7 * (1 + (k % 5)) * (s === "BTCUSDT" ? 60 : s === "ETHUSDT" ? 25 : 1);
  }
  window.__bump = (s, f) => { window.__px[s] *= f; };
  const gauss = () => (Math.random() + Math.random() + Math.random() - 1.5) / 1.5;
  class FakeWS {
    constructor(url) { this.url = url; setTimeout(() => { this.onopen && this.onopen(); this.start(); }, 150); }
    async start() {
      if (this.url.includes("miniTicker")) {
        const tick = () => {
          if (this.closed) return;
          const list = Object.keys(window.__px).map(s => {
            window.__px[s] *= 1 + gauss() * 0.0006;
            return { s, c: String(window.__px[s]), o: String(open[s]), h: "0", l: "0", q: String(vol[s]) };
          });
          this.onmessage && this.onmessage({ data: JSON.stringify(list) });
          setTimeout(tick, 1000);
        };
        tick();
      } else {
        const m = this.url.match(/ws\/(\w+)@kline_(\w+)/);
        const sym = m[1].toUpperCase(), iv = m[2];
        const d = await (await fetch(`/api/klines?symbol=${sym}&interval=${iv}`)).json();
        const last = d.candles[d.candles.length - 1];
        if (!last) return;
        const bar = { ...last };
        const tick = () => {
          if (this.closed) return;
          const c = window.__px[sym];
          bar.close = c; bar.high = Math.max(bar.high, c); bar.low = Math.min(bar.low, c); bar.volume *= 1.002;
          this.onmessage && this.onmessage({ data: JSON.stringify({ k: { t: bar.time * 1000, o: bar.open, h: bar.high, l: bar.low, c: bar.close, v: bar.volume } }) });
          setTimeout(tick, 1000);
        };
        tick();
      }
    }
    close() { this.closed = true; }
  }
  window.WebSocket = FakeWS;

  /* ---- altyazı, imleç, vurgu ---- */
  document.addEventListener("DOMContentLoaded", () => {
    const style = document.createElement("style");
    style.textContent = fonts + `
      section.card{scroll-margin-top:72px}
      #__cap{position:fixed;left:50%;bottom:26px;transform:translate(-50%,20px);opacity:0;z-index:9999;max-width:1100px;
        background:rgba(10,12,18,.88);color:#fff;border-radius:16px;padding:16px 26px 17px;border-left:5px solid #5b8cff;
        box-shadow:0 18px 50px rgba(0,0,0,.45);transition:opacity .45s,transform .45s;backdrop-filter:blur(8px);pointer-events:none}
      #__cap.on{opacity:1;transform:translate(-50%,0)}
      #__cap b{display:block;font-size:25px;font-weight:700;letter-spacing:-.01em}
      #__cap span{display:block;font-size:17px;color:#c9cfdb;margin-top:5px;line-height:1.4}
      #__cur{position:fixed;left:0;top:0;width:26px;height:26px;z-index:10000;pointer-events:none;
        transition:transform .65s cubic-bezier(.4,.1,.2,1);transform:translate(800px,450px);filter:drop-shadow(0 2px 3px rgba(0,0,0,.5))}
      .__rip{position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;border:3px solid #5b8cff;z-index:9998;
        pointer-events:none;animation:__rip .6s ease-out forwards}
      @keyframes __rip{from{transform:scale(.3);opacity:1}to{transform:scale(1.4);opacity:0}}
      .__spot{position:fixed;z-index:9997;border:3px solid #f5b301;border-radius:12px;pointer-events:none;
        box-shadow:0 0 0 6px rgba(245,179,1,.18),0 0 30px rgba(245,179,1,.35);transition:opacity .4s;animation:__pulse 1.4s ease-in-out infinite}
      @keyframes __pulse{50%{box-shadow:0 0 0 10px rgba(245,179,1,.10),0 0 40px rgba(245,179,1,.45)}}`;
    document.head.appendChild(style);
    const cap = document.createElement("div"); cap.id = "__cap"; document.body.appendChild(cap);
    const cur = document.createElement("div"); cur.id = "__cur";
    cur.innerHTML = `<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l15 11-6.5 1.2L16 21l-3 1.4-3.6-6.9L4 19z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
    document.body.appendChild(cur);
  });
  window.__caption = (title, sub) => {
    const cap = document.getElementById("__cap");
    cap.classList.remove("on");
    setTimeout(() => { cap.innerHTML = `<b>${title}</b>${sub ? `<span>${sub}</span>` : ""}`; cap.classList.add("on"); }, title ? 350 : 0);
    if (!title) cap.classList.remove("on");
  };
  window.__cursorTo = (x, y) => { document.getElementById("__cur").style.transform = `translate(${x - 4}px,${y - 2}px)`; };
  window.__ripple = (x, y) => {
    const r = document.createElement("div"); r.className = "__rip"; r.style.left = x + "px"; r.style.top = y + "px";
    document.body.appendChild(r); setTimeout(() => r.remove(), 700);
  };
  window.__spot = (sel, ms) => {
    const el = document.querySelector(sel); if (!el) return;
    const b = el.getBoundingClientRect(), s = document.createElement("div");
    s.className = "__spot"; Object.assign(s.style, { left: b.left - 6 + "px", top: b.top - 6 + "px", width: b.width + 12 + "px", height: b.height + 12 + "px" });
    document.body.appendChild(s); setTimeout(() => { s.style.opacity = 0; setTimeout(() => s.remove(), 400); }, ms);
  };
}

(async () => {
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(path.join(OUT, "frames"), { recursive: true });
  const server = spawn("python3", [path.join(__dirname, "demo_server.py"), path.join(OUT, "data"), String(PORT)], { stdio: ["ignore", "pipe", "inherit"] });
  server.stdout.on("data", d => process.stdout.write("[sunucu] " + d));
  await new Promise(res => server.stdout.once("data", res));

  const browser = await chromium.launch({ executablePath: process.env.CHROME || "/opt/pw-browsers/chromium", args: ["--no-proxy-server"] });
  const context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, colorScheme: "dark", locale: "tr-TR" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.addInitScript(pageInit, { prices: COINS, fonts: fontCss() });

  const cdp = await context.newCDPSession(page);
  let n = 0;
  const frames = [];
  cdp.on("Page.screencastFrame", async f => {
    const file = `f${String(n++).padStart(6, "0")}.jpg`;
    fs.writeFileSync(path.join(OUT, "frames", file), Buffer.from(f.data, "base64"));
    frames.push({ file, ts: f.metadata.timestamp });
    await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
  });

  // ---- yardımcılar ----
  const caption = (t, s) => page.evaluate(([t, s]) => window.__caption(t, s), [t, s || ""]);
  const spot = (sel, ms = 2500) => page.evaluate(([sel, ms]) => window.__spot(sel, ms), [sel, ms]);
  async function moveTo(sel, fx = 0.5, fy = 0.5) {
    const b = await page.locator(sel).first().boundingBox();
    const x = b.x + b.width * fx, y = b.y + b.height * fy;
    await page.evaluate(([x, y]) => window.__cursorTo(x, y), [x, y]);
    await wait(750);
    return { x, y };
  }
  async function click(sel, fx, fy) {
    const { x, y } = await moveTo(sel, fx, fy);
    await page.evaluate(([x, y]) => window.__ripple(x, y), [x, y]);
    await page.mouse.click(x, y);
    await wait(350);
  }
  async function scrollToEl(sel) {
    await page.evaluate(sel => document.querySelector(sel).scrollIntoView({ behavior: "smooth", block: "start" }), sel);
    await wait(1100);
  }
  const scrollTop = async () => { await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" })); await wait(1000); };
  const trFmt = v => v.toFixed(2).replace(".", ",");

  await page.goto(`http://127.0.0.1:${PORT}/`);
  await page.waitForSelector("#rows tr[data-s]");
  await page.evaluate(() => document.fonts.ready);
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: W, maxHeight: H, everyNthFrame: 1 });
  await wait(600);

  // 1) Açılış
  await caption("Uygulamayı açın: tüm coinler hemen listede", "Fiyatlar canlı akmaya başlar; 5 yıllık analizler coin coin hazırlanır");
  await wait(1800);
  server.kill("SIGUSR1");
  await page.waitForFunction(() => state.items.length && state.items.every(i => i.score != null), null, { timeout: 30000 });
  await wait(2500);

  // 2) Canlı fiyat
  await caption("Canlı fiyatlar", "Fiyat, 24 saatlik değişim ve hacim saniyede bir güncellenir");
  await moveTo("#rows tr:nth-child(3) [data-f='price']");
  await spot("thead th:nth-child(2)", 3200);
  await wait(3800);

  // 3) Sinyal ve puan
  await caption("AL / SAT sinyali ve puanı", "Her coin 6 göstergeden −7 ile +7 arası puan alır: +2 ve üstü AL, −2 ve altı SAT");
  await spot("thead th:nth-child(5)", 4200);
  await moveTo("thead th:nth-child(5)");
  await wait(4500);

  // 4) Filtre
  await caption("Tek tıkla filtrele", "Yalnızca AL ya da SAT sinyali verenleri gör");
  await click("[data-filter='buy']"); await wait(2200);
  await click("[data-filter='sell']"); await wait(2200);
  await click("[data-filter='all']"); await wait(800);

  // 5) Arama
  await caption("Coin ara", "Takip ettiğin coini saniyede bul");
  await click("#search");
  await page.keyboard.type("so", { delay: 260 });
  await wait(1800);
  await page.fill("#search", ""); await page.dispatchEvent("#search", "input"); await wait(600);

  // 6) Takip listesi
  await caption("Takip listesi", "☆ işaretine tıkla; takip ettiğin coinin sinyali değişince Mac bildirimi gelir");
  for (const s of ["BTCUSDT", "SOLUSDT", "AVAXUSDT"]) { await click(`[data-star='${s}']`); await wait(300); }
  await click("[data-filter='fav']"); await wait(2600);
  await click("[data-filter='all']"); await wait(600);

  // 7) Coin detayı
  await caption("Coin detayı", "Canlı fiyat, mum grafiği, 50 ve 200 günlük ortalamalar");
  await click("#rows tr[data-s='SOLUSDT']", 0.2);
  await page.waitForSelector("#detailBody .levels");
  await wait(3800);

  // 8) Zaman dilimleri
  await caption("1 dakikadan 1 haftaya grafik", "Son mum canlı oluşur");
  await click("[data-iv='15m']"); await wait(2000);
  await click("[data-iv='4h']"); await wait(2000);
  await click("[data-iv='1d']"); await wait(2400);

  // 9) İşlem planı
  await caption("İşlem planı: nereden alınır, nereden satılır?", "Alım bölgesi, Hedef 1–2, zarar-durdur ve risk/ödül; destek ve dirençlerden hesaplanır");
  await scrollToEl("#detailBody section.card");
  await spot("#detailBody .levels", 4200);
  await moveTo("#detailBody .lv.buy");
  await wait(2600);
  await moveTo("#detailBody .lv.stop");
  await wait(2400);
  await caption("Seviyeler grafikte çizgi olarak görünür", "Yeşil: alım bölgesi · Mavi: hedefler · Kırmızı: zarar-durdur");
  await scrollToEl("#intervals");
  await spot("#chart", 3600);
  await moveTo("#chart", 0.9, 0.55);
  await wait(4000);

  // 10) Tek tık alarm
  await caption("Tek tıkla seviye alarmı", "Alım bölgesine inince, hedefe çıkınca ya da zarar-durdura inerse haber verir");
  await scrollToEl("#detailBody section.card");
  await click("[data-planalarm='t1']");
  await wait(3000);

  // 11) Kendi fiyat alarmı → çalıyor
  await caption("Kendi fiyat alarmını kur", "Fiyat yazıp ▲ / ▼ seç; alarm çalınca bildirim gelir");
  await scrollToEl("#alarmBox section");
  const target = await page.evaluate(() => window.__px.SOLUSDT * 1.008);
  await click("#alarmPrice");
  await page.keyboard.type(trFmt(target), { delay: 140 });
  await click("#alarmBox [data-kind='above']");
  await wait(1800);
  await caption("Fiyat seviyeye gelince haber verir", "Uygulama açıkken Mac bildirim merkezine de düşer");
  await page.evaluate(() => window.__bump("SOLUSDT", 1.013));
  await wait(4200);

  // 12) Zaman dilimleri sinyali, emir defteri, 24 saat
  await caption("Zaman dilimlerine göre sinyal", "15 dk, 1 saat, 4 saat ve 1 gün için ayrı sinyal, RSI ve trend yönü");
  await scrollToEl("#detailBody .tf");
  await page.evaluate(() => window.scrollBy({ top: -60, behavior: "smooth" }));
  await spot("#detailBody .tf", 3200);
  await wait(3600);
  await caption("Neden bu sinyal? Emir defteri ve son 24 saat", "Gerekçeler, sinyalin 5 yıllık geçmiş başarısı, alıcı/satıcı baskısı");
  await scrollToEl("#detailBody .grid2");
  await moveTo("#detailBody .grid2 .bar", 0.5, 0.5).catch(() => {});
  await wait(4600);

  // 13) Strateji testi ve borsalar
  await caption("5 yıllık strateji testi ve en uygun borsa", "8 borsanın fiyatı komisyon dahil karşılaştırılır; tek tıkla işlem sayfasına git");
  await page.evaluate(() => [...document.querySelectorAll("#detailBody h2")].find(h => h.textContent.startsWith("5 yıllık")).scrollIntoView({ behavior: "smooth", block: "start" }));
  await wait(1200);
  await page.evaluate(() => window.scrollBy({ top: -80, behavior: "smooth" }));
  await wait(800);
  await moveTo("#detailBody table a");
  await wait(4200);

  // 14) Hızlı hareket
  await caption("Hızlı hareket uyarısı", "Bir coin 5 dakikada %3'ten fazla oynarsa anında haber verir");
  await scrollTop();
  await page.evaluate(() => window.__bump("AVAXUSDT", 1.046));
  await wait(2200);
  await spot("#movesCard", 3000);
  await moveTo("#moves .move");
  await wait(3600);
  await caption("", "");
  await wait(900);

  await cdp.send("Page.stopScreencast");
  await browser.close();
  server.kill();

  // ffmpeg concat listesi (her karenin ekranda kalma süresi)
  const lines = [];
  for (let i = 0; i < frames.length; i++) {
    const dur = i + 1 < frames.length ? Math.max(0.001, frames[i + 1].ts - frames[i].ts) : 1.0;
    lines.push(`file 'frames/${frames[i].file}'`, `duration ${dur.toFixed(4)}`);
  }
  lines.push(`file 'frames/${frames[frames.length - 1].file}'`);
  fs.writeFileSync(path.join(OUT, "frames.txt"), lines.join("\n"));
  const total = frames[frames.length - 1].ts - frames[0].ts;
  console.log(`${frames.length} kare, ${total.toFixed(1)} sn. Sayfa hataları:`, errors);
})().catch(e => { console.error(e); process.exit(1); });
