"""Background DSP engine: virtual sink + LSP LV2 chain in GStreamer."""

from __future__ import annotations

import array
import fcntl
import json
import math
import os
import signal
import socket
import sys
import threading
import time
import traceback
import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

from . import APP_NAME, SINK_NAME
from .meters import OutputMeters
from .dsp import (
    CHAIN_STAGES,
    blend_scale,
    clamp_master_db,
    db_to_band_gain,
    db_to_preamp_gain,
    default_blend,
    default_ride,
    default_tone,
    knee_db_to_gain,
    limiter_ceiling_to_gain,
    mix_levels,
    mix_eq_lifts,
    normalize_blend,
    normalize_chain,
    normalize_ride,
    normalize_tone,
    normalize_tone_auto,
    pair_auto_from_rta,
    scale_tone,
    threshold_db_to_gain,
)
from .paths import config_dir, load_state, pid_path, save_state, socket_path
from .record import build_record_bin, format_or_raise, resolve_output
from .pulse import (
    PulseError,
    current_output_role,
    default_sink,
    load_null_sink,
    normalize_output_role,
    output_inventory,
    pick_hardware_sink,
    prefer_a2dp,
    route_playback_to_hardware,
    playback_is_routed,
    resolve_output_role,
    set_default_sink,
    set_sink_port,
    sink_exists,
    sink_layout,
    unload_null_sink,
)

EQ_FACTORY = "lsp-plug-in-plugins-lv2-graph-equalizer-x16-stereo"
COMP_FACTORY = "lsp-plug-in-plugins-lv2-compressor-stereo"
LIM_FACTORY = "lsp-plug-in-plugins-lv2-limiter-stereo"
FILTER_FACTORY = "lsp-plug-in-plugins-lv2-filter-stereo"
EXP_FACTORY = "lsp-plug-in-plugins-lv2-expander-stereo"
WIDTH_FACTORY = "ladspa-matrix-spatialiser-1422-so-matrixspatialiser"
LCR_DELAY_FACTORY = "ladspa-lcr-delay-1436-so-lcrdelay"
REV_DELAY_FACTORY = "ladspa-revdelay-1605-so-revdelay"
RINGMOD_FACTORY = "ladspa-ringmod-1188-so-ringmod-1i1o1l"
ALIAS_FACTORY = "ladspa-alias-1407-so-alias"
CHEB_FACTORY = "ladspa-chebstortion-1430-so-chebstortion"
SIFTER_FACTORY = "ladspa-sifter-1210-so-sifter"

DSP_STATE_KEYS = (
    "preamp_db",
    "bands",
    "compressor",
    "limiter",
    "expander",
    "pre",
    "post",
    "fx",
    "delay",
    "preset",
    "dynamics_preset",
    "expander_preset",
    "preamp_preset",
    "fx_preset",
    "delay_preset",
    "profile",
    "blend",
    "ride",
    "master_db",
    "tone",
    "tone_auto",
    "tone_preset",
    "digital",
    "chain_order",
    "enabled",
    "hardware_sink",
    "output_role",
    "record",
)


