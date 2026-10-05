#!/bin/bash
# Tanıtım videosunu baştan üretir.
#   tools/promo/build.sh <çıktı_klasörü>
# Gerekenler: python3 (proje paketleri), node + playwright-core + @fontsource/inter (NODE_PATH ile),
# Chromium (CHROME, varsayılan /opt/pw-browsers/chromium), ffmpeg.
set -euo pipefail
OUT=$(mkdir -p "${1:-promo-out}" && cd "${1:-promo-out}" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)

node "$HERE/record.js" "$OUT"
node "$HERE/cards.js" "$OUT"
cd "$OUT"

enc=(-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -r 30)

# 1) Kayıt karelerini sabit 30 fps videoya çevir
ffmpeg -y -loglevel error -f concat -safe 0 -i frames.txt -fps_mode cfr -r 30 "${enc[@]}" main.mp4

# 2) Mac penceresi çerçevesinin içine yerleştir (içerik 1600x900, konum 160,106)
ffmpeg -y -loglevel error -f lavfi -i "color=c=black:s=1920x1080:r=30" -i main.mp4 -i frame.png \
  -filter_complex "[0][1]overlay=160:106:shortest=1[v];[v][2]overlay=0:0,settb=1/30" "${enc[@]}" framed.mp4

# 3) Giriş ve kapanış kartları
ffmpeg -y -loglevel error -loop 1 -framerate 30 -t 5 -i intro.png -vf "fade=in:st=0:d=0.8,settb=1/30" "${enc[@]}" intro.mp4
ffmpeg -y -loglevel error -loop 1 -framerate 30 -t 9 -i outro.png -vf "fade=out:st=8:d=1,settb=1/30" "${enc[@]}" outro.mp4

# 4) Geçişlerle birleştir, sessiz ses kanalı ekle (bazı paylaşım uygulamaları ses kanalı ister)
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 framed.mp4)
X=0.8
OFF2=$(python3 -c "print(round(5 - $X + $D - $X, 3))")
ffmpeg -y -loglevel error -i intro.mp4 -i framed.mp4 -i outro.mp4 -f lavfi -i "anullsrc=r=44100:cl=stereo" \
  -filter_complex "[0][1]xfade=transition=fade:duration=$X:offset=$(python3 -c "print(5 - $X)")[a];[a][2]xfade=transition=fade:duration=$X:offset=$OFF2,format=yuv420p[v]" \
  -map "[v]" -map 3:a -shortest -c:v libx264 -preset slow -crf 20 -r 30 -c:a aac -b:a 64k -movflags +faststart \
  CoinTakip-Tanitim.mp4

ffprobe -v error -show_entries format=duration,size -of default=nw=1 CoinTakip-Tanitim.mp4
echo "Hazır: $OUT/CoinTakip-Tanitim.mp4"
