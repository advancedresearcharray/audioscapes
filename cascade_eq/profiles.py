"""High-level rack profiles — one intent, every unit set together."""

from __future__ import annotations

from .dsp import interpolate_10, mix_from_step, normalize_chain, set_mix
from .presets import (
    delay_preset_mapping,
    dyn_preset_mapping,
    exp_preset_mapping,
    fx_preset_mapping,
    get_preset,
    match_delay_preset,
    match_dyn_preset,
    match_exp_preset,
    match_fx_preset,
    match_pre_preset,
    pre_preset_mapping,
)


def _step(n: int) -> float:
    return mix_from_step(int(n))


def _eq(bands10: list[float], preamp: float) -> tuple[list[float], float]:
    return interpolate_10(bands10), float(preamp)


def _tone(delay: dict, *, mix: float, master: float = 1.0, clarity: float = 0.55, color: float = 0.4) -> dict:
    delay["master"] = max(0.0, min(1.0, float(master)))
    delay["clarity"] = max(0.0, min(1.0, float(clarity)))
    delay["color"] = max(0.0, min(1.0, float(color)))
    set_mix(delay, mix)
    return delay


def _build(
    *,
    note: str,
    bands: list[float],
    preamp: float,
    pre_name: str,
    dyn_name: str,
    exp_name: str,
    fx_name: str,
    dly_name: str,
    pre_mix: float = 1.0,
    dyn_mix: float = 1.0,
    exp_mix: float = 1.0,
    fx_mix: float = 0.0,
    dly_mix: float = 0.0,
    chain: list[str] | None = None,
    clarity: float | None = None,
    color: float | None = None,
    master: float = 1.0,
    limiter: dict | None = None,
    preamp_db: float | None = None,
) -> dict:
    pre_db, pre, post = pre_preset_mapping(pre_name)
    if preamp_db is not None:
        pre_db = float(preamp_db)
    else:
        pre_db = float(preamp)
    compressor, lim = dyn_preset_mapping(dyn_name)
    if limiter:
        lim.update(limiter)
    expander = exp_preset_mapping(exp_name)
    fx = fx_preset_mapping(fx_name)
    delay = delay_preset_mapping(dly_name)
    set_mix(post, pre_mix)
    set_mix(compressor, dyn_mix)
    set_mix(expander, exp_mix)
    set_mix(fx, fx_mix)
    _tone(
        delay,
        mix=dly_mix,
        master=master,
        clarity=0.55 if clarity is None else clarity,
        color=0.4 if color is None else color,
    )
    return {
        "note": note,
        "preamp_db": pre_db,
        "bands": list(bands),
        "pre": pre,
        "post": post,
        "compressor": compressor,
        "limiter": lim,
        "expander": expander,
        "fx": fx,
        "delay": delay,
        "chain_order": normalize_chain(chain),
        "pre_name": pre_name,
        "dyn_name": dyn_name,
        "exp_name": exp_name,
        "fx_name": fx_name,
        "dly_name": dly_name,
    }


_REMASTER_CHAIN = ["pre", "exp", "eq", "dyn", "fx", "fx2"]
_BASS_CHAIN = ["pre", "eq", "dyn", "exp", "fx", "fx2"]
_QUALITY_CHAIN = ["pre", "exp", "eq", "dyn", "fx", "fx2"]


