"""Gain math and LSP Graphic EQ x16 band map."""

from __future__ import annotations

import math
from typing import Sequence

# LSP Graphic Equalizer x16 Stereo band labels (ISO-ish).
BANDS: tuple[tuple[str, str, int], ...] = (
    ("g-0", "16", 16),
    ("g-1", "25", 25),
    ("g-2", "40", 40),
    ("g-3", "63", 63),
    ("g-4", "100", 100),
    ("g-5", "160", 160),
    ("g-6", "250", 250),
    ("g-7", "400", 400),
    ("g-8", "630", 630),
    ("g-9", "1k", 1000),
    ("g-10", "1.6k", 1600),
    ("g-11", "2.5k", 2500),
    ("g-12", "4k", 4000),
    ("g-13", "6.3k", 6300),
    ("g-14", "10k", 10000),
    ("g-15", "16k", 16000),
)

BAND_PROPS = tuple(b[0] for b in BANDS)
BAND_LABELS = tuple(b[1] for b in BANDS)
GAIN_MIN = 0.01585
GAIN_MAX = 63.095749
PREAMP_MAX = 10.0


def db_to_gain(db: float) -> float:
    return float(10 ** (db / 20.0))


def gain_to_db(gain: float) -> float:
    g = max(float(gain), 1e-12)
    return 20.0 * math.log10(g)


def clamp_gain(gain: float) -> float:
    return max(GAIN_MIN, min(GAIN_MAX, float(gain)))


def clamp_preamp_gain(gain: float) -> float:
    return max(0.0, min(PREAMP_MAX, float(gain)))


def db_to_band_gain(db: float) -> float:
    return clamp_gain(db_to_gain(db))


def db_to_preamp_gain(db: float) -> float:
    return clamp_preamp_gain(db_to_gain(db))


def knee_db_to_gain(knee_db: float) -> float:
    """LSP compressor knee is a linear gain (1.0 = hard, ~0.5 = 6 dB)."""
    return max(0.0631, min(1.0, db_to_gain(-abs(knee_db))))


def threshold_db_to_gain(db: float) -> float:
    return max(0.001, min(1.0, db_to_gain(db)))


def limiter_ceiling_to_gain(db: float) -> float:
    return max(0.003981, min(1.0, db_to_gain(db)))


MASTER_MIN_DB = -12.0
MASTER_MAX_DB = 12.0
MASTER_STEPS = 25


def clamp_master_db(db: float) -> float:
    try:
        value = float(db)
    except (TypeError, ValueError):
        value = 0.0
    return max(MASTER_MIN_DB, min(MASTER_MAX_DB, value))


def master_knob_value(db: float) -> float:
    n = MASTER_STEPS - 1
    idx = int(round(clamp_master_db(db) - MASTER_MIN_DB))
    return max(0, min(n, idx)) / n


def master_db_from_knob(value: float) -> float:
    n = MASTER_STEPS - 1
    idx = int(round(max(0.0, min(1.0, float(value))) * n))
    return float(MASTER_MIN_DB + idx)


def default_tone() -> dict:
    return {"low_db": 0.0, "mid_db": 0.0, "high_db": 0.0}


def normalize_tone(raw) -> dict:
    base = default_tone()
    src = raw if isinstance(raw, dict) else {}
    for key in ("low_db", "mid_db", "high_db"):
        base[key] = clamp_master_db(src.get(key, 0.0))
    return base


