#!/usr/bin/env bash
# Generates a short synthetic two-speaker sales conversation and encodes it as
# AAC/M4A. Keeping the fixture synthetic avoids redistributing customer audio.
set -euo pipefail

out="${1:-$(dirname "$0")/fixtures/sales_call.m4a}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if command -v espeak-ng >/dev/null 2>&1; then
  tts=espeak-ng
elif command -v espeak >/dev/null 2>&1; then
  tts=espeak
else
  echo "espeak-ng or espeak is required to generate the demo audio" >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required to generate the demo audio" >&2
  exit 1
fi

lines=(
  "en-us|Hi Daniel, thanks for taking the call. I wanted to follow up on the pricing proposal we sent last week."
  "en-gb|Thanks Anna. The price is higher than what we pay today, so that is our main concern."
  "en-us|If you sign a two year contract, we can offer a fifteen percent discount on the license."
  "en-gb|That helps. Please send an updated quote by Friday and we can review the contract next Tuesday."
)

list="$work/list.txt"
i=0
for entry in "${lines[@]}"; do
  voice="${entry%%|*}"
  text="${entry#*|}"
  "$tts" -v "$voice" -s 150 -w "$work/$i.wav" "$text"
  printf "file '%s'\n" "$work/$i.wav" >> "$list"
  i=$((i + 1))
done

mkdir -p "$(dirname "$out")"
ffmpeg -loglevel error -y -f concat -safe 0 -i "$list" -ac 1 -ar 16000 -c:a aac -b:a 48k "$out"
echo "wrote $out"
