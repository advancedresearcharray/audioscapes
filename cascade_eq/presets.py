"""Built-in output presets (16-band dB gains + dynamics)."""

from __future__ import annotations

from .dsp import interpolate_10

# 10-band ISO: 32 64 125 250 500 1k 2k 4k 8k 16k → expanded to 16 LSP bands.


def _p(
    name: str,
    note: str,
    bands10: list[float],
    *,
    preamp: float = 0.0,
    compressor: dict | None = None,
    limiter: dict | None = None,
) -> dict:
    preset: dict = {
        "name": name,
        "note": note,
        "preamp_db": preamp,
        "bands": interpolate_10(bands10),
        "compressor": compressor
        or {
            "enabled": False,
            "mode": "rms",
            "threshold_db": -18.0,
            "ratio": 4.0,
            "attack_ms": 15.0,
            "release_ms": 120.0,
            "knee_db": 6.0,
            "makeup_db": 0.0,
            "lookahead_ms": 0.0,
        },
        "limiter": limiter
        or {
            "enabled": True,
            "ceiling_db": -1.0,
            "lookahead_ms": 5.0,
            "attack_ms": 5.0,
            "release_ms": 8.0,
            "alr": False,
        },
    }
    return preset


def _comp(**kwargs) -> dict:
    base = {
        "enabled": True,
        "mode": "rms",
        "threshold_db": -18.0,
        "ratio": 4.0,
        "attack_ms": 15.0,
        "release_ms": 120.0,
        "knee_db": 6.0,
        "makeup_db": 0.0,
        "lookahead_ms": 0.0,
    }
    base.update(kwargs)
    return base


def _lim(**kwargs) -> dict:
    base = {
        "enabled": True,
        "ceiling_db": -1.0,
        "lookahead_ms": 5.0,
        "attack_ms": 5.0,
        "release_ms": 8.0,
        "alr": False,
    }
    base.update(kwargs)
    return base


DYN_PRESETS: dict[str, dict] = {
    "SAFE": {
        "compressor": _comp(enabled=False),
        "limiter": _lim(ceiling_db=-1.0),
    },
    "SOFT": {
        "compressor": _comp(threshold_db=-16, ratio=2.5, attack_ms=20, release_ms=150, makeup_db=1.0, knee_db=8),
        "limiter": _lim(ceiling_db=-1.0),
    },
    "RADIO": {
        "compressor": _comp(threshold_db=-18, ratio=4.0, attack_ms=12, release_ms=90, makeup_db=2.5),
        "limiter": _lim(ceiling_db=-1.0, alr=True),
    },
    "TIGHT": {
        "compressor": _comp(threshold_db=-20, ratio=6.0, attack_ms=8, release_ms=80, makeup_db=3.0, mode="peak"),
        "limiter": _lim(ceiling_db=-0.5, lookahead_ms=8),
    },
    "LIMIT": {
        "compressor": _comp(enabled=False, mode="peak", threshold_db=-10, ratio=12.0, attack_ms=5, release_ms=50),
        "limiter": _lim(ceiling_db=-0.3, lookahead_ms=6, attack_ms=2, release_ms=12),
    },
    "GLUE": {
        "compressor": _comp(threshold_db=-18, ratio=2.0, attack_ms=30, release_ms=220, makeup_db=1.5, knee_db=10),
        "limiter": _lim(ceiling_db=-1.0),
    },
    "VOCAL": {
        "compressor": _comp(threshold_db=-18, ratio=3.5, attack_ms=8, release_ms=70, makeup_db=2.0, knee_db=6),
        "limiter": _lim(ceiling_db=-1.0, alr=True),
    },
    "PUNCH": {
        "compressor": _comp(threshold_db=-14, ratio=4.5, attack_ms=18, release_ms=70, makeup_db=2.0, mode="peak"),
        "limiter": _lim(ceiling_db=-0.5, lookahead_ms=6),
    },
    "BROADCAST": {
        "compressor": _comp(threshold_db=-22, ratio=5.0, attack_ms=10, release_ms=120, makeup_db=4.0, knee_db=8),
        "limiter": _lim(ceiling_db=-0.5, alr=True, lookahead_ms=8),
    },
    "WHISPER": {
        "compressor": _comp(threshold_db=-28, ratio=8.0, attack_ms=12, release_ms=180, makeup_db=8.0, knee_db=10),
        "limiter": _lim(ceiling_db=-1.5, alr=True, lookahead_ms=8),
    },
    "HOUSE": {
        "compressor": _comp(threshold_db=-14, ratio=3.2, attack_ms=28, release_ms=125, makeup_db=1.5, mode="peak"),
        "limiter": _lim(ceiling_db=-0.5, lookahead_ms=6),
    },
    "TRANCE": {
        "compressor": _comp(threshold_db=-16, ratio=4.0, attack_ms=32, release_ms=180, makeup_db=2.5, knee_db=8, mode="peak"),
        "limiter": _lim(ceiling_db=-0.3, lookahead_ms=8),
    },
    "STADIUM": {
        "compressor": _comp(threshold_db=-20, ratio=2.2, attack_ms=40, release_ms=280, makeup_db=2.0, knee_db=10),
        "limiter": _lim(ceiling_db=-1.0, lookahead_ms=10, release_ms=14),
    },
    "LARGE HALL": {
        "compressor": _comp(threshold_db=-22, ratio=1.8, attack_ms=50, release_ms=350, makeup_db=1.0, knee_db=12),
        "limiter": _lim(ceiling_db=-1.2, lookahead_ms=10, release_ms=16),
    },
    "WOODEN BOX": {
        "compressor": _comp(threshold_db=-12, ratio=5.0, attack_ms=4, release_ms=40, makeup_db=1.0, knee_db=3, mode="peak"),
        "limiter": _lim(ceiling_db=-0.8, lookahead_ms=4, attack_ms=2, release_ms=10),
    },
    "FROZEN CHAMBER": {
        "compressor": _comp(threshold_db=-26, ratio=6.0, attack_ms=60, release_ms=450, makeup_db=4.0, knee_db=10),
        "limiter": _lim(ceiling_db=-1.5, alr=True, lookahead_ms=10, release_ms=18),
    },
    "CATHEDRAL": {
        "compressor": _comp(threshold_db=-24, ratio=2.0, attack_ms=70, release_ms=520, makeup_db=1.5, knee_db=12),
        "limiter": _lim(ceiling_db=-1.5, lookahead_ms=12, release_ms=18),
    },
    "ARENA": {
        "compressor": _comp(threshold_db=-18, ratio=2.8, attack_ms=35, release_ms=220, makeup_db=2.0, knee_db=8),
        "limiter": _lim(ceiling_db=-0.8, lookahead_ms=8, release_ms=14),
    },
    "WAREHOUSE": {
        "compressor": _comp(threshold_db=-13, ratio=4.5, attack_ms=18, release_ms=95, makeup_db=2.0, mode="peak"),
        "limiter": _lim(ceiling_db=-0.4, lookahead_ms=6),
    },
}


