# Coin Tracker — Kripto Takip, Al-Sat Sinyali ve Uyarı Sistemi

Tüm coinleri takip eden, **son 5 yıllık günlük verilere** dayanarak **AL / SAT / BEKLE** sinyali üreten,
sinyal değiştiğinde **Telegram / Discord / Slack** üzerinden uyarı gönderen ve
**hangi borsadan en uygun alınacağını** gösteren bir Python aracı.

> ⚠️ **Yatırım tavsiyesi değildir.** Sinyaller teknik göstergelere dayalı otomatik hesaplamalardır.
> Geçmiş performans gelecekteki sonuçları garanti etmez. Kaybetmeyi göze alamayacağınız parayla işlem yapmayın.

## Neler yapar?

| Özellik | Açıklama |
|---|---|
| **Tüm coinler** | Binance'te işlem gören tüm USDT paritelerini veya hacme göre ilk N coini (varsayılan 100) otomatik bulur. Stablecoinler hariç tutulur. |
| **5 yıllık veri** | Her coin için son 5 yıl (+ gösterge ısınma süresi) günlük mum verisini indirir ve `data/ohlcv/` altında önbelleğe alır. Sonraki çalıştırmalarda yalnızca eksik günler indirilir. |
| **Al-sat sinyali** | 6 bileşenli puan: trend (200G ort.), golden/death cross bölgesi, RSI, MACD, Bollinger bantları ve **5 yıllık fiyat yüzdeliği** (fiyat 5 yılın neresinde?). |
| **Geçmiş başarı** | "Son 5 yılda bu sinyal kaç gün oluştu ve 30 gün sonra ortalama ne oldu?" sorusunu her coin için ayrı ayrı yanıtlar. |
| **Geriye dönük test** | Aynı kuralı son 5 yıl üzerinde komisyon ve iz süren stop ile test eder, **al-tut** stratejisiyle karşılaştırır. |
| **Risk seviyeleri** | AL sinyallerinde ATR tabanlı zarar-durdur ve kâr-al seviyeleri önerir. |
| **Nereden alınır?** | Binance, OKX, Bybit, KuCoin, Gate, Coinbase, Kraken ve BtcTurk fiyatlarını karşılaştırır, komisyon dahil en ucuz borsayı ve işlem bağlantısını verir. |
| **Uyarılar** | Sinyal seviyesi değiştiğinde (ör. BEKLE → AL) ve belirlediğiniz fiyat alarmlarında bildirim gönderir. Aynı uyarı tekrar tekrar gelmez. |
| **HTML rapor** | Tüm coinleri sıralanabilir tek bir tabloda gösterir. |

## Mac uygulaması: Coin Takip

Coin Takip kendi penceresinde açılan bir Mac uygulamasıdır; tarayıcı veya Terminal gerekmez.

- **Açılışta her şey güncel:** Tüm coinler listede hemen görünür ve fiyatları canlı akar. 5 yıllık analizler coin coin hazırlanıp listeye eklenir.
- **Coin coin takip:** Herhangi bir coinin yanındaki ☆ işaretine tıklayınca coin **⭐ Takip listem** sekmesine eklenir. Takip listenizdeki bir coinin sinyali değişince (ör. BEKLE → AL) **Mac bildirimi** gelir.
- **İşlem planı (nereden al, nereden sat):** Her coin için şu seviyeler hesaplanır ve grafikte çizgi olarak gösterilir:
  - **Alım bölgesi:** AL sinyalinde şu anki fiyat ile en yakın destek arası; diğer durumlarda fiyatın inmesi beklenen destek aralığı.
  - **Hedef 1 / Hedef 2 (satış):** Üstteki ilk iki direnç.
  - **Zarar-durdur:** Desteğin 1 ATR altı.
  - **Risk/ödül oranı**, son 1 yılın destek ve dirençleri, son 5 yılın ucuz ve pahalı bölgeleri.

  "🔔 Alım bölgesine inince / Hedef 1'e çıkınca / Zarar-durdura inerse haber ver" düğmeleriyle tek tıkla alarm kurulur.
