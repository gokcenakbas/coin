#!/bin/bash
# "Coin Takip.app" uygulamasını bu Mac'te derler. Çıktı: dist/Coin Takip.app
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
  done
fi
[ -n "$PY" ] || { echo "Python 3 bulunamadı: https://www.python.org/downloads/"; exit 1; }
echo "Python: $($PY --version)"

if [ ! -d .build-venv ]; then "$PY" -m venv .build-venv; fi
source .build-venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt -r requirements-app.txt "pyinstaller>=6.10"

# Simge: packaging/icon.png -> packaging/CoinTakip.icns
ICONSET=build/CoinTakip.iconset
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" packaging/icon.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) packaging/icon.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o packaging/CoinTakip.icns

python -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/CoinTakip.spec
echo "Hazır: dist/Coin Takip.app"
