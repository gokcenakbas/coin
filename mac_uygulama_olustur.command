#!/bin/bash
# Çift tıklayın: "Coin Takip" uygulamasını bu Mac'te oluşturur, Uygulamalar klasörüne kurar ve açar.
cd "$(dirname "$0")" || exit 1
pause() { read -r -p "Kapatmak için Enter'a basın"; }

echo "Coin Takip uygulaması hazırlanıyor (ilk sefer 3-5 dakika sürebilir)..."
if ! bash packaging/build_mac.sh; then
  echo
  echo "Uygulama oluşturulamadı. Yukarıdaki hata mesajını Claude'a gönderin."
  pause
  exit 1
fi

TARGET=/Applications
if [ ! -w "$TARGET" ]; then
  TARGET="$HOME/Applications"
  mkdir -p "$TARGET"
fi
rm -rf "$TARGET/Coin Takip.app"
cp -R "dist/Coin Takip.app" "$TARGET/"
echo
echo "Kuruldu: $TARGET/Coin Takip.app"
echo "Bundan sonra Launchpad'den veya Uygulamalar klasöründen 'Coin Takip'i açabilirsiniz."
open "$TARGET/Coin Takip.app"
pause