def dyn_preset_names() -> list[str]:
    return list(DYN_PRESETS)


def dyn_preset_mapping(name: str) -> tuple[dict, dict]:
    spec = DYN_PRESETS.get(str(name or "SAFE").strip().upper(), DYN_PRESETS["SAFE"])
    return dict(spec["compressor"]), dict(spec["limiter"])


def match_dyn_preset(compressor: dict | None, limiter: dict | None) -> str:
    compressor = compressor or {}
    limiter = limiter or {}
    for name, spec in DYN_PRESETS.items():
        if _dyn_close(compressor, spec["compressor"]) and _dyn_close(limiter, spec["limiter"]):
            return name
    return "CUSTOM"


def _dyn_close(got: dict, want: dict) -> bool:
    for key, value in want.items():
        have = got.get(key, value)
        if isinstance(value, float):
            if abs(float(have) - value) > 0.2:
                return False
        elif have != value:
            return False
    return True


def _pre_filt(**kwargs) -> dict:
    base = {"enabled": False, "hpf_hz": 40.0, "slope": "x2"}
    base.update(kwargs)
    return base


def _post(**kwargs) -> dict:
    base = {
        "enabled": True,
        "gain_db": 0.0,
        "width": 0.0,
        "balance": 0.0,
        "air_db": 0.0,
        "air_hz": 8000.0,
    }
    base.update(kwargs)
    return base


PRE_PRESETS: dict[str, dict] = {
    "FLAT": {
        "preamp_db": 0.0,
        "pre": _pre_filt(),
        "post": _post(),
    },
    "RUMBLE": {
        "preamp_db": 0.0,
        "pre": _pre_filt(enabled=True, hpf_hz=40.0, slope="x2"),
        "post": _post(),
    },
    "WARM": {
        "preamp_db": 1.5,
        "pre": _pre_filt(enabled=True, hpf_hz=55.0, slope="x2"),
        "post": _post(air_db=-1.0, air_hz=7000.0),
    },
    "WIDE": {
        "preamp_db": 0.0,
        "pre": _pre_filt(),
        "post": _post(width=0.45, air_db=1.0),
    },
    "AIR": {
        "preamp_db": 0.0,
        "pre": _pre_filt(enabled=True, hpf_hz=30.0, slope="x1"),
        "post": _post(air_db=2.5, air_hz=10000.0),
    },
    "LOUD": {
        "preamp_db": 3.0,
        "pre": _pre_filt(),
        "post": _post(gain_db=1.5),
    },
    "NARROW": {
        "preamp_db": 0.0,
        "pre": _pre_filt(),
        "post": _post(width=-0.35),
    },
    "PRESENCE": {
        "preamp_db": 0.5,
        "pre": _pre_filt(enabled=True, hpf_hz=70.0, slope="x2"),
        "post": _post(air_db=1.5, air_hz=6500.0),
    },
    "SUB": {
        "preamp_db": 0.0,
        "pre": _pre_filt(enabled=True, hpf_hz=90.0, slope="x4"),
        "post": _post(),
    },
    "STUDIO": {
        "preamp_db": 0.0,
        "pre": _pre_filt(enabled=True, hpf_hz=35.0, slope="x2"),
        "post": _post(width=0.2, air_db=0.8, air_hz=9000.0),
    },
    "HEADPHONE": {
        "preamp_db": -1.0,
        "pre": _pre_filt(),
        "post": _post(width=-0.15, air_db=1.0, air_hz=8000.0),
    },
}


