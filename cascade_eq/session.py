"""Split a recorded session on silence and radio-mix the tracks."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np

NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
@dataclass(frozen=True)
class MixTiming:
    count: int
    half: int
    bpm_ramp: int
    timing_walk: int

    @property
    def blend_counts(self) -> int:
        return self.count + self.half

    @property
    def tempo_counts(self) -> int:
        return self.bpm_ramp + self.timing_walk

    @property
    def tempo_bars(self) -> int:
        return max(1, self.tempo_counts // 4)


MIX_STYLES: dict[str, MixTiming] = {
    "house": MixTiming(count=32, half=16, bpm_ramp=64, timing_walk=64),
    "pop": MixTiming(count=8, half=0, bpm_ramp=16, timing_walk=8),
}

MIX_PRESETS: dict[str, dict] = {
    "POP": {
        "mix_style": "pop",
        "target_db": -12.0,
        "max_db": -1.0,
        "max_bpm_delta": 12.0,
        "harmonic_order": True,
        "max_energy_step": 2.0,
        "denoise": True,
    },
    "HOUSE": {
        "mix_style": "house",
        "target_db": -12.0,
        "max_db": -1.0,
        "max_bpm_delta": 4.0,
        "harmonic_order": True,
        "max_energy_step": 2.0,
        "denoise": True,
    },
    "RADIO": {
        "mix_style": "pop",
        "target_db": -10.0,
        "max_db": -0.3,
        "max_bpm_delta": 12.0,
        "harmonic_order": True,
        "max_energy_step": 3.0,
        "denoise": True,
    },
    "NIGHT": {
        "mix_style": "pop",
        "target_db": -16.0,
        "max_db": -3.0,
        "max_bpm_delta": 12.0,
        "harmonic_order": True,
        "max_energy_step": 2.0,
        "denoise": True,
    },
    "CLUB": {
        "mix_style": "house",
        "target_db": -10.0,
        "max_db": -0.3,
        "max_bpm_delta": 4.0,
        "harmonic_order": True,
        "max_energy_step": 2.0,
        "denoise": True,
    },
    "MIXTAPE": {
        "mix_style": "pop",
        "target_db": -12.0,
        "max_db": -1.0,
        "max_bpm_delta": 18.0,
        "harmonic_order": True,
        "max_energy_step": 4.0,
        "denoise": True,
    },
    "QUIET": {
        "mix_style": "pop",
        "target_db": -18.0,
        "max_db": -4.0,
        "max_bpm_delta": 12.0,
        "harmonic_order": True,
        "max_energy_step": 2.0,
        "denoise": True,
    },
    "LIVE": {
        "mix_style": "pop",
        "target_db": -12.0,
        "max_db": -1.0,
        "max_bpm_delta": 16.0,
        "harmonic_order": False,
        "max_energy_step": 4.0,
        "denoise": False,
    },
}


def mix_preset_names() -> list[str]:
    return list(MIX_PRESETS)


def mix_preset_mapping(name: str) -> dict:
    key = str(name or "POP").strip().upper()
    return dict(MIX_PRESETS.get(key, MIX_PRESETS["POP"]))


def mix_timing(style: str) -> MixTiming:
    key = str(style or "pop").strip().lower()
    return MIX_STYLES.get(key, MIX_STYLES["pop"])


SR = 48000
PCM_CODEC = "pcm_f32le"
ECHO_COUNTS = 8
ECHO_DECAY = 0.72
ECHO_WET = 0.85
CUT_FADE_SEC = 0.02
BUMP_MAX_GAP = 12.0


def _pcm_args() -> list[str]:
    return ["-ar", str(SR), "-ac", "2", "-c:a", PCM_CODEC]


def _flac24_args() -> list[str]:
    return ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"]


@dataclass
class MixSettings:
    target_db: float = -12.0
    max_db: float = -1.0
    max_bpm_delta: float = 4.0
    match_key: bool = True
    harmonic_order: bool = True
    max_energy_step: float = 2.0
    style: str = "pop"
    denoise: bool = True

    def timing(self) -> MixTiming:
        return mix_timing(self.style)

    @classmethod
    def from_mapping(cls, data: dict | None) -> MixSettings:
        data = dict(data or {})
        preset = str(data.get("preset") or "").strip().upper()
        if preset in MIX_PRESETS:
            data = {**mix_preset_mapping(preset), **data}
        if "target_db" in data:
            target = float(data["target_db"])
            ceiling = float(data.get("max_db", -1.0))
        else:
            target = -12.0
            ceiling = -1.0
        style = str(data.get("mix_style") or data.get("style") or "pop").strip().lower()
        if style not in MIX_STYLES:
            style = "pop"
        return cls(
            target_db=target,
            max_db=ceiling,
            max_bpm_delta=float(data.get("max_bpm_delta", 4.0)),
            match_key=bool(data.get("match_key", True)),
            harmonic_order=bool(data.get("harmonic_order", True)),
            max_energy_step=float(data.get("max_energy_step", 2.0)),
            style=style,
            denoise=bool(data.get("denoise", True)),
        )


@dataclass
class TrackInfo:
    index: int
    path: str
    start: float
    end: float
    duration: float
    bpm: float
    key: str
    root: int
    minor: bool
    peak_db: float = -6.0
    low_db: float = -8.0
    high_db: float = -8.0
    beat_phase: float = 0.0
    camelot: str = ""
    energy: int = 5
    energy_raw: float = 0.5

    @classmethod
    def from_row(cls, row: dict) -> TrackInfo:
        allowed = {item.name for item in fields(cls)}
        data = {key: value for key, value in row.items() if key in allowed}
        if "energy" not in row:
            data["energy"] = 0
        return cls(**data)


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is required for session split/mix. Install ffmpeg.")
    return exe


def _ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe is required for session split/mix. Install ffmpeg.")
    return exe


def _run(cmd: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin, capture_output=True, check=False)


def media_duration(path: Path) -> float:
    proc = _run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or "ffprobe failed")
    try:
        return float(proc.stdout.decode().strip())
    except ValueError as exc:
        raise RuntimeError(f"Could not read duration of {path}") from exc


def detect_silence(path: Path, noise_db: float = -38.0, min_silence: float = 1.6) -> list[tuple[float, float]]:
    """Return (start, end) silence intervals in seconds."""
    proc = _run(
        [
            _ffmpeg(),
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_silence}",
            "-f",
            "null",
            "-",
        ]
    )
    text = proc.stderr.decode("utf-8", "replace")
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", text)]
    out: list[tuple[float, float]] = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else media_duration(path)
        if end > start:
            out.append((start, end))
    return out


def silence_to_regions(duration: float, silences: list[tuple[float, float]], min_track: float = 8.0) -> list[tuple[float, float]]:
    cuts = [(0.0, duration)]
    occupied: list[tuple[float, float]] = []
    cursor = 0.0
    for s0, s1 in silences:
        s0 = max(0.0, min(duration, s0))
        s1 = max(0.0, min(duration, s1))
        if s0 > cursor + 0.05:
            occupied.append((cursor, s0))
        cursor = max(cursor, s1)
    if cursor < duration - 0.05:
        occupied.append((cursor, duration))
    tracks = [(a, b) for a, b in occupied if (b - a) >= min_track]
    return tracks or cuts


def extract_segment(src: Path, dest: Path, start: float, end: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(
        [
            _ffmpeg(),
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(src),
            "-c:a",
            "flac",
            str(dest),
        ]
    )
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or f"extract failed → {dest}")


def decode_audio(path: Path, sr: int = 22050, max_seconds: float | None = 90.0) -> tuple[np.ndarray, int]:
    cmd = [
        _ffmpeg(),
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "1",
        "-ar",
        str(sr),
    ]
    if max_seconds:
        cmd.extend(["-t", str(max_seconds)])
    cmd.append("pipe:1")
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or f"decode failed {path}")
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    return np.copy(audio), sr


def decode_stereo(path: Path, sr: int = 48000) -> tuple[np.ndarray, int]:
    proc = _run(
        [
            _ffmpeg(),
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            "2",
            "-ar",
            str(sr),
            "pipe:1",
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or f"decode failed {path}")
    raw = np.frombuffer(proc.stdout, dtype=np.float32)
    if raw.size % 2:
        raw = raw[: raw.size - 1]
    return raw.reshape(-1, 2).copy(), sr


def write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    stereo = audio if audio.ndim == 2 else np.column_stack([audio, audio])
    proc = subprocess.run(
        [
            _ffmpeg(),
            "-y",
            "-f",
            "f32le",
            "-ar",
            str(sr),
            "-ac",
            "2",
            "-i",
            "pipe:0",
            "-c:a",
            PCM_CODEC,
            str(path),
        ],
        input=np.ascontiguousarray(stereo, dtype=np.float32).tobytes(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not path.exists():
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or f"write failed {path}")


def estimate_bpm(mono: np.ndarray, sr: int) -> float:
    if mono.size < sr:
        return 120.0
    hop = 256
    rms = np.sqrt(np.mean(np.square(mono[: len(mono) - len(mono) % hop].reshape(-1, hop)), axis=1))
    env = np.maximum(0.0, np.diff(rms, prepend=rms[:1]))
    env -= env.mean()
    if env.std() < 1e-9:
        return 120.0
    env /= env.std()
    env_sr = sr / hop
    min_lag = int(env_sr * 60.0 / 180.0)
    max_lag = int(env_sr * 60.0 / 70.0)
    max_lag = min(max_lag, len(env) - 2)
    if max_lag <= min_lag + 2:
        return 120.0
    corr = np.correlate(env, env, mode="full")[len(env) - 1 :]
    window = corr[min_lag:max_lag]
    lag = int(np.argmax(window)) + min_lag
    bpm = 60.0 * env_sr / max(lag, 1)
    while bpm < 70:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    return float(np.clip(bpm, 70.0, 180.0))


def beat_phase(mono: np.ndarray, sr: int, bpm: float) -> float:
    period = 60.0 / max(bpm, 1.0)
    hop = 256
    rms = np.sqrt(np.mean(np.square(mono[: len(mono) - len(mono) % hop].reshape(-1, hop)), axis=1))
    env_sr = sr / hop
    n = min(len(rms), int(env_sr * 40))
    t = np.arange(n) / env_sr
    best_off, best = 0.0, -1.0
    for off in np.linspace(0.0, period, 24, endpoint=False):
        beats = np.cos(2 * math.pi * (t - off) / period)
        score = float(np.dot(rms[:n] - rms[:n].mean(), beats))
        if score > best:
            best, best_off = score, float(off)
    return best_off


def estimate_key(mono: np.ndarray, sr: int) -> tuple[str, int, bool]:
    n_fft = 4096
    hop = 2048
    if mono.size < n_fft:
        return "C", 0, False
    window = np.hanning(n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    chroma = np.zeros(12, dtype=np.float64)
    midi = 69 + 12 * np.log2(np.maximum(freqs, 1.0) / 440.0)
    pc = np.mod(np.round(midi), 12).astype(int)
    valid = (freqs > 50) & (freqs < 5000)
    frames = 1 + (len(mono) - n_fft) // hop
    step = max(1, frames // 80)
    for i in range(0, frames, step):
        frame = mono[i * hop : i * hop + n_fft] * window
        mag = np.abs(np.fft.rfft(frame))
        for k in range(12):
            chroma[k] += float(mag[valid & (pc == k)].sum())
    if chroma.sum() <= 0:
        return "C", 0, False
    chroma /= chroma.max()
    best = -1e9
    root, minor = 0, False
    for shift in range(12):
        rolled = np.roll(chroma, -shift)
        maj = float(np.dot(rolled, MAJOR_PROFILE))
        minh = float(np.dot(rolled, MINOR_PROFILE))
        if maj > best:
            best, root, minor = maj, shift, False
        if minh > best:
            best, root, minor = minh, shift, True
    name = NOTES[root] + ("m" if minor else "")
    return name, root, minor


def _amp_db(value: float) -> float:
    return 20.0 * math.log10(max(float(value), 1e-9))


def _band_rms_db(mono: np.ndarray, sr: int, f0: float, f1: float) -> float:
    n = int(mono.size)
    if n < 64:
        return -80.0
    spec = np.fft.rfft(mono)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    mask = (freqs >= f0) & (freqs < f1)
    if not np.any(mask):
        return -80.0
    filt = np.zeros_like(spec)
    filt[mask] = spec[mask]
    band = np.fft.irfft(filt, n=n)
    return _amp_db(float(np.sqrt(np.mean(np.square(band)))))


def analyze_levels(mono: np.ndarray, sr: int) -> tuple[float, float, float]:
    peak = _amp_db(float(np.max(np.abs(mono))) if mono.size else 1e-9)
    low = _band_rms_db(mono, sr, 40.0, 200.0)
    high = _band_rms_db(mono, sr, 5000.0, 12000.0)
    return peak, low, high


def measure_peak_db(path: Path) -> float:
    """True-file peak in dBFS via ffmpeg volumedetect (stereo, full length)."""
    proc = _run(
        [
            _ffmpeg(),
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
    )
    text = proc.stderr.decode("utf-8", "replace")
    match = re.search(r"max_volume:\s*([-\d.]+)\s*dB", text)
    if not match:
        return -6.0
    return float(match.group(1))


def measure_median_db(path: Path, sr: int = 48000, frame_ms: float = 50.0) -> float:
    """Median stereo RMS of short frames in dBFS, ignoring near-silence."""
    proc = _run(
        [
            _ffmpeg(),
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            "2",
            "-ar",
            str(sr),
            "pipe:1",
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or f"median measure failed {path}")
    raw = np.frombuffer(proc.stdout, dtype=np.float32)
    if raw.size % 2:
        raw = raw[: raw.size - 1]
    stereo = raw.reshape(-1, 2)
    hop = max(64, int(sr * frame_ms / 1000.0))
    if len(stereo) < hop:
        rms = float(np.sqrt(np.mean(np.square(stereo)))) if stereo.size else 1e-9
        return _amp_db(rms)
    usable = stereo[: len(stereo) - len(stereo) % hop]
    frames = usable.reshape(-1, hop, 2)
    rms = np.sqrt(np.mean(np.square(frames), axis=(1, 2)))
    db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    voiced = db[db > -50.0]
    if voiced.size == 0:
        voiced = db
    return float(np.median(voiced))


def camelot_code(root: int, minor: bool) -> tuple[str, int, str]:
    """Mixed In Key / Camelot wheel: 1A–12A (minor), 1B–12B (major)."""
    maj_root = (int(root) + 3) % 12 if minor else int(root) % 12
    number = (maj_root * 7 + 8) % 12
    if number == 0:
        number = 12
    letter = "A" if minor else "B"
    return f"{number}{letter}", number, letter


def camelot_distance(a: TrackInfo, b: TrackInfo) -> int:
    """0 = same key, 1 = Mixed In Key compatible, 2 = diagonal, 3+ = farther."""
    an, al = _camelot_parts(a)
    bn, bl = _camelot_parts(b)
    dn = min((an - bn) % 12, (bn - an) % 12)
    if an == bn and al == bl:
        return 0
    if an == bn:  # relative major/minor
        return 1
    if al == bl and dn == 1:  # ±1 on the wheel
        return 1
    if dn == 1:  # diagonal 8A→9B / 7B
        return 2
    if al == bl and dn == 2:
        return 2
    return 3 + dn


def _camelot_parts(track: TrackInfo) -> tuple[int, str]:
    code = track.camelot or camelot_code(track.root, track.minor)[0]
    number = int(re.sub(r"[^0-9]", "", code) or "8")
    letter = "A" if str(code).upper().endswith("A") else "B"
    return number, letter


def estimate_energy(mono: np.ndarray, sr: int) -> tuple[int, float]:
    """Waveform energy 1–10 in the Mixed In Key style (body loudness + drive + brightness)."""
    if mono.size < sr // 2:
        return 5, 0.5
    # Skip a typical DJ intro so the rating is the body, not the fade-in.
    skip = min(int(18 * sr), max(0, mono.size // 5))
    body = mono[skip:] if mono.size - skip > sr else mono
    hop = 512
    usable = body[: len(body) - len(body) % hop]
    if usable.size < hop:
        usable = body
        rms_f = np.array([float(np.sqrt(np.mean(np.square(usable))))], dtype=np.float64)
    else:
        frames = usable.reshape(-1, hop)
        rms_f = np.sqrt(np.mean(np.square(frames), axis=1))
    # Drop quiet breaks so energy follows the driving part of the waveform.
    floor = float(np.percentile(rms_f, 40)) if rms_f.size > 8 else 0.0
    driving = rms_f[rms_f >= floor] if rms_f.size else rms_f
    if driving.size == 0:
        driving = rms_f
    rms = float(np.sqrt(np.mean(np.square(driving))))
    rms_db = _amp_db(rms)
    flux = float(np.mean(np.maximum(0.0, np.diff(driving, prepend=driving[:1]))))
    n_fft = 2048
    window = np.hanning(n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    cents = []
    step = max(hop * 8, max(1, (len(body) - n_fft) // 32))
    for i in range(skip, max(skip + 1, len(mono) - n_fft), step):
        mag = np.abs(np.fft.rfft(mono[i : i + n_fft] * window))
        power = mag * mag
        den = float(power.sum()) + 1e-12
        cents.append(float(np.dot(freqs, power) / den))
    centroid = float(np.median(cents)) if cents else 1500.0
    loud = float(np.clip((rms_db + 34.0) / 26.0, 0.0, 1.0))
    drive = float(np.clip(flux * 55.0, 0.0, 1.0))
    bright = float(np.clip((centroid - 800.0) / 3200.0, 0.0, 1.0))
    raw = 0.52 * loud + 0.33 * drive + 0.15 * bright
    rating = int(np.clip(round(1.0 + raw * 9.0), 1, 10))
    return rating, round(raw, 3)


def analyze_track(path: Path) -> TrackInfo:
    mono, sr = decode_audio(path, sr=22050, max_seconds=90.0)
    bpm = estimate_bpm(mono, sr)
    key, root, minor = estimate_key(mono, sr)
    peak_db, low_db, high_db = analyze_levels(mono, sr)
    phase = beat_phase(mono, sr, bpm)
    camelot, _, _ = camelot_code(root, minor)
    energy, energy_raw = estimate_energy(mono, sr)
    return TrackInfo(
        index=0,
        path=str(path),
        start=0.0,
        end=0.0,
        duration=0.0,
        bpm=round(bpm, 1),
        key=key,
        root=root,
        minor=minor,
        peak_db=round(peak_db, 2),
        low_db=round(low_db, 2),
        high_db=round(high_db, 2),
        beat_phase=round(phase, 4),
        camelot=camelot,
        energy=energy,
        energy_raw=energy_raw,
    )


def key_shift_semitones(src_root: int, dst_root: int) -> int:
    delta = (dst_root - src_root) % 12
    if delta > 6:
        delta -= 12
    return int(delta)


def split_session(
    src: Path,
    out_dir: Path | None = None,
    noise_db: float = -38.0,
    min_silence: float = 1.6,
    min_track: float = 8.0,
    progress=None,
) -> dict:
    src = Path(src).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    out_dir = Path(out_dir) if out_dir else src.with_name(src.stem + "-tracks")
    out_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("detecting silence")
    duration = media_duration(src)
    regions = silence_to_regions(duration, detect_silence(src, noise_db, min_silence), min_track)
    tracks: list[TrackInfo] = []
    for i, (start, end) in enumerate(regions, start=1):
        dest = out_dir / f"track-{i:02d}.flac"
        if progress:
            progress(f"extracting track {i}/{len(regions)}")
        extract_segment(src, dest, start, end)
        if progress:
            progress(f"analyzing BPM / key / levels {i}/{len(regions)}")
        info = analyze_track(dest)
        info.index = i
        info.path = str(dest)
        info.start = round(start, 3)
        info.end = round(end, 3)
        info.duration = round(end - start, 3)
        tracks.append(info)
    manifest = {
        "source": str(src),
        "directory": str(out_dir),
        "tracks": [asdict(t) for t in tracks],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _count_seconds(bpm: float, beats: int = 4) -> float:
    return beats * 60.0 / max(bpm, 70.0)


def phrase_bounds(duration: float, bpm: float, phase: float, from_end: bool, beats: int) -> tuple[float, float]:
    period = 60.0 / max(bpm, 70.0)
    needed = beats * period
    if needed >= duration - 0.05:
        return 0.0, duration
    t = phase % period
    beats: list[float] = []
    while t < duration + period:
        if 0 <= t <= duration:
            beats.append(t)
        t += period
    if from_end:
        starts = [b for b in beats if b + needed <= duration + 0.05]
        start = starts[-1] if starts else max(0.0, duration - needed)
    else:
        start = beats[0] if beats else 0.0
    return start, min(duration, start + needed)


def bpm_plan(tracks: list[TrackInfo], max_delta: float | None = None) -> list[float]:
    """Each track plays at its native tempo. Wide BPM gaps switch, they do not stretch."""
    return [float(track.bpm) for track in tracks]


def transition_kind(bpm_a: float, bpm_b: float, max_delta: float) -> str:
    """Blend when tempos are close; bump or hard-cut when they are not."""
    gap = abs(float(bpm_a) - float(bpm_b))
    if gap <= float(max_delta):
        return "blend"
    if gap <= max(BUMP_MAX_GAP, float(max_delta) * 3.0):
        return "bump"
    return "cut"


def _smoothstep(t: float) -> float:
    t = min(1.0, max(0.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def mix_bpm(count: float, bpm_out: float, bpm_in: float, tmg: MixTiming) -> float:
    """Ramp from leaving BPM to incoming BPM, then hold incoming."""
    ramp = max(1, tmg.bpm_ramp)
    if count <= ramp:
        t = _smoothstep(count / ramp)
        return float(bpm_out + (bpm_in - bpm_out) * t)
    return float(bpm_in)


def _phase_error_beats(leaving: TrackInfo, incoming: TrackInfo) -> float:
    pa = 60.0 / max(leaving.bpm, 70.0)
    pb = 60.0 / max(incoming.bpm, 70.0)
    fa = (leaving.beat_phase % pa) / pa
    fb = (incoming.beat_phase % pb) / pb
    err = fb - fa
    if err > 0.5:
        err -= 1.0
    if err < -0.5:
        err += 1.0
    return float(err)


def _walk_ratio(count: float, phase_err: float, tmg: MixTiming) -> float:
    """After the BPM ramp, nudge incoming tempo so beats walk onto the mix grid."""
    if count < tmg.bpm_ramp:
        return 1.0
    if count >= tmg.tempo_counts:
        return 1.0
    walk = max(1, tmg.timing_walk)
    return 1.0 - phase_err / float(walk)


def _ensure_harmonic(track: TrackInfo) -> TrackInfo:
    if not track.camelot:
        track.camelot, _, _ = camelot_code(track.root, track.minor)
    if track.energy < 1:
        # Old manifests: derive a 1–10 from peak so ordering still works.
        track.energy = int(np.clip(round((track.peak_db + 22.0) / 2.4), 1, 10))
    return track


def order_tracks_mixed_in_key(tracks: list[TrackInfo], settings: MixSettings) -> list[TrackInfo]:
    """Next-track picker: Camelot compatibility, then waveform energy (Mixed In Key rules)."""
    remaining = [_ensure_harmonic(t) for t in tracks]
    if len(remaining) < 2 or not settings.harmonic_order:
        return remaining
    # Open on the lowest-energy cut so the show can build.
    remaining.sort(key=lambda t: (t.energy, t.index))
    ordered = [remaining.pop(0)]

    def fold_bpm(bpm: float, ref: float) -> float:
        choices = [bpm, bpm * 2.0, bpm / 2.0]
        viable = [c for c in choices if 70.0 <= c <= 180.0]
        return min(viable or [bpm], key=lambda c: abs(c - ref))

    def rank(cur: TrackInfo, cand: TrackInfo) -> tuple:
        key_pen = camelot_distance(cur, cand)
        energy_delta = cand.energy - cur.energy
        # Mixed In Key: raise energy by 1 when you can; stay flat; avoid drops and jumps.
        if energy_delta == 1:
            energy_pen = 0.0
        elif energy_delta == 0:
            energy_pen = 0.25
        elif energy_delta == 2:
            energy_pen = 0.85
        elif energy_delta > 0:
            energy_pen = energy_delta * 1.1
        else:
            energy_pen = abs(energy_delta) * 1.7
        extra = abs(energy_delta) - settings.max_energy_step
        if extra > 0:
            energy_pen += 8.0 * extra
        bpm_gap = abs(fold_bpm(cand.bpm, cur.bpm) - cur.bpm)
        bpm_pen = max(0.0, bpm_gap - settings.max_bpm_delta)
        harmonic_tier = 0 if key_pen <= 1 else (1 if key_pen == 2 else 2)
        return (harmonic_tier, energy_pen, bpm_pen, cand.index)

    while remaining:
        cur = ordered[-1]
        remaining.sort(key=lambda cand: rank(cur, cand))
        ordered.append(remaining.pop(0))
    return ordered


def order_tracks_pop_roots(tracks: list[TrackInfo]) -> list[TrackInfo]:
    """Low-energy opener, then climb sequential root keys (C, C#, D, … wrapping)."""
    remaining = [_ensure_harmonic(t) for t in tracks]
    if len(remaining) < 2:
        return remaining
    remaining.sort(key=lambda t: (t.energy, t.energy_raw, t.index))
    opener = remaining[0]
    start_root = int(opener.root) % 12
    by_root: dict[int, list[TrackInfo]] = {r: [] for r in range(12)}
    for track in remaining:
        by_root[int(track.root) % 12].append(track)
    for root in by_root:
        by_root[root].sort(key=lambda t: (t.energy, t.energy_raw, t.index))
    ordered: list[TrackInfo] = []
    seen: set[int] = set()
    for step in range(12):
        group = by_root[(start_root + step) % 12]
        if step == 0:
            group = [opener] + [t for t in group if t.index != opener.index]
        for track in group:
            if track.index in seen:
                continue
            ordered.append(track)
            seen.add(track.index)
    for track in remaining:
        if track.index not in seen:
            ordered.append(track)
    return ordered


def order_tracks(tracks: list[TrackInfo], settings: MixSettings) -> list[TrackInfo]:
    if settings.style == "pop":
        if not settings.harmonic_order:
            return [_ensure_harmonic(t) for t in tracks]
        return order_tracks_pop_roots(tracks)
    return order_tracks_mixed_in_key(tracks, settings)


def cleanup_filter() -> str:
    """Hiss, rumble, and click cleanup that keeps song length unchanged."""
    return "highpass=f=35,adeclick,afftdn=nr=12:nf=-50:nt=w:tn=1"


def mix_cleanup_filter() -> str:
    """De-click splices only. FFT denoise on the full mix sounds like swirling static."""
    return "adeclick"


def mix_post_gain_cleanup_filter() -> str:
    """After the median boost, residual hiss sits higher and needs another pass."""
    return "highpass=f=30,adeclick,afftdn=nr=10:nf=-38:nt=w:tn=1"


def level_filter(track: TrackInfo, settings: MixSettings) -> str:
    """Peak-normalize every track to the same song level. Pop skips the limiter so levels stay flat."""
    target = float(settings.target_db)
    gain = target - float(track.peak_db)
    if settings.style == "pop":
        return f"volume={gain:.3f}dB"
    limit = 10 ** (target / 20.0)
    return (
        f"volume={gain:.3f}dB,"
        f"alimiter=limit={limit:.5f}:attack=5:release=40"
    )


def _apply_af(src: Path, dest: Path, filt: str, label: str = "filter") -> None:
    proc = _run(
        [_ffmpeg(), "-y", "-i", str(src), "-af", filt, *_pcm_args(), str(dest)]
    )
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or f"{label} failed")


