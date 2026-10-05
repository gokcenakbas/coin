#!/bin/bash
# Mac'te çift tıklayarak çalıştırın: gerekli kurulumu yapar ve canlı paneli tarayıcıda açar.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 bulunamadı. Lütfen https://www.python.org/downloads/ adresinden kurup tekrar deneyin."
  read -r -p "Kapatmak için Enter'a basın"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "İlk kurulum yapılıyor (bir kerelik, 1-2 dakika)..."
  python3 -m venv .venv || { read -r -p "Kurulum başarısız. Kapatmak için Enter'a basın"; exit 1; }
fi
source .venv/bin/activate
pip install -q --upgrade pip >/dev/null 2>&1
pip install -q -r requirements.txt || { read -r -p "Paketler kurulamadı. Kapatmak için Enter'a basın"; exit 1; }

echo "Canlı panel açılıyor. İlk açılışta 5 yıllık veriler indirilir (birkaç dakika)."
echo "Bu pencere açık kaldığı sürece panel çalışır. Kapatmak için Ctrl+C veya pencereyi kapatın."
python -m cointracker dashboard
read -r -p "Kapatmak için Enter'a basın"
