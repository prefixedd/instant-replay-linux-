#!/usr/bin/env python3
"""
instant_replay.py — Linux instant replay / clip tool
Dependencies:  pip install pynput  &&  sudo apt install ffmpeg python3-tk
"""

import os, sys, time, signal, threading, subprocess, shutil
from datetime import datetime
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print("sudo apt install python3-tk"); sys.exit(1)

try:
    from pynput import keyboard
except ImportError:
    print("pip install pynput"); sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
SEGMENT_DURATION = 5
BUFFER_DIR = Path.home() / ".instant_replay_buffer"
CLIPS_DIR  = Path.home() / "Videos" / "InstantReplay"
DURATION_OPTIONS = [15, 30, 60, 120]
FPS_OPTIONS      = [30, 60]

# ── Audio detection ───────────────────────────────────────────────────────────
def list_all_sources():
    sources = []
    try:
        out = subprocess.check_output(["pactl", "list", "sources"],
                                      text=True, stderr=subprocess.DEVNULL)
        name = desc = state = None
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Name:"):        name  = s.split(":", 1)[1].strip()
            elif s.startswith("State:"):     state = s.split(":", 1)[1].strip()
            elif s.startswith("Description:"):
                desc = s.split(":", 1)[1].strip()
                if name and desc:
                    sources.append((name, desc, state or "UNKNOWN"))
                name = desc = state = None
    except Exception as e:
        print(f"[InstantReplay] pactl error: {e}")
    return sources

def get_monitors(all_src): return [(n,d,s) for n,d,s in all_src if "monitor" in n.lower()]
def get_mics(all_src):     return [(n,d,s) for n,d,s in all_src if "monitor" not in n.lower()]

# ── State ─────────────────────────────────────────────────────────────────────
class State:
    def __init__(self):
        self.duration        = 30
        self.fps             = 60
        self.system_audio    = True
        self.mic_audio       = True
        self.recording       = False
        self.ffmpeg_proc     = None
        self.pressed_keys    = set()
        self.lock            = threading.Lock()
        self.system_src_idx  = 0
        self.mic_src_idx     = 0

        all_src = list_all_sources()
        self.monitor_sources = get_monitors(all_src)
        self.mic_sources     = get_mics(all_src)

        print("[InstantReplay] Monitor sources:")
        for n,d,s in self.monitor_sources: print(f"  [{s}] {d}")
        print("[InstantReplay] Mic sources:")
        for n,d,s in self.mic_sources:     print(f"  [{s}] {d}")

    @property
    def system_src(self):
        return self.monitor_sources[self.system_src_idx][0] if self.monitor_sources else None
    @property
    def mic_src(self):
        return self.mic_sources[self.mic_src_idx][0] if self.mic_sources else None

state = State()

# ── Keepalive (prevents monitor suspend) ─────────────────────────────────────
_keepalive_proc = None