def _match_levels(src: Path, dest: Path, filt: str) -> None:
    _apply_af(src, dest, filt, "level match")


def _loop_to(audio: np.ndarray, frames: int) -> np.ndarray:
    if len(audio) == 0:
        return np.zeros((frames, 2), dtype=np.float32)
    if len(audio) >= frames:
        return audio[:frames]
    reps = int(math.ceil(frames / len(audio)))
    tiled = np.tile(audio, (reps, 1))
    return tiled[:frames]


def _in_gain(count: float, tmg: MixTiming) -> float:
    """Incoming mix volume: 0 → 1 over the style's fade-in counts."""
    span = max(1, tmg.count)
    t = min(1.0, max(0.0, float(count) / float(span)))
    return math.sin(t * math.pi / 2)


def _out_gain(count: float, tmg: MixTiming) -> float:
    """Leaving mix volume: hold until half, then 1 → 0 over the fade-out counts."""
    span = max(1, tmg.count)
    t = min(1.0, max(0.0, (float(count) - tmg.half) / float(span)))
    return math.cos(t * math.pi / 2)


def _blend_pair(
    leaving: np.ndarray,
    incoming: np.ndarray,
    out0: float,
    out1: float,
    in0: float,
    in1: float,
) -> np.ndarray:
    n = min(len(leaving), len(incoming))
    leaving, incoming = leaving[:n], incoming[:n]
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    gain_out = out0 + (out1 - out0) * t
    gain_in = in0 + (in1 - in0) * t
    return (leaving * gain_out + incoming * gain_in).astype(np.float32)


