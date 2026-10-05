// Giriş/kapanış kartlarını ve Mac pencere çerçevesini 1920x1080 PNG olarak üretir.
// Kullanım: node cards.js <çıktı_klasörü>   (FONT_DIR: @fontsource/inter/files)
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const OUT = path.resolve(process.argv[2] || "promo-out");
const ROOT = path.resolve(__dirname, "../..");
const icon = "data:image/png;base64," + fs.readFileSync(path.join(ROOT, "packaging/icon.png")).toString("base64");
// Pencere: içerik 1600x900, başlık çubuğu 32px; ekranın ortasında
const WX = 160, WY = 74, WW = 1600, TB = 32, CH = 900, R = 12;

function fonts() {
  const dir = process.env.FONT_DIR;
  if (!dir) return "";
  return [400, 500, 600, 700, 800].flatMap(w => ["latin", "latin-ext"].map(sub =>
    `@font-face{font-family:Inter;font-weight:${w};src:url(data:font/woff2;base64,${fs.readFileSync(path.join(dir, `inter-${sub}-${w}-normal.woff2`)).toString("base64")}) format('woff2')}`)).join("\n");
}

const base = `${fonts()}
*{box-sizing:border-box;margin:0}
html,body{width:1920px;height:1080px;font-family:Inter,sans-serif;color:#eef1f6;letter-spacing:-.01em}
.bg{position:absolute;inset:0;background:
  radial-gradient(900px 600px at 15% 10%,rgba(91,140,255,.28),transparent 60%),
  radial-gradient(800px 600px at 90% 95%,rgba(46,189,133,.22),transparent 60%),
  radial-gradient(700px 500px at 85% 5%,rgba(245,179,1,.12),transparent 60%),
  linear-gradient(160deg,#0b0e16,#121726 55%,#0c1119)}`;

const intro = `<style>${base}
.c{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:26px;text-align:center}
img{width:230px;height:230px;filter:drop-shadow(0 24px 60px rgba(0,0,0,.55))}
h1{font-size:112px;font-weight:800;letter-spacing:-.035em}
p{font-size:34px;color:#b9c1d3;font-weight:500;max-width:1300px;line-height:1.35}
.chips{display:flex;gap:14px;margin-top:14px}
.chips span{font-size:24px;font-weight:600;padding:12px 22px;border-radius:999px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12)}
.mac{font-size:22px;color:#8b93a3;margin-top:8px;font-weight:500}
</style><div class="bg"></div><div class="c"><img src="${icon}"><h1>Coin Takip</h1>
<p>Kripto paralar için canlı takip, al-sat sinyali ve uyarı uygulaması</p>
<div class="chips"><span>📈 5 yıllık veri</span><span>⚡ Canlı fiyat</span><span>🎯 Nereden al, nereden sat</span><span>🔔 Mac bildirimleri</span></div>
<div class="mac">macOS uygulaması · Apple Silicon ve Intel</div></div>`;

const outro = `<style>${base}
.c{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:34px}
.head{display:flex;align-items:center;gap:28px}
img{width:120px;height:120px}
h1{font-size:76px;font-weight:800;letter-spacing:-.03em}
.steps{display:grid;grid-template-columns:repeat(3,440px);gap:22px;margin-top:6px}
.s{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:20px;padding:30px 30px 32px}
.s b{display:inline-flex;width:46px;height:46px;border-radius:50%;background:#5b8cff;align-items:center;justify-content:center;font-size:24px;margin-bottom:16px}
.s h3{font-size:30px;font-weight:700;margin-bottom:10px}
.s p{font-size:22px;color:#b9c1d3;line-height:1.45}
.tg{font-size:26px;color:#c9cfdb;font-weight:500}
.d{font-size:20px;color:#8b93a3;text-align:center;line-height:1.6;margin-top:8px}
</style><div class="bg"></div><div class="c">
<div class="head"><img src="${icon}"><h1>Hemen başlayın</h1></div>
<div class="steps">
 <div class="s"><b>1</b><h3>İndirin</h3><p>GitHub → Actions → mac-app → Artifacts bölümünden Mac'inize uygun .dmg dosyası</p></div>
 <div class="s"><b>2</b><h3>Kurun</h3><p>Coin Takip'i Uygulamalar klasörüne sürükleyin, ilk açılışta “Yine de Aç” deyin</p></div>
 <div class="s"><b>3</b><h3>Takip edin</h3><p>Coinlerinizi ☆ ile takip listesine ekleyin, alarmlarınızı kurun</p></div>
</div>
<div class="tg">Mac kapalıyken de Telegram'dan uyarı alabilirsiniz.</div>
<div class="d">⚠️ Yatırım tavsiyesi değildir; sinyaller teknik göstergelere dayalı otomatik hesaplamalardır.<br>Videodaki fiyatlar ve grafikler demo verisiyle hazırlanmıştır.</div>
</div>`;

// Pencere çerçevesi: arka plan + gölge + başlık çubuğu; içerik alanı şeffaf (video bunun altına yerleşir)
const hole = (() => {
  const x = WX, y = WY + TB, w = WW, h = CH;
  return `M0 0H1920V1080H0Z M${x} ${y}H${x + w}V${y + h - R}Q${x + w} ${y + h} ${x + w - R} ${y + h}H${x + R}Q${x} ${y + h} ${x} ${y + h - R}Z`;
})();
const frame = `<style>${base}
html,body{background:transparent}
.bg{clip-path:path(evenodd,'${hole}')}
.win{position:absolute;left:${WX}px;top:${WY}px;width:${WW}px;height:${TB + CH}px;border-radius:${R}px;
  box-shadow:0 40px 120px rgba(0,0,0,.6),0 0 0 1px rgba(255,255,255,.10)}
.tb{position:absolute;left:${WX}px;top:${WY}px;width:${WW}px;height:${TB}px;border-radius:${R}px ${R}px 0 0;
  background:linear-gradient(#2a2e37,#23262e);border-bottom:1px solid #111;display:flex;align-items:center;justify-content:center}
.tl{position:absolute;left:14px;top:10px;display:flex;gap:8px}
.tl i{width:12px;height:12px;border-radius:50%;display:block}
.tb span{font-size:14px;font-weight:600;color:#c9cdd6}
</style><div class="bg"><div class="win"></div></div>
<div class="tb"><div class="tl"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i></div><span>Coin Takip</span></div>`;

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROME || "/opt/pw-browsers/chromium" });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  for (const [name, html, transparent] of [["intro", intro, false], ["outro", outro, false], ["frame", frame, true]]) {
    await page.setContent(html);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(OUT, `${name}.png`), omitBackground: transparent });
  }
  await browser.close();
  console.log("kartlar hazır");
})();
