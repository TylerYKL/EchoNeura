# Echo Dot 2nd Gen (RS03QR, "biscuit") — unlock runbook & EchoNeura integration

> **Read fully before touching the device.** This procedure modifies the
> partition table, **wipes userdata**, and can brick the device if interrupted.
> Nothing here is affiliated with or endorsed by Amazon; it voids any remaining
> warranty and breaches Amazon's device terms of use. Personal-device hacking is
> generally lawful in most jurisdictions — decide for yourself.

Your device: **Echo Dot 2nd generation (2016), model RS03QR, codename
`biscuit`, MediaTek MT8163, Fire OS based.** This is exactly the device the
community exploit targets.

## Credits & sources (canonical — always read these first)

| Resource | Link |
|---|---|
| XDA unlock/root/TWRP/unbrick thread (Rortiz2, sep 2025, updated mar 2026) | https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-2nd-gen-2016-biscuit.4761416/ |
| amonet exploit source (`mt8163-biscuit` branch, actively developed) | https://github.com/R0rt1z2/amonet/tree/mt8163-biscuit |
| TWRP device tree | https://github.com/R0rt1z2/twrp_device_amazon_biscuit |
| Earlier tethered-root PoC on 2nd-gen Echos (mic-audio exfil demo, Daniel B) | https://danieldb.uk/posts/alexa-2/ and https://www.hackster.io/news/this-tethered-root-lets-you-run-your-own-code-on-amazon-s-second-gen-echo-devices-32fc9cab77c9 |
| **This repo's toolkit** (fetcher, preflight, guided unlock, satellite client) | `tools/dot/` — see below |
| Original amonet exploit (xyz`, karnak) & first biscuit port (k4y0z) | credited in the XDA thread |

Current release at time of writing: **amonet-biscuit-v1.1.0** (2026-03-20) —
adds anti-BCB brick protection, LK payload flashing via fastboot, slot
switching (`fastboot set_active [a|b]`), faster flashing. Use the attachment in
the XDA thread, not a mirror.

## What the exploit is (so you know what you're running)

`amonet` is a **MediaTek bootrom (BROM) exploit**: the chip's mask ROM loads the
preloader over USB without authentication, so a payload pushed in BROM mode can
disable secure boot and launch a "hacked fastboot". From there the chain is:
unlock the bootloader → install the exploit into the `boot_a`/`boot_b` slots →
install TWRP recovery → flash a rooted Fire OS 5 image.

Key consequences to understand before starting:

- **A/B quirk:** the exploit lives in `boot_a`/`boot_b`; your OS boots from
  `boot_a_x`/`boot_b_x`. The preloader counts failed boots per slot — **if
  neither slot holds a bootable OS and the device retry-boots repeatedly, it
  bricks.** Never leave the device without a valid OS image flashed.
- **Fire OS 5 only after unlocking:** the exploit requires firmware
  **6.5.7.0 (NS6570/6077, version code 12383141252)** to run, but once
  installed **only Fire OS 5 ROMs boot** — flashing Fire OS 6 afterwards can
  soft-brick.
- **No screen:** all feedback is via the LED ring. Learn the states (below).
- **UART debug console** is force-enabled by the exploit on every boot
  (RX on the **C7** test pad — see the UART.jpg attachment in the XDA thread).
  Handy, not required.

## LED states cheat-sheet

| LED | Meaning |
|---|---|
| Blue boot flash | normal power-on |
| **Green** (steady, while holding action button at plug-in) | stock fastboot mode |
| **Spinning rainbow ring** | hacked fastboot (amonet) |
| **Pulsating cyan** | TWRP recovery |
| Rainbow ring then green pulses | zip installing |

Entering modes (device unplugged → plug in while…):
- **Stock fastboot:** hold the **action button (•)** while plugging in.
- **TWRP:** as soon as the blue LED appears, hold the **mute (mic-off) button ~5 s**; or `adb reboot recovery` from the OS; or `fastboot oem reboot-recovery` from hacked fastboot; or `sudo ./boot-recovery.sh` then plug in.
- **Hacked fastboot:** ~3 s after the blue LED appears, hold the **action button ~5 s**; or from TWRP `adb shell reboot-amonet` (plain `adb reboot` will NOT work); **from the running OS there is no direct method** — power-cycle first.

## Requirements

- A **Linux** machine (Ubuntu recommended; a live USB is fine). macOS/Windows are not supported by the scripts.
- A **MicroUSB data cable** (the Dot's power port carries data — charge-only cables will silently fail).
- Packages: `sudo apt install python3 python3-serial adb fastboot dos2unix` (on Ubuntu also `sudo apt-add-repository universe` first).
- **Disable ModemManager** — it grabs the serial device and breaks the exploit:
  ```bash
  sudo systemctl stop ModemManager
  sudo systemctl disable ModemManager
  ```
- Patience, and a device you've accepted you might lose.

## Toolkit: this repo runs the guide with you

`tools/dot/` turns the steps below into five commands with safety gates
(each gate waits for you to confirm the physical LED state; quitting is always
safe and prints where you stand):

```bash
cd tools/dot
./preflight.sh                            # host, cable, ModemManager, firmware version
./fetch-amonet.sh                         # verified download + extract of v1.1.0 (+f1r30s)
./root-assistant.sh ./amonet-dist/amonet  # brick → bootrom → fastboot → TWRP → flash, gated
./post-root.sh --host <ip-of-echoneura>   # push + smoke-test the satellite on the Dot
adb shell /data/local/tmp/satellite --host <ip> --port 8000   # the live mic loop
```

Verified direct links (checked 2026-09-05; if XDA reshuffles attachments the
scripts fall back to telling you exactly where to click):

| File | URL |
|---|---|
| amonet-biscuit-v1.1.0.zip (18.3 MB) | https://xdaforums.com/attachments/amonet-biscuit-v1-1-0-zip.6331296/ (alt .6327883) |
| f1r30s.zip (403 KB) | thread Attachments section (script probes id 6273805, verifies size+ZIP magic) |
| Fire OS 5.5.3.1 stock FW | https://d1s31zyz7dcc2d.cloudfront.net/8811a0fc982bf3331dc54f5aec45d936/update-kindle-full_biscuit-272.6.4.1_user_641575220.bin |
| Fire OS 5.5.5.4 stock FW | https://d1s31zyz7dcc2d.cloudfront.net/47a1457e0802980eb32f63cd3ce355c0/update-kindle-csm_biscuit-272.6.8.0_user_680767620.bin |

## Step-by-step (from the XDA thread, v1.1.0)

1. **Check firmware.** The device must run **Fire OS 6.5.7.0 (NS6570/6077)**,
   version code **12383141252**. If not, let it OTA-update first (plug in,
   wait; it may take several update cycles). In the Alexa app: Device → About.
2. **Download `amonet-biscuit-v1.1.0.zip`** from the XDA thread attachments,
   extract it, open a terminal in the extracted folder.
3. **Enter fastboot:** unplug power, then plug back in **holding the action
   button (•)** until you see the **green LED**.
   - Device already bricked / no fastboot? Open the shell and **short one of
     the marked pins** (see `short1.jpg`/`short2.jpg` attachments) to force
     BROM mode — opening the Dot 2 is straightforward (plastic clips).
4. **Run `sudo ./brick.sh`** and follow the on-screen prompts. This
   *deliberately corrupts the preloader header* to force the device into BROM
   download mode on the next plug-in. Success = **rainbow LED ring**; then
   unplug the device.
   - ⚠️ The author's note: on some other Amazon devices BROM USBDL is disabled,
     which would make a corrupted preloader a **hard brick**. That is *not*
     currently the case for biscuit — but the author still recommends the
     **pin-shorting method** as the safer route if you're able, since it skips
     `brick.sh` entirely.
   - If `brick.sh` reports "unsupported version": open the device and short the
     pin during the next step instead.
5. **Run `sudo ./bootrom-step.sh`**, then plug the device into the PC.
   (If you skipped/failed `brick.sh`: hold the pin short while unplugged, run
   the script, and keep holding until it tells you to release.)
6. The device reboots into **hacked fastboot** (spinning rainbow ring). Run
   **`sudo ./fastboot-step.sh`** and press Enter. This installs the unlock +
   TWRP permanently into the boot slots.
7. Success = device boots into **TWRP** (pulsating cyan LED). You are now
   unlocked.
8. **Flash an OS** (never leave it slot-less — see the A/B brick note):
   download a **Fire OS 5** stock firmware for biscuit (links in the XDA
   thread, e.g. `update-kindle-full_biscuit-272.6.4.1_user_641575220.bin`)
   **and `f1r30s.zip`** (the thread's magic zip: enables ADB + UART console,
   blocks OTA domains, disables dm-verity). Put both in one folder:
   ```bash
   adb shell twrp wipe data
   adb shell twrp wipe cache
   adb push f1r30s.zip /sdcard/
   adb shell twrp sideload
   adb sideload update.bin          # the stock firmware .bin
   adb shell twrp install /sdcard/f1r30s.zip
   ```
   **Order matters: stock firmware first, then f1r30s** — otherwise the OS will
   not boot. Each successful install pulses the LED green.
9. **Reboot.** The device boots rooted Fire OS 5 with ADB force-enabled:
   `adb devices` should list it. TWRP blocks flashing anything that would
   overwrite LK/preloader/TZ, and OTA is domain-blocked — you're off Amazon's
   update path now.

## Troubleshooting & unbrick

- **Stuck at bootrom-step** (a known report in the thread): retry with the
  pin-short method instead of `brick.sh`; check `dmesg` for the MTK USB device
  appearing/disappearing; make sure ModemManager is really disabled and you're
  using a data cable.
- **Boots to TWRP every time:** expected until step 8 is complete — TWRP *is*
  your recovery now; entering the OS normally happens once a firmware is
  installed to `boot_*_x`.
- **Already unlocked, updating amonet:** just flash the new zip in TWRP.
- **Hard brick:** follow the thread's UNBRICK section (force BROM via pin
  short, re-run `bootrom-step.sh`). The preloader tracks boot attempts — if
  both slots exhausted them, the thread explains the recovery.
- Questions → the XDA thread; include your firmware version and where the LED
  states got you.

## After unlock: making it an EchoNeura voice satellite

Reality check on what the unlocked Dot is: a **rooted Fire OS 5 (Android 5.1)
device with 256 MB RAM**, ADB, and UART. Amazon's Alexa stack still owns the
mic DSP path by default, and the hardware is far too weak for on-device ASR.
The practical integration, in order of sanity:

1. **Stream audio out, think on the server (recommended — and already built).**
   `tools/dot/satellite/` is a single **static ARMv7 musl binary** (~1.6 MB,
   vendored tinyalsa for ALSA capture + minimal RFC 6455 client) that streams
   mono S16LE PCM to `/api/voice/stream` and prints the transcript + assistant
   reply coming back. `make cross` builds it with zig (or the musl.cc gcc);
   `post-root.sh` pushes and smoke-tests it. No APK, no Termux, no dependencies
   on Android 5.1's libc. (A sideloaded APK remains the polished future option
   if you later want wake-word + foreground-service behaviour.)
   - Mic-sharing caveat: if the stock Alexa service still runs, it may hold the
     mic array. Options: disable Alexa (`pm disable-user` on the Alexa
     packages via root), or use the DSP's secondary capture path — test with
     `tinycap` first and see if you get signal while Alexa is idle.
2. **Protocol** is plain WebSocket + binary PCM frames — identical to the
   browser client at `/voice` and `tools/stream_audio_to_voice.py`. Full spec:
   [voice-protocol.md](voice-protocol.md).
3. **Wake word**: do it server-side (stream continuously, EchoNeura's VAD
   trims) or client-side with a tiny model (openWakeWord/Porcupine) inside the
   APK — 256 MB RAM is enough for the wake model alone, not for ASR.
4. **Output**: replies come back as JSON; play TTS via the Dot speaker from
   your client (Android `TextToSpeech` in the APK route), or accept
   text-only actions.
5. **Privacy note, honest version:** after this mod the mute button no longer
   guarantees anything — the earlier PoC on this device family demonstrated mic
   capture even while the ring showed muted. If you build the streaming client,
   *you* own that risk surface now. Wire the client to a physical switch or
   only stream while a button is held.

## Honest cost/benefit

- **Effort:** an evening if nothing fights you; a week if you're also learning
  adb/alsa and writing the APK. Brick risk is real but recoverable in most
  failure modes (that's what BROM shorting is for).
- **Alternative:** a Raspberry Pi Zero 2 W / Pi 3 + a USB or ReSpeaker mic
  (~S$40–90) runs the identical protocol with zero exploits, real Linux audio,
  and no 256 MB ceiling — and your unlocked Dot can still serve as a Bluetooth
  speaker. If the goal is "voice → EchoNeura → actions" rather than "rescue
  this specific puck", the Pi route ships sooner.
- **Either way the server side is already built:** everything in this repo
  treats the mic source as just another WebSocket client.