TONE_PROFILES: dict[str, dict] = {
    "Flat": {
        "low_db": 0.0,
        "mid_db": 0.0,
        "high_db": 0.0,
        "gain_db": 0.0,
        "note": "Unity LOW / MID / HIGH / GAIN.",
    },
    "80s POP": {
        "low_db": 8.0,
        "mid_db": 6.0,
        "high_db": 2.0,
        "gain_db": 6.0,
        "note": "Fat low, forward mid, light air, +6 dB gain.",
    },
    "Disco": {
        "low_db": 7.0,
        "mid_db": 3.0,
        "high_db": 5.0,
        "gain_db": 5.0,
        "note": "Kick and sparkle for four-on-the-floor.",
    },
    "Rock": {
        "low_db": 4.0,
        "mid_db": 2.0,
        "high_db": 5.0,
        "gain_db": 4.0,
        "note": "Tight body and bright edge.",
    },
    "Ballad": {
        "low_db": 3.0,
        "mid_db": 5.0,
        "high_db": 1.0,
        "gain_db": 2.0,
        "note": "Warm mids, easy gain.",
    },
    "Club": {
        "low_db": 8.0,
        "mid_db": 2.0,
        "high_db": 4.0,
        "gain_db": 8.0,
        "note": "Heavy low and loud — AUTO will pull it back from the red.",
    },
    "Night": {
        "low_db": 2.0,
        "mid_db": 1.0,
        "high_db": 3.0,
        "gain_db": -2.0,
        "note": "Quieter listen, a little air.",
    },
    "Voice": {
        "low_db": -2.0,
        "mid_db": 6.0,
        "high_db": 3.0,
        "gain_db": 2.0,
        "note": "Speech band forward, less rumble.",
    },
}


def tone_profile_names() -> list[str]:
    return list(TONE_PROFILES)


def apply_tone_profile(state: dict, name: str) -> dict:
    key = str(name).strip()
    spec = TONE_PROFILES.get(key)
    if spec is None:
        lower = {n.lower(): n for n in TONE_PROFILES}
        official = lower.get(key.lower())
        if official is None:
            raise KeyError(f"Unknown tone profile: {name}")
        key, spec = official, TONE_PROFILES[official]
    out = dict(state)
    out["tone"] = normalize_tone(spec)
    out["master_db"] = clamp_master_db(spec["gain_db"])
    out["tone_preset"] = key
    return out


def match_tone_preset(tone, master_db: float) -> str:
    tone = normalize_tone(tone)
    gain = clamp_master_db(master_db)
    for name, spec in TONE_PROFILES.items():
        if (
            abs(tone["low_db"] - spec["low_db"]) < 0.6
            and abs(tone["mid_db"] - spec["mid_db"]) < 0.6
            and abs(tone["high_db"] - spec["high_db"]) < 0.6
            and abs(gain - spec["gain_db"]) < 0.6
        ):
            return name
    return "Custom"


def scale_tone(tone, master_db: float, scale: float) -> tuple[dict, float]:
    amt = max(0.0, min(1.0, float(scale)))
    src = normalize_tone(tone)
    live = {key: clamp_master_db(src[key] * amt) for key in ("low_db", "mid_db", "high_db")}
    return live, clamp_master_db(clamp_master_db(master_db) * amt)


def default_tone_auto() -> dict:
    return {"enabled": False}


def normalize_tone_auto(raw) -> dict:
    src = raw if isinstance(raw, dict) else {}
    return {"enabled": bool(src.get("enabled", False))}


MIX_STEPS = 12


def mix_step(amount: float) -> int:
    """Map 0–1 mix onto detent 1–12."""
    v = max(0.0, min(1.0, float(amount)))
    return int(round(v * (MIX_STEPS - 1))) + 1


def mix_from_step(step: int) -> float:
    n = max(1, min(MIX_STEPS, int(step)))
    return (n - 1) / (MIX_STEPS - 1)


def mix_amount(rec: dict | None) -> float:
    """Effect usage 0–1 in 12 even steps. 1 = bypass, 12 = full wet."""
    rec = rec or {}
    if "mix" in rec:
        raw = float(rec["mix"])
    else:
        raw_w = rec.get("wet", 1.0)
        if isinstance(raw_w, bool):
            raw = 1.0 if raw_w else 0.0
        else:
            raw = float(raw_w)
    return mix_from_step(mix_step(raw))


def mix_levels(rec: dict | None) -> tuple[float, float]:
    """Return complementary (dry, wet). Dry is always 1 − wet."""
    wet = mix_amount(rec)
    return 1.0 - wet, wet


def set_mix(dst: dict, amount: float) -> dict:
    wet = mix_from_step(mix_step(amount))
    dst["mix"] = wet
    dst["wet"] = wet
    dst["dry"] = 1.0 - wet
    return dst