def _bump_arrays(leaving: np.ndarray, incoming: np.ndarray) -> np.ndarray:
    """Equal-power switch over the overlapping native-tempo audio."""
    n = min(len(leaving), len(incoming))
    if n < 2:
        return incoming[: max(1, n)].astype(np.float32)
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    return (
        leaving[:n] * np.cos(t * np.pi / 2) + incoming[:n] * np.sin(t * np.pi / 2)
    ).astype(np.float32)


def _echo_bed(
    leaving: np.ndarray,
    sample: np.ndarray,
    n_count: int,
    counts: int = ECHO_COUNTS,
    decay: float = ECHO_DECAY,
    wet: float = ECHO_WET,
) -> np.ndarray:
    """Outgoing stays at unity; an incoming 1-count sample repeats on each of 8 counts."""
    n_count = max(1, int(n_count))
    n_bed = n_count * max(1, int(counts))
    leave = _loop_to(leaving, n_bed).astype(np.float32)
    hit = _loop_to(sample, n_count).astype(np.float32)
    echo = np.zeros((n_bed, 2), dtype=np.float32)
    for k in range(counts):
        echo[k * n_count : (k + 1) * n_count] = hit * (decay ** k)
    echo *= wet
    room = 0.99 - float(np.max(np.abs(leave))) if leave.size else 0.99
    echo_peak = float(np.max(np.abs(echo))) if echo.size else 0.0
    if echo_peak > max(room, 1e-6) and room > 0:
        echo *= room / echo_peak
    return (leave + echo).astype(np.float32)