def pre_preset_names() -> list[str]:
    return list(PRE_PRESETS)


def pre_preset_mapping(name: str) -> tuple[float, dict, dict]:
    spec = PRE_PRESETS.get(str(name or "FLAT").strip().upper(), PRE_PRESETS["FLAT"])
    return float(spec["preamp_db"]), dict(spec["pre"]), dict(spec["post"])


def match_pre_preset(preamp_db: float, pre: dict | None, post: dict | None) -> str:
    pre = pre or {}
    post = post or {}
    for name, spec in PRE_PRESETS.items():
        if abs(float(preamp_db) - float(spec["preamp_db"])) > 0.2:
            continue
        if _dyn_close(pre, spec["pre"]) and _dyn_close(post, spec["post"]):
            return name
    return "CUSTOM"


def _exp(**kwargs) -> dict:
    base = {
        "enabled": True,
        "mode": "rms",
        "em": "down",
        "threshold_db": -40.0,
        "ratio": 4.0,
        "attack_ms": 5.0,
        "release_ms": 80.0,
        "knee_db": 6.0,
        "makeup_db": 0.0,
        "lookahead_ms": 0.0,
    }
    base.update(kwargs)
    return base


EXP_PRESETS: dict[str, dict] = {
    "OPEN": _exp(enabled=False),
    "GENTLE": _exp(threshold_db=-44, ratio=2.0, attack_ms=8, release_ms=120, knee_db=10),
    "GATE": _exp(threshold_db=-36, ratio=10.0, attack_ms=2, release_ms=60, mode="peak", knee_db=4),
    "RADIO": _exp(threshold_db=-42, ratio=3.5, attack_ms=6, release_ms=90, knee_db=8),
    "TIGHT": _exp(threshold_db=-32, ratio=8.0, attack_ms=3, release_ms=50, mode="peak"),
    "VOCAL": _exp(threshold_db=-38, ratio=4.0, attack_ms=4, release_ms=70, knee_db=6),
    "LIFT": _exp(em="up", threshold_db=-24, ratio=2.0, attack_ms=12, release_ms=150, makeup_db=1.0, knee_db=8),
}


def exp_preset_names() -> list[str]:
    return list(EXP_PRESETS)


def exp_preset_mapping(name: str) -> dict:
    return dict(EXP_PRESETS.get(str(name or "OPEN").strip().upper(), EXP_PRESETS["OPEN"]))


def match_exp_preset(expander: dict | None) -> str:
    expander = expander or {}
    for name, spec in EXP_PRESETS.items():
        if _dyn_close(expander, spec):
            return name
    return "CUSTOM"


def _fx(**kwargs) -> dict:
    base = {
        "hpf_on": False,
        "hpf_hz": 80.0,
        "hpf_slope": "x2",
        "lpf_on": False,
        "lpf_hz": 8000.0,
        "lpf_slope": "x2",
        "air_on": False,
        "air_db": 0.0,
        "air_hz": 10000.0,
        "echo_on": False,
        "echo_delay_ms": 280.0,
        "echo_intensity": 0.28,
        "echo_feedback": 0.22,
        "room_on": False,
        "room_size": 0.45,
        "room_level": 0.18,
        "room_damping": 0.25,
    }
    base.update(kwargs)
    return base


