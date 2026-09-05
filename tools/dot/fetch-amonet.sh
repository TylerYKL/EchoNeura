#!/usr/bin/env bash
# Download & verify the amonet-biscuit unlock kit + f1r30s post-root zip.
#
#   ./fetch-amonet.sh [dest-dir]        (default: ./amonet-dist)
#
# Sources (verified 2026-09-05 against the XDA thread, post #1):
#   amonet-biscuit-v1.1.0.zip  18.3 MB  attachments/amonet-biscuit-v1-1-0-zip.6331296
#                                      (changelog link: .6327883 — same release)
#   f1r30s.zip                  403 KB  thread attachments (id probed, verified by size+magic)
#   thread: https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-2nd-gen-2016-biscuit.4761416/
#
# If XDA refuses the direct download (cookies/UA), the script prints the exact
# manual steps — never silently continues with a bad file.
set -euo pipefail

DEST="${1:-./amonet-dist}"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
AMONET_URLS=(
  "https://xdaforums.com/attachments/amonet-biscuit-v1-1-0-zip.6331296/"
  "https://xdaforums.com/attachments/amonet-biscuit-v1-1-0-zip.6327883/"
)
F1R30S_URLS=(
  "https://xdaforums.com/attachments/f1r30s-zip.6273805/"
)

mkdir -p "$DEST"
cd "$DEST"

fetch() { # fetch <out> <min-bytes> <max-bytes> <url...>
  local out="$1" min="$2" max="$3"; shift 3
  for url in "$@"; do
    echo "-> trying $url"
    if curl -fL --retry 2 -A "$UA" -o "$out.part" "$url" 2>/dev/null; then
      local sz; sz=$(stat -c %s "$out.part" 2>/dev/null || echo 0)
      if [ "$sz" -ge "$min" ] && [ "$sz" -le "$max" ] && head -c2 "$out.part" | grep -q "PK"; then
        mv "$out.part" "$out"
        echo "   ok: $out ($sz bytes)"
        return 0
      fi
      rm -f "$out.part"
    fi
  done
  return 1
}

THREAD="https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-2nd-gen-2016-biscuit.4761416/"

if [ -f amonet-biscuit-v1.1.0.zip ]; then
  echo "amonet zip already present, skipping download"
elif fetch amonet-biscuit-v1.1.0.zip 17000000 20000000 "${AMONET_URLS[@]}"; then
  :
else
  cat <<EOF
!! Could not download amonet-biscuit-v1.1.0.zip automatically.
   Get it manually (free account may be required):
     $THREAD
   -> "CURRENT RELEASE: amonet-biscuit-v1.1.0.zip" (18.3 MB)
   Place it in $PWD and re-run this script.
EOF
  exit 1
fi

if [ -f f1r30s.zip ]; then
  echo "f1r30s.zip already present, skipping download"
elif fetch f1r30s.zip 380000 430000 "${F1R30S_URLS[@]}"; then
  :
else
  cat <<EOF
!! f1r30s.zip not auto-fetched (attachment id may have changed).
   Download it from the same thread's Attachments section (403 KB) into $PWD.
   It is required AFTER flashing stock firmware (enables ADB/UART, blocks OTA,
   disables dm-verity). The unlock itself does not need it.
EOF
fi

echo
echo "sha256 (cross-check with the thread comments before flashing):"
sha256sum amonet-biscuit-v1.1.0.zip 2>/dev/null || true
sha256sum f1r30s.zip 2>/dev/null || true

echo
echo "extracting amonet…"
unzip -q -o amonet-biscuit-v1.1.0.zip -d amonet
for f in brick.sh bootrom-step.sh fastboot-step.sh boot-recovery.sh boot-fastboot.sh; do
  [ -f "amonet/$f" ] || { echo "!! expected amonet/$f missing from zip"; exit 1; }
done
echo "verified: brick.sh bootrom-step.sh fastboot-step.sh boot-recovery.sh boot-fastboot.sh"
echo
echo "Next: ./preflight.sh   then   ./root-assistant.sh $PWD/amonet"
