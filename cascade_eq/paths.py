from __future__ import annotations

import json
import os
from pathlib import Path

from . import SINK_NAME


def xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def xdg_runtime() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/cascade-eq-{os.getuid()}"))


def config_dir() -> Path:
    d = xdg_config() / "cascade-eq"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path() -> Path:
    return config_dir() / "state.json"


def log_path() -> Path:
    return config_dir() / "daemon.log"


def sessions_dir() -> Path:
    music = Path(os.environ.get("XDG_MUSIC_DIR", Path.home() / "Music"))
    d = music / "Cascade EQ" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def socket_path() -> Path:
    d = xdg_runtime()
    d.mkdir(parents=True, exist_ok=True)
    return d / "cascade-eq.sock"


def pid_path() -> Path:
    return xdg_runtime() / "cascade-eq.pid"


def module_id_path() -> Path:
    return config_dir() / "null-sink-module"


def default_state() -> dict:
    from .dsp import empty_bands, CHAIN_STAGES

    return {
        "version": 1,
        "enabled": False,
        "preset": "Flat",
        "profile": "Clean",
        "preamp_db": 0.0,
        "master_db": 0.0,
        "tone": {
            "low_db": 0.0,
            "mid_db": 0.0,
            "high_db": 0.0,
        },
        "tone_preset": "Flat",
        "tone_auto": {"enabled": False},
        "digital": {
            "preset": "bypass",
            "mix": 0.0,
            "wet": 0.0,
            "dry": 1.0,
        },
        "bands": empty_bands(),
        "auto_eq": {"enabled": False, "lift": empty_bands()},
        "compressor": {
            "enabled": False,
            "mode": "rms",
            "threshold_db": -18.0,
            "ratio": 4.0,
            "attack_ms": 15.0,
            "release_ms": 120.0,
            "knee_db": 6.0,
            "makeup_db": 0.0,
            "lookahead_ms": 0.0,
            "mix": 1.0,
            "wet": 1.0,
            "dry": 0.0,
        },
        "limiter": {
            "enabled": True,
            "ceiling_db": -1.0,
            "lookahead_ms": 5.0,
            "attack_ms": 5.0,
            "release_ms": 8.0,
            "alr": False,
        },
        "dynamics_preset": "SAFE",
        "expander": {
            "enabled": False,
            "mode": "rms",
            "em": "down",
            "threshold_db": -40.0,
            "ratio": 4.0,
            "attack_ms": 5.0,
            "release_ms": 80.0,
            "knee_db": 6.0,
            "makeup_db": 0.0,
            "lookahead_ms": 0.0,
            "mix": 1.0,
            "wet": 1.0,
            "dry": 0.0,
        },
        "expander_preset": "OPEN",
        "pre": {
            "enabled": False,
            "hpf_hz": 40.0,
            "slope": "x2",
        },
        "post": {
            "enabled": True,
            "gain_db": 0.0,
            "width": 0.0,
            "balance": 0.0,
            "air_db": 0.0,
            "air_hz": 8000.0,
            "mix": 1.0,
            "wet": 1.0,
            "dry": 0.0,
        },
        "preamp_preset": "FLAT",
        "fx": {
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
            "mix": 1.0,
            "wet": 1.0,
            "dry": 0.0,
        },
        "fx_preset": "BYPASS",
        "delay": {
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
            "master": 1.0,
            "clarity": 0.55,
            "color": 0.4,
            "mix": 1.0,
            "wet": 1.0,
            "dry": 0.0,
        },
        "delay_preset": "BYPASS",
        "blend": {
            "enabled": True,
            "threshold_db": -8.0,
            "ratio": 5.0,
            "attack_ms": 8.0,
            "release_ms": 240.0,
        },
        "ride": {
            "enabled": True,
            "target_db": -18.0,
            "boost_db": 8.0,
            "cut_db": 12.0,
            "gate_db": -48.0,
            "ceiling_db": -1.0,
            "attack_ms": 900.0,
            "release_ms": 220.0,
        },
        "chain_order": list(CHAIN_STAGES),
        "hardware_sink": None,
        "output_role": None,
        "autostart": False,
        "record": {
            "format": "flac",
            "directory": str(Path.home() / "Music" / "Cascade EQ"),
        },
        "session": {
            "last": None,
            "preset": "POP",
            "target_db": -12.0,
            "max_db": -1.0,
            "max_bpm_delta": 12.0,
            "harmonic_order": True,
            "max_energy_step": 2.0,
            "mix_style": "pop",
            "denoise": True,
        },
        "rack_expanded": {
            "310": True,
            "316": False,
            "330": False,
            "340": False,
            "335": False,
            "350": False,
            "355": False,
            "370": False,
        },
        "skin": "1980s",
    }


def _merge(base: dict, incoming: dict) -> dict:
    out = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_state() -> dict:
    path = state_path()
    state = default_state()
    if not path.exists():
        return state
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if not isinstance(data, dict):
        return state
    merged = _merge(state, data)
    saved_ses = data.get("session") if isinstance(data.get("session"), dict) else {}
    if isinstance(merged.get("session"), dict) and "target_db" not in saved_ses:
        merged["session"]["target_db"] = -12.0
        merged["session"]["max_db"] = -1.0
        merged["session"].pop("min_db", None)
    bands = merged.get("bands")
    if not isinstance(bands, list) or len(bands) != 16:
        merged["bands"] = default_state()["bands"]
    else:
        merged["bands"] = [float(x) for x in bands]
    from .dsp import keep_mix, normalize_chain
    from .presets import delay_preset_mapping

    merged["chain_order"] = normalize_chain(merged.get("chain_order"))
    dly_name = str(merged.get("delay_preset") or "").strip().upper()
    dly = merged.get("delay") if isinstance(merged.get("delay"), dict) else {}
    if dly_name in {"AUTO", "AUTO WIDE"} or (
        dly.get("auto_on") and not str(dly.get("enhance") or "").strip()
        and dly_name not in {"NOISE FILTER", "AUDIO CLARITY", "AUTO VOLUME"}
    ):
        mapped = "AUDIO CLARITY" if dly_name == "AUTO WIDE" else "AUTO VOLUME"
        old = dict(dly)
        merged["delay"] = delay_preset_mapping(mapped)
        keep_mix(merged["delay"], old)
        merged["delay_preset"] = mapped
    return merged


def save_state(state: dict) -> None:
    path = state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def sink_name() -> str:
    return SINK_NAME
