# BIST Haber Botu (Aşama 1: Haber Toplama + Filtre + Discord)

Borsa İstanbul'u etkileyebilecek haberleri (RSS kaynakları + KAP bildirimleri)
toplar, hisse/anahtar kelime bazında **filtreler ve önem skoru** verir, eşiği
geçenleri **Discord'a** zengin mesaj (embed) olarak anlık gönderir.

> Bu, projenin ilk parçası. Aracı kurum (AKD/AKDE) modülü ayrı bir aşama olarak
> aynı projeye eklenecek şekilde tasarlandı (`asyncio` tabanlı, kaynak modülü
> takılıp çıkarılabilir).

## Bu botun yaptığı

1. **Toplama** — `sources.py`: birden çok RSS feed'i + (opsiyonel) KAP bildirim akışı.
2. **Filtreleme** — `filters.py`: metinde BIST hisse kodu/şirket adı eşleştirir,
   makro + şirket bazlı anahtar kelimelere ağırlık verip bir **önem skoru** üretir.
   Skoru eşiğin altında kalan haber gönderilmez.
3. **Tekilleştirme** — `store.py`: SQLite ile daha önce görülen haber tekrar gönderilmez.
4. **Çıkarım** — `inference.py`: kural tabanlı **olası etki tahmini** — etki yönü
   (🟢 pozitif / 🔴 negatif / 🟡 karışık / ⚪ belirsiz), nedeni ve etkilenmesi
   beklenen hisse/piyasa embed'de gösterilir. (LLM değildir; yatırım tavsiyesi değildir.)
5. **Zenginleştirme (opsiyonel)** — eşleşen hisselerin anlık fiyatı/günlük değişimi
   embed'e eklenir (`yfinance`, kapatılabilir).
