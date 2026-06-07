# Deploy — `/hisse` komut botu (bot.py)

`bot.py` **7/24 ayakta** kalması gereken bir Discord **gateway** botudur (slash
komutlarına anında cevap verir). GitHub Actions cron'u bunu **çalıştıramaz**
(uyan-çık döngüsü kalıcı bağlantı tutamaz; 7/24 Actions işi ToS'a aykırı) — bu
yüzden ayrı, sürekli açık bir sunucuda koşar. `main.py` (haber botu) Actions
cron'unda kalır; iki bot **bağımsız** deploy edilir.

Aşağıda **Oracle Cloud Always Free VM** üzerinde `systemd` ile kurulum var
(kalıcı ücretsiz; çökse/reboot olsa servis kendiliğinden kalkar). Adımlar
herhangi bir Linux VPS (Hetzner vb.) için de aynıdır.

## 1) VM oluştur (Oracle Always Free)

- Shape: **Ampere (ARM, A1.Flex)** ya da kapasite yoksa **VM.Standard.E2.1.Micro (AMD)** — ikisi de Always Free.
- İmaj: Ubuntu 22.04/24.04 (kullanıcı `ubuntu`) veya Oracle Linux (kullanıcı `opc`).
- SSH anahtarı ekle, VM'e bağlan: `ssh ubuntu@<VM_IP>`
- **Gelen port (ingress) gerekmez**: bot yalnızca *giden* bağlantı yapar (Discord/Yahoo/RSS). Security list'e dokunmana gerek yok.

## 2) Bağımlılıklar (sistem)

```bash
sudo apt update
sudo apt install -y python3 python3-venv git    # Python 3.11+ (3.12/3.13 ideal)
```

## 3) Repo + sanal ortam

```bash
git clone <REPO_URL> ~/discord-botu
cd ~/discord-botu
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 4) Ortam değişkenleri (`.env`)

`~/discord-botu/.env` oluştur (bot.py `load_dotenv()` ile okur):

```env
DISCORD_BOT_TOKEN=buraya_bot_token
# İsteğe bağlı: tek sunucuda anında komut senkronu (test için). Boşsa global (~1 saat).
GUILD_ID=
# İsteğe bağlı: /health'in deneyeceği RSS listesi (boşsa varsayılanlar).
# RSS_FEEDS=
```

> `.env` `.gitignore`'da olmalı — token'ı repoya koyma.

## 5) systemd servisi

```bash
sudo cp deploy/bist-hisse-bot.service /etc/systemd/system/
# Kullanıcı/yol farklıysa düzenle (User=, WorkingDirectory=, ExecStart=):
sudo nano /etc/systemd/system/bist-hisse-bot.service

sudo systemctl daemon-reload
sudo systemctl enable --now bist-hisse-bot      # şimdi başlat + boot'ta otomatik
```

Durum / log:

```bash
systemctl status bist-hisse-bot
journalctl -u bist-hisse-bot -f                 # canlı log
```

Discord'da `on_ready` logunu görüp `/hisse` ve `/health`'i dene. (Komutlar
ilk açılışta senkronlanır; `GUILD_ID` boşsa global yayılma ~1 saat sürebilir.)

## 6) Güncelleme (kod değişince)

```bash
cd ~/discord-botu && ./deploy/update.sh          # git pull + pip + restart
```

İstersen push'ta **otomatik** deploy: GitHub Actions'a, VM'e SSH'leyip
`update.sh` çalıştıran bir iş eklenebilir (SSH özel anahtarı repo secret'ı
olarak). İstersen bu workflow'u da hazırlayabiliriz.

## Notlar

- **`/health`'te seen.db**: Bu VM'de `main.py` koşmadığı için `seen.db` yoktur;
  `/health` "bulunamadı" gösterir (beklenen). İstersen `main.py`'yi de bu VM'de
  (RUN_ONCE'suz, sürekli döngü) ayrı bir servis olarak çalıştırıp Actions cron'unu
  emekliye ayırabilirsin — o zaman seen.db da burada olur. (KAP yine engelli kalır:
  Oracle de veri merkezi IP'sidir.)
- **ARM (Ampere) wheel'leri**: pandas/matplotlib/curl_cffi aarch64 tekerleri
  hazır gelir; derleme gerekmez.
