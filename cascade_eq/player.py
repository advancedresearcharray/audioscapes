"""Play files into the Cascade EQ virtual sink (through the rack)."""

from __future__ import annotations

import math
import shutil
import subprocess
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

from . import SINK_NAME
from .paths import load_state, sessions_dir
from .pulse import sink_exists


def _ensure_gst() -> None:
    Gst.init(None)


def latest_radio_mix() -> Path | None:
    ses = load_state().get("session") or {}
    tracks = ses.get("tracks")
    if tracks:
        mix = Path(tracks) / "radio-mix.flac"
        if mix.exists():
            return mix
    found = sorted(
        sessions_dir().glob("*-tracks/radio-mix.flac"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return found[0] if found else None


def _audio_sink():
    sink = Gst.ElementFactory.make("pipewiresink", "deck-out")
    if sink is not None:
        sink.set_property("sync", True)
        sink.set_property("client-name", "Cascade EQ Cassette")
        if sink_exists(SINK_NAME):
            sink.set_property("target-object", SINK_NAME)
            try:
                sink.set_property(
                    "stream-properties",
                    Gst.Structure.new_from_string(
                        "props,"
                        f"node.name={SINK_NAME}.cassette,"
                        f"target.object={SINK_NAME},"
                        "media.class=Stream/Output/Audio"
                    ),
                )
            except TypeError:
                pass
        return sink
    sink = Gst.ElementFactory.make("pulsesink", "deck-out")
    if sink is None:
        raise RuntimeError("GStreamer pipewiresink/pulsesink is missing.")
    sink.set_property("sync", True)
    if sink_exists(SINK_NAME):
        sink.set_property("device", SINK_NAME)
    return sink


def restore_high_speed_dub(src: Path, tape_rate: float = 2.0) -> Path:
    """Turn a 2× tape-speed recording back into a 1× tape (asetrate)."""
    src = Path(src).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    dest = src.with_name(src.stem + "-dub" + src.suffix)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return src
    rate = max(1.01, min(4.0, float(tape_rate)))
    sr = 48000
    slow = max(8000, int(round(sr / rate)))
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(src),
            "-af",
            f"asetrate={slow},aresample={sr}",
            "-c:a",
            "flac",
            "-sample_fmt",
            "s32",
            "-bits_per_raw_sample",
            "24",
            str(dest),
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError(
            (proc.stderr or b"").decode("utf-8", "replace")[:300] or "high-speed dub restore failed"
        )
    return dest


def _tape_filter():
    """Cassette path: rumble filter, optional dulling, analog 2× rate."""
    bits = [
        "audioconvert ! audioresample",
        "audiocheblimit name=tape-hpf mode=high-pass cutoff=28 poles=2",
        "audiocheblimit name=tape-lpf mode=low-pass cutoff=18000 poles=4",
    ]
    if Gst.ElementFactory.find("pitch") is not None:
        bits.append("pitch name=tape-pitch tempo=1.0 pitch=1.0 rate=1.0")
    bits.append("audioconvert")
    desc = " ! ".join(bits)
    try:
        filt = Gst.parse_bin_from_description(desc, True)
    except Exception:
        return None, None, None
    return filt, filt.get_by_name("tape-pitch"), filt.get_by_name("tape-lpf")


class FilePlayer:
    """playbin → cascade_eq so the file is processed by the rack."""

    def __init__(self) -> None:
        _ensure_gst()
        self.playbin = Gst.ElementFactory.make("playbin", "cassette-deck")
        if self.playbin is None:
            raise RuntimeError("GStreamer playbin is missing.")
        self.playbin.set_property("audio-sink", _audio_sink())
        video = Gst.ElementFactory.make("fakesink", "deck-video")
        if video is not None:
            self.playbin.set_property("video-sink", video)
        try:
            self.playbin.set_property("flags", 2)  # audio only
        except TypeError:
            pass
        filt, pitch, lpf = _tape_filter()
        self._pitch = pitch
        self._lpf = lpf
        if filt is not None:
            self.playbin.set_property("audio-filter", filt)
        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus)
        self.path: Path | None = None
        self.on_eos = None
        self.on_error = None
        self._error = ""
        self.dolby = True
        self.rate = 1.0
        GLib.timeout_add(70, self._tick_wow)

    def through_eq(self) -> bool:
        return sink_exists(SINK_NAME)

    def load(self, path: Path) -> None:
        path = Path(path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        was = self.state()
        self.stop()
        self.path = path
        self._error = ""
        self.playbin.set_property("uri", path.as_uri())
        if was == "playing":
            self.play()
        else:
            self.playbin.set_state(Gst.State.PAUSED)

    def play(self) -> None:
        if self.path is None:
            raise RuntimeError("Load a tape first.")
        self._apply_tape()
        self.playbin.set_state(Gst.State.PLAYING)

    def pause(self) -> None:
        if self.state() == "playing":
            self.playbin.set_state(Gst.State.PAUSED)

    def stop(self) -> None:
        self.playbin.set_state(Gst.State.NULL)

    def set_tape(self, dolby: bool | None = None, rate: float | None = None) -> None:
        if dolby is not None:
            self.dolby = bool(dolby)
        if rate is not None:
            self.rate = max(0.5, min(4.0, float(rate)))
        self._apply_tape()

    def _apply_tape(self) -> None:
        if self._lpf is not None:
            try:
                self._lpf.set_property("cutoff", 18000.0 if self.dolby else 10800.0)
            except Exception:
                pass
        if self._pitch is not None:
            try:
                self._pitch.set_property("rate", float(self.rate))
                self._pitch.set_property("pitch", 1.0)
                self._pitch.set_property("tempo", 1.0)
            except Exception:
                pass
        elif abs(self.rate - 1.0) > 0.02 or self.state() == "playing":
            ok, pos = self.playbin.query_position(Gst.Format.TIME)
            if ok:
                self.playbin.seek(
                    float(self.rate),
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                    Gst.SeekType.SET,
                    pos,
                    Gst.SeekType.NONE,
                    -1,
                )

    def _tick_wow(self) -> bool:
        if self._pitch is None or self.dolby or abs(self.rate - 1.0) > 0.02:
            return True
        if self.state() != "playing":
            return True
        t = time.time()
        wow = 1.0 + 0.0055 * math.sin(t * 1.55) + 0.0022 * math.sin(t * 6.4)
        try:
            self._pitch.set_property("rate", wow)
        except Exception:
            pass
        return True

    def seek_delta(self, seconds: float) -> None:
        ok, pos = self.playbin.query_position(Gst.Format.TIME)
        if not ok:
            return
        dest = max(0, int(pos + seconds * Gst.SECOND))
        self.playbin.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            dest,
        )

    def state(self) -> str:
        _ok, current, _pending = self.playbin.get_state(0)
        if current == Gst.State.PLAYING:
            return "playing"
        if current == Gst.State.PAUSED:
            return "paused"
        return "stopped"

    def progress(self) -> tuple[float, float]:
        ok_p, pos = self.playbin.query_position(Gst.Format.TIME)
        ok_d, dur = self.playbin.query_duration(Gst.Format.TIME)
        pos_s = (pos / Gst.SECOND) if ok_p else 0.0
        dur_s = (dur / Gst.SECOND) if ok_d and dur > 0 else 0.0
        return pos_s, dur_s

    def _on_bus(self, _bus, message) -> None:
        t = message.type
        if t == Gst.MessageType.EOS:
            self.playbin.set_state(Gst.State.NULL)
            cb = self.on_eos
            if cb:
                GLib.idle_add(cb)
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self._error = str(err) if err else (debug or "playback failed")
            self.playbin.set_state(Gst.State.NULL)
            cb = self.on_error
            if cb:
                GLib.idle_add(cb, self._error)
