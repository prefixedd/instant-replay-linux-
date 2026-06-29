# 🎮 Instant Replay — Linux Screen Recorder

A lightweight ShadowPlay-style instant replay tool for Linux. Continuously records your screen and audio into a rolling buffer, letting you save the last N seconds as a clip anytime with a hotkey.

---

## Features

- 🔁 **Rolling buffer** — always recording in the background, never misses a moment
- ⌨️ **Global hotkey** — `Ctrl+Shift+S` saves a clip from anywhere, even mid-game
- 🎬 **Clip durations** — 15s, 30s, 1 min, or 2 min
- 🎞️ **FPS options** — 30 or 60 fps
- 🔊 **System audio** — captures desktop/speaker audio via PipeWire/PulseAudio monitor source
- 🎙️ **Microphone** — captures mic input separately and mixes it with system audio
- 🎚️ **Device picker** — detects all audio devices and lets you switch with ◀ ▶ arrows
- 🟢🟡 **Device status** — shows RUNNING/SUSPENDED state for each audio source
- 🔇 **Per-source toggle** — enable/disable system audio and mic independently
- ▶️ **Auto-playback** — clips open in `ffplay` automatically after saving

---

## Requirements

### System packages
```bash
sudo apt install ffmpeg python3-tk
```

### Python packages
```bash
pip install pynput
```

> **Note:** Requires X11. Wayland is not currently supported (x11grab and pynput global hotkeys need X11).

---

## Installation

```bash
# Clone or download the script
git clone https://github.com/yourname/instant-replay
cd instant-replay

# Install dependencies
sudo apt install ffmpeg python3-tk
pip install pynput

# Run
python3 instant_replay.py
```

---

## Usage

### Running
```bash
python3 instant_replay.py
```

The GUI opens and recording starts automatically.

### Saving a clip

| Method | Action |
|--------|--------|
| `Ctrl+Shift+S` | Save clip instantly from anywhere |
| 💾 Save Clip Now button | Save from the GUI |

Clips are saved to `~/Videos/InstantReplay/` and open automatically in `ffplay`.

### GUI controls

| Control | Description |
|---------|-------------|
| Clip Duration | Choose 15s / 30s / 1min / 2min |
| FPS | 30 or 60 fps (restarts recording when changed) |
| 🔊 System Audio | Toggle desktop audio on/off, pick device with ◀ ▶ |
| 🎙 Microphone | Toggle mic on/off, pick device with ◀ ▶ |
| ⏹ Stop Recording | Pause the buffer |
| 💾 Save Clip Now | Save the last N seconds immediately |

---

## File Structure

```
instant-replay/
├── instant_replay.py       # Main script
├── README.md               # This file
~/.instant_replay_buffer/   # Temporary ring buffer segments (auto-managed)
~/Videos/InstantReplay/     # Saved clips output folder
```

---

## How It Works

```
┌─────────────────────────────────────────────────┐
│                  ffmpeg (always running)         │
│                                                  │
│  Screen (x11grab) ──┐                            │
│  System Audio ──────┼──► mix ──► seg00001.mkv   │
│  Microphone ────────┘           seg00002.mkv    │
│                                 seg00003.mkv    │
│                             (ring buffer, 5s    │
│                              chunks, auto-wrap) │
└─────────────────────────────────────────────────┘
                          │
                   Ctrl+Shift+S
                          │
                          ▼
         concat last N seconds → clip_YYYY-MM-DD_HH-MM-SS.mp4
                          │
                          ▼
                    ffplay opens it
```

- ffmpeg runs continuously, writing 5-second `.mkv` segments into `~/.instant_replay_buffer/`
- Old segments are overwritten automatically (ring buffer via `-segment_wrap`)
- On save: the last N seconds of segments are concatenated and trimmed with `-sseof`
- A silent keepalive stream prevents PipeWire monitor sources from suspending

---

## Troubleshooting

**No audio in saved clips**
```bash
# Check if audio is actually in the file
ffmpeg -i ~/Videos/InstantReplay/clip_*.mp4 -af volumedetect -f null /dev/null 2>&1 | grep volume
# Play with ffplay to confirm
ffplay ~/Videos/InstantReplay/clip_*.mp4
```

**Monitor source is suspended / no system audio**

This is handled automatically by the keepalive stream on startup. If it still happens:
```bash
pactl suspend-source <source-name> 0
```

**Recording doesn't start**
```bash
# Check ffmpeg is installed
ffmpeg -version
# Check your display variable
echo $DISPLAY   # should output :0 or similar
```

**Hotkey not working**

Make sure you're on X11, not Wayland:
```bash
echo $XDG_SESSION_TYPE   # should say x11
```

---

## Known Limitations

- X11 only (no Wayland support)
- Captures the full screen (no window/monitor selection yet)
- Buffer clears on restart — can't save a clip from before the app was opened