- **Fiyat alarmı:** Coinin sayfasından "şu fiyatın üstüne çıkınca / altına inince" alarmı kurulur. Alarm çalınca Mac bildirimi gelir.
- Canlı panelin tüm özellikleri (grafikler, emir defteri, zaman dilimleri, nereden alınır) uygulamada da vardır.

### Kurulum — seçenek 1: kendi Mac'inizde oluşturun (önerilen)
1. Python'u kurun: https://www.python.org/downloads/
2. Projeyi indirin (bir kerelik): `git clone https://github.com/gokcenakbas/coin.git`
3. `coin` klasöründeki **`mac_uygulama_olustur.command`** dosyasına çift tıklayın.
   Uygulama 3-5 dakikada oluşturulur, **Uygulamalar** klasörüne kurulur ve açılır.
   Bundan sonra uygulamayı Launchpad'den ya da Dock'tan açabilirsiniz.

### Kurulum — seçenek 2: hazır .dmg indirin
GitHub'da **Actions → mac-app** altındaki son başarılı çalıştırmanın **Artifacts** bölümünden indirin
(M1/M2/M3/M4 işlemcili Mac'ler için `CoinTakip-Apple-Silicon`, Intel işlemcili Mac'ler için `CoinTakip-Intel`).
`.dmg` dosyasını açıp uygulamayı Uygulamalar klasörüne sürükleyin.