def keep_mix(dst: dict, src: dict | None) -> dict:
    return set_mix(dst, mix_amount(src))


def keep_dly_tone(dst: dict, src: dict | None) -> dict:
    src = src or {}
    for key, default in (("master", 1.0), ("clarity", 0.55), ("color", 0.4)):
        raw = src.get(key, dst.get(key, default))
        dst[key] = max(0.0, min(1.0, float(raw)))
    return dst


def default_blend() -> dict:
    """Background FX-bus compressor: duck wet chains before they hit the red."""
    return {
        "enabled": True,
        "threshold_db": -8.0,
        "ratio": 5.0,
        "attack_ms": 8.0,
        "release_ms": 240.0,
    }


def normalize_blend(raw) -> dict:
    base = default_blend()
    src = raw if isinstance(raw, dict) else {}
    base["enabled"] = bool(src.get("enabled", True))
    try:
        base["threshold_db"] = max(-36.0, min(0.0, float(src.get("threshold_db", -8.0))))
    except (TypeError, ValueError):
        pass
    try:
        base["ratio"] = max(1.0, min(20.0, float(src.get("ratio", 5.0))))
    except (TypeError, ValueError):
        pass
    try:
        base["attack_ms"] = max(0.5, min(200.0, float(src.get("attack_ms", 8.0))))
    except (TypeError, ValueError):
        pass
    try:
        base["release_ms"] = max(5.0, min(2000.0, float(src.get("release_ms", 240.0))))
    except (TypeError, ValueError):
        pass
    return base


def blend_scale(gr: float) -> float:
    """0 = full wet allowed, 1 = FX fully blended back to dry."""
    return max(0.0, min(1.0, 1.0 - float(gr)))


def default_ride() -> dict:
    """Watch the output waveform histogram and ride makeup toward a target RMS."""
    return {
        "enabled": True,
        "target_db": -18.0,
        "boost_db": 8.0,
        "cut_db": 12.0,
        "gate_db": -48.0,
        "ceiling_db": -1.0,
        "attack_ms": 900.0,
        "release_ms": 220.0,
    }


def normalize_ride(raw) -> dict:
    base = default_ride()
    src = raw if isinstance(raw, dict) else {}
    base["enabled"] = bool(src.get("enabled", True))
    clamps = {
        "target_db": (-36.0, -6.0),
        "boost_db": (0.0, 12.0),
        "cut_db": (0.0, 18.0),
        "gate_db": (-72.0, -24.0),
        "ceiling_db": (-6.0, 0.0),
        "attack_ms": (80.0, 4000.0),
        "release_ms": (40.0, 2000.0),
    }
    for key, (lo, hi) in clamps.items():
        try:
            base[key] = max(lo, min(hi, float(src.get(key, base[key]))))
        except (TypeError, ValueError):
            pass
    return base


CHAIN_STAGES: tuple[str, ...] = ("pre", "eq", "exp", "dyn", "fx", "fx2")
CHAIN_LABELS: dict[str, str] = {
    "pre": "PREAMP",
    "eq": "EQ",
    "exp": "EXPANDER",
    "dyn": "DYN",
    "fx": "FX",
    "fx2": "ENHANCE",
}


def normalize_chain(order) -> list[str]:
    """Return a permutation of every processing stage, default first."""
    seen: list[str] = []
    for key in order or []:
        k = str(key).strip().lower()
        if k in CHAIN_STAGES and k not in seen:
            seen.append(k)
    for key in CHAIN_STAGES:
        if key not in seen:
            seen.append(key)
    return seen


def empty_bands() -> list[float]:
    return [0.0] * len(BANDS)


AUTO_EQ_BEATS = 4


def four_beat_seconds(bpm: float, beats: int = AUTO_EQ_BEATS) -> float:
    """Duration of a listen window: `beats` at the guessed tempo."""
    tempo = max(70.0, min(180.0, float(bpm) if bpm else 120.0))
    count = max(1, int(beats))
    return max(1.2, min(3.6, count * 60.0 / tempo))


