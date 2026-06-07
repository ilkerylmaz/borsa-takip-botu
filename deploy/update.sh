#!/usr/bin/env bash
# Botu güncelle: en son kodu çek, bağımlılıkları tazele, servisi yeniden başlat.
# Kullanım (VM'de):  ./deploy/update.sh
set -euo pipefail

cd "$(dirname "$0")/.."
echo "→ git pull"
git pull --ff-only
echo "→ bağımlılıklar"
.venv/bin/pip install -q -r requirements.txt
echo "→ servis yeniden başlatılıyor"
sudo systemctl restart bist-hisse-bot
echo "✓ Güncellendi. Log: journalctl -u bist-hisse-bot -f"