def _write_echo_throw(
    leaving_src: Path,
    incoming_src: Path,
    dest: Path,
    outro_start: float,
    intro_start: float,
    leave_bpm: float,
    counts: int = ECHO_COUNTS,
) -> None:
    count_sec = _count_seconds(leave_bpm, 1)
    bed_dur = count_sec * counts
    tail = dest.with_name(dest.stem + "-leave.wav")
    hit = dest.with_name(dest.stem + "-hit.wav")
    _slice_seconds(leaving_src, outro_start, bed_dur, tail)
    _slice_seconds(incoming_src, intro_start, count_sec, hit)
    leaving, sr = decode_stereo(tail, sr=SR)
    sample, _ = decode_stereo(hit, sr=SR)
    n_count = max(1, int(round(count_sec * sr)))
    write_wav(dest, _echo_bed(leaving, sample, n_count, counts), sr)


def _write_switch(
    leaving_src: Path,
    incoming_src: Path,
    dest: Path,
    leave_at: float,
    enter_at: float,
    leave_dur: float,
    enter_dur: float,
) -> float:
    """Native-tempo bump or hard cut. Returns incoming seconds consumed."""
    tail = dest.with_name(dest.stem + "-tail.wav")
    head = dest.with_name(dest.stem + "-head.wav")
    _slice_seconds(leaving_src, leave_at, leave_dur, tail)
    _slice_seconds(incoming_src, enter_at, enter_dur, head)
    a, sr = decode_stereo(tail, sr=48000)
    b, _ = decode_stereo(head, sr=48000)
    blended = _bump_arrays(a, b)
    write_wav(dest, blended, sr)
    return float(len(blended) / max(sr, 1))