FX_PRESETS: dict[str, dict] = {
    "BYPASS": _fx(),
    "HIGH PASS": _fx(hpf_on=True, hpf_hz=80.0, hpf_slope="x2"),
    "LOW PASS": _fx(lpf_on=True, lpf_hz=4500.0, lpf_slope="x2"),
    "ADD AIR": _fx(air_on=True, air_db=3.0, air_hz=10000.0),
    "ECHO": _fx(echo_on=True, echo_delay_ms=280.0, echo_intensity=0.32, echo_feedback=0.24),
    "SLAP": _fx(echo_on=True, echo_delay_ms=90.0, echo_intensity=0.22, echo_feedback=0.08),
    "DUB": _fx(echo_on=True, echo_delay_ms=420.0, echo_intensity=0.38, echo_feedback=0.42),
    "ROOM": _fx(room_on=True, room_size=0.35, room_level=0.16, room_damping=0.3),
    "HALL": _fx(room_on=True, room_size=0.78, room_level=0.26, room_damping=0.22),
    "TELEPHONE": _fx(hpf_on=True, hpf_hz=400.0, hpf_slope="x4", lpf_on=True, lpf_hz=2800.0, lpf_slope="x4"),
    "VINYL": _fx(hpf_on=True, hpf_hz=45.0, lpf_on=True, lpf_hz=11000.0, room_on=True, room_size=0.22, room_level=0.10),
    "NIGHT AIR": _fx(hpf_on=True, hpf_hz=55.0, air_on=True, air_db=2.0, air_hz=9000.0),
}


def fx_preset_names() -> list[str]:
    return list(FX_PRESETS)


def fx_preset_mapping(name: str) -> dict:
    return dict(FX_PRESETS.get(str(name or "BYPASS").strip().upper(), FX_PRESETS["BYPASS"]))


def match_fx_preset(fx: dict | None) -> str:
    fx = fx or {}
    for name, spec in FX_PRESETS.items():
        if _dyn_close(fx, spec):
            return name
    return "CUSTOM"


def _de(**kwargs) -> dict:
    base = {
        "low_on": False,
        "low_ft": "Lo-shelf",
        "low_hz": 110.0,
        "low_db": 0.0,
        "low_slope": "x2",
        "mid_on": False,
        "mid_ft": "Bell",
        "mid_hz": 1000.0,
        "mid_db": 0.0,
        "mid_q": 0.85,
        "high_on": False,
        "high_ft": "Hi-shelf",
        "high_hz": 8000.0,
        "high_db": 0.0,
        "width": 0.0,
        "echo_on": False,
        "echo_delay_ms": 90.0,
        "echo_intensity": 0.18,
        "echo_feedback": 0.10,
        "room_on": False,
        "room_size": 0.30,
        "room_level": 0.14,
        "room_damping": 0.35,
        "gain_db": 0.0,
        "mix": 1.0,
    }
    base.update(kwargs)
    return base


DIGITAL_PRESETS: dict[str, dict] = {
    "bypass": _de(mix=0.0),
    "floor": _de(
        low_on=True,
        low_ft="Lo-shelf",
        low_hz=95.0,
        low_db=3.2,
        mid_on=True,
        mid_ft="Bell",
        mid_hz=220.0,
        mid_db=1.4,
        mid_q=0.7,
        high_on=True,
        high_ft="Hi-shelf",
        high_hz=9000.0,
        high_db=-0.8,
        gain_db=0.4,
        mix=1.0,
    ),
    "headroom": _de(
        low_on=True,
        low_ft="Hi-pass",
        low_hz=52.0,
        low_slope="x2",
        mid_on=True,
        mid_ft="Bell",
        mid_hz=2400.0,
        mid_db=-1.4,
        mid_q=0.8,
        high_on=True,
        high_ft="Hi-shelf",
        high_hz=11000.0,
        high_db=-1.2,
        width=-0.10,
        gain_db=-2.6,
        mix=1.0,
    ),
    "clarity": _de(
        low_on=True,
        low_ft="Hi-pass",
        low_hz=38.0,
        low_slope="x1",
        mid_on=True,
        mid_ft="Bell",
        mid_hz=3200.0,
        mid_db=2.6,
        mid_q=0.9,
        high_on=True,
        high_ft="Hi-shelf",
        high_hz=9500.0,
        high_db=1.8,
        width=0.06,
        gain_db=-0.6,
        mix=1.0,
    ),
    "stereo": _de(
        high_on=True,
        high_ft="Hi-shelf",
        high_hz=7500.0,
        high_db=0.7,
        width=0.58,
        gain_db=-0.3,
        mix=1.0,
    ),
    "layers": _de(
        mid_on=True,
        mid_ft="Bell",
        mid_hz=1600.0,
        mid_db=0.8,
        mid_q=0.7,
        high_on=True,
        high_ft="Hi-shelf",
        high_hz=8000.0,
        high_db=0.6,
        width=0.22,
        echo_on=True,
        echo_delay_ms=88.0,
        echo_intensity=0.20,
        echo_feedback=0.12,
        room_on=True,
        room_size=0.34,
        room_level=0.16,
        room_damping=0.38,
        gain_db=-0.8,
        mix=0.34,
    ),
    "punch": _de(
        low_on=True,
        low_ft="Lo-shelf",
        low_hz=85.0,
        low_db=2.0,
        mid_on=True,
        mid_ft="Bell",
        mid_hz=110.0,
        mid_db=1.6,
        mid_q=0.75,
        high_on=True,
        high_ft="Bell",
        high_hz=2800.0,
        high_db=1.8,
        width=0.08,
        gain_db=-0.4,
        mix=1.0,
    ),
}

