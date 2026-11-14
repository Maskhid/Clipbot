#!/usr/bin/env bash
# Downloads YouTube episodes INSIDE the Codespace (GitHub's computer, not your internet),
# stores them in the repo's "episodes" release and adds them as clip sources.
#
#   bash scripts/yt_to_release.sh "YOUTUBE_LINK_1" "YOUTUBE_LINK_2"
#   BRAND=peakgaminggt bash scripts/yt_to_release.sh "LINK"     # copy settings from another brand
set -uo pipefail
BRAND="${BRAND:-lhm}"
TAG="episodes"
[ $# -ge 1 ] || { echo "Usage: bash scripts/yt_to_release.sh \"YOUTUBE_LINK\" [more links]"; exit 1; }

echo "== Getting tools ready =="
command -v ffmpeg >/dev/null || bash scripts/install_ffmpeg.sh >/dev/null
python -m pip install -q -U "yt-dlp[default]"
JS=()
command -v deno >/dev/null && JS=(--js-runtimes deno) || { command -v node >/dev/null && JS=(--js-runtimes node); }
COOK=()
[ -f ~/yt_cookies.txt ] && COOK=(--cookies ~/yt_cookies.txt)

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner) || { echo "gh can't see the repo"; exit 1; }
gh release view "$TAG" >/dev/null 2>&1 || gh release create "$TAG" --title "Episodes" \
  --notes "Full source videos for clipping (added by yt_to_release.sh)" --prerelease >/dev/null

mkdir -p /tmp/eps
LINKS=()
for url in "$@"; do
  echo "== Downloading: $url =="
  rm -f /tmp/eps/*
  if ! python -m yt_dlp "${JS[@]}" "${COOK[@]}" --restrict-filenames --no-playlist \
      -f "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b" \
      --merge-output-format mp4 -o "/tmp/eps/%(title).70s.%(ext)s" "$url"; then
    echo "!! YouTube refused this download from the Codespace. Tell Claude: 'YouTube blocked in Codespace'."
    continue
  fi
  f=$(ls /tmp/eps/*.mp4 | head -1)
  size=$(stat -c %s "$f")
  if [ "$size" -gt 1900000000 ]; then   # GitHub release files must be under 2 GB
    echo "== File is $((size/1000000)) MB, shrinking it to fit GitHub's 2 GB limit =="
    ffmpeg -y -v error -i "$f" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 160k "${f%.mp4}_s.mp4" && mv "${f%.mp4}_s.mp4" "$f"
  fi
  name=$(basename "$f")
  echo "== Uploading $name to GitHub =="
  gh release upload "$TAG" "$f" --clobber || { echo "!! Upload failed"; continue; }
  LINKS+=("https://github.com/$REPO/releases/download/$TAG/$name")
  rm -f "$f"
done

if [ ${#LINKS[@]} -eq 0 ]; then
  echo "Nothing was added."
  exit 1
fi
echo "== Adding ${#LINKS[@]} episode(s) as clip sources (settings copied from '$BRAND') =="
python -m clipbot add-source "${LINKS[@]}" --like "$BRAND"
echo
echo "Done. Now push:  git add -A && git commit -m \"New episodes\" ; git pull --rebase -q && git push"