def _rubberband(src: Path, dest: Path, tempo: float, pitch: float) -> None:
    tempo = float(np.clip(tempo, 0.5, 2.0))
    pitch = float(np.clip(pitch, 0.5, 2.0))
    af = f"rubberband=tempo={tempo:.5f}:pitch={pitch:.5f}:transients=crisp"
    proc = _run(
        [
            _ffmpeg(),
            "-y",
            "-i",
            str(src),
            "-af",
            af,
            *_pcm_args(),
            str(dest),
        ]
    )
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or "rubberband failed")


def _slice_seconds(path: Path, start: float, dur: float, dest: Path) -> None:
    proc = _run(
        [
            _ffmpeg(),
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{max(0.05, dur):.3f}",
            "-i",
            str(path),
            *_pcm_args(),
            str(dest),
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or "slice failed")


def radio_mix(
    manifest: dict,
    output: Path,
    progress=None,
    settings: MixSettings | None = None,
) -> dict:
    settings = settings or MixSettings()
    tmg = settings.timing()
    tracks = [TrackInfo.from_row(row) for row in manifest.get("tracks") or []]
    if len(tracks) < 2:
        raise RuntimeError("Need at least two tracks to radio-mix. Record a session with gaps between songs.")
    tracks = order_tracks(tracks, settings)
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    played = bpm_plan(tracks, settings.max_bpm_delta)
    root = tracks[0]
    pieces: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="cascade-mix-") as tmp:
        tmp_path = Path(tmp)
        matched: list[Path] = []
        for i, track in enumerate(tracks):
            src_path = Path(track.path)
            if settings.denoise:
                if progress:
                    progress(f"cleaning noise {i + 1}/{len(tracks)}")
                cleaned = tmp_path / f"clean-{i:02d}.wav"
                _apply_af(src_path, cleaned, cleanup_filter(), "denoise")
                src_path = cleaned
            dest = tmp_path / f"matched-{i:02d}.wav"
            if progress:
                progress(
                    f"normalizing {i + 1}/{len(tracks)} to {settings.target_db:.0f} dB"
                )
            track.peak_db = measure_peak_db(src_path)
            _match_levels(src_path, dest, level_filter(track, settings))
            matched.append(dest)

        carry_skip = 0.0
        transitions: list[str] = []
        pop = settings.style == "pop"
        for i, track in enumerate(tracks):
            native = max(track.bpm, 70.0)
            play = played[i]
            dur = track.duration
            body_start = 0.0 if pop else carry_skip
            if i == len(tracks) - 1:
                if progress:
                    progress(
                        f"mixing {i + 1}/{len(tracks)}  {settings.style}  "
                        f"{track.camelot} E{track.energy} {track.key} {native:.0f} BPM"
                    )
                body_dur = max(0.2, dur - body_start)
                body = tmp_path / f"body-{i:02d}.wav"
                _slice_seconds(matched[i], body_start, body_dur, body)
                pieces.append(body)
                break

            nxt = tracks[i + 1]
            nxt_native = max(nxt.bpm, 70.0)
            nxt_play = played[i + 1]
            if pop:
                kind = "echo"
                transitions.append(kind)
                if progress:
                    progress(
                        f"mixing {i + 1}/{len(tracks)}  pop  echo  "
                        f"{track.key} E{track.energy} {native:.0f}→{nxt_native:.0f} BPM  "
                        f"{nxt.key}"
                    )
                outro_start, _ = phrase_bounds(
                    dur, native, track.beat_phase, from_end=True, beats=ECHO_COUNTS
                )
                outro_start = max(body_start, outro_start)
                intro_start, _ = phrase_bounds(
                    nxt.duration, nxt_native, nxt.beat_phase, from_end=False, beats=1
                )
                body_dur = max(0.2, outro_start - body_start)
                body = tmp_path / f"body-{i:02d}.wav"
                _slice_seconds(matched[i], body_start, body_dur, body)
                pieces.append(body)
                throw = tmp_path / f"echo-{i:02d}.wav"
                _write_echo_throw(
                    matched[i],
                    matched[i + 1],
                    throw,
                    outro_start,
                    intro_start,
                    native,
                )
                pieces.append(throw)
                carry_skip = 0.0
                continue

            kind = transition_kind(native, nxt_native, settings.max_bpm_delta)
            transitions.append(kind)
            if progress:
                progress(
                    f"mixing {i + 1}/{len(tracks)}  {settings.style}  {kind}  "
                    f"{track.camelot} E{track.energy} {track.key} {native:.0f}→{nxt_native:.0f} BPM"
                )

            if kind != "blend":
                outro_start, _ = phrase_bounds(dur, native, track.beat_phase, from_end=True, beats=1)
                outro_start = max(body_start, min(outro_start, max(body_start, dur - 0.25)))
                intro_start, _ = phrase_bounds(nxt.duration, nxt_native, nxt.beat_phase, from_end=False, beats=1)
                body_dur = max(0.2, outro_start - body_start)
                body = tmp_path / f"body-{i:02d}.wav"
                _slice_seconds(matched[i], body_start, body_dur, body)
                pieces.append(body)
                if kind == "bump":
                    leave_dur = _count_seconds(native, 1)
                    enter_dur = _count_seconds(nxt_native, 1)
                else:
                    leave_dur = enter_dur = CUT_FADE_SEC
                splice = tmp_path / f"{kind}-{i:02d}.wav"
                used = _write_switch(
                    matched[i],
                    matched[i + 1],
                    splice,
                    outro_start,
                    intro_start,
                    leave_dur,
                    enter_dur,
                )
                pieces.append(splice)
                carry_skip = intro_start + used
                continue

            phase_err = _phase_error_beats(track, nxt)
            outro_start, _ = phrase_bounds(dur, native, track.beat_phase, from_end=True, beats=tmg.blend_counts)
            outro_start = max(body_start, outro_start)
            intro_start, _ = phrase_bounds(nxt.duration, nxt_native, nxt.beat_phase, from_end=False, beats=tmg.tempo_counts)
            body_dur = max(0.2, outro_start - body_start)
            body = tmp_path / f"body-{i:02d}.wav"
            _slice_seconds(matched[i], body_start, body_dur, body)
            pieces.append(body)

            bar_native_a = _count_seconds(native, 4)
            bar_native_b = _count_seconds(nxt_native, 4)
            pitch_b = 1.0
            if settings.match_key and camelot_distance(track, nxt) >= 2:
                pitch_b = 2 ** (key_shift_semitones(nxt.root, track.root) / 12.0)
            blend_bars: list[np.ndarray] = []
            sr = 48000
            for bar in range(tmg.tempo_bars):
                count0 = bar * 4
                count1 = (bar + 1) * 4
                bpm_bar = mix_bpm(0.5 * (count0 + count1), play, nxt_play, tmg)
                frames = int(_count_seconds(bpm_bar, 4) * sr)
                intro_raw = tmp_path / f"intro-{i:02d}-{bar}.wav"
                _slice_seconds(matched[i + 1], intro_start + bar * bar_native_b, bar_native_b, intro_raw)
                intro_rb = tmp_path / f"intro-rb-{i:02d}-{bar}.wav"
                in_ratio = (bpm_bar / nxt_native) * _walk_ratio(count0, phase_err, tmg)
                _rubberband(intro_raw, intro_rb, in_ratio, pitch_b)
                b, _ = decode_stereo(intro_rb, sr=48000)
                incoming = _loop_to(b, frames)
                if _out_gain(count0, tmg) > 0.002 or _out_gain(count1, tmg) > 0.002:
                    outro_raw = tmp_path / f"outro-{i:02d}-{bar}.wav"
                    _slice_seconds(matched[i], outro_start + bar * bar_native_a, bar_native_a, outro_raw)
                    outro_rb = tmp_path / f"outro-rb-{i:02d}-{bar}.wav"
                    _rubberband(outro_raw, outro_rb, bpm_bar / native, 1.0)
                    a, sr = decode_stereo(outro_rb, sr=48000)
                    leaving = _loop_to(a, frames)
                else:
                    leaving = np.zeros((frames, 2), dtype=np.float32)
                blend_bars.append(
                    _blend_pair(
                        leaving,
                        incoming,
                        _out_gain(count0, tmg),
                        _out_gain(count1, tmg),
                        _in_gain(count0, tmg),
                        _in_gain(count1, tmg),
                    )
                )
            blend = np.concatenate(blend_bars, axis=0)
            blend_path = tmp_path / f"blend-{i:02d}.wav"
            write_wav(blend_path, blend, sr)
            pieces.append(blend_path)
            carry_skip = intro_start + tmg.tempo_bars * bar_native_b

        concat = tmp_path / "concat.txt"
        concat.write_text("".join(f"file '{p}'\n" for p in pieces), encoding="utf-8")
        if progress:
            progress("writing radio mix")
        raw = tmp_path / "mix-raw.wav"
        proc = _run(
            [
                _ffmpeg(),
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                *_pcm_args(),
                str(raw),
            ]
        )
        if proc.returncode != 0 or not raw.exists():
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or "mix concat failed")
        src = raw
        if settings.denoise:
            if progress:
                progress("de-clicking mix")
            cleaned_mix = tmp_path / "mix-clean.wav"
            _apply_af(src, cleaned_mix, mix_cleanup_filter(), "mix de-click")
            src = cleaned_mix
        if progress:
            progress("measuring mix peak")
        median_in = measure_median_db(src)
        peak = measure_peak_db(src)
        ceiling = float(settings.max_db)
        mix_gain = 0.0
        if peak > ceiling:
            mix_gain = ceiling - peak
            if progress:
                progress(f"trimming peak {peak:.1f} → {ceiling:.0f} dB")
            dest = tmp_path / "mix-ceiling.wav"
            limit = 10 ** (ceiling / 20.0)
            _apply_af(
                src,
                dest,
                f"volume={mix_gain:.3f}dB,alimiter=limit={limit:.5f}:attack=2:release=250",
                "peak ceiling",
            )
            src = dest
        proc = _run(
            [_ffmpeg(), "-y", "-i", str(src), *_flac24_args(), str(output)]
        )
        if proc.returncode != 0 or not output.exists():
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or "mix encode failed")
    return {
        "ok": True,
        "output": str(output),
        "tracks": len(tracks),
        "target_bpm": round(float(np.median(played)), 1),
        "played_bpm": [round(x, 1) for x in played],
        "mix_root": root.camelot or root.key,
        "target_db": settings.target_db,
        "min_db": settings.target_db,
        "max_db": settings.max_db,
        "median_db": settings.max_db,
        "median_in_db": round(median_in, 2),
        "mix_gain_db": round(mix_gain, 2),
        "peak_db": round(min(peak, ceiling) if mix_gain else peak, 2),
        "bit_depth": 24,
        "max_bpm_delta": settings.max_bpm_delta,
        "style": settings.style,
        "denoise": settings.denoise,
        "transitions": transitions,
        "transition_counts": {
            "echo": transitions.count("echo"),
            "blend": transitions.count("blend"),
            "bump": transitions.count("bump"),
            "cut": transitions.count("cut"),
        },
        "counts": tmg.count,
        "blend_counts": tmg.blend_counts,
        "bpm_ramp": tmg.bpm_ramp,
        "timing_walk": tmg.timing_walk,
        "order": [
            {
                "index": t.index,
                "camelot": t.camelot,
                "energy": t.energy,
                "key": t.key,
                "root": t.root,
                "bpm": t.bpm,
                "into": transitions[i] if i < len(transitions) else "end",
                "path": t.path,
            }
            for i, t in enumerate(tracks)
        ],
    }