DIGITAL_KEYS = (
    ("floor", "FLOOR"),
    ("headroom", "HEADROOM"),
    ("clarity", "CLARITY"),
    ("stereo", "STEREO"),
    ("layers", "LAYERS"),
    ("punch", "PUNCH"),
)

_DIGITAL_ALIASES = {
    "off": "bypass",
    "bypass": "bypass",
    "none": "bypass",
    "floor": "floor",
    "increase floor": "floor",
    "headroom": "headroom",
    "increase headroom": "headroom",
    "clarity": "clarity",
    "boost clarity": "clarity",
    "stereo": "stereo",
    "boost stereo": "stereo",
    "layers": "layers",
    "add layers": "layers",
    "punch": "punch",
}


def digital_preset_names() -> list[str]:
    return [key for key, _label in DIGITAL_KEYS]


def normalize_digital_name(name: str | None) -> str:
    raw = str(name or "bypass").strip().lower().replace("_", " ").replace("-", " ")
    return _DIGITAL_ALIASES.get(raw, "bypass" if raw not in DIGITAL_PRESETS else raw)


def digital_preset_mapping(name: str | None) -> dict:
    return dict(DIGITAL_PRESETS[normalize_digital_name(name)])


def normalize_digital(raw) -> dict:
    from .dsp import mix_amount, set_mix

    src = raw if isinstance(raw, dict) else {}
    name = normalize_digital_name(src.get("preset"))
    out = {"preset": name}
    spec = DIGITAL_PRESETS[name]
    if "mix" in src or "wet" in src:
        set_mix(out, mix_amount(src))
    else:
        set_mix(out, float(spec.get("mix", 0.0 if name == "bypass" else 1.0)))
    return out


def default_digital() -> dict:
    return normalize_digital({"preset": "bypass"})


def _dly(**kwargs) -> dict:
    base = {
        "pong_on": False,
        "l_delay_ms": 0.0,
        "r_delay_ms": 375.0,
        "c_delay_ms": 0.0,
        "l_level": 50.0,
        "r_level": 50.0,
        "c_level": 0.0,
        "feedback": 28.0,
        "spread": 40.0,
        "high_damp": 25.0,
        "low_damp": 40.0,
        "rev_on": False,
        "rev_time": 0.42,
        "rev_feedback": 0.18,
        "chop_on": False,
        "chop_hz": 8.0,
        "chop_depth": 1.0,
        "chop_square": True,
        "crush_on": False,
        "alias": 0.55,
        "cheb": 0.0,
        "sift_on": False,
        "sift": 28.0,
        "auto_on": False,
        "enhance": "",
    }
    base.update(kwargs)
    return base


DELAY_PRESETS: dict[str, dict] = {
    "BYPASS": _dly(),
    "NOISE FILTER": _dly(auto_on=True, enhance="noise"),
    "AUDIO CLARITY": _dly(
        auto_on=True,
        enhance="clarity",
        pong_on=True,
        l_delay_ms=16.0,
        r_delay_ms=23.0,
        spread=48.0,
        feedback=6.0,
        high_damp=18.0,
        low_damp=22.0,
    ),
    "AUTO VOLUME": _dly(auto_on=True, enhance="level"),
    "PING PONG": _dly(pong_on=True, l_delay_ms=0.0, r_delay_ms=375.0, spread=48.0, feedback=32.0),
    "BOUNCE": _dly(pong_on=True, l_delay_ms=0.0, r_delay_ms=540.0, spread=50.0, feedback=44.0, high_damp=42.0),
    "LEFT RIGHT": _dly(pong_on=True, l_delay_ms=190.0, r_delay_ms=380.0, spread=50.0, feedback=24.0),
    "CHOP": _dly(chop_on=True, chop_hz=8.0, chop_depth=1.0, chop_square=True),
    "STUTTER": _dly(chop_on=True, chop_hz=16.0, chop_depth=1.15, chop_square=True),
    "GATE CHOP": _dly(chop_on=True, chop_hz=5.5, chop_depth=1.45, chop_square=True),
    "DEMATERIALIZE": _dly(rev_on=True, rev_time=0.46, rev_feedback=0.22, sift_on=True, sift=32.0),
    "VANISH": _dly(rev_on=True, rev_time=0.88, rev_feedback=0.36, sift_on=True, sift=70.0),
    "CRUSH": _dly(crush_on=True, alias=0.55, cheb=0.0),
    "BITCRUSH": _dly(crush_on=True, alias=0.92, cheb=0.55),
    "SHRED": _dly(crush_on=True, alias=0.72, cheb=1.45),
    "HAUNT": _dly(
        pong_on=True,
        l_delay_ms=0.0,
        r_delay_ms=430.0,
        spread=48.0,
        feedback=36.0,
        rev_on=True,
        rev_time=0.30,
        rev_feedback=0.14,
    ),
    "GLITCH": _dly(
        chop_on=True,
        chop_hz=12.0,
        chop_depth=0.9,
        chop_square=True,
        sift_on=True,
        sift=96.0,
        crush_on=True,
        alias=0.28,
    ),
}