def start_keepalive():
    global _keepalive_proc
    stop_keepalive()
    try:
        _keepalive_proc = subprocess.Popen([
            "ffmpeg", "-loglevel", "quiet",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-f", "pulse", "default"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[InstantReplay] Keepalive started")
    except Exception as e:
        print(f"[InstantReplay] Keepalive failed: {e}")

def stop_keepalive():
    global _keepalive_proc
    if _keepalive_proc:
        try: _keepalive_proc.kill(); _keepalive_proc.wait(timeout=2)
        except: pass
        _keepalive_proc = None

# ── FFmpeg command ────────────────────────────────────────────────────────────
def build_ffmpeg_cmd(pattern: str) -> list:
    display  = os.environ.get("DISPLAY", ":0")
    fps      = state.fps
    max_segs = (max(DURATION_OPTIONS) // SEGMENT_DURATION) + 3
    use_sys  = state.system_audio and state.system_src
    use_mic  = state.mic_audio    and state.mic_src

    cmd = ["ffmpeg", "-y", "-loglevel", "warning",
           "-f", "x11grab", "-framerate", str(fps), "-i", display]

    # Add audio inputs AFTER video input
    # Input indices: 0=video, 1=sys_audio (if on), 2=mic (if both on) or 1=mic (if only mic)
    n_audio = 0
    sys_idx = mic_idx = None

    if use_sys:
        cmd += ["-f", "pulse", "-i", state.system_src]
        sys_idx = n_audio + 1   # +1 because input 0 is video
        n_audio += 1
    if use_mic:
        cmd += ["-f", "pulse", "-i", state.mic_src]
        mic_idx = n_audio + 1
        n_audio += 1

    # Video codec
    cmd += ["-c:v", "libx264", "-preset", "ultrafast",
            "-tune", "zerolatency", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(fps)]

    # Audio codec — build filter_complex only when mixing 2 sources
    if n_audio == 0:
        cmd += ["-an"]
    elif n_audio == 1:
        # Single audio source — just map it directly, no filter needed
        audio_input_idx = sys_idx if sys_idx is not None else mic_idx
        cmd += [
            "-map", "0:v",
            "-map", f"{audio_input_idx}:a",
            "-c:a", "aac", "-b:a", "128k",
        ]
    else:
        # Two sources — mix them with amix
        cmd += [
            "-filter_complex",
            f"[{sys_idx}:a][{mic_idx}:a]amix=inputs=2:duration=longest:dropout_transition=0[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:a", "aac", "-b:a", "128k",
        ]

    # Segmented output
    cmd += ["-f", "segment",
            "-segment_time", str(SEGMENT_DURATION),
            "-segment_wrap", str(max_segs),
            "-reset_timestamps", "1",
            pattern]
    return cmd

# ── Recording ─────────────────────────────────────────────────────────────────
def start_recording():
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    for f in BUFFER_DIR.glob("seg*.mkv"): f.unlink(missing_ok=True)

    # Unsuspend monitor source
    if state.system_audio and state.system_src:
        subprocess.run(["pactl", "suspend-source", state.system_src, "0"],
                       capture_output=True)
        time.sleep(0.4)

    pattern = str(BUFFER_DIR / "seg%05d.mkv")
    cmd = build_ffmpeg_cmd(pattern)
    print(f"[InstantReplay] CMD: {' '.join(cmd)}")

    with state.lock:
        state.ffmpeg_proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        state.recording = True

    def log_stderr():
        for line in state.ffmpeg_proc.stderr:
            print(f"[ffmpeg] {line.decode(errors='replace').rstrip()}")
    threading.Thread(target=log_stderr, daemon=True).start()

def stop_recording():
    with state.lock:
        if state.ffmpeg_proc and state.recording:
            try: state.ffmpeg_proc.stdin.write(b"q"); state.ffmpeg_proc.stdin.flush()
            except: pass
            try: state.ffmpeg_proc.wait(timeout=5)
            except subprocess.TimeoutExpired: state.ffmpeg_proc.kill()
            state.ffmpeg_proc = None
        state.recording = False
    print("[InstantReplay] Stopped.")

def restart_recording():
    stop_recording(); time.sleep(0.5); start_recording()

# ── Clip saving ───────────────────────────────────────────────────────────────
def save_clip():
    duration = state.duration
    out_path = CLIPS_DIR / f"clip_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp4"

    segs = sorted(BUFFER_DIR.glob("seg*.mkv"), key=lambda p: p.stat().st_mtime)
    if not segs:
        print("[InstantReplay] No segments found."); return None

    collected, total = [], 0
    for seg in reversed(segs):
        collected.insert(0, seg)
        total += SEGMENT_DURATION
        if total >= duration + SEGMENT_DURATION: break

    concat_file = BUFFER_DIR / "concat.txt"
    concat_file.write_text("".join(f"file '{s}'\n" for s in collected))

    cmd = ["ffmpeg", "-y",
           "-f", "concat", "-safe", "0", "-sseof", f"-{duration}",
           "-i", str(concat_file),
           "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-c:a", "aac", "-b:a", "128k",
           str(out_path)]

    print(f"[InstantReplay] Saving {duration}s → {out_path}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    concat_file.unlink(missing_ok=True)
    if r.returncode != 0:
        print("[InstantReplay] Save error:", r.stderr[-600:]); return None
    print(f"[InstantReplay] Saved: {out_path}")
    return str(out_path)

# ── Hotkey ────────────────────────────────────────────────────────────────────
def on_press(key):
    state.pressed_keys.add(key)
    ctrl  = keyboard.Key.ctrl_l in state.pressed_keys or keyboard.Key.ctrl_r in state.pressed_keys
    shift = keyboard.Key.shift  in state.pressed_keys
    s     = keyboard.KeyCode.from_char('s') in state.pressed_keys
    if ctrl and shift and s:
        threading.Thread(target=save_clip, daemon=True).start()

def on_release(key): state.pressed_keys.discard(key)

def start_hotkey_listener():
    l = keyboard.Listener(on_press=on_press, on_release=on_release)
    l.daemon = True; l.start()

# ── Audio row widget ──────────────────────────────────────────────────────────
class AudioRow(tk.Frame):
    def __init__(self, parent, icon, label, sources, enabled_var,
                 on_toggle, on_device_change, **kwargs):
        super().__init__(parent, **kwargs)
        self.sources = sources
        self.on_toggle = on_toggle
        self.on_device_change = on_device_change
        self._idx = 0

        tk.Checkbutton(self, variable=enabled_var, command=on_toggle).pack(side="left")
        tk.Label(self, text=f"{icon} {label}", font=("Sans", 10),
                 width=14, anchor="w").pack(side="left")

        if not sources:
            tk.Label(self, text="(no device found)", fg="gray",
                     font=("Sans", 9)).pack(side="left", padx=4)
            return

        self.btn_prev = tk.Button(self, text="◀", width=2, relief="flat",
                                  font=("Sans", 9), command=self._prev)
        self.btn_prev.pack(side="left")

        self.dev_lbl = tk.Label(self, font=("Sans", 9), fg="#222",
                                width=32, anchor="w", relief="sunken",
                                bg="white", padx=4)
        self.dev_lbl.pack(side="left", padx=2)

        self.btn_next = tk.Button(self, text="▶", width=2, relief="flat",
                                  font=("Sans", 9), command=self._next)
        self.btn_next.pack(side="left")

        self.cnt_lbl = tk.Label(self, font=("Mono", 8), fg="gray", width=5)
        self.cnt_lbl.pack(side="left", padx=(4, 0))
        self._refresh()

    def _refresh(self):
        if not self.sources: return
        _, desc, src_state = self.sources[self._idx]
        short = desc[:34] if len(desc) <= 34 else desc[:32] + "…"
        dot = "🟢" if src_state == "RUNNING" else "🟡"
        self.dev_lbl.config(text=f"{dot} {short}")
        self.cnt_lbl.config(text=f"{self._idx+1}/{len(self.sources)}")
        self.btn_prev.config(state="normal" if self._idx > 0 else "disabled")
        self.btn_next.config(state="normal" if self._idx < len(self.sources)-1 else "disabled")

    def _prev(self):
        if self._idx > 0:
            self._idx -= 1; self._refresh(); self.on_device_change(self._idx)
    def _next(self):
        if self._idx < len(self.sources)-1:
            self._idx += 1; self._refresh(); self.on_device_change(self._idx)

# ── GUI ───────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Instant Replay")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self._blink = False
        self._tick()

    def _build_ui(self):
        P = dict(padx=14, pady=5)

        tk.Label(self, text="🎮 Instant Replay", font=("Sans", 14, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(14, 4))

        # Duration
        tk.Label(self, text="Clip Duration").grid(row=1, column=0, sticky="w", **P)
        self.dur_var = tk.IntVar(value=state.duration)
        f = tk.Frame(self); f.grid(row=1, column=1, sticky="w", **P)
        for s in DURATION_OPTIONS:
            tk.Radiobutton(f, text=f"{s}s" if s<60 else f"{s//60}min",
                           variable=self.dur_var, value=s,
                           command=lambda: setattr(state, 'duration', self.dur_var.get())
                           ).pack(side="left")

        # FPS
        tk.Label(self, text="FPS").grid(row=2, column=0, sticky="w", **P)
        self.fps_var = tk.IntVar(value=state.fps)
        f = tk.Frame(self); f.grid(row=2, column=1, sticky="w", **P)
        for fps in FPS_OPTIONS:
            tk.Radiobutton(f, text=f"{fps} fps", variable=self.fps_var, value=fps,
                           command=self._on_fps).pack(side="left")

        ttk.Separator(self, orient="horizontal").grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=6)

        tk.Label(self, text="Audio", font=("Sans", 10, "bold")).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=14, pady=(2,4))

        self.sys_var = tk.BooleanVar(value=state.system_audio)
        AudioRow(self, "🔊", "System Audio", state.monitor_sources,
                 self.sys_var, self._on_sys_toggle, self._on_sys_dev
                 ).grid(row=5, column=0, columnspan=2, sticky="w", padx=14, pady=3)

        self.mic_var = tk.BooleanVar(value=state.mic_audio)
        AudioRow(self, "🎙", "Microphone", state.mic_sources,
                 self.mic_var, self._on_mic_toggle, self._on_mic_dev
                 ).grid(row=6, column=0, columnspan=2, sticky="w", padx=14, pady=3)

        ttk.Separator(self, orient="horizontal").grid(
            row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=6)

        self.status_lbl = tk.Label(self, text="Initialising…", fg="gray", font=("Mono", 10))
        self.status_lbl.grid(row=8, column=0, columnspan=2, pady=4)

        bf = tk.Frame(self); bf.grid(row=9, column=0, columnspan=2, pady=(4,6))
        self.rec_btn = tk.Button(bf, text="⏹ Stop Recording",
                                 width=18, command=self._toggle_rec)
        self.rec_btn.pack(side="left", padx=6)
        tk.Button(bf, text="💾 Save Clip Now", width=18,
                  command=self._save_now).pack(side="left", padx=6)

        tk.Label(self, text="Hotkey: Ctrl+Shift+S  |  Clips → ~/Videos/InstantReplay",
                 fg="gray", font=("Sans", 8)).grid(
            row=10, column=0, columnspan=2, pady=(0,12))

    def _on_sys_toggle(self):
        state.system_audio = self.sys_var.get()
        if state.recording: threading.Thread(target=restart_recording, daemon=True).start()

    def _on_sys_dev(self, idx):
        state.system_src_idx = idx
        if state.recording and state.system_audio:
            threading.Thread(target=restart_recording, daemon=True).start()

    def _on_mic_toggle(self):
        state.mic_audio = self.mic_var.get()
        if state.recording: threading.Thread(target=restart_recording, daemon=True).start()

    def _on_mic_dev(self, idx):
        state.mic_src_idx = idx
        if state.recording and state.mic_audio:
            threading.Thread(target=restart_recording, daemon=True).start()

    def _on_fps(self):
        new = self.fps_var.get()
        if new != state.fps:
            state.fps = new
            threading.Thread(target=restart_recording, daemon=True).start()

    def _toggle_rec(self):
        if state.recording:
            stop_recording(); self.rec_btn.config(text="▶ Start Recording")
        else:
            threading.Thread(target=start_recording, daemon=True).start()
            self.rec_btn.config(text="⏹ Stop Recording")

    def _save_now(self):
        if not state.recording:
            messagebox.showwarning("Not recording", "Start recording first."); return
        threading.Thread(target=self._do_save, daemon=True).start()

    def _do_save(self):
        path = save_clip()
        if path:
            # Open in ffplay automatically — detached so it doesn't block
            subprocess.Popen(["ffplay", "-autoexit", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.after(0, lambda: messagebox.showerror("Error",
                "Save failed — check terminal for details."))

    def _tick(self):
        if state.recording:
            self._blink = not self._blink
            dot = "🔴" if self._blink else "⭕"
            parts = (["sys"] if state.system_audio and state.system_src else []) + \
                    (["mic"] if state.mic_audio    and state.mic_src    else [])
            audio = "+".join(parts) or "muted"
            self.status_lbl.config(
                text=f"{dot} Recording  {state.fps}fps  🔉{audio}  buf:{state.duration}s",
                fg="#cc2200")
        else:
            self.status_lbl.config(text="⏸ Paused", fg="gray")
        self.after(800, self._tick)

    def on_close(self):
        stop_recording(); stop_keepalive(); self.destroy()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found:  sudo apt install ffmpeg"); sys.exit(1)

    signal.signal(signal.SIGINT, lambda *_: (stop_recording(), stop_keepalive(), sys.exit(0)))
    start_hotkey_listener()
    start_keepalive()
    time.sleep(1.2)   # let keepalive wake up monitor sources
    threading.Thread(target=start_recording, daemon=True).start()
    App().mainloop()

if __name__ == "__main__":
    main()
