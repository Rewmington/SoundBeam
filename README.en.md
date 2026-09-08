# SoundBeam

Stream audio currently playing on your phone / tablet to your PC in real time over your **local network** — an audio "screen cast". Think AirPlay's audio-return scenario, but over a custom LAN protocol, Android → Windows.

| Phone | Desktop | Transport | Latency |
|-------|---------|-----------|---------|
| Android 10+, captures audio from any app currently playing system sound (music, video, games) | Windows, Python + tkinter GUI, plays to a chosen audio output device | UDP + raw PCM (48 kHz / 16-bit / stereo, ≈ 1.5 Mbps) | ≈ 100–200 ms (incl. an 80 ms jitter buffer) |

## Architecture

```
┌─────────────┐  MediaProjection   ┌──────────────┐   UDP/PCM   ┌──────────────────────┐
│ Android App │  AudioPlaybackCapture │  Capture+Send │ ──────────▶ │ Desktop receiver     │
│ (Kotlin)    │  (PCM 48k/16bit/stereo) │               │             │ + jitter buffer (80ms)│ → Speakers
└─────────────┘   default port 50051   └──────────────┘             │ → sounddevice output │
                                                                   └──────────────────────┘
```

## Features

- **Automatic discovery of PCs**: one tap on the phone lists every connectable PC on the LAN. When "allow discovery" is enabled, the desktop periodically announces itself over **three redundant channels — multicast, limited broadcast, and QUERY/response** (robust across different routers / AP multicast policies); the phone answers instantly when scanning. Both sides stop scanning automatically once connected.
- **Manual connection**: if discovery fails (e.g. the router has AP isolation enabled), enter the PC's IP and port manually.
- **Mute the phone** (Android 12+): an in-app toggle keeps capturing while suppressing playback on the phone itself.
- **Hot-switch output device**: switch between speakers / headphones on the fly while playing, without stopping the stream or losing the volume level.
- **Live stats**: packet rate, packet-loss rate, bitrate (kbps), and audio level (dB); WASAPI low-latency output.
- **System tray residency**: closing the window docks it to the tray and keeps receiving in the background.
- **One-click packaging**: produces a single-file `SoundBeam.exe` (~66 MB) that runs without Python installed.

### No-App mode (the phone needs nothing installed)

Alternative way to get phone audio to your PC speakers:

- **Bluetooth receiver**: native A2DP sink on Windows 10 1903+ (`desktop/bt_sink.py` + `bthelper.exe`). Pair the phone with the PC in Windows Bluetooth settings, then connect from the app with one click — phone audio plays straight from the PC speakers.

## Directory Layout

```
desktop/          Desktop side (Python 3.10+)
  ├─ gui.py           Main UI (ttkbootstrap + system tray)
  ├─ receiver.py      Receiver + jitter buffer (pad silence on loss)
  ├─ player.py        Player (sounddevice)
  ├─ discovery.py     LAN device discovery (multicast / broadcast / response)
  ├─ protocol.py      Audio stream protocol packing / parsing
  ├─ bt_sink.py       Bluetooth sink (A2DP)
  ├─ bt/              C# source for the Bluetooth helper (builds bthelper.exe)
  ├─ mock_phone.py    Simulated phone (test the link with no phone)
  ├─ selftest.py      End-to-end self test
  └─ test_protocol.py Protocol unit tests
android/          Phone side (Kotlin + Gradle; only AndroidX / Material official libs, no network / third-party SDKs)
docs/protocol.md  Transport protocol spec (audio stream v1 + discovery v1)
dist/             Packaged output (SoundBeam.exe)
build_exe.bat     One-click desktop packaging script
```

## Desktop (PC) Usage

### Requirements

- Windows 10 / 11 + Python 3.10+ (3.14 verified)
- Install dependencies: `pip install -r desktop/requirements.txt` (sounddevice, numpy, ttkbootstrap, pystray)

### Run

```bash
python -m desktop
```

UI notes:

- The **PC's IP** is shown at the top — the phone needs this value.
- Listens on UDP 50051 by default; the port is configurable.
- Pick the audio output device and adjust the volume.
- Click **Start Receiving** and wait for the phone to connect.
- "Allow discovery" is on by default and turns off automatically once a phone connects.

### Package as a single-file exe (no Python needed)

Run `build_exe.bat` from the project root. The result is `dist\SoundBeam.exe` (~66 MB) — copy it to any Windows PC and run it directly.

### Test without a phone (optional)

```bash
python -m desktop.selftest                                    # end-to-end self-check (protocol tests + simulated stream + jitter-buffer stats)
python -m desktop.mock_phone --ip 127.0.0.1 --duration 5      # simulate a phone sending a 5 s sine wave
```

## Phone (Android) Usage

1. Build the APK (see "Build" below), or install the prebuilt `app/build/outputs/apk/debug/app-debug.apk`.
2. Open the app.
3. **Auto discovery**: tap **Scan for PCs** (the PC must have "allow discovery" enabled and be receiving), pick the target from the list — IP and port fill in automatically. If nothing shows up, enter the PC's IP and port manually.
4. Optionally enable **Mute phone** (Android 12+, keeps capturing while silencing the phone).
5. Tap **Start streaming** → the system shows a **screen-capture consent** dialog → allow it.
6. Play any audio on the phone and the PC outputs it; both sides stop scanning once connected.

> iOS note: this release supports Android only. iOS does not let third-party apps capture global audio, so real-time relaying isn't possible there.

### Build

Requires Android SDK (platform 35 / build-tools) + JDK 17:

```bash
cd android
./gradlew assembleDebug     # output: app/build/outputs/apk/debug/app-debug.apk
```

## Notes & Limitations

- **Audio capture requires Android 10+**; on Android 14+ the system re-prompts for authorization every time you tap **Start streaming** (an OS restriction).
- Apps that set `FLAG_SECURE` or forbid capture (`ALLOW_CAPTURE_BY_NONE`) can't have their audio captured.
- The stream is real-time **unencrypted PCM**, intended for **trusted LANs only**; it is unavailable across subnets / the internet and not recommended.
- On packet loss the receiver pads silence — expect occasional glitches on a marginal link; if WiFi is weak, lower the volume or switch channels.
- The Bluetooth helper `bthelper.exe` is bundled in packaged releases; source builds need to compile `desktop/bt` first and place the result at `desktop/bthelper.exe`.

## Roadmap (v2 candidates)

- **Opus compression** to lower the bitrate on weak networks
- UI / stability polish and a proper release build

## License

This project is released under the **Apache License 2.0**. See [LICENSE](LICENSE).
