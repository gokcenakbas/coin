#!/bin/bash
# Mac'te çift tıklayarak çalıştırın: gerekli kurulumu yapar, tüm coinleri tarar ve raporu tarayıcıda açar.
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

echo "Veriler güncelleniyor ve rapor hazırlanıyor (ilk sefer birkaç dakika sürebilir)..."
python -m cointracker report --out rapor.html && open rapor.html

echo
python -m cointracker scan --no-update --limit 30
echo
read -r -p "Kapatmak için Enter'a basın"