def load_track_manifest(src: str | Path | None) -> dict | None:
    """Load split-session tracks from a directory, manifest.json, or session file."""
    if not src:
        return None
    p = Path(src).expanduser()
    candidates: list[Path] = []
    if p.is_dir():
        candidates.append(p / "manifest.json")
    elif p.is_file():
        if p.name == "manifest.json":
            candidates.append(p)
        else:
            candidates.append(p.with_name(p.stem + "-tracks") / "manifest.json")
            if p.parent.name.endswith("-tracks"):
                candidates.append(p.parent / "manifest.json")
    for man in candidates:
        if not man.exists():
            continue
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("tracks"):
            data.setdefault("directory", str(man.parent))
            return data
    return None


def mix_session(
    src: Path,
    output: Path | None = None,
    progress=None,
    settings: MixSettings | None = None,
    **split_kw,
) -> dict:
    src = Path(src).expanduser()
    settings = settings or MixSettings()
    if src.is_dir() and (src / "manifest.json").exists():
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        out_dir = src
    else:
        manifest = split_session(src, progress=progress, **split_kw)
        out_dir = Path(manifest["directory"])
    output = Path(output) if output else out_dir / "radio-mix.flac"
    mix = radio_mix(manifest, output, progress=progress, settings=settings)
    manifest["mix_order"] = mix.get("order")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    mix["manifest"] = str(out_dir / "manifest.json")
    mix["directory"] = str(out_dir)
    mix["tracks_detail"] = mix.get("order") or manifest["tracks"]
    return mix