6. **Bildirim** — `notifier.py`: Discord webhook'una sade bir embed gönderir
   (haber tarihi/saati Discord'un yerel saat gösterimiyle), 429 (rate limit) yönetir.

## Kurulum

```bash
pip install -r requirements.txt
cp .env.example .env
# .env dosyasını aç ve DISCORD_WEBHOOK_URL değerini gir
python main.py
```

### Discord webhook nasıl alınır
Discord sunucunda: Kanal ayarları → Entegrasyonlar → Webhook'lar → Yeni Webhook →
URL'yi kopyala → `.env` içine `DISCORD_WEBHOOK_URL` olarak yapıştır.

## Ayarlar (.env)

| Anahtar | Açıklama | Varsayılan |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | Discord webhook adresi (zorunlu) | — |
| `POLL_INTERVAL_SECONDS` | Kaç saniyede bir kontrol | `120` |
| `MIN_RELEVANCE_SCORE` | Bu skorun altındaki haber gönderilmez | `3` |
| `ENABLE_KAP` | KAP bildirimlerini çek (`1`/`0`) | `1` |
| `ENABLE_PRICE` | Eşleşen hisseye anlık fiyat ekle (`1`/`0`) | `1` |
| `RSS_FEEDS` | Virgülle ayrılmış RSS adresleri (opsiyonel, override) | dahili liste |
| `RUN_ONCE` | `1` ise tek tur çalışıp çıkar (zamanlanmış ortamlar için) | `0` |
| `SEEN_DB_PATH` | Tekilleştirme veritabanının yolu | `seen.db` |
| `DISCORD_BOT_TOKEN` | `/hisse` komut botunun token'ı (sadece `bot.py` için) | — |
| `GUILD_ID` | Dev: komutların anında göründüğü test sunucusu ID'si | boş (global) |

## /hisse Komutu (ayrı bot: bot.py)

Haber botundan bağımsız ikinci bir giriş noktası: Discord'a **sürekli bağlı**
kalan bir gateway botu. Chatte `/hisse kod:THYAO periyot:1 Ay` yazınca:

- 💰 anlık fiyat + günlük değişim
- 📊 son seans hacmi (lot + TL karşılığı)
- 🔄 dolaşımdaki lot sayısı ve piyasa değeri (+ toplam piyasa değeri)
- 📈 günlük mum grafiği (1 Hafta / 1 Ay / 3 Ay / 1 Yıl): mumların üzerinde
  **SMA200**, altında **MACD(12,26,9)** ve **RSI(14)** panelleri
- 🔘 grafiğin altındaki düğmelerle periyot **anında değiştirilir** (yeni komut
  yazmaya gerek yok; grafik yerinde yeniden çizilir)

SMA200 fiyattan çok uzaktaysa bile grafikte **her zaman görünür**: yakınsa
kendi yerinde çizilir, çok uzaksa kenara kıstırılıp gerçek değeri `▲`/`▼` ile
etiketlenir (mumlar ezilmesin diye).

`kod` alanı yazarken otomatik tamamlanır (`tickers.py` listesinden); listede
olmayan BIST kodları da kabul edilir (veri Yahoo Finance'tan geldiği sürece).

> **Önemli:** Komut botu, haber botu gibi "10 dakikada bir uyan" modeliyle
> çalışamaz — komuta anında cevap için sürekli açık bir süreç gerekir. Bu yüzden
> GitHub Actions'ta DEĞİL, şimdilik lokalde (`python bot.py`) çalıştırılır.
> Haber botu Actions'ta aynen çalışmaya devam eder; ikisi bağımsızdır.

### Bot hesabı kurulumu (bir kerelik)

1. <https://discord.com/developers/applications> → **New Application** → isim ver.
2. Sol menü **Bot** → **Reset Token** → token'ı kopyala → `.env`'e
   `DISCORD_BOT_TOKEN=...` olarak yapıştır (asla commit'leme).
   Ayrıcalıklı intent (Presence/Members/Message Content) **gerekmez**, hiçbirini açma.
3. Sol menü **OAuth2 → URL Generator**: scope olarak `bot` + `applications.commands`
   işaretle; bot izinlerinden `Send Messages`, `Embed Links`, `Attach Files` seç.
   Üretilen URL'yi tarayıcıda aç ve botu sunucuna davet et.
4. (Önerilen, dev için) Discord'da sunucu adına sağ tık → **Sunucu ID'sini Kopyala**
   (Geliştirici Modu açık olmalı) → `.env`'e `GUILD_ID=...` yaz. Böylece komutlar
   **anında** görünür; `GUILD_ID` boşsa global kayıt ~1 saat sürebilir.
5. Çalıştır: `python bot.py` → logda "Bot hazır" görünce `/hisse` kullanılabilir.

### Grafik üzerinde hızlı deneme (Discord'suz)

```bash
python charting.py THYAO 1a   # _chart_THYAO.png yazar (1h=1 hafta, 1a=1 ay, 3a=3 ay, 1y=1 yıl)
```

## Ücretsiz çalıştırma: GitHub Actions (haber botu)

Bot, sunucu olmadan GitHub Actions üzerinde zamanlanmış olarak çalışabilir
(`.github/workflows/bot.yml`). Her ~10-20 dakikada bir tek tur atar
(`RUN_ONCE=1`), `seen.db` turlar arasında cache ile taşınır. Public repo'da
tamamen ücretsizdir.

Kurulum:

1. GitHub'da **public** bir repo oluştur (private repo'da aylık dakika kotası
   bu sıklığa yetmez).
2. Kodu push'la (`.env` zaten `.gitignore`'da — webhook URL'i asla commit'leme).
3. Repo'da **Settings → Secrets and variables → Actions → New repository secret**:
   `DISCORD_WEBHOOK_URL` = webhook adresin.
4. **Actions** sekmesinden workflow'u etkinleştir; ilk denemeyi
   **Run workflow** düğmesiyle elle tetikleyebilirsin.

Notlar:
- İlk çalıştırma sessizdir (mevcut haberleri işaretler, göndermez) — spam olmaz.
- GitHub, 60 gün commit olmayan repo'larda zamanlanmış workflow'u durdurur;
  e-posta ile uyarır, tek tıkla yeniden etkinleştirilir.
- Workflow'da `ENABLE_KAP=0` — KAP veri merkezi IP'lerini zaten engelliyor.

## Genişletme noktaları
- **Hisse listesi:** `tickers.py` içindeki `TICKERS` sözlüğü. Tam liste için
  `borsapy`/`isyatirimhisse` ile otomatik doldurma fonksiyonu eklenebilir.
- **Anahtar kelimeler / ağırlıklar:** `filters.py` → `KEYWORD_WEIGHTS`.
- **Çıkarım kalıpları:** `inference.py` → `SENTIMENT_PATTERNS` (katlanmış/ASCII
  regex, yön, ağırlık, etiket). Ağırlığı 0 olan satırlar yöne etki etmez,
  sadece "neden" etiketi üretir.
- **LLM ile sınıflandırma:** `filters.py` → `llm_classify()` (varsayılan kapalı,
  bir Claude API çağrısıyla "bu haber hangi hisseyi nasıl etkiler" sınıflandırması).

## Not
KAP'ın resmî bir herkese açık API'si yoktur; `sources.py` içindeki KAP fonksiyonu
sitenin iç uç noktasını kullanır. **Haziran 2026 itibarıyla KAP'ın yeni sitesi bu
uç noktayı WAF arkasına aldı ve bot isteklerini engelliyor** — bot bu durumda
uyarı verip KAP'ı atlar, RSS kaynakları çalışmaya devam eder. Uyarıyı susturmak
için `.env`'de `ENABLE_KAP=0` yapabilirsin. KAP erişimi tekrar açılırsa
`sources.py` içindeki `fetch_kap()` olduğu gibi çalışacaktır.

## Yasal
Bu araç yalnızca bilgilendirme amaçlıdır, yatırım danışmanlığı / yatırım tavsiyesi
değildir.
