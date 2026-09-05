# Echo Dot RS03QR rooting toolkit

Everything needed to turn a 2nd-gen Echo Dot (biscuit) into an EchoNeura voice
satellite. Read `docs/device/echo-dot-rs03qr-jailbreak.md` first — this toolkit
executes that guide, it does not replace it.

## The five commands, in order

```bash
./preflight.sh                      # host + cable + ModemManager + firmware checks
./fetch-amonet.sh                   # downloads amonet-biscuit-v1.1.0.zip (+f1r30s best-effort), verifies, extracts
./root-assistant.sh ./amonet-dist/amonet   # gate-by-gate unlock with LED confirmations
./post-root.sh --host <ip-of-this-machine> # installs + smoke-tests the satellite on the Dot
adb shell /data/local/tmp/satellite --host <ip> --port 8000   # the live loop
```

Each gate in `root-assistant.sh` waits for you to confirm a physical LED state
(green = stock fastboot, rainbow = bricked/hacked fastboot, pulsating cyan =
TWRP). Quitting at any gate is safe and prints where you stand.

## satellite/ — the on-device client

One static C binary (vendored tinyalsa 1.1.1 for ALSA capture + a minimal
RFC 6455 client) that streams mono S16LE PCM to `/api/voice/stream` and prints
the transcript + assistant reply that come back. Protocol spec:
`docs/device/voice-protocol.md`.

```bash
cd satellite
make native     # ./satellite        — test on any Linux box: --file x.wav
make cross      # ./satellite-arm    — static ARMv7 musl for the Dot (zig or musl.cc gcc)
```

`make cross` prefers `zig cc -target arm-linux-musleabihf` (zig ships a full
musl cross toolchain; `pip install ziglang` if you have no system zig) and
falls back to downloading the musl.cc cross gcc. IP literals bypass
`getaddrinfo`, so static builds work with `--host 192.168.x.x` even under
static glibc. No TLS: for remote servers tunnel (`ssh -L 8000:api:8000`).

Tested in CI-fashion from this repo: native build streams
`data/samples/demo.wav` to the live backend and prints the mock assistant's
reply; the ARM build is verified as a static `EM_ARM` `ET_EXEC` ELF.

## Honest defaults

- The Dot (256 MB RAM, Android 5.1) only *captures and streams* — all
  intelligence stays server-side in EchoNeura.
- After unlocking, the hardware mute button no longer guarantees privacy; the
  satellite only streams while running, and `post-root.sh` prints how to
  disable the stock Alexa mic owner if it holds the ALSA device.
- No jailbreak needed alternative: any Linux box with a mic (Pi, laptop) runs
  the identical protocol via `tools/stream_audio_to_voice.py` or the `/voice`
  web page.