def auto_reveal_from_rta(
    rta: Sequence[float],
    previous: Sequence[float] | None = None,
    strength: float = 0.9,
) -> list[float]:
    """Compress quiet low/mid/high up toward the loudest region."""
    n = len(BANDS)
    floor_db = 6.0
    levels = [max(0.0, float(v)) for v in (list(rta) + [0.0] * n)[:n]]
    prev = [max(0.0, float(v)) for v in (list(previous or empty_bands()) + [0.0] * n)[:n]]
    amt = max(0.0, min(1.0, float(strength)))
    if max(levels, default=0.0) < 0.008:
        held = [max(floor_db, v) for v in prev] if any(prev) else [floor_db] * n
        return [max(0.0, min(12.0, round(v * 2.0) / 2.0)) for v in held]

    def region_mean(start: int, end: int) -> float:
        chunk = levels[start:end]
        return sum(chunk) / max(1, len(chunk))

    regions = ((0, 7), (7, 11), (11, 16))
    energies = [region_mean(a, b) for a, b in regions]
    peak_e = max(max(energies), 1e-9)
    region_boost = [
        min(12.0, 20.0 * math.log10(peak_e / max(e, 1e-9)) * amt) for e in energies
    ]
    measured = [20.0 * math.log10(max(v, 1e-6)) for v in levels]
    peak_m = max(measured)
    out: list[float] = []
    for i, (meas, old) in enumerate(zip(measured, prev)):
        if i < 7:
            rb = region_boost[0]
        elif i < 11:
            rb = region_boost[1]
        else:
            rb = region_boost[2]
        depth = max(0.0, min(1.0, (peak_m - meas) / 28.0))
        target = min(12.0, floor_db + rb + depth * 6.0 * amt)
        blended = 0.15 * old + 0.85 * target
        out.append(max(0.0, min(12.0, round(blended * 2.0) / 2.0)))
    return out


def mix_eq_lifts(bands: Sequence[float] | None, lifts: Sequence[float] | None) -> list[float]:
    n = len(BANDS)
    base = [float(v) for v in (list(bands or empty_bands()) + [0.0] * n)[:n]]
    extra = [max(0.0, float(v)) for v in (list(lifts or empty_bands()) + [0.0] * n)[:n]]
    return [max(-12.0, min(12.0, b + e)) for b, e in zip(base, extra)]


def auto_eq_from_rta(
    rta: Sequence[float],
    current: Sequence[float] | None = None,
    strength: float = 0.72,
) -> list[float]:
    """Reveal buried detail as additive lifts (does not rewrite the user curve)."""
    return auto_reveal_from_rta(rta, None, strength)


def resolve_band(token: str) -> int:
    """Map '1k', 'g-9', '9', or '1000' to a band index."""
    raw = token.strip().lower().replace("hz", "")
    if raw.startswith("g-") or raw.startswith("g_"):
        prop = "g-" + raw.split("-", 1)[-1].replace("_", "")
        if prop in BAND_PROPS:
            return BAND_PROPS.index(prop)
    for i, label in enumerate(BAND_LABELS):
        if raw == label.lower():
            return i
    if raw.isdigit():
        n = int(raw)
        if 0 <= n < len(BANDS):
            return n
        freqs = [b[2] for b in BANDS]
        return min(range(len(freqs)), key=lambda i: abs(freqs[i] - n))
    raise ValueError(f"Unknown EQ band {token!r}. Try: {' '.join(BAND_LABELS)}")


def interpolate_10(values: Sequence[float]) -> list[float]:
    """Expand a classic 10-band ISO curve onto the 16-band LSP graphic EQ."""
    src_f = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    dst = []
    for _, _, freq in BANDS:
        if freq <= src_f[0]:
            dst.append(float(values[0]))
            continue
        if freq >= src_f[-1]:
            dst.append(float(values[-1]))
            continue
        for i in range(len(src_f) - 1):
            lo, hi = src_f[i], src_f[i + 1]
            if lo <= freq <= hi:
                t = math.log(freq / lo) / math.log(hi / lo)
                dst.append(float(values[i]) + t * (float(values[i + 1]) - float(values[i])))
                break
    return dst