def _profiles() -> dict[str, dict]:
    flat, _ = _eq([0] * 10, 0.0)
    quality, q_pre = _eq([-3.0, -2.0, -1.0, -1.5, -2.0, 0.5, 1.8, 2.0, 1.5, 0.8], -1.0)
    bass, b_pre = _eq([7.0, 6.0, 3.5, 1.0, -1.5, -1.0, 0.0, 0.5, 1.5, 1.0], -2.5)
    bright, br_pre = _eq([-1.0, -1.0, 0.0, -0.5, -1.0, 0.5, 2.0, 3.5, 5.0, 5.0], -1.5)
    warm, w_pre = _eq([2.5, 2.5, 2.0, 1.5, 1.0, 0.0, -0.5, -1.5, -2.5, -3.0], 0.5)
    remaster, r_pre = _eq([2.0, 1.5, 0.5, -0.5, -1.5, 0.5, 1.5, 2.0, 2.5, 1.5], -1.5)
    louder, l_pre = _eq([3.0, 2.0, 1.0, 0.0, 0.0, 0.0, 0.5, 1.0, 1.5, 1.0], 1.0)
    punch, p_pre = _eq([5.0, 4.0, 1.0, -1.5, -2.0, 0.0, 1.5, 2.5, 2.0, 1.0], -2.0)
    restore, rs_pre = _eq([-4.0, -3.0, -1.0, 0.5, 1.5, 2.0, 2.0, 1.0, -0.5, -2.0], -0.5)
    wide, wd_pre = _eq([0.5, 0.5, 0.0, -1.0, -1.0, 0.0, 1.0, 1.5, 1.0, 0.5], 0.0)
    return {
        "Clean": _build(
            note="Transparent path. Safety limiter only — a known starting point.",
            bands=flat,
            preamp=0.0,
            pre_name="FLAT",
            dyn_name="SAFE",
            exp_name="OPEN",
            fx_name="BYPASS",
            dly_name="BYPASS",
            dyn_mix=_step(1),
            exp_mix=_step(1),
            fx_mix=_step(1),
            dly_mix=_step(1),
        ),
        "Add Headroom": _build(
            note="Drops preamp 4 dB and parks the ceiling at −3 dB so peaks stop kissing 0.",
            bands=flat,
            preamp=-4.0,
            pre_name="RUMBLE",
            dyn_name="SAFE",
            exp_name="OPEN",
            fx_name="BYPASS",
            dly_name="BYPASS",
            dyn_mix=_step(1),
            exp_mix=_step(1),
            fx_mix=_step(1),
            dly_mix=_step(1),
            limiter={"enabled": True, "ceiling_db": -3.0, "lookahead_ms": 8.0, "attack_ms": 4.0, "release_ms": 12.0, "alr": False},
        ),
        "Improve Quality": _build(
            note="De-mud, gentle glue, hiss-down, and audio clarity — the everyday ‘make it better’ chain.",
            bands=quality,
            preamp=q_pre,
            pre_name="STUDIO",
            dyn_name="GLUE",
            exp_name="GENTLE",
            fx_name="NIGHT AIR",
            dly_name="AUDIO CLARITY",
            pre_mix=_step(12),
            dyn_mix=_step(8),
            exp_mix=_step(8),
            fx_mix=_step(5),
            dly_mix=_step(8),
            chain=_QUALITY_CHAIN,
            clarity=0.74,
            color=0.22,
        ),
        "Bass Infused": _build(
            note="Sub and kick forward, mud scooped, punch compressor. FX stays out of the low end.",
            bands=bass,
            preamp=b_pre,
            pre_name="FLAT",
            dyn_name="PUNCH",
            exp_name="OPEN",
            fx_name="BYPASS",
            dly_name="BYPASS",
            dyn_mix=_step(10),
            exp_mix=_step(1),
            fx_mix=_step(1),
            dly_mix=_step(1),
            chain=_BASS_CHAIN,
            limiter={"enabled": True, "ceiling_db": -0.5, "lookahead_ms": 6.0},
        ),
        "Brighter": _build(
            note="Presence and air without extra bass. Soft glue so the top doesn’t turn brittle.",
            bands=bright,
            preamp=br_pre,
            pre_name="AIR",
            dyn_name="SOFT",
            exp_name="OPEN",
            fx_name="ADD AIR",
            dly_name="AUDIO CLARITY",
            dyn_mix=_step(6),
            exp_mix=_step(1),
            fx_mix=_step(6),
            dly_mix=_step(5),
            clarity=0.80,
            color=0.14,
        ),
        "Warmer": _build(
            note="Low-mids forward, softer treble, slow glue. A little vinyl room, not a cave.",
            bands=warm,
            preamp=w_pre,
            pre_name="WARM",
            dyn_name="GLUE",
            exp_name="OPEN",
            fx_name="VINYL",
            dly_name="BYPASS",
            dyn_mix=_step(8),
            exp_mix=_step(1),
            fx_mix=_step(4),
            dly_mix=_step(1),
            color=0.70,
            clarity=0.28,
        ),
        "Remaster": _build(
            note="Full polish: rumble out, gentle gate, studio EQ, glue, clarity, ALR limiter.",
            bands=remaster,
            preamp=r_pre,
            pre_name="STUDIO",
            dyn_name="GLUE",
            exp_name="GENTLE",
            fx_name="NIGHT AIR",
            dly_name="AUDIO CLARITY",
            dyn_mix=_step(9),
            exp_mix=_step(6),
            fx_mix=_step(4),
            dly_mix=_step(7),
            chain=_REMASTER_CHAIN,
            clarity=0.70,
            color=0.26,
            limiter={"enabled": True, "ceiling_db": -0.8, "lookahead_ms": 10.0, "release_ms": 14.0, "alr": True},
        ),
        "Louder": _build(
            note="Broadcast-style density and auto-volume. Louder average, same −0.3 dB ceiling.",
            bands=louder,
            preamp=l_pre,
            pre_name="LOUD",
            dyn_name="BROADCAST",
            exp_name="GENTLE",
            fx_name="BYPASS",
            dly_name="AUTO VOLUME",
            dyn_mix=_step(12),
            exp_mix=_step(5),
            fx_mix=_step(1),
            dly_mix=_step(12),
            limiter={"enabled": True, "ceiling_db": -0.3, "lookahead_ms": 8.0, "alr": True},
        ),
        "Night Listen": _build(
            note="Quiet-level contour: less bass, heavy compression, auto-volume so whispers stay audible.",
            bands=list(get_preset("Night")["bands"]),
            preamp=float(get_preset("Night")["preamp_db"]),
            pre_name="FLAT",
            dyn_name="WHISPER",
            exp_name="GENTLE",
            fx_name="BYPASS",
            dly_name="AUTO VOLUME",
            dyn_mix=_step(12),
            exp_mix=_step(7),
            fx_mix=_step(1),
            dly_mix=_step(11),
        ),
        "Voice": _build(
            note="Speech band, rumble/hiss cut, vocal compressor, noise filter. No echo.",
            bands=list(get_preset("Spoken")["bands"]),
            preamp=float(get_preset("Spoken")["preamp_db"]),
            pre_name="PRESENCE",
            dyn_name="VOCAL",
            exp_name="VOCAL",
            fx_name="HIGH PASS",
            dly_name="NOISE FILTER",
            dyn_mix=_step(11),
            exp_mix=_step(9),
            fx_mix=_step(7),
            dly_mix=_step(12),
        ),
        "Wider": _build(
            note="Stereo image out, light air and clarity. No slap or hall.",
            bands=wide,
            preamp=wd_pre,
            pre_name="WIDE",
            dyn_name="GLUE",
            exp_name="OPEN",
            fx_name="ADD AIR",
            dly_name="AUDIO CLARITY",
            dyn_mix=_step(6),
            exp_mix=_step(1),
            fx_mix=_step(4),
            dly_mix=_step(8),
            clarity=0.62,
            color=0.20,
        ),
        "Punch": _build(
            note="Kick/snare attack, scooped mud, peak compressor. Dry FX so transients stay sharp.",
            bands=punch,
            preamp=p_pre,
            pre_name="FLAT",
            dyn_name="PUNCH",
            exp_name="OPEN",
            fx_name="BYPASS",
            dly_name="BYPASS",
            dyn_mix=_step(12),
            exp_mix=_step(1),
            fx_mix=_step(1),
            dly_mix=_step(1),
            chain=_BASS_CHAIN,
        ),
        "Restore": _build(
            note="Old or noisy sources: rumble out, gate, hiss-down, de-harsh top, soft glue.",
            bands=restore,
            preamp=rs_pre,
            pre_name="RUMBLE",
            dyn_name="SOFT",
            exp_name="GATE",
            fx_name="HIGH PASS",
            dly_name="NOISE FILTER",
            dyn_mix=_step(7),
            exp_mix=_step(9),
            fx_mix=_step(6),
            dly_mix=_step(12),
            chain=_QUALITY_CHAIN,
            limiter={"enabled": True, "ceiling_db": -1.5, "lookahead_ms": 8.0, "alr": True},
        ),
        "Cinema": _build(
            note="Dialogue lift, a bit of LFE weight, movie glue, a hint of hall.",
            bands=list(get_preset("Movie")["bands"]),
            preamp=float(get_preset("Movie")["preamp_db"]),
            pre_name="STUDIO",
            dyn_name="RADIO",
            exp_name="GENTLE",
            fx_name="HALL",
            dly_name="BYPASS",
            dyn_mix=_step(9),
            exp_mix=_step(5),
            fx_mix=_step(3),
            dly_mix=_step(1),
        ),
        "Translate": _build(
            note="Small speakers and earbuds: dump sub they cannot play, lift mids they can.",
            bands=list(get_preset("Laptop Speakers")["bands"]),
            preamp=float(get_preset("Laptop Speakers")["preamp_db"]),
            pre_name="SUB",
            dyn_name="SOFT",
            exp_name="OPEN",
            fx_name="HIGH PASS",
            dly_name="BYPASS",
            dyn_mix=_step(8),
            exp_mix=_step(1),
            fx_mix=_step(7),
            dly_mix=_step(1),
        ),
    }


