#!/usr/bin/env bash
# After a successful unlock: verify root/ADB, install the EchoNeura satellite
# (mic → WebSocket) on the Dot, and start it against your EchoNeura server.
#
#   ./post-root.sh --host <echoneura-ip> [--port 8000] [--seconds 0]
#
# The satellite streams the mic live; Ctrl-C ends the utterance and prints the
# transcript + assistant reply. Protocol: docs/device/voice-protocol.md
set -uo pipefail

HOST=""; PORT=8000; EXTRA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    *) EXTRA="$EXTRA $1"; shift;;
  esac
done
[ -n "$HOST" ] || { echo "usage: post-root.sh --host <ip-of-echoneura-server> [--port 8000]"; exit 2; }

cd "$(dirname "$0")"

echo "== device check =="
adb wait-for-device || exit 1
adb devices | sed 's/^/  /'
rel=$(adb shell getprop ro.build.version.release 2>/dev/null | tr -d '\r')
echo "  android: ${rel:-?} (expect 5.1 on Fire OS 5)"
adb root >/dev/null 2>&1 || true
id=$(adb shell id 2>/dev/null | tr -d '\r')
echo "  shell:   $id"

echo
echo "== satellite binary =="
if [ ! -f satellite/satellite-arm ]; then
  echo "  building static ARM binary (needs zig or the musl cross toolchain)…"
  (cd satellite && make cross) || { echo "!! build failed — see tools/dot/satellite/Makefile"; exit 1; }
fi
adb push satellite/satellite-arm /data/local/tmp/satellite >/dev/null
adb shell chmod 755 /data/local/tmp/satellite
echo "  installed /data/local/tmp/satellite"

echo
echo "== mic sanity (2 s capture, expect no errors) =="
adb shell "/data/local/tmp/satellite --host $HOST --port $PORT --seconds 2 --source dot $EXTRA" || true

echo
cat <<EOF
Mic path notes (read once):
  * If capture fails with 'pcm busy': the stock Alexa service holds the mic.
    Disable it (root):  adb shell pm disable-user --user 0 com.amazon.alexa
    (package names vary by Fire OS 5 build — list with: adb shell pm list packages | grep -i alexa)
  * Wrong card? Probe:  adb shell cat /proc/asound/cards
    then pass --card N --device M.
  * Keep it honest: after this mod the mute button guarantees NOTHING. Stream
    only while you intend to (the satellite stops on Ctrl-C / --seconds).

Live loop:
  adb shell /data/local/tmp/satellite --host $HOST --port $PORT $EXTRA
Results also appear in EchoNeura: /voice history panel, GET /api/voice/utterances
EOF
