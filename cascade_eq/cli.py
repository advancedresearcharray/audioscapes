"""Non-interactive CLI for Cascade EQ."""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time

from . import APP_NAME, __version__
from .client import ClientError, ensure_daemon, ping, request, start_daemon
from .dsp import BAND_LABELS, CHAIN_LABELS, MIX_STEPS, keep_mix, mix_amount, mix_step, normalize_chain, resolve_band
from .paths import load_state, save_state, socket_path
from .presets import apply_preset, preset_names
from .pulse import default_sink, output_inventory, pick_hardware_sink, resolve_output_role


def _die(message: str, hint: str | None = None) -> int:
    print(f"Error: {message}", file=sys.stderr)
    if hint:
        print(hint, file=sys.stderr)
    return 2


def _print_json(data: dict) -> int:
    print(json.dumps(data, indent=2))
    return 0


def _print_status(data: dict, as_json: bool) -> int:
    if as_json:
        return _print_json(data)
    state = data.get("state") or load_state()
    enabled = "on" if data.get("enabled") or data.get("processing") else "off"
    print(f"{APP_NAME} {enabled}")
    print(f"profile: {state.get('profile', '—')}")
    print(f"preset: {state.get('preset', '—')}")
    order = normalize_chain(state.get("chain_order"))
    print("chain: " + " > ".join(CHAIN_LABELS.get(k, k.upper()) for k in order))
    print(f"virtual_sink: {data.get('virtual_sink') or '—'}")
    print(f"hardware_sink: {data.get('hardware_sink') or pick_hardware_sink() or '—'}")
    print(f"output: {data.get('output_role') or (data.get('state') or {}).get('output_role') or '—'}")
    print(f"default_sink: {data.get('default_sink') or default_sink() or '—'}")
    print(f"preamp_db: {state.get('preamp_db', 0)}")
    print(f"master_db: {state.get('master_db', 0)}")
    tone = state.get("tone") or {}
    print(
        "tone: "
        f"low={tone.get('low_db', 0)}dB "
        f"mid={tone.get('mid_db', 0)}dB "
        f"high={tone.get('high_db', 0)}dB"
    )
    bands = state.get("bands") or []
    shown = " ".join(
        f"{label}={gain:+.1f}" for label, gain in zip(BAND_LABELS, bands, strict=False)
    )
    if shown:
        print(f"bands_db: {shown}")
    comp = state.get("compressor") or {}
    lim = state.get("limiter") or {}
    print(
        "compressor: "
        f"{'on' if comp.get('enabled') else 'off'} "
        f"thr={comp.get('threshold_db')}dB ratio={comp.get('ratio')} "
        f"{comp.get('mode', 'rms')}"
    )
    print(
        "limiter: "
        f"{'on' if lim.get('enabled') else 'off'} "
        f"ceiling={lim.get('ceiling_db')}dB lookahead={lim.get('lookahead_ms')}ms"
    )
    pre = state.get("pre") or {}
    post = state.get("post") or {}
    print(
        "pre: "
        f"{'on' if pre.get('enabled') else 'off'} "
        f"hpf={pre.get('hpf_hz')}Hz slope={pre.get('slope', 'x2')}"
    )
    print(
        "post: "
        f"{'on' if post.get('enabled') else 'off'} "
        f"gain={post.get('gain_db')}dB width={post.get('width')} "
        f"air={post.get('air_db')}dB"
    )
    digital = state.get("digital") or {}
    print(f"digital: {digital.get('preset', 'bypass')}")
    exp = state.get("expander") or {}
    print(
        "expander: "
        f"{'on' if exp.get('enabled') else 'off'} "
        f"{exp.get('em', 'down')} "
        f"thr={exp.get('threshold_db')}dB ratio={exp.get('ratio')}"
    )
    fx = state.get("fx") or {}
    bits = []
    bits.append(f"mix={mix_step(mix_amount(fx))}/{MIX_STEPS}")
    if fx.get("hpf_on"):
        bits.append(f"hpf={fx.get('hpf_hz')}Hz")
    if fx.get("lpf_on"):
        bits.append(f"lpf={fx.get('lpf_hz')}Hz")
    if fx.get("air_on"):
        bits.append(f"air={fx.get('air_db')}dB")
    if fx.get("echo_on"):
        bits.append(f"echo={fx.get('echo_delay_ms')}ms")
    if fx.get("room_on"):
        bits.append("room")
    print("fx: " + (" ".join(bits) if bits else "bypass"))
    dly = state.get("delay") or {}
    dbits = [f"mix={mix_step(mix_amount(dly))}/{MIX_STEPS}"]
    if dly.get("auto_on"):
        mode = str(dly.get("enhance") or "").strip().lower()
        dbits.append({"noise": "noise", "clarity": "clarity", "level": "volume"}.get(mode, "auto"))
    dbits.append(f"master={float(dly.get('master', 1.0)):.2f}")
    if str(dly.get("enhance") or "") == "clarity" or dly.get("pong_on"):
        dbits.append(f"clarity={float(dly.get('clarity', 0.55)):.2f}")
        dbits.append(f"color={float(dly.get('color', 0.4)):.2f}")
    if dly.get("pong_on"):
        dbits.append(f"pong={dly.get('r_delay_ms')}ms")
    if dly.get("chop_on"):
        dbits.append(f"chop={dly.get('chop_hz')}Hz")
    if dly.get("rev_on"):
        dbits.append(f"rev={dly.get('rev_time')}s")
    if dly.get("crush_on"):
        dbits.append("crush")
    if dly.get("sift_on"):
        dbits.append("sift")
    print("enhance: " + " ".join(dbits))
    blend = state.get("blend") or {}
    print(
        "blend: "
        f"{'on' if blend.get('enabled', True) else 'off'} "
        f"thr={blend.get('threshold_db')}dB ratio={blend.get('ratio')} "
        f"atk={blend.get('attack_ms')}ms rel={blend.get('release_ms')}ms"
    )
    ride = state.get("ride") or {}
    print(
        "ride: "
        f"{'on' if ride.get('enabled', True) else 'off'} "
        f"target={ride.get('target_db')}dB boost={ride.get('boost_db')} "
        f"cut={ride.get('cut_db')}"
    )
    rec = data.get("record") or {}
    if rec.get("recording"):
        print(
            "record: on "
            f"{rec.get('format')} {rec.get('elapsed_sec')}s "
            f"{rec.get('path')}"
        )
    else:
        print("record: off")
    return 0