PROFILES: dict[str, dict] = _profiles()

_ALIASES = {
    "headroom": "Add Headroom",
    "add headroom": "Add Headroom",
    "quality": "Improve Quality",
    "improve quality": "Improve Quality",
    "better": "Improve Quality",
    "bass": "Bass Infused",
    "bass infused": "Bass Infused",
    "sub": "Bass Infused",
    "bright": "Brighter",
    "brighter": "Brighter",
    "make it brighter": "Brighter",
    "warm": "Warmer",
    "warmer": "Warmer",
    "make it warmer": "Warmer",
    "remaster": "Remaster",
    "master": "Remaster",
    "loud": "Louder",
    "louder": "Louder",
    "night": "Night Listen",
    "night listen": "Night Listen",
    "voice": "Voice",
    "speech": "Voice",
    "podcast": "Voice",
    "wide": "Wider",
    "wider": "Wider",
    "punch": "Punch",
    "restore": "Restore",
    "denoise": "Restore",
    "cinema": "Cinema",
    "movie": "Cinema",
    "translate": "Translate",
    "laptop": "Translate",
    "clean": "Clean",
    "flat": "Clean",
    "bypass": "Clean",
}


def profile_names() -> list[str]:
    return list(PROFILES)


def resolve_profile(name: str) -> str:
    raw = str(name or "").strip()
    if raw in PROFILES:
        return raw
    key = raw.lower().replace("_", " ").replace("-", " ")
    if key in _ALIASES:
        return _ALIASES[key]
    for official in PROFILES:
        if official.lower() == key:
            return official
    raise KeyError(f"Unknown profile {name!r}. Try: {', '.join(profile_names())}")


def get_profile(name: str) -> dict:
    return PROFILES[resolve_profile(name)]


def apply_profile(state: dict, name: str) -> dict:
    spec = get_profile(name)
    official = resolve_profile(name)
    state = dict(state)
    state["profile"] = official
    state["preset"] = "Custom"
    state["preamp_db"] = spec["preamp_db"]
    state["bands"] = list(spec["bands"])
    state["pre"] = dict(spec["pre"])
    state["post"] = dict(spec["post"])
    state["compressor"] = dict(spec["compressor"])
    state["limiter"] = dict(spec["limiter"])
    state["expander"] = dict(spec["expander"])
    state["fx"] = dict(spec["fx"])
    state["delay"] = dict(spec["delay"])
    state["chain_order"] = list(spec["chain_order"])
    state["preamp_preset"] = match_pre_preset(spec["preamp_db"], spec["pre"], spec["post"])
    state["dynamics_preset"] = match_dyn_preset(spec["compressor"], spec["limiter"])
    state["expander_preset"] = match_exp_preset(spec["expander"])
    state["fx_preset"] = match_fx_preset(spec["fx"])
    state["delay_preset"] = match_delay_preset(spec["delay"])
    return state