def delay_preset_names() -> list[str]:
    return list(DELAY_PRESETS)


def delay_preset_mapping(name: str) -> dict:
    key = str(name or "BYPASS").strip().upper()
    if key == "AUTO":
        key = "AUTO VOLUME"
    elif key == "AUTO WIDE":
        key = "AUDIO CLARITY"
    return dict(DELAY_PRESETS.get(key, DELAY_PRESETS["BYPASS"]))


def match_delay_preset(delay: dict | None) -> str:
    delay = delay or {}
    for name, spec in DELAY_PRESETS.items():
        if _dyn_close(delay, spec):
            return name
    return "CUSTOM"


PRESETS: list[dict] = [
    _p("Flat", "Unity EQ. Safety limiter only.", [0] * 10),
    _p(
        "Bass Boost",
        "Extra low end for small speakers and earbuds.",
        [7, 6, 4, 2, 0, 0, 0, 0, 0, 0],
        preamp=-2.5,
    ),
    _p(
        "Bass Reduce",
        "Cut rumble and muddy rooms.",
        [-6, -5, -3, -1, 0, 0, 0, 0, 0, 0],
    ),
    _p(
        "Treble Boost",
        "Air and detail without touching the kick.",
        [0, 0, 0, 0, 0, 0, 1, 3, 5, 6],
        preamp=-1.5,
    ),
    _p(
        "V-Shape",
        "Smiley curve: punchy lows, scooped mids, bright highs.",
        [5, 4, 2, 0, -2, -3, -1, 2, 4, 5],
        preamp=-2.0,
    ),
    _p(
        "Vocal",
        "Presence for voices; less boom.",
        [-4, -3, -2, -1, 1, 3, 4, 3, 1, 0],
        preamp=-1.0,
        compressor=_comp(threshold_db=-16, ratio=3.0, attack_ms=12, release_ms=90, makeup_db=1.5),
    ),
    _p(
        "Podcast",
        "Speech-first: high-pass feel, mid lift, gentle glue.",
        [-8, -6, -3, 0, 2, 4, 3, 1, 0, -1],
        preamp=-1.0,
        compressor=_comp(threshold_db=-20, ratio=4.5, attack_ms=8, release_ms=80, makeup_db=3, mode="rms"),
        limiter=_lim(ceiling_db=-1.0, alr=True),
    ),
    _p(
        "Night",
        "Quiet-level listening: heavy compression, less bass.",
        [-3, -3, -2, -1, 1, 2, 2, 1, 0, 0],
        compressor=_comp(threshold_db=-24, ratio=6.0, attack_ms=10, release_ms=150, makeup_db=6, knee_db=8),
        limiter=_lim(ceiling_db=-1.5, alr=True, lookahead_ms=8),
    ),
    _p(
        "Laptop Speakers",
        "Drop sub-bass the cones cannot play; boost upper mids.",
        [-8, -7, -4, -1, 1, 3, 4, 3, 2, 1],
        preamp=-1.0,
        compressor=_comp(threshold_db=-16, ratio=3.5, makeup_db=2),
    ),
    _p(
        "Headphones",
        "Slight warmth and a smooth top — a common Harman-ish tilt.",
        [3, 2, 1, 0, 0, -1, 0, 1, 2, 1],
        preamp=-1.0,
    ),
    _p(
        "Rock",
        "Kick and snare, scooped mud, crunchy highs.",
        [4, 3, 1, -1, -2, 0, 2, 3, 2, 1],
        preamp=-1.5,
        compressor=_comp(threshold_db=-14, ratio=3.0, attack_ms=20, release_ms=100, makeup_db=1, mode="peak"),
    ),
    _p(
        "Electronic",
        "Sub weight, clean mids, shiny hats.",
        [6, 5, 2, 0, -1, -2, 0, 2, 4, 3],
        preamp=-2.5,
        compressor=_comp(threshold_db=-12, ratio=2.5, attack_ms=25, release_ms=80, mode="peak"),
        limiter=_lim(ceiling_db=-0.3, lookahead_ms=6),
    ),
    _p(
        "Classical",
        "Mostly flat with a little air and hall warmth.",
        [1, 1, 0, 0, 0, 0, 0, 1, 2, 2],
    ),
    _p(
        "Jazz",
        "Body on bass and horns, silk on cymbals.",
        [2, 2, 1, 0, 0, 1, 1, 0, 1, 0],
    ),
    _p(
        "Acoustic",
        "Guitar body and vocal clarity.",
        [-2, -1, 0, 1, 2, 2, 1, 2, 1, 0],
        compressor=_comp(threshold_db=-18, ratio=2.5, attack_ms=18, makeup_db=1),
    ),
    _p(
        "Movie",
        "Dialogue lift plus a bit of LFE weight.",
        [4, 3, 1, 0, 1, 3, 4, 2, 1, 0],
        preamp=-1.5,
        compressor=_comp(threshold_db=-20, ratio=3.5, attack_ms=10, release_ms=180, makeup_db=2),
        limiter=_lim(ceiling_db=-1.0, lookahead_ms=8),
    ),
    _p(
        "Loudness",
        "Fletcher-Munson contour for low playback volume.",
        [6, 5, 3, 1, 0, 0, 1, 2, 3, 2],
        preamp=-2.0,
        compressor=_comp(threshold_db=-22, ratio=3.0, makeup_db=2),
    ),
    _p(
        "Warm",
        "Low-mids forward, softer treble.",
        [2, 2, 2, 1, 1, 0, -1, -2, -3, -3],
    ),
    _p(
        "Bright",
        "Forward presence and air.",
        [-1, -1, 0, 0, 0, 1, 2, 4, 5, 5],
        preamp=-2.0,
    ),
    _p(
        "Bluetooth Safe",
        "Tight limiter for wireless clipping and cheap DAC peaks.",
        [0] * 10,
        limiter=_lim(ceiling_db=-2.0, lookahead_ms=8, release_ms=12, alr=False),
        compressor=_comp(enabled=True, threshold_db=-10, ratio=2.0, attack_ms=5, release_ms=50, makeup_db=0),
    ),
    _p(
        "Dance",
        "Club lows, scooped mud, sparkly hats.",
        [6, 5, 2, 0, -2, -2, 0, 2, 4, 4],
        preamp=-2.5,
        compressor=_comp(threshold_db=-14, ratio=3.0, attack_ms=22, release_ms=90, mode="peak"),
        limiter=_lim(ceiling_db=-0.5, lookahead_ms=6),
    ),
    _p(
        "Hip-Hop",
        "Sub and kick forward, mids a little darker.",
        [7, 6, 3, 1, 0, -1, -1, 1, 2, 1],
        preamp=-2.5,
        compressor=_comp(threshold_db=-16, ratio=3.5, attack_ms=25, release_ms=110, makeup_db=1.5, mode="peak"),
    ),
    _p(
        "Piano",
        "Body in the low mids, clear hammer attack.",
        [-2, -1, 1, 2, 1, 0, 1, 2, 1, 0],
        compressor=_comp(threshold_db=-18, ratio=2.5, attack_ms=15, makeup_db=1),
    ),
    _p(
        "TV",
        "Dialogue first, less rumble, safe peaks.",
        [-6, -5, -2, 1, 3, 4, 3, 2, 1, 0],
        preamp=-1.0,
        compressor=_comp(threshold_db=-20, ratio=4.0, attack_ms=8, release_ms=90, makeup_db=2.5),
        limiter=_lim(ceiling_db=-1.0, alr=True),
    ),
    _p(
        "Presence",
        "Forward 2–6 kHz without extra bass.",
        [-1, -1, 0, 0, 0, 1, 3, 4, 2, 1],
        preamp=-1.5,
    ),
    _p(
        "Club",
        "Heavy low end and a bright top for PA.",
        [8, 6, 3, 0, -2, -3, 0, 2, 4, 5],
        preamp=-3.0,
        compressor=_comp(threshold_db=-12, ratio=3.0, attack_ms=20, mode="peak"),
        limiter=_lim(ceiling_db=-0.3, lookahead_ms=6),
    ),
    _p(
        "Spoken",
        "Tight speech band, rumble and hiss cut.",
        [-10, -8, -4, 1, 3, 5, 4, 1, -1, -3],
        preamp=-1.0,
        compressor=_comp(threshold_db=-22, ratio=5.0, attack_ms=6, release_ms=70, makeup_db=3.5),
        limiter=_lim(ceiling_db=-1.0, alr=True),
    ),
    _p(
        "Car",
        "Road-noise contour: more low-mids and presence.",
        [2, 3, 3, 2, 1, 0, 2, 3, 2, 1],
        preamp=-1.5,
        compressor=_comp(threshold_db=-16, ratio=3.0, makeup_db=1.5),
    ),
    _p(
        "House",
        "Four-on-the-floor: kick weight, scooped mud, pumping hats.",
        [7, 6, 3, 0, -3, -2, 0, 2, 4, 5],
        preamp=-2.5,
        compressor=_comp(threshold_db=-14, ratio=3.2, attack_ms=28, release_ms=125, makeup_db=1.5, mode="peak"),
        limiter=_lim(ceiling_db=-0.5, lookahead_ms=6),
    ),
    _p(
        "Trance",
        "Bigger sub, deeper mid scoop, long pump and icy air.",
        [8, 6, 2, -1, -3, -3, 0, 3, 5, 6],
        preamp=-3.0,
        compressor=_comp(threshold_db=-16, ratio=4.0, attack_ms=32, release_ms=180, makeup_db=2.5, knee_db=8, mode="peak"),
        limiter=_lim(ceiling_db=-0.3, lookahead_ms=8),
    ),
    _p(
        "Stadium",
        "Concert PA: sub thump, voice carry, distant rolled air.",
        [5, 4, 1, -2, -1, 2, 4, 3, 1, -1],
        preamp=-2.0,
        compressor=_comp(threshold_db=-20, ratio=2.2, attack_ms=40, release_ms=280, makeup_db=2.0, knee_db=10),
        limiter=_lim(ceiling_db=-1.0, lookahead_ms=10, release_ms=14),
    ),
    _p(
        "Large Hall",
        "Warm bloom, soft presence, slow glue like a long room.",
        [3, 3, 2, 2, 1, 0, 1, 1, 0, -2],
        preamp=-1.5,
        compressor=_comp(threshold_db=-22, ratio=1.8, attack_ms=50, release_ms=350, makeup_db=1.0, knee_db=12),
        limiter=_lim(ceiling_db=-1.2, lookahead_ms=10, release_ms=16),
    ),
    _p(
        "Wooden Box",
        "Small resonant space: boxy low-mids, no sub, damped air.",
        [-6, -4, 3, 5, 4, 1, -1, 0, -2, -4],
        preamp=-1.0,
        compressor=_comp(threshold_db=-12, ratio=5.0, attack_ms=4, release_ms=40, makeup_db=1.0, knee_db=3, mode="peak"),
        limiter=_lim(ceiling_db=-0.8, lookahead_ms=4, attack_ms=2, release_ms=10),
    ),
    _p(
        "Frozen Chamber",
        "Thin body, held compression, ice on the top octave.",
        [-5, -4, -3, -4, -2, 0, 2, 4, 6, 7],
        preamp=-1.5,
        compressor=_comp(threshold_db=-26, ratio=6.0, attack_ms=60, release_ms=450, makeup_db=4.0, knee_db=10),
        limiter=_lim(ceiling_db=-1.5, alr=True, lookahead_ms=10, release_ms=18),
    ),
    _p(
        "Cathedral",
        "Distant and slow: warm nave, soft treble, long decay feel.",
        [2, 2, 1, 2, 1, 0, -1, 0, 1, -1],
        preamp=-1.0,
        compressor=_comp(threshold_db=-24, ratio=2.0, attack_ms=70, release_ms=520, makeup_db=1.5, knee_db=12),
        limiter=_lim(ceiling_db=-1.5, lookahead_ms=12, release_ms=18),
    ),
    _p(
        "Warehouse",
        "Hard walls: industrial lows, mid scoop, aggressive peaks.",
        [6, 5, 2, -1, -3, -2, 1, 4, 3, 2],
        preamp=-2.5,
        compressor=_comp(threshold_db=-13, ratio=4.5, attack_ms=18, release_ms=95, makeup_db=2.0, mode="peak"),
        limiter=_lim(ceiling_db=-0.4, lookahead_ms=6),
    ),
    _p(
        "Arena",
        "Wide PA glue with crowd-carrying presence.",
        [4, 3, 1, -1, 0, 2, 3, 3, 2, 0],
        preamp=-2.0,
        compressor=_comp(threshold_db=-18, ratio=2.8, attack_ms=35, release_ms=220, makeup_db=2.0, knee_db=8),
        limiter=_lim(ceiling_db=-0.8, lookahead_ms=8, release_ms=14),
    ),
]


def preset_names() -> list[str]:
    return [p["name"] for p in PRESETS]


def get_preset(name: str) -> dict:
    key = name.strip().lower()
    for preset in PRESETS:
        if preset["name"].lower() == key:
            return preset
    raise KeyError(f"Unknown preset {name!r}. Try: {', '.join(preset_names())}")


def apply_preset(state: dict, name: str) -> dict:
    preset = get_preset(name)
    state = dict(state)
    state["preset"] = preset["name"]
    state["preamp_db"] = preset["preamp_db"]
    state["bands"] = list(preset["bands"])
    state["compressor"] = dict(preset["compressor"])
    state["limiter"] = dict(preset["limiter"])
    return state
