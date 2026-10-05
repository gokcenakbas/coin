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
  web/           panel arayüzü (index.html) ve grafik kütüphanesi
  cli.py         komut satırı
tests/           pytest testleri (ağ gerektirmez)
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