def cmd_gui(_args: argparse.Namespace) -> int:
    from .gui import run_gui

    return run_gui()


def cmd_daemon(args: argparse.Namespace) -> int:
    from .daemon import run_daemon

    if args.foreground:
        return run_daemon()
    if ping():
        print(f"daemon already running ({socket_path()})")
        return 0
    start_daemon()
    print(f"daemon started ({socket_path()})")
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    if args.dry_run:
        hw = pick_hardware_sink()
        print("would start daemon, create sink cascade_eq, and set it as default")
        print(f"hardware_target: {hw or 'none'}")
        return 0
    ensure_daemon()
    data = request({"cmd": "enable"})
    if args.json:
        return _print_json(data)
    print(f"enabled → default sink cascade_eq → {data.get('hardware_sink')}")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    if args.dry_run:
        print("would restore the hardware sink and stop DSP")
        return 0
    if not ping():
        print("already disabled")
        return 0
    data = request({"cmd": "disable"})
    if args.json:
        return _print_json(data)
    print(f"disabled → default sink {data.get('default_sink') or data.get('hardware_sink')}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if ping():
        data = request({"cmd": "status"})
    else:
        data = {"ok": True, "running": False, "processing": False, "enabled": False, "state": load_state()}
        data["hardware_sink"] = data["state"].get("hardware_sink")
        data["output_role"] = data["state"].get("output_role")
        data["default_sink"] = default_sink()
        data["virtual_sink"] = None
        data["outputs"] = output_inventory(data["hardware_sink"])
    return _print_status(data, args.json)


def cmd_quit(_args: argparse.Namespace) -> int:
    if not ping():
        print("daemon not running")
        return 0
    request({"cmd": "quit"})
    print("daemon stopping")
    return 0


def cmd_output(args: argparse.Namespace) -> int:
    role = args.role
    if args.dry_run:
        sink, port = resolve_output_role(role)
        if not sink:
            return _die(f"No {role} output is connected", "  cascade-eq output speakers")
        extra = f" port {port}" if port else ""
        print(f"would switch output to {role} → {sink}{extra}")
        return 0
    ensure_daemon()
    data = request({"cmd": "output", "role": role})
    if args.json:
        return _print_json(data)
    hw = data.get("hardware_sink") or "—"
    live = data.get("output_role") or role
    print(f"output {live} → {hw}")
    return 0


def cmd_preset_list(args: argparse.Namespace) -> int:
    names = preset_names()
    if args.json:
        return _print_json({"ok": True, "presets": names})
    print("\n".join(names))
    return 0


def cmd_preset_load(args: argparse.Namespace) -> int:
    state = apply_preset(load_state(), args.name)
    if args.dry_run:
        print(f"would load preset {state['preset']}")
        return 0
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    elif not args.local:
        ensure_daemon()
        request({"cmd": "apply", "state": state})
    print(f"loaded preset {state['preset']}")
    return 0


def cmd_profile_list(args: argparse.Namespace) -> int:
    from .profiles import PROFILES, profile_names

    names = profile_names()
    if args.json:
        return _print_json(
            {
                "ok": True,
                "profiles": [
                    {"name": name, "note": PROFILES[name]["note"]} for name in names
                ],
            }
        )
    for name in names:
        print(f"{name:16}  {PROFILES[name]['note']}")
    return 0


def cmd_profile_load(args: argparse.Namespace) -> int:
    from .profiles import apply_profile, resolve_profile

    try:
        official = resolve_profile(args.name)
    except KeyError as exc:
        return _die(str(exc), "  cascade-eq profile list")
    state = apply_profile(load_state(), official)
    if args.dry_run:
        print(f"would load profile {official}")
        return 0
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    elif not args.local:
        ensure_daemon()
        request({"cmd": "apply", "state": state})
    print(f"loaded profile {official}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    state = load_state()
    if args.preamp is not None:
        state["preamp_db"] = float(args.preamp)
        state["preset"] = "Custom"
    if args.master is not None:
        from .dsp import clamp_master_db

        state["master_db"] = clamp_master_db(args.master)
    if any(v is not None for v in (args.low, args.mid, args.high)):
        from .dsp import clamp_master_db, normalize_tone

        tone = normalize_tone(state.get("tone"))
        if args.low is not None:
            tone["low_db"] = clamp_master_db(args.low)
        if args.mid is not None:
            tone["mid_db"] = clamp_master_db(args.mid)
        if args.high is not None:
            tone["high_db"] = clamp_master_db(args.high)
        state["tone"] = tone
        from .dsp import match_tone_preset

        state["tone_preset"] = match_tone_preset(tone, state.get("master_db", 0))
    if args.band:
        bands = list(state.get("bands") or [0.0] * 16)
        for item in args.band:
            if "=" not in item:
                return _die(
                    f"Invalid --band {item!r}",
                    "  cascade-eq set --band 1k=3 --band 63=-2",
                )
            token, value = item.split("=", 1)
            try:
                idx = resolve_band(token)
                bands[idx] = float(value)
            except ValueError as exc:
                return _die(str(exc), "  cascade-eq set --band 1k=3")
        state["bands"] = bands
        state["preset"] = "Custom"
    if args.preamp is None and not args.band and args.master is None and args.low is None and args.mid is None and args.high is None:
        return _die("Nothing to set.", "  cascade-eq set --master 6 --low 3 --high 2")
    if args.dry_run:
        print("would apply gain/EQ changes")
        return 0
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    elif args.require_running:
        return _die("Daemon is not running.", "  cascade-eq enable")
    print(
        "eq updated"
        if args.band or args.preamp is not None
        else "tone updated"
        if any(v is not None for v in (args.low, args.mid, args.high))
        else "master updated"
    )
    return 0


def cmd_tone_list(args: argparse.Namespace) -> int:
    from .dsp import TONE_PROFILES, tone_profile_names

    names = tone_profile_names()
    if args.json:
        print(json.dumps([{"name": n, "note": TONE_PROFILES[n]["note"], **{k: TONE_PROFILES[n][k] for k in ("low_db", "mid_db", "high_db", "gain_db")}} for n in names]))
        return 0
    for name in names:
        spec = TONE_PROFILES[name]
        print(
            f"{name:10}  L{spec['low_db']:+.0f} M{spec['mid_db']:+.0f} "
            f"H{spec['high_db']:+.0f} G{spec['gain_db']:+.0f}  {spec['note']}"
        )
    return 0


def cmd_tone_load(args: argparse.Namespace) -> int:
    from .dsp import apply_tone_profile, empty_bands, normalize_tone_auto

    state = load_state()
    try:
        state = apply_tone_profile(state, args.name)
    except KeyError as exc:
        return _die(str(exc), "  cascade-eq tone list")
    auto = normalize_tone_auto(state.get("tone_auto"))
    if args.auto:
        auto["enabled"] = True
    if args.auto_off:
        auto["enabled"] = False
    state["tone_auto"] = auto
    eq = dict(state.get("auto_eq") or {"enabled": False, "lift": empty_bands()})
    if auto["enabled"]:
        eq["enabled"] = True
        if not eq.get("lift"):
            eq["lift"] = empty_bands()
        state["auto_eq"] = eq
    elif args.auto_off:
        eq["enabled"] = False
        state["auto_eq"] = eq
    if args.dry_run:
        print(f"would load tone {state['tone_preset']}")
        return 0
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    print(f"loaded tone {state['tone_preset']}")
    return 0


def cmd_eq_auto(args: argparse.Namespace) -> int:
    from .client import meters as read_meters
    from .dsp import BAND_LABELS, auto_reveal_from_rta, empty_bands, four_beat_seconds

    if args.dry_run:
        beats = max(1, int(args.beats))
        if args.seconds is not None:
            print(f"would listen {float(args.seconds):.1f}s and lift buried detail")
        else:
            print(f"would listen {beats} beats and lift buried detail")
        return 0
    ensure_daemon()
    request({"cmd": "status"})
    bpm = 120.0
    probe = read_meters(rta=True)
    try:
        bpm = float(probe.get("bpm") or 120.0)
    except (TypeError, ValueError):
        bpm = 120.0
    if args.seconds is not None:
        seconds = max(0.4, min(8.0, float(args.seconds)))
    else:
        seconds = four_beat_seconds(bpm, max(1, int(args.beats)))
    frames: list[list[float]] = []
    steps = max(6, int(seconds / 0.05))
    for _ in range(steps):
        data = read_meters(rta=True)
        rta = list(data.get("rta") or [])
        if rta and max(rta) > 0.02:
            frames.append(rta[:16])
        time.sleep(0.05)
    if not frames:
        return _die(
            "No audio on the RTA.",
            "  Play something through cascade_eq, then: cascade-eq eq auto",
        )
    n = 16
    avg = [sum(row[i] for row in frames) / len(frames) for i in range(n)]
    state = load_state()
    prev = ((state.get("auto_eq") or {}).get("lift") or empty_bands())
    state["auto_eq"] = {"enabled": True, "lift": auto_reveal_from_rta(avg, prev)}
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    shown = " ".join(
        f"{label}={gain:+.1f}" for label, gain in zip(BAND_LABELS, state["auto_eq"]["lift"], strict=False) if gain > 0.05
    )
    print(f"auto reveal  {seconds:.1f}s  {bpm:.0f} BPM  {shown or 'flat (no buried detail)'}")
    return 0


def cmd_compressor(args: argparse.Namespace) -> int:
    state = load_state()
    c = dict(state.get("compressor") or {})
    if args.on:
        c["enabled"] = True
    if args.off:
        c["enabled"] = False
    mapping = {
        "threshold": "threshold_db",
        "ratio": "ratio",
        "attack": "attack_ms",
        "release": "release_ms",
        "knee": "knee_db",
        "makeup": "makeup_db",
        "lookahead": "lookahead_ms",
    }
    for arg, key in mapping.items():
        val = getattr(args, arg)
        if val is not None:
            c[key] = float(val)
    if args.mode:
        c["mode"] = args.mode
    state["compressor"] = c
    state["preset"] = "Custom"
    if args.dry_run:
        print("would apply compressor")
        return 0
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    print("compressor updated")
    return 0


def cmd_blend(args: argparse.Namespace) -> int:
    from .dsp import normalize_blend

    state = load_state()
    blend = normalize_blend(state.get("blend"))
    if args.on:
        blend["enabled"] = True
    if args.off:
        blend["enabled"] = False
    mapping = {
        "threshold": "threshold_db",
        "ratio": "ratio",
        "attack": "attack_ms",
        "release": "release_ms",
    }
    for arg, key in mapping.items():
        val = getattr(args, arg)
        if val is not None:
            blend[key] = float(val)
    state["blend"] = normalize_blend(blend)
    if args.dry_run:
        print("would apply blend compressor")
        return 0
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    print("blend compressor updated")
    return 0


def cmd_ride(args: argparse.Namespace) -> int:
    from .dsp import normalize_ride

    state = load_state()
    ride = normalize_ride(state.get("ride"))
    if args.on:
        ride["enabled"] = True
    if args.off:
        ride["enabled"] = False
    mapping = {
        "target": "target_db",
        "boost": "boost_db",
        "cut": "cut_db",
        "gate": "gate_db",
        "ceiling": "ceiling_db",
        "attack": "attack_ms",
        "release": "release_ms",
    }
    for arg, key in mapping.items():
        val = getattr(args, arg)
        if val is not None:
            ride[key] = float(val)
    state["ride"] = normalize_ride(ride)
    if args.dry_run:
        print("would apply wave ride")
        return 0
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    print("wave ride updated")
    return 0


def cmd_limiter(args: argparse.Namespace) -> int:
    state = load_state()
    lim = dict(state.get("limiter") or {})
    if args.on:
        lim["enabled"] = True
    if args.off:
        lim["enabled"] = False
    if args.alr:
        lim["alr"] = True
    if args.no_alr:
        lim["alr"] = False
    mapping = {
        "ceiling": "ceiling_db",
        "lookahead": "lookahead_ms",
        "attack": "attack_ms",
        "release": "release_ms",
    }
    for arg, key in mapping.items():
        val = getattr(args, arg)
        if val is not None:
            lim[key] = float(val)
    state["limiter"] = lim
    state["preset"] = "Custom"
    if args.dry_run:
        print("would apply limiter")
        return 0
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    print("limiter updated")
    return 0


def _apply_state(state: dict, dry_run: bool, verb: str) -> int:
    if dry_run:
        print(f"would {verb}")
        return 0
    save_state(state)
    if ping():
        request({"cmd": "apply", "state": state})
    print(verb)
    return 0


def cmd_expander(args: argparse.Namespace) -> int:
    from .presets import exp_preset_mapping, exp_preset_names, match_exp_preset

    if args.list:
        names = exp_preset_names()
        if args.json:
            return _print_json({"ok": True, "presets": names})
        print("\n".join(names))
        return 0
    state = load_state()
    if args.preset:
        name = str(args.preset).strip().upper()
        names = exp_preset_names()
        if name not in names:
            return _die(f"Unknown expander preset {args.preset!r}", f"  try: {', '.join(names)}")
        old = dict(state.get("expander") or {})
        state["expander"] = exp_preset_mapping(name)
        keep_mix(state["expander"], old)
        state["expander_preset"] = name
        return _apply_state(state, args.dry_run, f"expander {name}")
    name = match_exp_preset(state.get("expander"))
    print(name)
    return 0


def cmd_fx(args: argparse.Namespace) -> int:
    from .presets import fx_preset_mapping, fx_preset_names, match_fx_preset

    if args.list:
        names = fx_preset_names()
        if args.json:
            return _print_json({"ok": True, "presets": names})
        print("\n".join(names))
        return 0
    state = load_state()
    if args.preset:
        name = str(args.preset).strip().upper()
        names = fx_preset_names()
        if name not in names:
            return _die(f"Unknown FX preset {args.preset!r}", f"  try: {', '.join(names)}")
        old = dict(state.get("fx") or {})
        state["fx"] = fx_preset_mapping(name)
        keep_mix(state["fx"], old)
        state["fx_preset"] = name
        return _apply_state(state, args.dry_run, f"fx {name}")
    name = match_fx_preset(state.get("fx"))
    print(name)
    return 0


def cmd_digital(args: argparse.Namespace) -> int:
    from .presets import digital_preset_names, normalize_digital, normalize_digital_name

    names = ["bypass", *digital_preset_names()]
    if args.list:
        if args.json:
            return _print_json({"ok": True, "presets": names})
        print("\n".join(names))
        return 0
    state = load_state()
    chosen = "bypass" if args.off else args.preset
    if chosen:
        name = normalize_digital_name(chosen)
        if name not in names:
            return _die(f"Unknown digital preset {chosen!r}", f"  try: {', '.join(names)}")
        state["digital"] = normalize_digital({"preset": name})
        return _apply_state(state, args.dry_run, f"digital {name}")
    rec = normalize_digital(state.get("digital"))
    print(rec.get("preset", "bypass"))
    return 0


def cmd_delay(args: argparse.Namespace) -> int:
    from .presets import delay_preset_mapping, delay_preset_names, match_delay_preset

    if args.list:
        names = delay_preset_names()
        if args.json:
            return _print_json({"ok": True, "presets": names})
        print("\n".join(names))
        return 0
    state = load_state()
    if args.preset:
        name = str(args.preset).strip().upper()
        names = delay_preset_names()
        if name not in names:
            return _die(f"Unknown enhance preset {args.preset!r}", f"  try: {', '.join(names)}")
        old = dict(state.get("delay") or {})
        state["delay"] = delay_preset_mapping(name)
        keep_mix(state["delay"], old)
        state["delay_preset"] = name
        return _apply_state(state, args.dry_run, f"fx2 {name}")
    name = match_delay_preset(state.get("delay"))
    print(name)
    return 0


def cmd_record_formats(args: argparse.Namespace) -> int:
    from .record import available_formats

    rows = available_formats()
    if args.json:
        return _print_json(
            {
                "ok": True,
                "formats": [
                    {"id": f.key, "label": f.label, "ext": f.ext, "lossy": f.lossy} for f in rows
                ],
            }
        )
    for fmt in rows:
        kind = "lossy" if fmt.lossy else "lossless"
        print(f"{fmt.key:8} {kind:9} {fmt.ext:6} {fmt.label}")
    return 0


def cmd_record_start(args: argparse.Namespace) -> int:
    from .record import available_formats, format_or_raise

    fmt_key = args.format
    try:
        fmt = format_or_raise(fmt_key)
    except (ValueError, RuntimeError) as exc:
        known = " ".join(f.key for f in available_formats())
        return _die(str(exc), f"  cascade-eq record start --format flac\n  Available: {known}")
    if args.dry_run:
        dest = args.output or f"~/Music/Cascade EQ/cascade-<timestamp>{fmt.ext}"
        print(f"would record {fmt.key} → {dest}")
        return 0
    ensure_daemon()
    data = request({"cmd": "status"})
    if not data.get("processing"):
        return _die("Processing is off.", "  cascade-eq enable")
    payload = {
        "cmd": "record-start",
        "format": fmt.key,
        "path": args.output,
        "force": bool(args.force),
        "exists": "overwrite" if args.force else "error",
    }
    if args.bitrate is not None:
        payload["bitrate"] = args.bitrate
    try:
        data = request(payload, timeout=12.0)
    except ClientError as exc:
        return _die(str(exc), "  cascade-eq record start --format flac --output ~/Music/take.flac")
    if args.json:
        return _print_json(data)
    print(f"recording {data.get('format')} → {data.get('path')}")
    return 0


def cmd_record_stop(args: argparse.Namespace) -> int:
    if args.dry_run:
        print("would stop recording and finalize the file")
        return 0
    if not ping():
        print("not recording")
        return 0
    data = request({"cmd": "record-stop"}, timeout=12.0)
    if args.json:
        return _print_json(data)
    saved = data.get("saved") or data.get("path")
    if saved:
        print(f"saved {saved}")
    else:
        print("not recording")
    return 0


def cmd_record_status(args: argparse.Namespace) -> int:
    if ping():
        data = request({"cmd": "record-status"})
    else:
        data = {"ok": True, "recording": False}
    if args.json:
        return _print_json(data)
    if data.get("recording"):
        print(
            f"recording {data.get('format')} {data.get('elapsed_sec')}s "
            f"{data.get('path')}"
        )
    else:
        print("not recording")
    return 0


def _session_source(args: argparse.Namespace):
    from pathlib import Path

    from .paths import load_state, sessions_dir

    if getattr(args, "input", None):
        path = Path(args.input).expanduser()
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    last = (load_state().get("session") or {}).get("last")
    if last and Path(last).exists():
        return Path(last)
    sessions = sessions_dir()
    files = sorted(sessions.glob("session-*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = [p for p in files if p.suffix.lower() not in {".json"}]
    if files:
        return files[0]
    raise FileNotFoundError("No session file. Pass --input or record a session first.")


def _remember_session(path) -> None:
    from pathlib import Path

    from .paths import load_state, save_state

    state = load_state()
    rec = dict(state.get("session") or {})
    rec["last"] = str(Path(path))
    state["session"] = rec
    save_state(state)


def cmd_session_split(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .session import split_session

    try:
        src = _session_source(args)
    except FileNotFoundError as exc:
        return _die(str(exc), "  cascade-eq session split --input ~/Music/Cascade\\ EQ/sessions/session.flac")
    if args.dry_run:
        print(f"would split {src} on silence into tracks")
        return 0

    def prog(msg: str) -> None:
        print(msg, flush=True)

    try:
        data = split_session(
            src,
            out_dir=Path(args.output).expanduser() if args.output else None,
            noise_db=args.noise,
            min_silence=args.min_silence,
            min_track=args.min_track,
            progress=None if args.json else prog,
        )
    except (RuntimeError, FileNotFoundError) as exc:
        return _die(str(exc), "  cascade-eq session split --input file.flac")
    _remember_session(src)
    if args.json:
        return _print_json(data)
    print(f"split {data['source']} → {len(data['tracks'])} tracks in {data['directory']}")
    for row in data["tracks"]:
        print(
            f"  {row['index']:02d}  {row.get('camelot') or row['key']}  "
            f"E{row.get('energy', '—')}  {row['duration']:.1f}s  "
            f"{row['bpm']:.0f} BPM  {row['key']}  {row['path']}"
        )
    return 0


def cmd_session_mix(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .session import MixSettings, mix_session

    try:
        src = _session_source(args)
    except FileNotFoundError as exc:
        return _die(str(exc), "  cascade-eq session mix --input file.flac")
    if args.dry_run:
        print(f"would split {src} and radio-mix in {getattr(args, 'style', 'pop')} style")
        return 0

    def prog(msg: str) -> None:
        print(msg, flush=True)

    try:
        data = mix_session(
            src,
            output=Path(args.output).expanduser() if args.output else None,
            progress=None if args.json else prog,
            settings=MixSettings(
                target_db=args.target_db,
                max_db=args.max_db,
                max_bpm_delta=args.max_bpm_delta,
                match_key=not args.no_key_match,
                harmonic_order=not args.keep_order,
                max_energy_step=args.max_energy_step,
                style=args.style,
                denoise=not args.no_denoise,
            ),
        )
    except (RuntimeError, FileNotFoundError) as exc:
        return _die(str(exc), "  cascade-eq session mix --input file.flac")
    _remember_session(src)
    if args.json:
        return _print_json(data)
    print(
        f"radio mix {data['tracks']} tracks  {data.get('style', 'pop')}  "
        f"start {data.get('mix_root')}  {data.get('bit_depth', 16)}-bit  "
        f"songs {data.get('target_db', data.get('min_db'))} dB  "
        f"peak {data.get('peak_db', data.get('max_db'))} dB "
        f"({data.get('mix_gain_db', 0):+.1f} dB) → {data['output']}"
    )
    counts = data.get("transition_counts") or {}
    if counts:
        print(
            f"  transitions  echo {counts.get('echo', 0)}  "
            f"blend {counts.get('blend', 0)}  "
            f"bump {counts.get('bump', 0)}  cut {counts.get('cut', 0)}"
            + ("  denoise on" if data.get("denoise") else "")
        )
    for i, row in enumerate(data.get("tracks_detail") or []):
        into = row.get("into")
        extra = f"  → {into}" if into and into != "end" else ""
        print(
            f"  {row.get('index', 0):02d}  {row.get('key')}  "
            f"E{row.get('energy', '—')}  {row.get('bpm')} BPM{extra}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cascade-eq",
        description="SONIC-RAK system-wide Ubuntu equalizer with compressor and limiter (PipeWire).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              cascade-eq
              cascade-eq gui
              cascade-eq enable
              cascade-eq preset list
              cascade-eq preset load "Bass Boost"
              cascade-eq profile list
              cascade-eq profile load "Remaster"
              cascade-eq profile load headroom
              cascade-eq tone load AUTO --auto
              cascade-eq tone load "80s POP" --auto
              cascade-eq eq auto
              cascade-eq compressor --on --threshold -18 --ratio 4 --mode rms
              cascade-eq blend --on --threshold -8
              cascade-eq ride --on --target -18
              cascade-eq expander --preset GATE
              cascade-eq fx --preset "ADD AIR"
              cascade-eq fx2 --preset "NOISE FILTER"
              cascade-eq limiter --on --ceiling -1 --lookahead 5
              cascade-eq record start --format flac
              cascade-eq record stop
              cascade-eq session split --input ~/Music/show.flac
              cascade-eq session mix --input ~/Music/show.flac
              cascade-eq set --master 6
              cascade-eq output speakers
              cascade-eq output headphones
              cascade-eq status --json
            """
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=False)

    g = sub.add_parser("gui", help="Open the control panel (default if you pass no command)")
    g.set_defaults(func=cmd_gui)

    d = sub.add_parser("daemon", help="Start the DSP daemon")
    d.add_argument("--foreground", action="store_true", help="Run in this terminal (logs to stderr)")
    d.set_defaults(func=cmd_daemon)

    e = sub.add_parser(
        "enable",
        help="Start processing and set Cascade EQ as the default output",
        epilog="Examples:\n  cascade-eq enable\n  cascade-eq enable --dry-run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    e.add_argument("--dry-run", action="store_true", help="Print the plan without changing audio")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_enable)

    x = sub.add_parser("disable", help="Restore the previous output and stop DSP")
    x.add_argument("--dry-run", action="store_true")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_disable)

    s = sub.add_parser("status", help="Show processing state")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    q = sub.add_parser("quit", help="Stop the daemon")
    q.set_defaults(func=cmd_quit)

    outp = sub.add_parser(
        "output",
        help="Send the processed mix to speakers, USB audio, or headphones",
        epilog="Examples:\n  cascade-eq output speakers\n  cascade-eq output usb\n  cascade-eq output headphones",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    outp.add_argument("role", choices=("speakers", "usb", "headphones"))
    outp.add_argument("--dry-run", action="store_true")
    outp.add_argument("--json", action="store_true")
    outp.set_defaults(func=cmd_output)

    pr = sub.add_parser("preset", help="List or load built-in presets")
    pr_sub = pr.add_subparsers(dest="preset_cmd", required=True)
    pl = pr_sub.add_parser("list", help="Print preset names")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_preset_list)
    pd = pr_sub.add_parser("load", help="Load a built-in preset")
    pd.add_argument("name")
    pd.add_argument("--dry-run", action="store_true")
    pd.add_argument("--local", action="store_true", help="Write state only; do not start the daemon")
    pd.set_defaults(func=cmd_preset_load)

    pf = sub.add_parser("profile", help="Load a whole-rack intent profile")
    pf_sub = pf.add_subparsers(dest="profile_cmd", required=True)
    pfl = pf_sub.add_parser("list", help="Print profile names and what they do")
    pfl.add_argument("--json", action="store_true")
    pfl.set_defaults(func=cmd_profile_list)
    pfd = pf_sub.add_parser("load", help="Set every rack unit for one intent")
    pfd.add_argument("name")
    pfd.add_argument("--dry-run", action="store_true")
    pfd.add_argument("--local", action="store_true", help="Write state only; do not start the daemon")
    pfd.set_defaults(func=cmd_profile_load)

    tn = sub.add_parser(
        "tone",
        help="LOW / MID / HIGH / GAIN profiles, with optional AUTO ride",
        epilog='Examples:\n  cascade-eq tone list\n  cascade-eq tone load AUTO\n  cascade-eq tone load "80s POP" --auto',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    tn_sub = tn.add_subparsers(dest="tone_cmd", required=True)
    tnl = tn_sub.add_parser("list", help="Print tone knob profiles")
    tnl.add_argument("--json", action="store_true")
    tnl.set_defaults(func=cmd_tone_list)
    tnd = tn_sub.add_parser("load", help="Set LOW / MID / HIGH / GAIN")
    tnd.add_argument("name")
    tnd.add_argument("--auto", action="store_true", help="Ride LOW/MID/HIGH/GAIN every beat and turn on EQ AUTO")
    tnd.add_argument("--auto-off", action="store_true")
    tnd.add_argument("--dry-run", action="store_true")
    tnd.set_defaults(func=cmd_tone_load)

    st = sub.add_parser("set", help="Set master gain, preamp, and/or EQ band gains in dB")
    st.add_argument("--master", type=float, metavar="DB", help="Master output gain, -12 to +12 (does not change the profile)")
    st.add_argument("--low", type=float, metavar="DB", help="Low-shelf tone gain, -12 to +12")
    st.add_argument("--mid", type=float, metavar="DB", help="Mid bell tone gain, -12 to +12")
    st.add_argument("--high", type=float, metavar="DB", help="High-shelf tone gain, -12 to +12")
    st.add_argument("--preamp", type=float, metavar="DB")
    st.add_argument(
        "--band",
        action="append",
        metavar="BAND=DB",
        help="Band as label, index, or Hz. Repeatable. Example: --band 1k=3",
    )
    st.add_argument("--dry-run", action="store_true")
    st.add_argument("--require-running", action="store_true")
    st.set_defaults(func=cmd_set)

    eqa = sub.add_parser(
        "eq",
        help="Lift buried detail from the live 16-band RTA without changing the graphic EQ curve",
        epilog="Examples:\n  cascade-eq eq auto\n  cascade-eq eq auto --beats 1\n  cascade-eq eq auto --seconds 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    eq_sub = eqa.add_subparsers(dest="eq_cmd", required=True)
    eq_auto = eq_sub.add_parser("auto", help="Listen for 1 beat and lift buried detail")
    eq_auto.add_argument("--beats", type=int, default=1, metavar="N", help="Listen window in beats (default: 1)")
    eq_auto.add_argument("--seconds", type=float, default=None, metavar="SEC", help="Override listen time in seconds")
    eq_auto.add_argument("--dry-run", action="store_true")
    eq_auto.set_defaults(func=cmd_eq_auto)

    c = sub.add_parser(
        "compressor",
        help="Set compressor parameters",
        epilog="Examples:\n  cascade-eq compressor --on --threshold -18 --ratio 4 --mode rms\n  cascade-eq compressor --off",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    c.add_argument("--on", action="store_true")
    c.add_argument("--off", action="store_true")
    c.add_argument("--threshold", type=float, metavar="DB")
    c.add_argument("--ratio", type=float)
    c.add_argument("--attack", type=float, metavar="MS")
    c.add_argument("--release", type=float, metavar="MS")
    c.add_argument("--knee", type=float, metavar="DB")
    c.add_argument("--makeup", type=float, metavar="DB")
    c.add_argument("--lookahead", type=float, metavar="MS")
    c.add_argument("--mode", choices=("rms", "peak"))
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_compressor)

    bl = sub.add_parser(
        "blend",
        help="Background compressor that ducks FX/enhance before the mix hits the red",
        epilog="Examples:\n  cascade-eq blend --on --threshold -8 --ratio 5\n  cascade-eq blend --off",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bl.add_argument("--on", action="store_true")
    bl.add_argument("--off", action="store_true")
    bl.add_argument("--threshold", type=float, metavar="DB")
    bl.add_argument("--ratio", type=float)
    bl.add_argument("--attack", type=float, metavar="MS")
    bl.add_argument("--release", type=float, metavar="MS")
    bl.add_argument("--dry-run", action="store_true")
    bl.set_defaults(func=cmd_blend)

    rd = sub.add_parser(
        "ride",
        help="Watch the output waveform histogram and ride level up or down",
        epilog="Examples:\n  cascade-eq ride --on --target -18\n  cascade-eq ride --off",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rd.add_argument("--on", action="store_true")
    rd.add_argument("--off", action="store_true")
    rd.add_argument("--target", type=float, metavar="DB", help="Output RMS target (default -18)")
    rd.add_argument("--boost", type=float, metavar="DB", help="Max automatic boost")
    rd.add_argument("--cut", type=float, metavar="DB", help="Max automatic cut")
    rd.add_argument("--gate", type=float, metavar="DB")
    rd.add_argument("--ceiling", type=float, metavar="DB")
    rd.add_argument("--attack", type=float, metavar="MS")
    rd.add_argument("--release", type=float, metavar="MS")
    rd.add_argument("--dry-run", action="store_true")
    rd.set_defaults(func=cmd_ride)

    ex = sub.add_parser(
        "expander",
        help="Load a downward expander / gate preset",
        epilog="Examples:\n  cascade-eq expander --preset GATE\n  cascade-eq expander --list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ex.add_argument("--preset", metavar="NAME")
    ex.add_argument("--list", action="store_true")
    ex.add_argument("--json", action="store_true")
    ex.add_argument("--dry-run", action="store_true")
    ex.set_defaults(func=cmd_expander)

    fx = sub.add_parser(
        "fx",
        help="Load an FX-rack preset (echo, filters, air, room)",
        epilog='Examples:\n  cascade-eq fx --preset ECHO\n  cascade-eq fx --preset "ADD AIR"\n  cascade-eq fx --list',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    fx.add_argument("--preset", metavar="NAME")
    fx.add_argument("--list", action="store_true")
    fx.add_argument("--json", action="store_true")
    fx.add_argument("--dry-run", action="store_true")
    fx.set_defaults(func=cmd_fx)

    de = sub.add_parser(
        "digital",
        aliases=["de"],
        help="Post-stage digital effect on the 310 (floor, headroom, clarity, stereo, layers, punch)",
        epilog="Examples:\n  cascade-eq digital floor\n  cascade-eq digital stereo\n  cascade-eq digital --off\n  cascade-eq digital --list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    de.add_argument(
        "preset",
        nargs="?",
        choices=("bypass", "floor", "headroom", "clarity", "stereo", "layers", "punch"),
    )
    de.add_argument("--off", action="store_true", help="Bypass the post digital effect")
    de.add_argument("--list", action="store_true")
    de.add_argument("--json", action="store_true")
    de.add_argument("--dry-run", action="store_true")
    de.set_defaults(func=cmd_digital)

    dly = sub.add_parser(
        "fx2",
        aliases=["delay"],
        help="Load noise filter, audio clarity, or auto volume",
        epilog='Examples:\n  cascade-eq fx2 --preset "NOISE FILTER"\n  cascade-eq fx2 --preset "AUDIO CLARITY"\n  cascade-eq fx2 --preset "AUTO VOLUME"\n  cascade-eq fx2 --list',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dly.add_argument("--preset", metavar="NAME")
    dly.add_argument("--list", action="store_true")
    dly.add_argument("--json", action="store_true")
    dly.add_argument("--dry-run", action="store_true")
    dly.set_defaults(func=cmd_delay)

    lim = sub.add_parser(
        "limiter",
        help="Set limiter parameters",
        epilog="Examples:\n  cascade-eq limiter --on --ceiling -1 --lookahead 5\n  cascade-eq limiter --no-alr",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lim.add_argument("--on", action="store_true")
    lim.add_argument("--off", action="store_true")
    lim.add_argument("--ceiling", type=float, metavar="DB")
    lim.add_argument("--lookahead", type=float, metavar="MS")
    lim.add_argument("--attack", type=float, metavar="MS")
    lim.add_argument("--release", type=float, metavar="MS")
    lim.add_argument("--alr", action="store_true", help="Enable automatic level regulation")
    lim.add_argument("--no-alr", action="store_true")
    lim.add_argument("--dry-run", action="store_true")
    lim.set_defaults(func=cmd_limiter)

    rec = sub.add_parser("record", help="Record the processed mix to a file")
    rec_sub = rec.add_subparsers(dest="record_cmd", required=True)
    rf = rec_sub.add_parser("formats", help="List available lossless and lossy formats")
    rf.add_argument("--json", action="store_true")
    rf.set_defaults(func=cmd_record_formats)
    rs = rec_sub.add_parser(
        "start",
        help="Start recording the processed output",
        epilog=textwrap.dedent(
            """\
            Examples:
              cascade-eq record start --format flac
              cascade-eq record start --format mp3 --output ~/Music/take.mp3
              cascade-eq record start --format wav32 --force --output /tmp/mix.wav
              cascade-eq record start --format opus --bitrate 160
            """
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rs.add_argument(
        "--format",
        default="flac",
        metavar="ID",
        help="Format id (default: flac). See: cascade-eq record formats",
    )
    rs.add_argument("--output", metavar="PATH", help="File or directory. Default: ~/Music/Cascade EQ/")
    rs.add_argument("--bitrate", type=int, metavar="KBPS", help="Lossy bitrate in kbps (mp3, aac, opus)")
    rs.add_argument("--force", action="store_true", help="Overwrite the output file if it exists")
    rs.add_argument("--dry-run", action="store_true")
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(func=cmd_record_start)
    rx = rec_sub.add_parser(
        "stop",
        help="Stop recording and finalize the file",
        epilog="Examples:\n  cascade-eq record stop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rx.add_argument("--dry-run", action="store_true")
    rx.add_argument("--json", action="store_true")
    rx.set_defaults(func=cmd_record_stop)
    rst = rec_sub.add_parser("status", help="Show whether a recording is in progress")
    rst.add_argument("--json", action="store_true")
    rst.set_defaults(func=cmd_record_status)

    ses = sub.add_parser("session", help="Split a recorded session into tracks and radio-mix them")
    ses_sub = ses.add_subparsers(dest="session_cmd", required=True)
    ss = ses_sub.add_parser(
        "split",
        help="Split a session file on silence into song tracks",
        epilog=textwrap.dedent(
            """\
            Examples:
              cascade-eq session split --input ~/Music/Cascade\\ EQ/sessions/session-20260818.flac
              cascade-eq session split --min-silence 2 --noise -40
            """
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ss.add_argument("--input", metavar="PATH", help="Session audio file. Default: last session recording")
    ss.add_argument("--output", metavar="DIR", help="Directory for track-01.flac … and manifest.json")
    ss.add_argument("--noise", type=float, default=-38.0, metavar="DB", help="Silence threshold in dB (default: -38)")
    ss.add_argument("--min-silence", type=float, default=1.6, metavar="SEC")
    ss.add_argument("--min-track", type=float, default=8.0, metavar="SEC")
    ss.add_argument("--dry-run", action="store_true")
    ss.add_argument("--json", action="store_true")
    ss.set_defaults(func=cmd_session_split)
    sm = ses_sub.add_parser(
        "mix",
        help="Split if needed, then mix tracks like a radio show (pop or house)",
        epilog=textwrap.dedent(
            """\
            Examples:
              cascade-eq session mix --input ~/Music/show.flac
              cascade-eq session mix --input ~/Music/show-tracks --output ~/Music/radio.flac
              cascade-eq session mix --target-db -12 --max-db -1 --max-bpm-delta 4
              cascade-eq session mix --style pop
              cascade-eq session mix --style house
            """
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sm.add_argument("--input", metavar="PATH", help="Session file or a -tracks directory with manifest.json")
    sm.add_argument("--output", metavar="PATH", help="Radio-mix file (default: <tracks>/radio-mix.flac)")
    sm.add_argument("--target-db", "--min-db", type=float, default=-12.0, help="Normalize each track peak to this level (default: -12)")
    sm.add_argument("--max-db", type=float, default=-1.0, help="Peak ceiling for the finished mix; never boosts (default: -1)")
    sm.add_argument("--max-bpm-delta", type=float, default=4.0, help="Max BPM change between adjacent tracks (default: 4)")
    sm.add_argument("--max-energy-step", type=float, default=2.0, help="Max energy jump 1–10 (default: 2)")
    sm.add_argument("--style", choices=("pop", "house"), default="pop", help="Mix style: pop (short radio fades) or house (long 32-count blends). Default: pop")
    sm.add_argument("--no-denoise", action="store_true", help="Skip hiss/click cleanup on tracks and the finished mix")
    sm.add_argument("--keep-order", action="store_true", help="Keep recorded order instead of key/energy picking")
    sm.add_argument("--no-key-match", action="store_true", help="Do not pitch-shift intros when keys are not compatible")
    sm.add_argument("--dry-run", action="store_true")
    sm.add_argument("--json", action="store_true")
    sm.set_defaults(func=cmd_session_mix)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        return cmd_gui(args)
    try:
        return int(func(args))
    except ClientError as exc:
        return _die(str(exc), "  cascade-eq status")
    except KeyError as exc:
        return _die(str(exc).strip("'\""), '  cascade-eq preset list')
    except KeyboardInterrupt:
        return 130
