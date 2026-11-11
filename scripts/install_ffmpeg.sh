#!/usr/bin/env bash
# Installs ffmpeg. Tries Ubuntu's package servers for up to 4 minutes; if they're down
# (it happens), downloads a ready-made ffmpeg from GitHub instead.
set -u
if command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null; then
  echo "ffmpeg already installed"; exit 0
fi
if timeout 240 bash -c 'sudo apt-get update -qq -o Acquire::Retries=2 && sudo apt-get install -y -qq --no-install-recommends -o Acquire::Retries=2 ffmpeg'; then
  command -v ffmpeg >/dev/null && { echo "ffmpeg installed from Ubuntu"; exit 0; }
fi
echo "Ubuntu servers unreachable -> using the static ffmpeg build from GitHub"
url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
for i in 1 2 3; do
  curl -fsSL --retry 3 -o /tmp/ffmpeg.tar.xz "$url" && break
  sleep 10
done
mkdir -p /tmp/ffmpeg && tar -xf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1
sudo install -m 755 /tmp/ffmpeg/bin/ffmpeg /tmp/ffmpeg/bin/ffprobe /usr/local/bin/
ffmpeg -version | head -1
