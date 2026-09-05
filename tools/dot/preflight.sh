#!/usr/bin/env bash
# Host readiness checks for the biscuit unlock. Run BEFORE touching the device.
set -uo pipefail

pass=0; warn=0; fail=0
ok()   { echo "  [PASS] $1"; pass=$((pass+1)); }
wrn()  { echo "  [WARN] $1"; warn=$((warn+1)); }
bad()  { echo "  [FAIL] $1"; fail=$((fail+1)); }

echo "== EchoNeura Dot root — preflight =="

[ "$(uname -s)" = "Linux" ] && ok "Linux host" || bad "amonet needs Linux (use a live USB if needed)"

command -v sudo >/dev/null && ok "sudo" || bad "sudo missing"
command -v python3 >/dev/null && ok "python3" || bad "python3 missing (apt install python3)"
python3 -c "import serial" 2>/dev/null && ok "python3-serial" || bad "pyserial missing (apt install python3-serial)"
command -v adb >/dev/null && ok "adb" || bad "adb missing (apt install adb)"
command -v fastboot >/dev/null && ok "fastboot" || bad "fastboot missing (apt install fastboot)"
command -v unzip >/dev/null && ok "unzip" || bad "unzip missing"

if systemctl is-active --quiet ModemManager 2>/dev/null; then
  bad "ModemManager is RUNNING — it hijacks the serial port. Run:
        sudo systemctl stop ModemManager && sudo systemctl disable ModemManager"
else
  ok "ModemManager not active"
fi

echo
echo "-- USB devices (look for MediaTek / amonet states) --"
if command -v lsusb >/dev/null; then
  lsusb | sed 's/^/  /'
  if lsusb | grep -qiE "0e8d:|MediaTek"; then
    ok "a MediaTek USB device is present"
  else
    wrn "no MediaTek device now (expected while the Dot is off/unplugged)"
  fi
else
  wrn "lsusb missing (apt install usbutils) — check cables manually"
fi

echo
echo "-- Device over ADB (only if it still boots stock) --"
if command -v adb >/dev/null && adb devices 2>/dev/null | grep -qE "device$"; then
  ok "adb sees a device"
  fw=$(adb shell getprop ro.amazon.fireos.version 2>/dev/null | tr -d '\r')
  build=$(adb shell getprop ro.build.version.incremental 2>/dev/null | tr -d '\r')
  echo "  Fire OS: ${fw:-?}  build: ${build:-?}"
  if [ "$fw" = "6.5.7.0" ] || [ "$build" = "12383141252" ]; then
    ok "firmware is the supported 6.5.7.0 / 12383141252"
  else
    wrn "firmware is NOT 6.5.7.0 (12383141252) — OTA-update FIRST, the exploit checks this"
  fi
else
  wrn "no adb device (fine if the Dot is off, already in fastboot, or bricked)"
fi

echo
echo "== $pass passed, $warn warnings, $fail failures =="
if [ "$fail" -gt 0 ]; then
  echo "Fix the FAIL items, then re-run. Guide: docs/device/echo-dot-rs03qr-jailbreak.md"
  exit 1
fi
echo "Ready. Next: ./root-assistant.sh <path-to-extracted-amonet>"