Uygulama Apple tarafından imzalanmadığı için ilk açılışta macOS uyarı verir. Bunu bir kez geçmeniz yeterli:
**Sistem Ayarları → Gizlilik ve Güvenlik** sayfasının en altındaki **"Yine de Aç"** düğmesine tıklayın.
(Seçenek 1 ile kendi Mac'inizde oluşturulan uygulamada bu uyarı çıkmaz.)

Uygulamanın verileri, takip listesi ve günlük dosyası `~/Library/Application Support/CoinTakip/` klasöründe tutulur.
Ayarları değiştirmek için `config.example.yaml` dosyasını bu klasöre `config.yaml` adıyla kopyalayıp düzenleyin.

## Canlı panel (önerilen)

```bash
python -m cointracker dashboard
```

Tarayıcıda **http://localhost:8050** otomatik açılır (Mac'te `mac_baslat.command` dosyasına çift tıklamak da aynı işi yapar):

- **Anlık fiyatlar:** Tüm coinlerin fiyatı, 24 saatlik değişimi ve hacmi Binance canlı akışından **her saniye** güncellenir.
- **Mum grafiği:** 1 dk, 5 dk, 15 dk, 1 sa, 4 sa, 1 gün ve 1 hafta seçenekleriyle. Son mum canlı oluşur; 50/200 ortalama ve Bollinger bantları da gösterilir.
- **Zaman dilimlerine göre sinyal:** 15 dk / 1 saat / 4 saat / 1 gün için ayrı AL/SAT puanı, RSI ve trend yönü.
- **Emir defteri:** Fiyatın ±%2'si içindeki alıcı ve satıcı emirleri ile alıcı/satıcı baskısı, makas ve en büyük alış/satış duvarları.
- **Son 24 saat:** En yüksek ve en düşük fiyat, ortalama fiyat (VWAP), işlem hacmi ve işlem sayısı.
- **Hızlı hareket uyarısı:** Bir coin 5 dakika içinde %3'ten fazla yükselir veya düşerse panelde görünür. "🔔 Bildirimleri aç" ile masaüstü bildirimi olarak da gelir.
- Coine tıklayınca gerekçeler, 5 yıllık istatistikler, strateji testi ve hangi borsadan alınacağı da görünür.

Panel yalnızca sizin bilgisayarınızdan erişilebilir; terminal penceresi açık kaldığı sürece çalışır.
Sinyaller her 15 dakikada bir yeniden hesaplanır (`--refresh 5` ile 5 dakikaya düşürülebilir).

## Kurulum

```bash
git clone https://github.com/gokcenakbas/coin.git
cd coin
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml                   # isteğe bağlı, ayarları düzenleyin
```

API anahtarı **gerekmez**; yalnızca borsaların herkese açık fiyat verileri kullanılır.

## Kullanım

```bash
# 1) 5 yıllık veriyi indir (ilk sefer 100 coin için birkaç dakika sürer)
python -m cointracker update

# 2) Tüm coinlerin güncel sinyalleri (puana göre sıralı)
python -m cointracker scan
python -m cointracker scan --only buy --backtest     # yalnızca AL sinyalleri + 5 yıllık strateji getirisi
python -m cointracker scan --coins BTC ETH SOL       # yalnızca belirli coinler

# 3) Tek coin için ayrıntılı analiz: gerekçeler, 5 yıllık istatistik, test sonucu, borsa fiyatları
python -m cointracker analyze BTC

# 4) Nereden alayım?
python -m cointracker where SOL

# 5) Stratejinin 5 yıllık geriye dönük testi
python -m cointracker backtest

# 6) Sürekli takip ve uyarı (varsayılan saatte bir)
python -m cointracker watch
python -m cointracker watch --interval 30            # 30 dakikada bir
python -m cointracker watch --once                   # tek tur (cron için)

# 7) HTML rapor
python -m cointracker report --out rapor.html
```

### Örnek çıktı (`analyze`)

```
🟢 SOLUSDT: AL  (puan +3)
Fiyat: 142.30 USDT | 7g -8.1% | 30g -21.4%
  • Fiyat 200 günlük ortalamanın %12.4 altında (düşüş trendi)
  • RSI 27.9 → aşırı satım bölgesi (tepki yükselişi ihtimali)
  • MACD sinyal çizgisini yukarı kesti (al sinyali)
  • Fiyat alt Bollinger bandının altında (aşırı düşüş)
  • 5 yıllık fiyat yüzdeliği: %18 (0 = 5 yılın en ucuzu, 100 = en pahalısı) → tarihsel olarak UCUZ
Son 5 yılda bu sinyal 212 gün görüldü → 30 gün sonra ort. +9.1%, yükselme oranı %61 (tüm günlerin ortalaması +4.0%)
Öneri: zarar-durdur ≈ 126.10, kâr-al ≈ 166.60
En uygun alış: okx 142.25 (SOL/USDT) → https://www.okx.com/tr/trade-spot/sol-usdt
```
*(rakamlar örnektir)*

## Sinyaller ne kadar güvenilir?

Panelin üstündeki **"Sinyaller ne kadar güvenilir?"** kartı, açık olan coin listesinin son 5 yılını kullanarak
şunu canlı olarak hesaplar: AL sinyalinden 30 gün sonra fiyat yüzde kaç oranında yükseldi, herhangi bir günde
bu oran neydi? Her coinin "Neden?" bölümünde aynı karşılaştırma o coin için ve piyasa durumuna
(Bitcoin yükselişte / kararsız / düşüşte) göre ayrı ayrı gösterilir. Başlıktaki **Piyasa** göstergesi Bitcoin'in
200 günlük ortalamaya göre trendini gösterir.

Gerçek veriyle yapılan ölçüm (Ekim 2026, en büyük 35 coin, `tools/research/`):

| | Son 5 yıl | Son 2 yıl |
|---|---|---|
| AL sinyalinden 30 gün sonra yükselme | %40 | %32 |
| Herhangi bir günde 30 gün sonra yükselme | %43 | %43 |
| Mevcut kuralla açılan işlemlerin kârla kapanma oranı | %36 | %34 |

Yani teknik puan tek başına rastgele bir günden daha iyi sonuç vermedi. Bitcoin düşüşteyken AL sinyallerini
engellemek de sonucu iyileştirmedi (o dönemlerde AL sinyalleri ortalamadan kötü değildi). Ayrıca
"yükseliş trendinde geri çekilmede al" stratejisi denendi: %60-67 başarı oranına ulaştı ama son 2 yılda zarar
ettirdi, bu yüzden uygulamaya eklenmedi. Sinyalleri tek başına karar olarak değil, yardımcı bilgi olarak kullanın.

## Hızlı hareketler önceden sezilebilir mi?

`tools/research/fastmoves.py` ile ölçüldü (Ekim 2026, 39 coin, 1 yıllık saatlik veri; "hızlı hareket" = sonraki
4 saatte ±%5; herhangi bir saatte olasılığı ~%6-7). Sonuçlar ilk 8 ay / son 4 ay ayrı ayrı:

| Öncü iz | Olasılık (kat) | Yön |
|---|---|---|
| Saatlik hacim normalin ≥3 katı | ~%16 (2,3-2,5×) | yukarı ≈ aşağı |
| Saatlik hacim normalin ≥5 katı | ~%22 (3,1-3,2×) | yukarı ≈ aşağı |
| Hacimle 3 günlük tepe kırılımı | %18-24 (2,4-3,8×) | iki yön de |
| Hacimle 3 günlük dip kırılımı | tutarsız (2,3× / 1,0×) | — |
| Sıkışma (Bollinger bandı 30 günün en darında) | **~%3 (0,4-0,5×)** | — |

Panelin **🚀 Patlama adayları** kartı yalnızca tutarlı çıkan iki izi (hacim patlaması, hacimli yukarı kırılım)
gösterir; büyük hareket olasılığını söyler, yönünü söylemez. **⚡ Erken uyarı** ise tüm coinlerin 1 dakikalık
mumlarını izler: bir dakikadaki hacim normalin 5 katını aşar ve fiyat aynı dakikada %0,8'den fazla oynarsa haber verir
(5 dakikada %3 eşiğini beklemeden).

## Sinyal nasıl hesaplanır?

Her gün için aşağıdaki bileşenler toplanır:

| Bileşen | + (al yönü) | − (sat yönü) |
|---|---|---|
| Trend | Fiyat > 200G ort. → +1 | Fiyat < 200G ort. → −1 |
| Ortalama kesişimi | 50G ort. > 200G ort. → +1 | 50G ort. < 200G ort. → −1 |
| RSI(14) | < 30 → +2, 30–35 → +1 | > 70 → −2, 65–70 → −1 |
| MACD | MACD > sinyal → +1 | MACD < sinyal → −1 |
| Bollinger(20, 2) | Fiyat < alt bant → +1 | Fiyat > üst bant → −1 |
| 5 yıllık değer | Fiyat 5 yılın alt %20'sinde → +1 | Üst %10'unda → −1 |

**Puan ≥ 4 → GÜÇLÜ AL, ≥ 2 → AL, ≤ −2 → SAT, ≤ −4 → GÜÇLÜ SAT**, arası BEKLE.
Tüm eşikler `config.yaml` içinden değiştirilebilir.

**Geriye dönük test kuralı:** gün sonunda puan AL eşiğindeyse ertesi gün açılışta alınır, SAT eşiğine
düşerse ertesi gün açılışta satılır; ayrıca 2×ATR iz süren stop vardır. Her işlemde %0.1 komisyon düşülür.
Sonuçlar al-tut ile hem getiri hem de en büyük düşüş (max drawdown) açısından karşılaştırılır.

## Uyarıları telefona almak

### Telegram
1. Telegram'da **@BotFather**'a `/newbot` yazıp bir bot oluşturun, verdiği **token**'ı alın.
2. Botunuza bir mesaj gönderin, ardından `https://api.telegram.org/bot<TOKEN>/getUpdates` adresinden `chat.id` değerini bulun.
3. Ortam değişkenlerini tanımlayın:
   ```bash
   export TELEGRAM_BOT_TOKEN="123456:ABC..."
   export TELEGRAM_CHAT_ID="123456789"
   python -m cointracker watch
   ```

### Discord / Slack
Kanal ayarlarından bir *Incoming Webhook* oluşturun ve `ALERT_WEBHOOK_URL` ortam değişkenine verin.

### Fiyat alarmları
`config.yaml` içinde:
```yaml
alerts:
  price_alerts:
    - {coin: BTC, above: 150000}
    - {coin: ETH, below: 2000}
```

### Sunucusuz çalıştırma (GitHub Actions)
`.github/workflows/alerts.yml` her saat başı `watch --once` çalıştırır ve veriyi/uyarı durumunu önbellekte saklar.
Depo ayarlarında **Settings → Secrets and variables → Actions** altına `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
(veya `ALERT_WEBHOOK_URL`) ekleyin. Kendi `config.yaml` dosyanızı kullanmak isterseniz `.gitignore`'dan çıkarıp commit edin.

## Proje yapısı

```
cointracker/
  config.py      ayarlar ve varsayılanlar
  data.py        Binance (yedek: CryptoCompare) veri indirme, coin listesi, yerel önbellek
  indicators.py  SMA, RSI, MACD, Bollinger, ATR, 5 yıllık yüzdelik, zirveden düşüş
  signals.py     puanlama, AL/SAT seviyesi, gerekçeler, 5 yıllık sinyal istatistiği
  backtest.py    geriye dönük test
  exchanges.py   borsalar arası fiyat karşılaştırma ("nereden alınır")
  alerts.py      Telegram / webhook / konsol bildirimleri, uyarı durumu
  engine.py      tüm adımları coinler için birlikte çalıştırır
  report.py      HTML rapor
  live.py        gün içi sinyaller, emir defteri, 24 saatlik özet
  dashboard.py   canlı panel sunucusu
  app.py         Mac uygulaması (kendi penceresi, Mac bildirimleri)
  web/           panel arayüzü (index.html) ve grafik kütüphanesi
  cli.py         komut satırı
tests/           pytest testleri (ağ gerektirmez)
packaging/       uygulama paketleme (PyInstaller), simge, derleme betiği
```

## Tanıtım videosu

`tools/promo/` klasörü, uygulamanın tanıtım videosunu demo verisiyle baştan üretir.
Betik tarayıcıyı otomatik yönetir, altyazıları ve Mac pencere çerçevesini ekler.
Arayüz değiştiğinde videoyu güncellemek için:

```bash
npm i playwright-core @fontsource/inter        # bir kerelik, tools/promo içinde
NODE_PATH=tools/promo/node_modules FONT_DIR=tools/promo/node_modules/@fontsource/inter/files \
  tools/promo/build.sh promo-out               # çıktı: promo-out/CoinTakip-Tanitim.mp4
```

## Testler

```bash
python -m pytest -q
```

## Notlar ve sınırlamalar

- Günlük mumlar kullanılır; günün son mumu henüz kapanmadığı için gün içinde sinyal değişebilir.
- 5 yıldan daha yeni coinlerde "5 yıllık" istatistikler mevcut verinin tamamı üzerinden hesaplanır
  (en az 1 yıl veri yoksa değer bileşeni hesaba katılmaz; 220 günden kısa geçmişi olan coinler atlanır).
- Borsa karşılaştırmasında USD ve USDT pariteleri yaklaşık eşdeğer kabul edilir; komisyonlar standart
  kademenin yaklaşık taker oranlarıdır. Güncel oranları borsanın sitesinden kontrol edin.
- Binance bazı ülkelerden erişime kapalıdır; araç önce `data-api.binance.vision` (herkese açık piyasa verisi)
  adresini dener.