def _log(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")


def _set(el, name: str, value) -> None:
    try:
        el.set_property(name, value)
    except Exception as exc:  # noqa: BLE001 — LV2 enum types vary
        _log(f"property {el.get_name()}.{name}={value!r} failed: {exc}")


def _set_pair(pair, name: str, value) -> None:
    if not pair:
        return
    for el in pair:
        _set(el, name, value)


def _apply_tone(engine, raw) -> None:
    spec = normalize_tone(raw)

    def one(el, ftype: str, hz: float, db: float, q: float | None = None, slope: str = "x2") -> None:
        if el is None:
            return
        on = abs(float(db)) >= 0.5
        _set(el, "enabled", on)
        _set(el, "ft", ftype)
        _set(el, "fm", "RLC (BT)")
        _set(el, "s", slope)
        _set(el, "f", hz)
        _set(el, "g", db_to_band_gain(db if on else 0.0))
        _set(el, "g-in", 1.0)
        _set(el, "g-out", 1.0)
        if q is not None:
            _set(el, "q", q)

    one(engine.tone_low, "Lo-shelf", 140.0, spec["low_db"])
    one(engine.tone_mid, "Bell", 1000.0, spec["mid_db"], q=0.85)
    one(engine.tone_high, "Hi-shelf", 8000.0, spec["high_db"])


def _apply_digital(engine, raw) -> None:
    from .dsp import db_to_band_gain, db_to_gain, mix_levels
    from .presets import digital_preset_mapping, normalize_digital

    rec = normalize_digital(raw)
    spec = digital_preset_mapping(rec["preset"])
    on = rec["preset"] != "bypass"
    if engine.de_low is None:
        return
    dry, wet = mix_levels(rec) if on else (1.0, 0.0)
    if engine.de_dry_amp is not None:
        _set(engine.de_dry_amp, "amplification", dry)
        _set(engine.de_wet_amp, "amplification", wet)

    def filt(el, enabled: bool, ftype: str, hz: float, db: float = 0.0, q: float | None = None, slope: str = "x2") -> None:
        if el is None:
            return
        shaped = ftype in {"Hi-pass", "Lo-pass"}
        live = bool(on and enabled and (shaped or abs(float(db)) >= 0.1))
        _set(el, "enabled", live)
        _set(el, "ft", ftype)
        _set(el, "fm", "RLC (BT)")
        _set(el, "s", slope)
        _set(el, "f", max(20.0, min(16000.0, float(hz))))
        _set(el, "g", db_to_band_gain(db if live and not shaped else 0.0))
        _set(el, "g-in", 1.0)
        _set(el, "g-out", 1.0)
        if q is not None:
            _set(el, "q", q)

    filt(
        engine.de_low,
        bool(spec.get("low_on")),
        str(spec.get("low_ft") or "Lo-shelf"),
        float(spec.get("low_hz", 110)),
        float(spec.get("low_db", 0)),
        slope=str(spec.get("low_slope") or "x2"),
    )
    filt(
        engine.de_mid,
        bool(spec.get("mid_on")),
        str(spec.get("mid_ft") or "Bell"),
        float(spec.get("mid_hz", 1000)),
        float(spec.get("mid_db", 0)),
        q=float(spec.get("mid_q", 0.85)),
    )
    filt(
        engine.de_high,
        bool(spec.get("high_on")),
        str(spec.get("high_ft") or "Hi-shelf"),
        float(spec.get("high_hz", 8000)),
        float(spec.get("high_db", 0)),
        q=float(spec.get("mid_q", 0.85)) if str(spec.get("high_ft") or "") == "Bell" else None,
    )
    width = float(spec.get("width", 0.0) if on else 0.0)
    _set(engine.de_width, "width", int(round(max(-1.0, min(1.0, width)) * 256.0)))
    echo_on = on and bool(spec.get("echo_on"))
    delay_ms = float(spec.get("echo_delay_ms", 90) if echo_on else 90.0)
    delay_ns = int(max(1, min(1_000_000_000, delay_ms * 1_000_000)))
    _set(engine.de_echo, "delay", delay_ns)
    _set(engine.de_echo, "intensity", max(0.0, min(1.0, float(spec.get("echo_intensity", 0.18)) if echo_on else 0.0)))
    _set(
        engine.de_echo,
        "feedback",
        max(0.0, min(0.85, float(spec.get("echo_feedback", 0.10)) if echo_on else 0.0)),
    )
    room_on = on and bool(spec.get("room_on"))
    _set(engine.de_verb, "level", max(0.0, min(1.0, float(spec.get("room_level", 0.14)) if room_on else 0.0)))
    _set(engine.de_verb, "room-size", max(0.0, min(1.0, float(spec.get("room_size", 0.36)))))
    _set(engine.de_verb, "damping", max(0.0, min(1.0, float(spec.get("room_damping", 0.32)))))
    gain_db = float(spec.get("gain_db", 0.0) if on and rec["preset"] != "bypass" else 0.0)
    _set(engine.de_makeup, "amplification", max(0.25, min(4.0, db_to_gain(gain_db))))
    _set(engine.de_makeup, "clipping-method", "clip")


def apply_dsp(engine, state: dict) -> None:
    eq, comp, lim = engine.eq, engine.comp, engine.lim
    _set(eq, "enabled", True)
    _set(eq, "g-in", db_to_preamp_gain(float(state.get("preamp_db", 0))))
    bands = state.get("bands") or [0.0] * 16
    auto = state.get("auto_eq") or {}
    auto_on = bool(auto.get("enabled"))
    lifts = auto.get("lift") if auto_on else None
    for i, db in enumerate(mix_eq_lifts(bands, lifts)[:16]):
        _set(eq, f"g-{i}", db_to_band_gain(float(db)))
    engine.eq_bands = [float(v) for v in (list(bands) + [0.0] * 16)[:16]]
    engine.auto_eq_on = auto_on
    if auto_on:
        engine.auto_eq_lifts = [max(0.0, float(v)) for v in (list(auto.get("lift") or []) + [0.0] * 16)[:16]]
    else:
        engine.auto_eq_lifts = [0.0] * 16

    c = state.get("compressor") or {}
    _set(comp, "enabled", True if auto_on else bool(c.get("enabled")))
    mode = str(c.get("mode", "rms")).lower()
    _set(comp, "scm", "RMS" if auto_on or mode == "rms" else "Peak")
    if auto_on:
        _set(comp, "al", threshold_db_to_gain(-22.0))
        _set(comp, "cr", 2.8)
        _set(comp, "at", 22.0)
        _set(comp, "rt", 220.0)
        _set(comp, "kn", knee_db_to_gain(8.0))
        _set(comp, "mk", db_to_preamp_gain(3.0))
        _set(comp, "sla", 4.0)
        if engine.dyn_dry_amp is not None:
            _set(engine.dyn_dry_amp, "amplification", 0.0)
            _set(engine.dyn_wet_amp, "amplification", 1.0)
    else:
        _set(comp, "al", threshold_db_to_gain(float(c.get("threshold_db", -18))))
        _set(comp, "cr", max(1.0, min(100.0, float(c.get("ratio", 4)))))
        _set(comp, "at", max(0.0, min(2000.0, float(c.get("attack_ms", 15)))))
        _set(comp, "rt", max(0.0, min(5000.0, float(c.get("release_ms", 120)))))
        _set(comp, "kn", knee_db_to_gain(float(c.get("knee_db", 6))))
        _set(comp, "mk", db_to_preamp_gain(float(c.get("makeup_db", 0))))
        _set(comp, "sla", max(0.0, min(20.0, float(c.get("lookahead_ms", 0)))))
        dyn_dry, dyn_wet = mix_levels(c)
        if engine.dyn_dry_amp is not None:
            _set(engine.dyn_dry_amp, "amplification", dyn_dry)
            _set(engine.dyn_wet_amp, "amplification", dyn_wet)

    limst = state.get("limiter") or {}
    _set(lim, "enabled", bool(limst.get("enabled", True)))
    _set(lim, "th", limiter_ceiling_to_gain(float(limst.get("ceiling_db", -1))))
    _set(lim, "lk", max(0.1, min(20.0, float(limst.get("lookahead_ms", 5)))))
    _set(lim, "at", max(0.25, min(20.0, float(limst.get("attack_ms", 5)))))
    _set(lim, "rt", max(0.25, min(20.0, float(limst.get("release_ms", 8)))))
    _set(lim, "alr", bool(limst.get("alr", False)))
    _set(lim, "boost", False)

    pre = state.get("pre") or {}
    pre_on = False if auto_on else bool(pre.get("enabled"))
    _set(engine.pre, "enabled", pre_on)
    _set(engine.pre, "ft", "Hi-pass")
    _set(engine.pre, "fm", "RLC (BT)")
    _set(engine.pre, "s", str(pre.get("slope", "x2")))
    _set(engine.pre, "f", max(10.0, min(400.0, float(pre.get("hpf_hz", 40)))))
    _set(engine.pre, "g-in", 1.0)
    _set(engine.pre, "g-out", 1.0)

    post = state.get("post") or {}
    post_on = bool(post.get("enabled", True))
    air_db = float(post.get("air_db", 0))
    _set(engine.postf, "enabled", post_on and abs(air_db) >= 0.1)
    _set(engine.postf, "ft", "Hi-shelf")
    _set(engine.postf, "fm", "RLC (BT)")
    _set(engine.postf, "s", "x1")
    _set(engine.postf, "f", max(2000.0, min(16000.0, float(post.get("air_hz", 8000)))))
    _set(engine.postf, "g", db_to_band_gain(air_db if post_on else 0.0))
    gain_db = float(post.get("gain_db", 0)) if post_on else 0.0
    engine.post_gain_db = gain_db
    _set(engine.amp, "clipping-method", "clip")
    bal = float(post.get("balance", 0)) if post_on else 0.0
    _set(engine.pan, "panorama", max(-1.0, min(1.0, bal)))
    width = float(post.get("width", 0.0)) if post_on else 0.0
    width = max(-1.0, min(1.0, width))
    # LADSPA Matrix Spatialiser: 0 is unchanged stereo; ±512 is extreme.
    _set(engine.widener, "width", int(round(width * 256.0)))
    post_dry, post_wet = mix_levels(post)
    engine.post_dry_target = post_dry
    engine.post_wet_target = post_wet
    if engine.post_dry_amp is not None:
        _set(engine.post_dry_amp, "amplification", post_dry)
        _set(engine.post_wet_amp, "amplification", post_wet)
    if engine.pre_dry_amp is not None:
        _set(engine.pre_dry_amp, "amplification", post_dry)
        _set(engine.pre_wet_amp, "amplification", post_wet)

    exp = state.get("expander") or {}
    if engine.exp is not None:
        _set(engine.exp, "enabled", False if auto_on else bool(exp.get("enabled")))
        mode = str(exp.get("mode", "rms")).lower()
        _set(engine.exp, "scm", "RMS" if mode == "rms" else "Peak")
        em = str(exp.get("em", "down")).lower()
        _set(engine.exp, "em", "Up" if em == "up" else "Down")
        _set(engine.exp, "al", threshold_db_to_gain(float(exp.get("threshold_db", -40))))
        _set(engine.exp, "er", max(1.0, min(100.0, float(exp.get("ratio", 4)))))
        _set(engine.exp, "at", max(0.0, min(2000.0, float(exp.get("attack_ms", 5)))))
        _set(engine.exp, "rt", max(0.0, min(5000.0, float(exp.get("release_ms", 80)))))
        _set(engine.exp, "kn", knee_db_to_gain(float(exp.get("knee_db", 6))))
        _set(engine.exp, "mk", db_to_preamp_gain(float(exp.get("makeup_db", 0))))
        _set(engine.exp, "sla", max(0.0, min(20.0, float(exp.get("lookahead_ms", 0)))))
        exp_dry, exp_wet = mix_levels(exp)
        if engine.exp_dry_amp is not None:
            _set(engine.exp_dry_amp, "amplification", exp_dry)
            _set(engine.exp_wet_amp, "amplification", exp_wet)

    fx = state.get("fx") or {}
    if engine.fx_hpf is not None:
        fx_dry, fx_wet = mix_levels(fx)
        engine.fx_dry_target = fx_dry
        engine.fx_wet_target = fx_wet
        if engine.fx_dry_amp is not None:
            _set(engine.fx_dry_amp, "amplification", fx_dry)
            _set(engine.fx_wet_amp, "amplification", fx_wet)
        hpf_on = bool(fx.get("hpf_on"))
        _set(engine.fx_hpf, "enabled", hpf_on)
        _set(engine.fx_hpf, "ft", "Hi-pass")
        _set(engine.fx_hpf, "fm", "RLC (BT)")
        _set(engine.fx_hpf, "s", str(fx.get("hpf_slope", "x2")))
        _set(engine.fx_hpf, "f", max(20.0, min(16000.0, float(fx.get("hpf_hz", 80)))))
        _set(engine.fx_hpf, "g-in", 1.0)
        _set(engine.fx_hpf, "g-out", 1.0)
        lpf_on = bool(fx.get("lpf_on"))
        _set(engine.fx_lpf, "enabled", lpf_on)
        _set(engine.fx_lpf, "ft", "Lo-pass")
        _set(engine.fx_lpf, "fm", "RLC (BT)")
        _set(engine.fx_lpf, "s", str(fx.get("lpf_slope", "x2")))
        _set(engine.fx_lpf, "f", max(200.0, min(16000.0, float(fx.get("lpf_hz", 8000)))))
        _set(engine.fx_lpf, "g-in", 1.0)
        _set(engine.fx_lpf, "g-out", 1.0)
        air_db = float(fx.get("air_db", 0))
        air_on = bool(fx.get("air_on")) and abs(air_db) >= 0.1
        _set(engine.fx_air, "enabled", air_on)
        _set(engine.fx_air, "ft", "Hi-shelf")
        _set(engine.fx_air, "fm", "RLC (BT)")
        _set(engine.fx_air, "s", "x1")
        _set(engine.fx_air, "f", max(2000.0, min(16000.0, float(fx.get("air_hz", 10000)))))
        _set(engine.fx_air, "g", db_to_band_gain(air_db if air_on else 0.0))
        echo_on = bool(fx.get("echo_on"))
        delay_ns = int(max(1, min(1_000_000_000, float(fx.get("echo_delay_ms", 280)) * 1_000_000)))
        _set(engine.fx_echo, "delay", delay_ns)
        _set(engine.fx_echo, "intensity", max(0.0, min(1.0, float(fx.get("echo_intensity", 0.28)) if echo_on else 0.0)))
        _set(engine.fx_echo, "feedback", max(0.0, min(0.85, float(fx.get("echo_feedback", 0.22)) if echo_on else 0.0)))
        room_on = bool(fx.get("room_on"))
        _set(engine.fx_verb, "level", max(0.0, min(1.0, float(fx.get("room_level", 0.18)) if room_on else 0.0)))
        _set(engine.fx_verb, "room-size", max(0.0, min(1.0, float(fx.get("room_size", 0.45)))))
        _set(engine.fx_verb, "damping", max(0.0, min(1.0, float(fx.get("room_damping", 0.25)))))

    dly = state.get("delay") or {}
    dly_dry, dly_wet = mix_levels(dly)
    pong = bool(dly.get("pong_on"))
    rev = bool(dly.get("rev_on"))
    chop = bool(dly.get("chop_on"))
    crush = bool(dly.get("crush_on"))
    sift = bool(dly.get("sift_on"))
    engine.dly_auto = bool(dly.get("auto_on"))
    engine.dly_enhance = str(dly.get("enhance") or "").strip().lower()
    engine.dly_mix = dly_wet
    engine.dly_master = max(0.0, min(1.0, float(dly.get("master", 1.0))))
    clarity_k = max(0.0, min(1.0, float(dly.get("clarity", 0.55))))
    color_k = max(0.0, min(1.0, float(dly.get("color", 0.4))))
    clarity_on = clarity_k > 0.04 and engine.dly_enhance != "noise"
    color_on = color_k > 0.04 and engine.dly_enhance != "noise"
    special = rev or chop or crush or sift
    if engine.dly_auto:
        engine.dly_dry_target = 1.0
        if engine.dly_enhance == "noise":
            engine.dly_wet_target = 0.0
            engine.dly_spec_target = 0.0
        else:
            wet = dly_wet if pong or engine.dly_enhance == "clarity" else 0.0
            if clarity_on:
                wet = max(wet, min(1.2, dly_wet * (0.55 + 0.70 * clarity_k)))
            engine.dly_wet_target = wet
            engine.dly_spec_target = dly_wet if special else 0.0
    else:
        engine.dly_dry_target = dly_dry
        engine.dly_wet_target = dly_wet if pong or not special else 0.0
        engine.dly_spec_target = dly_wet if special else 0.0
    if engine.dly_auto:
        engine.apply_auto_fx()
    else:
        engine.dly_gap = 0.0
        engine._apply_dly_amps(engine.dly_dry_target, engine.dly_wet_target, engine.dly_spec_target)
    engine.blend = normalize_blend(state.get("blend"))
    engine.ride = normalize_ride(state.get("ride"))
    engine.tone_auto_on = bool(normalize_tone_auto(state.get("tone_auto")).get("enabled"))
    engine.tone_target = normalize_tone(state.get("tone"))
    engine.master_target = clamp_master_db(state.get("master_db", 0.0))
    engine.apply_tone_auto()
    _apply_digital(engine, state.get("digital"))
    engine.apply_blend_comp()
    engine.apply_wave_ride()
    if engine.dly_lcr is not None:
        _set(engine.dly_lcr, "dry-wet-level", 1.0 if pong or clarity_on or engine.dly_enhance == "clarity" else 0.0)
        l_ms = max(0.0, min(2700.0, float(dly.get("l_delay_ms", 0))))
        r_ms = max(0.0, min(2700.0, float(dly.get("r_delay_ms", 375))))
        spread = max(0.0, min(50.0, float(dly.get("spread", 40))))
        high_damp = max(0.0, min(100.0, float(dly.get("high_damp", 25))))
        low_damp = max(0.0, min(100.0, float(dly.get("low_damp", 40))))
        feedback = max(-100.0, min(100.0, float(dly.get("feedback", 28))))
        l_level = max(0.0, min(50.0, float(dly.get("l_level", 50))))
        r_level = max(0.0, min(50.0, float(dly.get("r_level", 50))))
        c_level = max(0.0, min(50.0, float(dly.get("c_level", 0))))
        c_ms = max(0.0, min(2700.0, float(dly.get("c_delay_ms", 0))))
        if clarity_on or engine.dly_enhance == "clarity":
            # Wider, longer space; light damp so it stays audible without frying.
            l_ms = 22.0 + 28.0 * clarity_k
            r_ms = 38.0 + 42.0 * clarity_k
            c_ms = 28.0 + 22.0 * clarity_k
            spread = 30.0 + 20.0 * clarity_k
            high_damp = 8.0 + 26.0 * clarity_k + 22.0 * color_k
            low_damp = 14.0 + 58.0 * color_k
            feedback = (12.0 + 22.0 * clarity_k) * (1.0 - 0.45 * color_k)
            l_level = 50.0
            r_level = 50.0
            c_level = 10.0 + 22.0 * clarity_k
        elif engine.dly_auto and engine.dly_enhance not in {"noise", "level"}:
            if l_ms < 80.0:
                l_ms = 180.0
            if r_ms < 80.0:
                r_ms = 320.0
        _set(engine.dly_lcr, "l-delay", l_ms)
        _set(engine.dly_lcr, "r-delay", r_ms)
        _set(engine.dly_lcr, "c-delay", c_ms)
        _set(engine.dly_lcr, "l-level", l_level)
        _set(engine.dly_lcr, "r-level", r_level)
        _set(engine.dly_lcr, "c-level", c_level)
        _set(engine.dly_lcr, "feedback", feedback)
        _set(engine.dly_lcr, "spread", spread)
        _set(engine.dly_lcr, "high-damp", high_damp)
        _set(engine.dly_lcr, "low-damp", low_damp)
    _set_pair(engine.dly_rev, "delay-time", max(0.0, min(5.0, float(dly.get("rev_time", 0.42)) if rev else 0.0)))
    _set_pair(engine.dly_rev, "wet-level", -6.0 if rev else -70.0)
    _set_pair(engine.dly_rev, "dry-level", -70.0 if rev else 0.0)
    _set_pair(engine.dly_rev, "feedback", max(0.0, min(1.0, float(dly.get("rev_feedback", 0.18)) if rev else 0.0)))
    _set_pair(engine.dly_rev, "crossfade-samples", 1250)
    _set_pair(engine.dly_chop, "frequency", max(1.0, min(1000.0, float(dly.get("chop_hz", 8)))))
    _set_pair(engine.dly_chop, "modulation-depth", max(0.0, min(2.0, float(dly.get("chop_depth", 1.0)) if chop else 0.0)))
    square = bool(dly.get("chop_square", True))
    _set_pair(engine.dly_chop, "square-level", 1.0 if square else 0.0)
    _set_pair(engine.dly_chop, "sine-level", 0.0 if square else 1.0)
    _set_pair(engine.dly_chop, "sawtooth-level", 0.0)
    _set_pair(engine.dly_chop, "triangle-level", 0.0)
    _set_pair(engine.dly_crush, "aliasing-level", max(0.0, min(1.0, float(dly.get("alias", 0.55)) if crush else 0.0)))
    _set_pair(engine.dly_cheb, "distortion", max(0.0, min(3.0, float(dly.get("cheb", 0.0)) if crush else 0.0)))
    _set_pair(engine.dly_sift, "sift-size", max(1.0, min(1000.0, float(dly.get("sift", 28.0)) if sift else 1.0)))


class Engine:
    def __init__(self) -> None:
        self.pipeline: Gst.Pipeline | None = None
        self.eq = None
        self.comp = None
        self.lim = None
        self.exp = None
        self.pre = None
        self.postf = None
        self.tone_low = None
        self.tone_mid = None
        self.tone_high = None
        self.de_low = None
        self.de_mid = None
        self.de_high = None
        self.de_width = None
        self.de_echo = None
        self.de_verb = None
        self.de_makeup = None
        self.de_dry_amp = None
        self.de_wet_amp = None
        self.fx_hpf = None
        self.fx_lpf = None
        self.fx_air = None
        self.fx_echo = None
        self.fx_verb = None
        self.fx_dry_amp = None
        self.fx_wet_amp = None
        self.dly_lcr = None
        self.dly_rev = None
        self.dly_chop = None
        self.dly_crush = None
        self.dly_cheb = None
        self.dly_sift = None
        self.dly_dry_amp = None
        self.dly_wet_amp = None
        self.dly_spec_amp = None
        self.dly_tap = None
        self.dly_auto = False
        self.dly_enhance = ""
        self.dly_mix = 0.0
        self.dly_master = 1.0
        self.dly_env = 0.04
        self.dly_avg = 0.04
        self.dly_gap = 0.0
        self.dly_dry_target = 1.0
        self.dly_wet_target = 0.0
        self.dly_spec_target = 0.0
        self.chain_order: list[str] = list(CHAIN_STAGES)
        self.exp_dry_amp = None
        self.exp_wet_amp = None
        self.dyn_dry_amp = None
        self.dyn_wet_amp = None
        self.pre_dry_amp = None
        self.pre_wet_amp = None
        self.post_dry_amp = None
        self.post_wet_amp = None
        self.fx_dry_target = 1.0
        self.fx_wet_target = 0.0
        self.post_dry_target = 0.0
        self.post_wet_target = 1.0
        self.dly_dry_live = 1.0
        self.dly_wet_live = 0.0
        self.dly_spec_live = 0.0
        self.blend = default_blend()
        self.blend_gr = 0.0
        self.blend_gr_db = 0.0
        self.ride = default_ride()
        self.ride_db = 0.0
        self.tone_auto_on = False
        self.tone_scale = 1.0
        self.tone_target = default_tone()
        self.tone_live = dict(default_tone())
        self.master_target = 0.0
        self._tone_beat_due = 0.0
        self.auto_eq_on = False
        self.auto_eq_lifts = [0.0] * 16
        self.eq_bands = [0.0] * 16
        self.auto_intent = "full"
        self._intent_votes = 0
        self._auto_want_tone = None
        self._auto_want_gain = 0.0
        self._auto_want_lifts = None
        self._rta_until = 0.0
        self.post_gain_db = 0.0
        self.master_db = 0.0
        self.amp = None
        self.pan = None
        self.widener = None
        self.pw_sink = None
        self.hardware_sink: str | None = None
        self.processing = False
        self.meters = OutputMeters()
        self.rec_bin = None
        self.rec_tee_pad = None
        self.rec_path: str | None = None
        self.rec_format: str | None = None
        self.rec_started: float = 0.0
        self.rec_lossy = False

    def _make(self, factory: str, name: str):
        el = Gst.ElementFactory.make(factory, name)
        if el is None:
            raise RuntimeError(
                f"GStreamer element {factory} is missing. "
                "Install gstreamer1.0-plugins-bad and lsp-plugins-lv2."
            )
        return el

    def _link(self, a, b) -> None:
        if not a.link(b):
            raise RuntimeError(f"Failed to link {a.get_name()} → {b.get_name()}")

    def _mix_pair(self, prefix: str):
        tee = self._make("tee", f"{prefix}_tee")
        qd = self._make("queue", f"{prefix}_qd")
        qw = self._make("queue", f"{prefix}_qw")
        ad = self._make("audioamplify", f"{prefix}_dry")
        aw = self._make("audioamplify", f"{prefix}_wet")
        mix = self._make("adder", f"{prefix}_mix")
        for q in (qd, qw):
            q.set_property("max-size-buffers", 3)
            q.set_property("max-size-time", 0)
            q.set_property("max-size-bytes", 0)
            q.set_property("leaky", 2)
        for amp in (ad, aw):
            amp.set_property("clipping-method", "clip")
            amp.set_property("amplification", 1.0)
        return tee, qd, ad, qw, aw, mix

    def _mono_ladspa(self, factory: str, name: str):
        """Mono LADSPA with convert in/out. Keep off the stereo ping-pong path."""
        bin_ = Gst.Bin.new(name)
        cin = self._make("audioconvert", f"{name}_cin")
        plug = self._make(factory, f"{name}_p")
        cout = self._make("audioconvert", f"{name}_cout")
        caps = self._make("capsfilter", f"{name}_caps")
        caps.set_property(
            "caps",
            Gst.Caps.from_string("audio/x-raw,format=F32LE,channels=2,rate=48000,layout=interleaved"),
        )
        for el in (cin, plug, cout, caps):
            bin_.add(el)
        if not cin.link(plug) or not plug.link(cout) or not cout.link(caps):
            raise RuntimeError(f"Failed to link {name}")
        sink_pad = Gst.GhostPad.new("sink", cin.get_static_pad("sink"))
        src_pad = Gst.GhostPad.new("src", caps.get_static_pad("src"))
        sink_pad.set_active(True)
        src_pad.set_active(True)
        bin_.add_pad(sink_pad)
        bin_.add_pad(src_pad)
        return bin_, (plug,)

    def build(self, hardware: str, state: dict | None = None) -> None:
        self.stop_pipeline()
        self.chain_order = normalize_chain((state or {}).get("chain_order"))
        pipeline = Gst.Pipeline.new("cascade")
        src = self._make("pipewiresrc", "src")
        q1 = self._make("queue", "q1")
        convert_in = self._make("audioconvert", "convert_in")
        resample = self._make("audioresample", "resample")
        caps = self._make("capsfilter", "caps")
        pre_tee, pre_qd, pre_dry_amp, pre_qw, pre_wet_amp, pre_mix = self._mix_pair("pre")
        pre = self._make(FILTER_FACTORY, "pre")
        eq = self._make(EQ_FACTORY, "eq")
        exp_tee, exp_qd, exp_dry_amp, exp_qw, exp_wet_amp, exp_mix = self._mix_pair("exp")
        exp = self._make(EXP_FACTORY, "exp")
        dyn_tee, dyn_qd, dyn_dry_amp, dyn_qw, dyn_wet_amp, dyn_mix = self._mix_pair("dyn")
        comp = self._make(COMP_FACTORY, "comp")
        lim = self._make(LIM_FACTORY, "lim")
        fx_tee, fx_qd, fx_dry_amp, fx_qw, fx_wet_amp, fx_mix = self._mix_pair("fx")
        fx_hpf = self._make(FILTER_FACTORY, "fx_hpf")
        fx_lpf = self._make(FILTER_FACTORY, "fx_lpf")
        fx_air = self._make(FILTER_FACTORY, "fx_air")
        fx_echo = self._make("audioecho", "fx_echo")
        fx_verb = self._make("freeverb", "fx_verb")
        dly_tee, dly_qd, dly_dry_amp, dly_qw, dly_wet_amp, dly_mix = self._mix_pair("dly")
        dly_qs = self._make("queue", "dly_qs")
        dly_spec_amp = self._make("audioamplify", "dly_spec")
        dly_qtap = self._make("queue", "dly_qtap")
        dly_tap_convert = self._make("audioconvert", "dly_tap_convert")
        dly_tap_caps = self._make("capsfilter", "dly_tap_caps")
        dly_tap = self._make("appsink", "dly_tap")
        dly_qs.set_property("max-size-buffers", 3)
        dly_qs.set_property("max-size-time", 0)
        dly_qs.set_property("max-size-bytes", 0)
        dly_qs.set_property("leaky", 2)
        dly_spec_amp.set_property("clipping-method", "clip")
        dly_spec_amp.set_property("amplification", 0.0)
        dly_lcr = self._make(LCR_DELAY_FACTORY, "dly_lcr")
        dly_rev, dly_rev_pair = self._mono_ladspa(REV_DELAY_FACTORY, "dly_rev")
        dly_chop, dly_chop_pair = self._mono_ladspa(RINGMOD_FACTORY, "dly_chop")
        dly_crush, dly_crush_pair = self._mono_ladspa(ALIAS_FACTORY, "dly_crush")
        dly_cheb, dly_cheb_pair = self._mono_ladspa(CHEB_FACTORY, "dly_cheb")
        dly_sift, dly_sift_pair = self._mono_ladspa(SIFTER_FACTORY, "dly_sift")
        post_tee, post_qd, post_dry_amp, post_qw, post_wet_amp, post_mix = self._mix_pair("post")
        postf = self._make(FILTER_FACTORY, "postf")
        tone_low = self._make(FILTER_FACTORY, "tone_low")
        tone_mid = self._make(FILTER_FACTORY, "tone_mid")
        tone_high = self._make(FILTER_FACTORY, "tone_high")
        de_tee, de_qd, de_dry_amp, de_qw, de_wet_amp, de_mix = self._mix_pair("de")
        de_low = self._make(FILTER_FACTORY, "de_low")
        de_mid = self._make(FILTER_FACTORY, "de_mid")
        de_high = self._make(FILTER_FACTORY, "de_high")
        de_width = self._make(WIDTH_FACTORY, "de_width")
        de_echo = self._make("audioecho", "de_echo")
        de_verb = self._make("freeverb", "de_verb")
        de_makeup = self._make("audioamplify", "de_makeup")
        pan = self._make("audiopanorama", "pan")
        widener = self._make(WIDTH_FACTORY, "width")
        amp = self._make("audioamplify", "amp")
        convert_out = self._make("audioconvert", "convert_out")
        resample_out = self._make("audioresample", "resample_out")
        caps_out = self._make("capsfilter", "caps_out")
        tee = self._make("tee", "tee")
        q2 = self._make("queue", "q2")
        sink = self._make("pipewiresink", "sink")
        q_tap = self._make("queue", "q_tap")
        tap_convert = self._make("audioconvert", "tap_convert")
        tap_caps = self._make("capsfilter", "tap_caps")
        appsink = self._make("appsink", "tap")

        caps.set_property(
            "caps",
            Gst.Caps.from_string("audio/x-raw,format=F32LE,channels=2,rate=48000,layout=interleaved"),
        )
        channels, rate = sink_layout(hardware)
        caps_out.set_property(
            "caps",
            Gst.Caps.from_string(f"audio/x-raw,format=F32LE,channels={channels},rate={rate}"),
        )
        for queue in (q1, q2):
            queue.set_property("max-size-buffers", 3)
            queue.set_property("max-size-time", 0)
            queue.set_property("max-size-bytes", 0)
            queue.set_property("leaky", 2)

        src.set_property("do-timestamp", True)
        src.set_property("client-name", APP_NAME)
        src.set_property("target-object", SINK_NAME)
        src.set_property(
            "stream-properties",
            Gst.Structure.new_from_string(
                f"props,node.name={SINK_NAME}.capture,stream.capture.sink=true"
            ),
        )

        tap_caps.set_property(
            "caps",
            Gst.Caps.from_string("audio/x-raw,format=F32LE,channels=2,layout=interleaved"),
        )
        q_tap.set_property("max-size-buffers", 2)
        q_tap.set_property("leaky", 2)
        appsink.set_property("emit-signals", False)
        appsink.set_property("sync", False)
        appsink.set_property("drop", True)
        appsink.set_property("max-buffers", 4)
        appsink.set_property("async", False)
        sink.set_property("client-name", APP_NAME)
        sink.set_property("sync", False)
        sink.set_property("qos", False)
        sink.set_property("target-object", hardware)
        sink.set_property(
            "stream-properties",
            Gst.Structure.new_from_string(
                "props,"
                f"node.name={SINK_NAME}.playback,"
                f"target.object={hardware},"
                "node.dont-reconnect=true,"
                "media.class=Stream/Output/Audio"
            ),
        )
        fx_echo.set_property("max-delay", 1_000_000_000)
        fx_echo.set_property("delay", 280_000_000)
        fx_echo.set_property("intensity", 0.0)
        fx_echo.set_property("feedback", 0.0)
        fx_verb.set_property("level", 0.0)
        de_echo.set_property("max-delay", 1_000_000_000)
        de_echo.set_property("delay", 90_000_000)
        de_echo.set_property("intensity", 0.0)
        de_echo.set_property("feedback", 0.0)
        de_verb.set_property("level", 0.0)
        de_makeup.set_property("clipping-method", "clip")
        de_makeup.set_property("amplification", 1.0)
        dly_lcr.set_property("dry-wet-level", 0.0)
        dly_tap_caps.set_property(
            "caps",
            Gst.Caps.from_string("audio/x-raw,format=F32LE,channels=2,layout=interleaved"),
        )
        dly_qtap.set_property("max-size-buffers", 2)
        dly_qtap.set_property("max-size-time", 0)
        dly_qtap.set_property("max-size-bytes", 0)
        dly_qtap.set_property("leaky", 2)
        dly_tap.set_property("emit-signals", False)
        dly_tap.set_property("sync", False)
        dly_tap.set_property("drop", True)
        dly_tap.set_property("max-buffers", 4)
        dly_tap.set_property("async", False)

        parts = (
            src,
            q1,
            convert_in,
            resample,
            caps,
            pre_tee,
            pre_qd,
            pre_dry_amp,
            pre_qw,
            pre,
            pre_wet_amp,
            pre_mix,
            eq,
            exp_tee,
            exp_qd,
            exp_dry_amp,
            exp_qw,
            exp,
            exp_wet_amp,
            exp_mix,
            dyn_tee,
            dyn_qd,
            dyn_dry_amp,
            dyn_qw,
            comp,
            dyn_wet_amp,
            dyn_mix,
            lim,
            fx_tee,
            fx_qd,
            fx_dry_amp,
            fx_qw,
            fx_hpf,
            fx_lpf,
            fx_air,
            fx_echo,
            fx_verb,
            fx_wet_amp,
            fx_mix,
            dly_tee,
            dly_qd,
            dly_dry_amp,
            dly_qw,
            dly_lcr,
            dly_wet_amp,
            dly_qs,
            dly_rev,
            dly_chop,
            dly_crush,
            dly_cheb,
            dly_sift,
            dly_spec_amp,
            dly_mix,
            dly_qtap,
            dly_tap_convert,
            dly_tap_caps,
            dly_tap,
            post_tee,
            post_qd,
            post_dry_amp,
            post_qw,
            postf,
            tone_low,
            tone_mid,
            tone_high,
            de_tee,
            de_qd,
            de_dry_amp,
            de_qw,
            de_low,
            de_mid,
            de_high,
            de_width,
            de_echo,
            de_verb,
            de_makeup,
            de_wet_amp,
            de_mix,
            pan,
            widener,
            amp,
            post_wet_amp,
            post_mix,
            convert_out,
            resample_out,
            caps_out,
            tee,
            q2,
            sink,
            q_tap,
            tap_convert,
            tap_caps,
            appsink,
        )
        for el in parts:
            pipeline.add(el)
        link = self._link
        link(src, q1)
        link(q1, convert_in)
        link(convert_in, resample)
        link(resample, caps)
        link(pre_tee, pre_qw)
        link(pre_qw, pre)
        link(pre, pre_wet_amp)
        link(pre_wet_amp, pre_mix)
        link(pre_tee, pre_qd)
        link(pre_qd, pre_dry_amp)
        link(pre_dry_amp, pre_mix)
        link(exp_tee, exp_qw)
        link(exp_qw, exp)
        link(exp, exp_wet_amp)
        link(exp_wet_amp, exp_mix)
        link(exp_tee, exp_qd)
        link(exp_qd, exp_dry_amp)
        link(exp_dry_amp, exp_mix)
        link(dyn_tee, dyn_qw)
        link(dyn_qw, comp)
        link(comp, dyn_wet_amp)
        link(dyn_wet_amp, dyn_mix)
        link(dyn_tee, dyn_qd)
        link(dyn_qd, dyn_dry_amp)
        link(dyn_dry_amp, dyn_mix)
        link(dyn_mix, lim)
        link(fx_tee, fx_qw)
        link(fx_qw, fx_hpf)
        link(fx_hpf, fx_lpf)
        link(fx_lpf, fx_air)
        link(fx_air, fx_echo)
        link(fx_echo, fx_verb)
        link(fx_verb, fx_wet_amp)
        link(fx_wet_amp, fx_mix)
        link(fx_tee, fx_qd)
        link(fx_qd, fx_dry_amp)
        link(fx_dry_amp, fx_mix)
        link(dly_tee, dly_qw)
        link(dly_qw, dly_lcr)
        link(dly_lcr, dly_wet_amp)
        link(dly_wet_amp, dly_mix)
        link(dly_tee, dly_qs)
        link(dly_qs, dly_rev)
        link(dly_rev, dly_chop)
        link(dly_chop, dly_crush)
        link(dly_crush, dly_cheb)
        link(dly_cheb, dly_sift)
        link(dly_sift, dly_spec_amp)
        link(dly_spec_amp, dly_mix)
        link(dly_tee, dly_qd)
        link(dly_qd, dly_dry_amp)
        link(dly_dry_amp, dly_mix)
        link(dly_tee, dly_qtap)
        link(dly_qtap, dly_tap_convert)
        link(dly_tap_convert, dly_tap_caps)
        link(dly_tap_caps, dly_tap)
        heads = {
            "pre": pre_tee,
            "eq": eq,
            "exp": exp_tee,
            "dyn": dyn_tee,
            "fx": fx_tee,
            "fx2": dly_tee,
        }
        tails = {
            "pre": pre_mix,
            "eq": eq,
            "exp": exp_mix,
            "dyn": lim,
            "fx": fx_mix,
            "fx2": dly_mix,
        }
        order = self.chain_order
        link(caps, heads[order[0]])
        for prev, nxt in zip(order, order[1:]):
            link(tails[prev], heads[nxt])
        link(tails[order[-1]], post_tee)
        link(post_tee, post_qw)
        link(post_qw, postf)
        link(postf, pan)
        link(pan, widener)
        link(widener, amp)
        link(amp, post_wet_amp)
        link(post_wet_amp, post_mix)
        link(post_tee, post_qd)
        link(post_qd, post_dry_amp)
        link(post_dry_amp, post_mix)
        link(post_mix, tone_low)
        link(tone_low, tone_mid)
        link(tone_mid, tone_high)
        link(tone_high, de_tee)
        link(de_tee, de_qw)
        link(de_qw, de_low)
        link(de_low, de_mid)
        link(de_mid, de_high)
        link(de_high, de_width)
        link(de_width, de_echo)
        link(de_echo, de_verb)
        link(de_verb, de_makeup)
        link(de_makeup, de_wet_amp)
        link(de_wet_amp, de_mix)
        link(de_tee, de_qd)
        link(de_qd, de_dry_amp)
        link(de_dry_amp, de_mix)
        link(de_mix, convert_out)
        link(convert_out, resample_out)
        link(resample_out, caps_out)
        link(caps_out, tee)
        link(tee, q2)
        link(q2, sink)
        if not tee.link(q_tap) or not q_tap.link(tap_convert) or not tap_convert.link(tap_caps) or not tap_caps.link(appsink):
            raise RuntimeError("Failed to link output meter tap")

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus)

        self.pipeline = pipeline
        self.eq = eq
        self.comp = comp
        self.lim = lim
        self.exp = exp
        self.pre = pre
        self.postf = postf
        self.tone_low = tone_low
        self.tone_mid = tone_mid
        self.tone_high = tone_high
        self.de_low = de_low
        self.de_mid = de_mid
        self.de_high = de_high
        self.de_width = de_width
        self.de_echo = de_echo
        self.de_verb = de_verb
        self.de_makeup = de_makeup
        self.de_dry_amp = de_dry_amp
        self.de_wet_amp = de_wet_amp
        self.fx_hpf = fx_hpf
        self.fx_lpf = fx_lpf
        self.fx_air = fx_air
        self.fx_echo = fx_echo
        self.fx_verb = fx_verb
        self.fx_dry_amp = fx_dry_amp
        self.fx_wet_amp = fx_wet_amp
        self.dly_lcr = dly_lcr
        self.dly_rev = dly_rev_pair
        self.dly_chop = dly_chop_pair
        self.dly_crush = dly_crush_pair
        self.dly_cheb = dly_cheb_pair
        self.dly_sift = dly_sift_pair
        self.dly_dry_amp = dly_dry_amp
        self.dly_wet_amp = dly_wet_amp
        self.dly_spec_amp = dly_spec_amp
        self.dly_tap = dly_tap
        self.dly_env = 0.04
        self.dly_avg = 0.04
        self.dly_gap = 0.0
        self.exp_dry_amp = exp_dry_amp
        self.exp_wet_amp = exp_wet_amp
        self.dyn_dry_amp = dyn_dry_amp
        self.dyn_wet_amp = dyn_wet_amp
        self.pre_dry_amp = pre_dry_amp
        self.pre_wet_amp = pre_wet_amp
        self.post_dry_amp = post_dry_amp
        self.post_wet_amp = post_wet_amp
        self.amp = amp
        self.pan = pan
        self.widener = widener
        self.pw_sink = sink
        self.hardware_sink = hardware
        self.meters.appsink = appsink
        self.meters.reset()

    def _on_bus(self, _bus, message) -> None:
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            _log(f"GStreamer error: {err} ({debug})")
            self.processing = False
        elif t == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            _log(f"GStreamer warning: {err} ({debug})")

    def play(self, state: dict) -> None:
        if not self.pipeline:
            raise RuntimeError("Pipeline not built")
        apply_dsp(self, state)
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Could not start the audio pipeline")
        _ok, state_now, pending = self.pipeline.get_state(5 * Gst.SECOND)
        if state_now != Gst.State.PLAYING:
            raise RuntimeError(f"Audio pipeline stayed in {state_now.value_nick} (pending {pending.value_nick})")
        self.processing = True

    def stop_pipeline(self) -> None:
        self.stop_record()
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.eq = self.comp = self.lim = self.exp = self.pw_sink = None
        self.pre = self.postf = self.amp = self.pan = self.widener = None
        self.tone_low = self.tone_mid = self.tone_high = None
        self.de_low = self.de_mid = self.de_high = None
        self.de_width = self.de_echo = self.de_verb = self.de_makeup = None
        self.de_dry_amp = self.de_wet_amp = None
        self.fx_hpf = self.fx_lpf = self.fx_air = self.fx_echo = self.fx_verb = None
        self.fx_dry_amp = self.fx_wet_amp = self.exp_dry_amp = self.exp_wet_amp = None
        self.dly_lcr = self.dly_rev = self.dly_chop = self.dly_crush = None
        self.dly_cheb = self.dly_sift = self.dly_dry_amp = self.dly_wet_amp = None
        self.dly_spec_amp = self.dly_tap = None
        self.dly_auto = False
        self.dly_env = 0.04
        self.dly_avg = 0.04
        self.dly_gap = 0.0
        self.dyn_dry_amp = self.dyn_wet_amp = self.pre_dry_amp = self.pre_wet_amp = None
        self.post_dry_amp = self.post_wet_amp = None
        self.blend_gr = 0.0
        self.blend_gr_db = 0.0
        self.ride_db = 0.0
        self.meters.appsink = None
        self.meters.reset()
        self.processing = False

    def ingest_output_meters(self) -> None:
        sink = self.meters.appsink
        if sink is None or not self.processing:
            self.meters.decay()
            return
        sample = sink.emit("try-pull-sample", 0)
        if sample is None:
            self.meters.decay()
            return
        pulled = [sample]
        want_rta = (
            time.monotonic() < float(getattr(self, "_rta_until", 0.0))
            or bool(getattr(self, "auto_eq_on", False))
            or bool(getattr(self, "tone_auto_on", False))
        )
        extra = 5 if want_rta else 8
        for _ in range(extra):
            nxt = sink.emit("try-pull-sample", 0)
            if nxt is None:
                break
            pulled.append(nxt)
        chunks = pulled if want_rta else pulled[-1:]
        fed = False
        for sample in chunks:
            buf = sample.get_buffer()
            if buf is None:
                continue
            ok, mapped = buf.map(Gst.MapFlags.READ)
            if not ok:
                continue
            try:
                data = array.array("f")
                raw = mapped.data
                data.frombytes(raw if isinstance(raw, (bytes, bytearray)) else bytes(raw))
            finally:
                buf.unmap(mapped)
            if data:
                self.meters.ingest(data, rta=want_rta)
                fed = True
        if not fed:
            self.meters.decay()

    def meter_snapshot(self, rta: bool = False) -> dict:
        if rta:
            self._rta_until = time.monotonic() + 1.5
        snap = self.meters.snapshot()
        snap["fx2_gap"] = round(float(self.dly_gap), 4)
        snap["fx2_auto"] = bool(self.dly_auto)
        snap["blend_gr"] = round(float(self.blend_gr), 4)
        snap["blend_gr_db"] = round(float(self.blend_gr_db), 2)
        snap["blend_on"] = bool((self.blend or {}).get("enabled", True))
        snap["ride_db"] = round(float(self.ride_db), 2)
        snap["ride_on"] = bool((self.ride or {}).get("enabled", True))
        snap["ride_target_db"] = float((self.ride or {}).get("target_db", -18.0))
        snap["tone_auto"] = bool(self.tone_auto_on)
        snap["tone_scale"] = round(float(self.tone_scale), 3)
        live = dict(self.tone_live or {})
        live["gain_db"] = round(float(self.master_db), 2)
        snap["tone_live"] = live
        snap["eq_auto"] = bool(getattr(self, "auto_eq_on", False))
        snap["eq_lifts"] = [round(float(v), 2) for v in (getattr(self, "auto_eq_lifts", None) or [])[:16]]
        snap["auto_intent"] = str(getattr(self, "auto_intent", "") or "")
        return snap

    def harvest_meters(self) -> dict:
        self.ingest_output_meters()
        return self.meter_snapshot()

    def _pull_rms(self, sink) -> float | None:
        if sink is None:
            return None
        best = None
        for _ in range(1):
            sample = sink.emit("try-pull-sample", 0)
            if sample is None:
                break
            buf = sample.get_buffer()
            if buf is None:
                continue
            ok, mapped = buf.map(Gst.MapFlags.READ)
            if not ok:
                continue
            try:
                data = array.array("f")
                data.frombytes(bytes(mapped.data))
            finally:
                buf.unmap(mapped)
            if not data:
                continue
            acc = 0.0
            for sample_v in data:
                acc += float(sample_v) * float(sample_v)
            rms = math.sqrt(acc / len(data))
            best = rms if best is None else 0.4 * best + 0.6 * rms
        return best

    def _apply_dly_amps(self, dry: float, wet: float, spec: float) -> None:
        """Store unscaled enhance/FX2 mix; blend compressor writes the amps."""
        self.dly_dry_live = float(dry)
        self.dly_wet_live = float(wet)
        self.dly_spec_live = float(spec)

    def _commit_dly_amps(self, dry: float, wet: float, spec: float) -> None:
        master = max(0.0, min(1.0, float(getattr(self, "dly_master", 1.0))))
        if self.dly_dry_amp is None:
            return
        _set(self.dly_dry_amp, "amplification", max(0.0, dry * master))
        _set(self.dly_wet_amp, "amplification", max(0.0, wet * master))
        if self.dly_spec_amp is not None:
            _set(self.dly_spec_amp, "amplification", max(0.0, spec * master))

    def _update_blend_gr(self, dt_ms: float = 20.0) -> None:
        cfg = self.blend or default_blend()
        peak = max(float(self.meters.peak_l), float(self.meters.peak_r))
        peak_db = 20.0 * math.log10(max(peak, 1e-9))
        thr = float(cfg.get("threshold_db", -8.0))
        ratio = max(1.0, float(cfg.get("ratio", 5.0)))
        attack = max(0.5, float(cfg.get("attack_ms", 8.0)))
        release = max(1.0, float(cfg.get("release_ms", 240.0)))
        if bool(cfg.get("enabled", True)) and self.processing:
            overshoot = peak_db - thr
            desired = min(24.0, max(0.0, overshoot * (1.0 - 1.0 / ratio)))
        else:
            desired = 0.0
        current = float(self.blend_gr_db)
        tau = attack if desired > current else release
        coeff = 1.0 - math.exp(-dt_ms / tau)
        gr_db = current + (desired - current) * coeff
        if gr_db < 0.05:
            gr_db = 0.0
        self.blend_gr_db = gr_db
        self.blend_gr = 1.0 - math.pow(10.0, -gr_db / 20.0)

    def apply_blend_comp(self) -> None:
        """Duck FX / enhance wet mix as the output approaches clip."""
        self._update_blend_gr()
        scale = blend_scale(self.blend_gr)
        if self.fx_dry_amp is not None:
            if scale >= 0.999:
                wet = max(0.0, float(self.fx_wet_target))
                dry = max(0.0, float(self.fx_dry_target))
            else:
                wet = max(0.0, float(self.fx_wet_target) * scale)
                dry = max(0.0, 1.0 - wet)
            _set(self.fx_dry_amp, "amplification", dry)
            _set(self.fx_wet_amp, "amplification", wet)
        fry = scale ** 1.65
        if scale >= 0.999:
            dry, wet, spec = self.dly_dry_live, self.dly_wet_live, self.dly_spec_live
        else:
            wet = self.dly_wet_live * scale
            spec = self.dly_spec_live * fry
            dry = self.dly_dry_live + (self.dly_wet_live - wet) + (self.dly_spec_live - spec)
        self._commit_dly_amps(dry, wet, spec)
        if self.post_dry_amp is not None:
            if scale >= 0.999:
                pwet = max(0.0, float(self.post_wet_target))
                pdry = max(0.0, float(self.post_dry_target))
            else:
                post_scale = 0.35 + 0.65 * scale
                pwet = max(0.0, float(self.post_wet_target) * post_scale)
                pdry = max(0.0, 1.0 - pwet)
            _set(self.post_dry_amp, "amplification", pdry)
            _set(self.post_wet_amp, "amplification", pwet)

    def _commit_output_amp(self) -> None:
        if self.amp is None:
            return
        total_db = float(self.post_gain_db) + float(self.ride_db) + float(self.master_db)
        gain = 10 ** (total_db / 20.0)
        _set(self.amp, "amplification", max(0.05, min(6.0, gain)))

    def apply_wave_ride(self, dt_ms: float = 20.0) -> None:
        """Closed-loop makeup: watch output waveform/histogram and ride toward target RMS."""
        cfg = self.ride or default_ride()
        enabled = bool(cfg.get("enabled", True)) and self.processing
        if not enabled:
            current = float(self.ride_db)
            tau = max(40.0, float(cfg.get("release_ms", 220.0)))
            coeff = 1.0 - math.exp(-dt_ms / tau)
            self.ride_db = current * (1.0 - coeff)
            if abs(self.ride_db) < 0.05:
                self.ride_db = 0.0
            self._commit_output_amp()
            return
        rms = 0.5 * (float(self.meters.rms_l) + float(self.meters.rms_r))
        peak = max(float(self.meters.peak_l), float(self.meters.peak_r))
        rms_db = 20.0 * math.log10(max(rms, 1e-9))
        peak_db = 20.0 * math.log10(max(peak, 1e-9))
        form_db = float(getattr(self.meters, "form_db", rms_db))
        gate = float(cfg.get("gate_db", -48.0))
        if rms_db < gate:
            self._commit_output_amp()
            return
        level_db = 0.62 * rms_db + 0.38 * form_db - float(self.master_db)
        target = float(cfg.get("target_db", -18.0))
        ceiling = float(cfg.get("ceiling_db", -1.0))
        error = target - level_db
        headroom = ceiling - peak_db
        error = min(error, headroom)
        boost = float(cfg.get("boost_db", 8.0))
        cut = float(cfg.get("cut_db", 12.0))
        current = float(self.ride_db)
        desired = max(-cut, min(boost, current + error))
        tau = float(cfg.get("attack_ms", 900.0) if desired > current else cfg.get("release_ms", 220.0))
        if getattr(self, "tone_auto_on", False) or getattr(self, "auto_eq_on", False):
            tau = max(tau, 900.0) * 2.4
        coeff = 1.0 - math.exp(-dt_ms / max(40.0, tau))
        self.ride_db = current + (desired - current) * coeff
        if abs(self.ride_db) < 0.05:
            self.ride_db = 0.0
        self._commit_output_amp()

    def apply_tone_auto(self, dt_ms: float = 20.0) -> None:
        """Ride TONE and EQ on the same beat, sharing headroom for a full mix."""
        target = self.tone_target or default_tone()
        master = float(self.master_target)
        need_tone = bool(self.tone_auto_on and self.processing)
        need_eq = bool(getattr(self, "auto_eq_on", False) and self.processing)
        if not need_tone:
            self.tone_scale = 1.0
            live, live_gain = scale_tone(target, master, 1.0)
            _apply_tone(self, live)
            self.master_db = live_gain
            self.tone_live = {**live, "gain_db": live_gain}
        if not need_tone and not need_eq:
            self._tone_beat_due = 0.0
            return
        now = time.monotonic()
        due = float(getattr(self, "_tone_beat_due", 0.0) or 0.0)
        if due <= 0.0 or now >= due:
            bpm = max(70.0, min(180.0, float(getattr(self.meters, "bpm", 0.0) or 120.0)))
            self._tone_beat_due = now + (60.0 / bpm)
            rta = list(getattr(self.meters, "rta", []) or [])
            peak = max(float(self.meters.peak_l), float(self.meters.peak_r))
            rms = 0.5 * (float(self.meters.rms_l) + float(self.meters.rms_r))
            peak_db = 20.0 * math.log10(max(peak, 1e-9))
            rms_db = 20.0 * math.log10(max(rms, 1e-9))
            ceiling = float((self.ride or {}).get("ceiling_db", -1.0))
            lifts, live, live_gain, scale, guess = pair_auto_from_rta(
                rta,
                tone_target=target,
                master_db=master,
                prev_tone=self.tone_live,
                prev_lifts=getattr(self, "auto_eq_lifts", None),
                prev_scale=float(self.tone_scale),
                prev_intent=str(getattr(self, "auto_intent", "") or ""),
                headroom_db=ceiling - peak_db,
                rms_db=rms_db,
                peak_db=peak_db,
                bpm=bpm,
                paired=bool(need_tone and need_eq),
            )
            held = str(getattr(self, "auto_intent", "") or guess)
            votes = int(getattr(self, "_intent_votes", 0) or 0)
            if guess == held:
                votes = 4
            else:
                votes -= 1
                if votes <= 0:
                    held = guess
                    votes = 3
            self.auto_intent = held
            self._intent_votes = votes
            self._auto_want_tone = live
            self._auto_want_gain = live_gain
            self._auto_want_lifts = lifts
            self.tone_scale = scale
        coeff = 1.0 - math.exp(-dt_ms / 620.0)
        if need_tone and isinstance(getattr(self, "_auto_want_tone", None), dict):
            want = self._auto_want_tone
            cur = dict(self.tone_live or {})
            live = {
                key: float(cur.get(key, 0.0)) + (float(want.get(key, 0.0)) - float(cur.get(key, 0.0))) * coeff
                for key in ("low_db", "mid_db", "high_db")
            }
            gain = float(cur.get("gain_db", self.master_db)) + (
                float(self._auto_want_gain) - float(cur.get("gain_db", self.master_db))
            ) * coeff
            self.master_db = gain
            self.tone_live = {**live, "gain_db": gain}
            _apply_tone(self, live)
        if need_eq and self._auto_want_lifts:
            cur = list(getattr(self, "auto_eq_lifts", None) or [0.0] * 16)
            want = list(self._auto_want_lifts) + [0.0] * 16
            self.auto_eq_lifts = [
                float(cur[i] if i < len(cur) else 0.0)
                + (float(want[i]) - float(cur[i] if i < len(cur) else 0.0)) * coeff
                for i in range(16)
            ]
            self._commit_eq_mix()

    def _commit_eq_mix(self) -> None:
        if self.eq is None:
            return
        lifts = self.auto_eq_lifts if getattr(self, "auto_eq_on", False) else None
        for i, db in enumerate(mix_eq_lifts(getattr(self, "eq_bands", None), lifts)[:16]):
            _set(self.eq, f"g-{i}", db_to_band_gain(float(db)))

    def apply_auto_fx(self) -> bool:
        """Ride noise, clarity, or level from the input envelope. Dry is never replaced."""
        if self.dly_dry_amp is None:
            self.dly_gap = 0.0
            return True
        if not self.dly_auto:
            return True
        mix = max(0.0, min(1.0, float(self.dly_mix)))
        mode = self.dly_enhance
        if not self.processing:
            self.dly_gap = 0.0
            self._apply_dly_amps(1.0, 0.0, 0.0)
            return True
        rms = self._pull_rms(self.dly_tap)
        if rms is None:
            rms = 0.0
        if rms > self.dly_avg:
            self.dly_avg = self.dly_avg * 0.97 + rms * 0.03
        else:
            self.dly_avg = self.dly_avg * 0.995 + rms * 0.005
        if rms >= self.dly_env:
            self.dly_env = self.dly_env * 0.22 + rms * 0.78
        else:
            self.dly_env = self.dly_env * 0.90 + rms * 0.10
        rel = self.dly_env / max(self.dly_avg, 0.006)
        gap = max(0.0, min(1.0, (0.48 - rel) / 0.30))
        self.dly_gap = gap
        if mode == "noise":
            self._apply_dly_amps(max(0.08, 1.0 - mix * 0.72 * gap), 0.0, 0.0)
        elif mode == "level":
            open_ = 1.0 - 0.22 * gap
            self._apply_dly_amps(
                min(1.7, 1.0 + mix * 0.62 * gap),
                self.dly_wet_target * open_,
                self.dly_spec_target * open_,
            )
        elif mode == "clarity":
            open_ = 1.0 - 0.18 * gap
            self._apply_dly_amps(1.0, self.dly_wet_target * open_, self.dly_spec_target * open_)
        else:
            self._apply_dly_amps(
                self.dly_dry_target,
                self.dly_wet_target * gap,
                self.dly_spec_target * gap,
            )
        return True

    def record_status(self) -> dict:
        active = self.rec_bin is not None
        elapsed = (time.monotonic() - self.rec_started) if active and self.rec_started else 0.0
        return {
            "recording": active,
            "path": self.rec_path,
            "format": self.rec_format,
            "lossy": self.rec_lossy,
            "elapsed_sec": round(elapsed, 1),
        }

    def start_record(self, path: str, fmt_key: str, bitrate: int | None = None) -> dict:
        if not self.processing or self.pipeline is None:
            raise RuntimeError("Enable processing before recording.")
        if self.rec_bin is not None:
            raise RuntimeError(f"Already recording to {self.rec_path}")
        fmt = format_or_raise(fmt_key)
        rec = build_record_bin(path, fmt.key, bitrate)
        tee = self.pipeline.get_by_name("tee")
        if tee is None:
            raise RuntimeError("DSP tee is missing")
        self.pipeline.add(rec)
        tmpl = tee.get_pad_template("src_%u")
        tee_pad = tee.request_pad(tmpl, None, None)
        if tee_pad is None:
            self.pipeline.remove(rec)
            raise RuntimeError("Could not tap the mix for recording")
        ghost = rec.get_static_pad("sink")
        if tee_pad.link(ghost) != Gst.PadLinkReturn.OK:
            tee.release_request_pad(tee_pad)
            self.pipeline.remove(rec)
            raise RuntimeError("Failed to link recorder to the mix")
        rec.sync_state_with_parent()
        self.rec_bin = rec
        self.rec_tee_pad = tee_pad
        self.rec_path = path
        self.rec_format = fmt.key
        self.rec_lossy = fmt.lossy
        self.rec_started = time.monotonic()
        _log(f"recording {fmt.key} → {path}")
        return self.record_status()

    def stop_record(self) -> dict:
        rec = self.rec_bin
        tee_pad = self.rec_tee_pad
        path = self.rec_path
        if rec is None:
            return {"recording": False, "path": None, "format": None, "elapsed_sec": 0, "lossy": False}
        done = threading.Event()

        def _probe(_pad, info):
            if info.get_event().type == Gst.EventType.EOS:
                done.set()
            return Gst.PadProbeReturn.OK

        sink = rec.get_by_name("rec_file")
        if sink is not None:
            spad = sink.get_static_pad("sink")
            if spad is not None:
                spad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, _probe)
        ghost = rec.get_static_pad("sink")
        if ghost is not None:
            ghost.send_event(Gst.Event.new_eos())
        done.wait(timeout=2.5)
        if tee_pad is not None:
            peer = tee_pad.get_peer()
            if peer is not None:
                tee_pad.unlink(peer)
            tee = self.pipeline.get_by_name("tee") if self.pipeline else None
            if tee is not None:
                tee.release_request_pad(tee_pad)
        rec.set_state(Gst.State.NULL)
        if self.pipeline is not None:
            self.pipeline.remove(rec)
        self.rec_bin = None
        self.rec_tee_pad = None
        self.rec_path = None
        self.rec_format = None
        self.rec_started = 0.0
        self.rec_lossy = False
        _log(f"recording stopped ({path})")
        return {"recording": False, "path": path, "format": None, "elapsed_sec": 0, "lossy": False, "saved": path}

    def retarget(self, hardware: str, state: dict) -> None:
        order = normalize_chain(state.get("chain_order"))
        same = (
            hardware == self.hardware_sink
            and self.processing
            and self.dly_tap is not None
            and list(self.chain_order) == order
        )
        if same:
            apply_dsp(self, state)
            return
        self.build(hardware, state)
        self.play(state)


