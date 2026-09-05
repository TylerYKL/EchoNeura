#!/usr/bin/env bash
# Guided, gate-by-gate runner for the amonet-biscuit unlock (RS03QR only).
#
#   ./root-assistant.sh <path-to-extracted-amonet>
#
# Every physical step is confirmed by YOU (LED states) before the next command
# runs; aborting at any gate is safe and prints where you stand + unbrick path.
# Canonical guide: docs/device/echo-dot-rs03qr-jailbreak.md
set -uo pipefail

AMONET="${1:?usage: root-assistant.sh <path-to-extracted-amonet>}"
cd "$AMONET" || exit 1
for f in brick.sh bootrom-step.sh fastboot-step.sh; do
  [ -x "$f" ] || [ -f "$f" ] || { echo "!! $AMONET/$f not found — run fetch-amonet.sh first"; exit 1; }
done

step=0
gate() { # gate <prompt>
  step=$((step+1))
  echo
  echo "════ STEP $step ════"
  echo "$1"
  while true; do
    printf "continue? [y] yes  [n] no/retry prompt  [q] quit: "
    read -r ans
    case "$ans" in
      y|Y) return 0;;
      n|N) return 1;;
      q|Q) echo "Aborted. State is preserved; re-run this script to resume."; exit 0;;
      *) ;;
    esac
  done
}

echo "════ STEP 0: firmware ════"
echo "In the Alexa app (Device → About) or via adb, confirm Fire OS 6.5.7.0"
echo "(version code 12383141252). If not: plug the Dot in and let it OTA-update"
echo "(may take several cycles) BEFORE continuing. The exploit refuses others."
gate "Firmware confirmed as 6.5.7.0 / 12383141252?" || echo "(continue only after updating)"

echo
echo "════ STEP 1: stock fastboot attempt ════"
echo "Unplug the power cable. Plug it back in HOLDING the ACTION button (•)."
echo "Watch the ring: GREEN LED = stock fastboot (then we can skip bricking)."
gate "Did you see a steady GREEN LED?"
if [ $? -eq 0 ]; then
  echo "Great — device is in stock fastboot; skipping brick.sh."
  SKIP_BRICK=1
else
  SKIP_BRICK=0
  echo "No green LED. Two options:"
  echo "  a) proceed with brick.sh (software route), or"
  echo "  b) SAFER: open the case and short one pin (short1.jpg/short2.jpg in the"
  echo "     XDA thread) — skips brick.sh entirely."
fi

if [ "$SKIP_BRICK" -eq 0 ]; then
  gate "Choose route: answer y = run brick.sh now, n = I will pin-short instead (skip to step 3)"
  if [ $? -eq 0 ]; then
    echo
    echo "▶ running: sudo ./brick.sh   (follow ITS on-screen prompts)"
    echo "  Success looks like: RAINBOW LED RING. Then UNPLUG the device."
    echo "  'unsupported version' message? → use the pin-short route instead."
    sudo ./brick.sh
    gate "Did the ring show a RAINBOW pattern (then you unplugged)?" || {
      echo "brick.sh did not succeed. Use the pin-short route: hold the short while";
      echo "unplugged, then continue to step 3 and keep holding until told to release.";
    }
  fi
fi

echo
echo "════ STEP 3: bootrom exploit ════"
echo "▶ running: sudo ./bootrom-step.sh"
echo "  THEN plug the device in. (Pin-short route: hold the short now, release"
echo "  when the script says so.)"
sudo ./bootrom-step.sh
gate "Device rebooted into HACKED fastboot = SPINNING RAINBOW ring?" || {
  echo "Not spinning rainbow? Re-run this step; check cable (DATA microUSB!),";
  echo "ModemManager off (preflight.sh), and try the pin-short timing again.";
  exit 1;
}

echo
echo "════ STEP 4: install unlock + TWRP ════"
echo "▶ running: sudo ./fastboot-step.sh   (press Enter when it asks)"
sudo ./fastboot-step.sh
echo
echo "Success = device boots TWRP: PULSATING CYAN LED."
gate "Pulsating cyan LED (TWRP) visible?" || {
  echo "No cyan? Enter TWRP manually: unplug, plug in, and when the blue LED";
  echo "appears hold the MUTE button ~5 s. Then re-run from this step.";
  exit 1;
}

echo
echo "════ STEP 5: wipe + flash OS + f1r30s ════"
echo "You need, in ONE folder: a Fire OS 5 stock .bin (thread → Stock firmwares,"
echo "e.g. update-kindle-full_biscuit-272.6.4.1_user_641575220.bin) and f1r30s.zip."
echo "ORDER MATTERS: stock firmware FIRST, f1r30s SECOND, or the OS won't boot."
echo "Never leave the device without an OS image (A/B boot-count brick risk)."
FW="${FW:-}"
F1="${F1R30S:-}"
[ -z "$FW" ] && { printf "path to stock firmware .bin: "; read -r FW; }
[ -z "$F1" ] && { printf "path to f1r30s.zip: "; read -r F1; }
[ -f "$FW" ] && [ -f "$F1" ] || { echo "!! files not found"; exit 1; }

adb wait-for-device
adb shell twrp wipe data
adb shell twrp wipe cache
adb push "$F1" /sdcard/f1r30s.zip
echo "▶ sideloading stock firmware (LED pulses green when done)…"
adb shell twrp sideload &
sleep 2
adb sideload "$FW"
wait
echo "▶ installing f1r30s.zip…"
adb shell twrp install /sdcard/f1r30s.zip

echo
echo "════ STEP 6: reboot rooted ════"
adb shell twrp reboot system
echo
echo "Device should boot Fire OS 5 with ADB force-enabled."
echo "When the ring settles, verify with:  adb shell getprop ro.build.version.release"
echo "Then hand over to EchoNeura:  ./post-root.sh --host <this-machine-ip> --port 8000"