class Daemon:
    def __init__(self) -> None:
        self.engine = Engine()
        self.loop = GLib.MainLoop()
        self.server: socket.socket | None = None
        self.state = load_state()

    def status(self) -> dict:
        hw = self.engine.hardware_sink or self.state.get("hardware_sink")
        try:
            outputs = output_inventory(hw)
            role = current_output_role(hw)
        except Exception:  # noqa: BLE001
            outputs = {}
            role = self.state.get("output_role")
        return {
            "ok": True,
            "running": True,
            "processing": self.engine.processing,
            "enabled": bool(self.state.get("enabled")),
            "preset": self.state.get("preset"),
            "profile": self.state.get("profile"),
            "hardware_sink": hw,
            "output_role": role,
            "outputs": outputs,
            "virtual_sink": SINK_NAME if sink_exists(SINK_NAME) else None,
            "default_sink": default_sink(),
            "state": self.state,
            "record": self.engine.record_status(),
        }

    def _bind_output(self, role: str | None = None) -> str:
        requested = role if role is not None else self.state.get("output_role")
        if requested:
            role_n = normalize_output_role(str(requested))
            sink, port = resolve_output_role(role_n, preferred=self.state.get("hardware_sink"))
        else:
            sink = pick_hardware_sink(self.state.get("hardware_sink"))
            port = None
            role_n = current_output_role(sink) if sink else "speakers"
        if not sink and role is None:
            sink = pick_hardware_sink(self.state.get("hardware_sink"))
            port = None
            role_n = current_output_role(sink) if sink else "speakers"
        if not sink:
            labels = {
                "speakers": "speakers",
                "usb": "USB audio",
                "headphones": "headphones",
            }
            raise PulseError(f"No {labels.get(role_n, role_n)} output is connected.")
        if sink.startswith("bluez_output."):
            sink = prefer_a2dp(sink)
        if port:
            try:
                set_sink_port(sink, port)
            except PulseError as exc:
                if role is not None:
                    raise
                _log(f"set-sink-port: {exc}")
        self.state["hardware_sink"] = sink
        self.state["output_role"] = role_n if requested else current_output_role(sink)
        return sink

    def set_output(self, role: str) -> dict:
        hardware = self._bind_output(role)
        save_state(self.state)
        if self.state.get("enabled"):
            load_null_sink()
            self.engine.retarget(hardware, self.state)
            if default_sink() != SINK_NAME:
                set_default_sink(SINK_NAME)
            route_playback_to_hardware(hardware)
        return self.status()

    def enable(self) -> dict:
        disk = load_state()
        for key in DSP_STATE_KEYS:
            if key in disk:
                self.state[key] = disk[key]
        hardware = self._bind_output()
        load_null_sink()
        for _ in range(40):
            if sink_exists(SINK_NAME):
                break
            time.sleep(0.05)
        self.engine.retarget(hardware, self.state)
        if default_sink() != SINK_NAME:
            set_default_sink(SINK_NAME)
        for _ in range(20):
            time.sleep(0.08)
            route_playback_to_hardware(hardware)
            if playback_is_routed(hardware):
                break
        self.state["enabled"] = True
        save_state(self.state)
        return self.status()

    def disable(self) -> dict:
        hardware = self.state.get("hardware_sink") or pick_hardware_sink()
        if hardware and not sink_exists(str(hardware)):
            hardware = pick_hardware_sink()
        current = default_sink()
        if current == SINK_NAME and hardware:
            set_default_sink(hardware)
        self.engine.stop_pipeline()
        unload_null_sink()
        self.state["enabled"] = False
        save_state(self.state)
        return self.status()

    def _watch_output(self) -> bool:
        if not self.state.get("enabled"):
            return True
        try:
            hw = self.engine.hardware_sink
            if self.engine.processing and hw and sink_exists(hw):
                if not playback_is_routed(hw):
                    route_playback_to_hardware(hw)
                return True
            nxt = pick_hardware_sink(None, self.state.get("output_role"))
            if not nxt:
                nxt = pick_hardware_sink()
            if not nxt:
                return True
            if nxt.startswith("bluez_output."):
                nxt = prefer_a2dp(nxt)
            if nxt == hw and self.engine.processing:
                return True
            _log(f"output lost ({hw}); switching to {nxt}")
            self.state["hardware_sink"] = nxt
            self.state["output_role"] = current_output_role(nxt)
            save_state(self.state)
            self.engine.retarget(nxt, self.state)
            if default_sink() != SINK_NAME:
                set_default_sink(SINK_NAME)
            route_playback_to_hardware(nxt)
        except Exception as exc:  # noqa: BLE001
            _log(f"output watch: {exc}")
        return True

    def apply(self, incoming: dict) -> dict:
        if "state" in incoming and isinstance(incoming["state"], dict):
            incoming = incoming["state"]
        for key in DSP_STATE_KEYS:
            if key in incoming:
                self.state[key] = incoming[key]
        self.state["chain_order"] = normalize_chain(self.state.get("chain_order"))
        save_state(self.state)
        if self.state.get("enabled"):
            if self.engine.processing:
                hw = self.engine.hardware_sink
                if hw:
                    self.engine.retarget(hw, self.state)
                else:
                    apply_dsp(self.engine, self.state)
            else:
                return self.enable()
        elif self.engine.processing:
            return self.disable()
        return self.status()

    def record_start(self, msg: dict) -> dict:
        recst = dict(self.state.get("record") or {})
        fmt = format_or_raise(str(msg.get("format") or recst.get("format") or "flac"))
        exists = "overwrite" if msg.get("force") or msg.get("overwrite") else str(msg.get("exists") or "unique")
        dest = resolve_output(msg.get("path") or msg.get("output"), fmt, recst.get("directory"), exists)
        bitrate = msg.get("bitrate")
        info = self.engine.start_record(str(dest), fmt.key, int(bitrate) if bitrate is not None else None)
        recst["format"] = fmt.key
        recst["directory"] = str(dest.parent)
        self.state["record"] = recst
        save_state(self.state)
        return {"ok": True, **info}

    def handle(self, msg: dict) -> dict:
        cmd = msg.get("cmd")
        try:
            if cmd == "ping":
                return {"ok": True, "pong": True}
            if cmd == "status":
                return self.status()
            if cmd == "enable":
                return self.enable()
            if cmd == "disable":
                return self.disable()
            if cmd == "apply":
                return self.apply(msg)
            if cmd == "output":
                return self.set_output(str(msg.get("role") or ""))
            if cmd == "meters":
                return {"ok": True, **self.engine.meter_snapshot(rta=bool(msg.get("rta")))}
            if cmd == "record-start":
                return self.record_start(msg)
            if cmd == "record-stop":
                return {"ok": True, **self.engine.stop_record()}
            if cmd == "record-status":
                return {"ok": True, **self.engine.record_status()}
            if cmd == "quit":
                GLib.idle_add(self.shutdown)
                return {"ok": True, "stopping": True}
            return {"ok": False, "error": f"Unknown command {cmd!r}"}
        except Exception as exc:  # noqa: BLE001
            _log(traceback.format_exc())
            return {"ok": False, "error": str(exc)}

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()

    def _serve_conn(self, conn: socket.socket) -> None:
        try:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    conn.close()
                    return
                data += chunk
                if len(data) > 1_000_000:
                    raise ValueError("request too large")
            msg = json.loads(data.decode("utf-8"))
            if not isinstance(msg, dict):
                raise ValueError("JSON object required")
            if msg.get("cmd") == "meters":
                reply = {"ok": True, **self.engine.meter_snapshot(rta=bool(msg.get("rta")))}
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                conn.close()
                return
        except Exception as exc:  # noqa: BLE001
            try:
                conn.sendall((json.dumps({"ok": False, "error": str(exc)}) + "\n").encode("utf-8"))
            except OSError:
                pass
            conn.close()
            return

        box: dict = {}
        done = threading.Event()

        def run() -> bool:
            box["reply"] = self.handle(msg)
            done.set()
            return False

        GLib.idle_add(run)
        if not done.wait(timeout=20):
            box["reply"] = {"ok": False, "error": "Timed out applying audio changes"}
        try:
            conn.sendall((json.dumps(box.get("reply", {"ok": False})) + "\n").encode("utf-8"))
        except OSError:
            pass
        conn.close()

    def listen(self) -> None:
        path = socket_path()
        if path.exists():
            path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(8)
        self.server = server
        threading.Thread(target=self._accept_loop, daemon=True).start()
        pid_path().write_text(str(os.getpid()) + "\n", encoding="utf-8")
        _log(f"daemon listening on {path}")
        GLib.timeout_add(1000, self._watch_output)
        GLib.timeout_add(20, self._tick_auto_fx)

    def _tick_auto_fx(self) -> bool:
        try:
            self.engine.ingest_output_meters()
            self.engine.apply_auto_fx()
            self.engine.apply_blend_comp()
            self.engine.apply_tone_auto()
            self.engine.apply_wave_ride()
        except Exception as exc:  # noqa: BLE001
            _log(f"auto fx: {exc}")
        return True

    def shutdown(self) -> bool:
        try:
            if self.state.get("enabled"):
                self.disable()
            unload_null_sink()
        except Exception as exc:  # noqa: BLE001
            _log(f"shutdown: {exc}")
        if self.server:
            self.server.close()
        socket_path().unlink(missing_ok=True)
        pid_path().unlink(missing_ok=True)
        self.loop.quit()
        return False


def run_daemon() -> int:
    Gst.init(None)
    lock_path = config_dir() / "daemon.lock"
    lock_fh = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log("daemon already running")
        print("daemon already running", file=sys.stderr)
        return 0
    daemon = Daemon()
    daemon._lock_fh = lock_fh

    def _stop(*_args) -> None:
        daemon.shutdown()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    daemon.listen()
    if daemon.state.get("enabled"):
        try:
            daemon.enable()
        except Exception as exc:  # noqa: BLE001
            _log(f"auto-enable failed: {exc}")
    daemon.loop.run()
    return 0
