"""PulseAudio-on-PipeWire helpers (pactl)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from typing import Any

from . import SINK_NAME
from .paths import module_id_path

VIRTUAL_HINTS = ("cascade_eq", "easyeffects", "easy_effects", "jamesdsp", "pulse_effects")


class PulseError(RuntimeError):
    pass


def _pactl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("pactl")
    if not exe:
        raise PulseError("pactl not found. Install pipewire-pulse.")
    proc = subprocess.run([exe, *args], capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "pactl failed").strip()
        raise PulseError(err)
    return proc


def pactl_json(cmd: str) -> Any:
    proc = _pactl("--format=json", *cmd.split())
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


def default_sink() -> str | None:
    proc = _pactl("get-default-sink", check=False)
    name = proc.stdout.strip()
    return name or None


def set_default_sink(name: str) -> None:
    _pactl("set-default-sink", name)


def unmute_sink(name: str) -> None:
    _pactl("set-sink-mute", name, "0", check=False)


def list_sinks() -> list[dict]:
    data = pactl_json("list sinks")
    if not isinstance(data, list):
        return []
    return data


def sink_names() -> list[str]:
    return [s.get("name", "") for s in list_sinks() if s.get("name")]


def sink_exists(name: str) -> bool:
    return name in sink_names()


def is_virtual_name(name: str) -> bool:
    lower = name.lower()
    return any(h in lower for h in VIRTUAL_HINTS)


def sink_by_name(name: str) -> dict | None:
    for sink in list_sinks():
        if sink.get("name") == name:
            return sink
    return None


def sink_layout(name: str) -> tuple[int, int]:
    """Return (channels, rate) for a sink, defaulting to stereo 48 kHz."""
    sink = sink_by_name(name) or {}
    spec = str(sink.get("sample_specification") or "")
    channels = 2
    rate = 48000
    matched = re.search(r"(\d+)ch", spec)
    if matched:
        channels = int(matched.group(1))
    matched = re.search(r"(\d+)Hz", spec)
    if matched:
        rate = int(matched.group(1))
    return max(1, channels), max(8000, rate)


def bluetooth_card_for_sink(sink_name: str) -> str | None:
    if "bluez_output." in sink_name:
        body = sink_name.split("bluez_output.", 1)[1]
        addr = body.rsplit(".", 1)[0]
        return f"bluez_card.{addr}"
    sink = sink_by_name(sink_name) or {}
    props = sink.get("properties") or {}
    addr = str(props.get("api.bluez5.address") or "").replace(":", "_")
    if addr:
        return f"bluez_card.{addr}"
    return None


def prefer_a2dp(sink_name: str) -> str:
    """Switch a Bluetooth headset from HSP/HFP (16 kHz mono) to A2DP music."""
    card = bluetooth_card_for_sink(sink_name)
    if not card:
        return sink_name
    for profile in ("a2dp-sink-sbc_xq", "a2dp-sink"):
        proc = _pactl("set-card-profile", card, profile, check=False)
        if proc.returncode == 0:
            break
    for _ in range(50):
        for name in sink_names():
            if name.startswith("bluez_output.") and sink_layout(name)[0] >= 2:
                return name
        time.sleep(0.1)
    for name in sink_names():
        if name.startswith("bluez_output."):
            return name
    return sink_name


def _pw_link(*args: str) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("pw-link")
    if not exe:
        raise PulseError("pw-link not found")
    return subprocess.run([exe, *args], capture_output=True, text=True, check=False)


def _ports(kind: str, prefix: str) -> list[str]:
    flag = "-o" if kind == "output" else "-i"
    proc = _pw_link(flag)
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith(prefix)]


def playback_is_routed(hardware: str) -> bool:
    current = None
    for line in _pw_link("-l").stdout.splitlines():
        stripped = line.strip()
        if not line[:1].isspace():
            current = stripped if stripped.startswith(f"{SINK_NAME}.playback:") else None
        elif current and "|->" in stripped and hardware in stripped:
            return True
    return False


def route_playback_to_hardware(hardware: str) -> None:
    """Break leftover links and connect processed output to the real device."""
    unmute_sink(hardware)
    unmute_sink(SINK_NAME)
    current = None
    for line in _pw_link("-l").stdout.splitlines():
        stripped = line.strip()
        if not line[:1].isspace():
            current = stripped.split()[0] if stripped.startswith(f"{SINK_NAME}.playback:") else None
        elif current and "|->" in stripped:
            dest = stripped.split("|->", 1)[-1].strip()
            _pw_link("-d", current, dest)

    outs = _ports("output", f"{SINK_NAME}.playback:")
    ins = _ports("input", f"{hardware}:")
    if not outs or not ins:
        return
    if len(ins) == 1:
        for out in outs:
            _pw_link(out, ins[0])
        return
    for out in outs:
        suffix = out.rsplit(":", 1)[-1]
        want = suffix.replace("output_", "playback_")
        match = next(
            (item for item in ins if item.endswith(":" + want) or item.endswith(":" + suffix)),
            None,
        )
        if match:
            _pw_link(out, match)
        else:
            _pw_link(out, ins[0])


def pick_hardware_sink(preferred: str | None = None, role: str | None = None) -> str | None:
    names = sink_names()
    if preferred and preferred in names and not is_virtual_name(preferred):
        return preferred
    if role:
        sink, _port = resolve_output_role(role)
        if sink:
            return sink
    current = default_sink()
    if current and not is_virtual_name(current):
        return current
    for name in names:
        if not is_virtual_name(name):
            return name
    return current


def _sink_props(sink: dict) -> dict:
    props = sink.get("properties") or {}
    return props if isinstance(props, dict) else {}


def _sink_bus(sink: dict) -> str:
    return str(_sink_props(sink).get("device.bus") or "").lower()


def _text_blob(*parts: object) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def _active_port_name(sink: dict) -> str | None:
    raw = sink.get("active_port")
    if isinstance(raw, dict):
        name = raw.get("name")
        return str(name) if name else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def classify_port(port: dict | None) -> str | None:
    if not port:
        return None
    blob = _text_blob(port.get("name"), port.get("description"), port.get("type"))
    if any(key in blob for key in ("headphone", "headset", "phones")):
        return "headphones"
    if any(key in blob for key in ("speaker", "line", "hdmi", "displayport", "aux")):
        return "speakers"
    return None


def classify_sink(sink: dict) -> str:
    name = str(sink.get("name") or "")
    if not name or is_virtual_name(name):
        return "virtual"
    blob = _text_blob(name, sink.get("description"), _sink_props(sink).get("device.form_factor"))
    bus = _sink_bus(sink)
    if name.startswith("bluez_output.") or bus == "bluetooth":
        return "headphones"
    if bus == "usb" or "usb-" in name.lower() or ".usb" in name.lower():
        if any(key in blob for key in ("headphone", "headset")):
            return "headphones"
        return "usb"
    port_role = classify_port(_port_by_name(sink, _active_port_name(sink)))
    if port_role:
        return port_role
    return "speakers"


def _port_by_name(sink: dict, name: str | None) -> dict | None:
    if not name:
        return None
    for port in sink.get("ports") or []:
        if isinstance(port, dict) and port.get("name") == name:
            return port
    return None


def _port_available(port: dict) -> bool:
    avail = str(port.get("availability") or "").lower()
    return avail not in {"not available", "no", "unavailable"}


def set_sink_port(sink: str, port: str) -> None:
    proc = _pactl("set-sink-port", sink, port, check=False)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "set-sink-port failed").strip()
        raise PulseError(err)
    unmute_sink(sink)


def _candidates_for_role(role: str) -> list[tuple[str, str | None, bool]]:
    """Return (sink_name, port_name_or_none, plugged_in) for a role."""
    role = normalize_output_role(role)
    found: list[tuple[str, str | None, bool]] = []
    for sink in list_sinks():
        name = str(sink.get("name") or "")
        if not name or is_virtual_name(name):
            continue
        sink_role = classify_sink(sink)
        ports = [p for p in (sink.get("ports") or []) if isinstance(p, dict)]
        matching = [(p, classify_port(p)) for p in ports]
        matching = [(p, r) for p, r in matching if r == role]
        if matching:
            for port, _r in matching:
                found.append((name, str(port.get("name")), _port_available(port)))
            continue
        if sink_role == role:
            found.append((name, None, True))
    return found


def normalize_output_role(role: str | None) -> str:
    raw = str(role or "").strip().lower().replace("_", " ").replace("-", " ")
    aliases = {
        "speaker": "speakers",
        "speakers": "speakers",
        "spkr": "speakers",
        "usb": "usb",
        "usb audio": "usb",
        "headphone": "headphones",
        "headphones": "headphones",
        "headset": "headphones",
        "phones": "headphones",
    }
    return aliases.get(raw, "speakers")


def resolve_output_role(role: str, preferred: str | None = None) -> tuple[str | None, str | None]:
    role = normalize_output_role(role)
    cands = _candidates_for_role(role)
    if not cands:
        return None, None
    if preferred:
        for name, port, _ok in cands:
            if name == preferred:
                return name, port
    for name, port, ok in cands:
        if ok:
            return name, port
    name, port, _ok = cands[0]
    return name, port


def output_inventory(active_sink: str | None = None) -> dict:
    active_sink = active_sink or ""
    active_role = None
    sink = sink_by_name(active_sink) if active_sink else None
    if sink:
        active_role = classify_sink(sink)
    roles = ("speakers", "usb", "headphones")
    out: dict = {}
    for role in roles:
        cands = _candidates_for_role(role)
        plugged = any(ok for _n, _p, ok in cands)
        present = bool(cands)
        out[role] = {
            "available": present,
            "plugged": plugged if role != "speakers" else present,
            "active": active_role == role,
            "sink": cands[0][0] if cands else None,
        }
    return out


def current_output_role(hardware_sink: str | None) -> str:
    sink = sink_by_name(hardware_sink) if hardware_sink else None
    if not sink:
        return "speakers"
    return classify_sink(sink)


def load_null_sink() -> str:
    if sink_exists(SINK_NAME):
        return SINK_NAME
    proc = _pactl(
        "load-module",
        "module-null-sink",
        f"sink_name={SINK_NAME}",
        "sink_properties=device.description=Cascade-EQ",
    )
    module_id = proc.stdout.strip()
    if module_id:
        module_id_path().write_text(module_id + "\n", encoding="utf-8")
    if not sink_exists(SINK_NAME):
        raise PulseError(f"Created null sink {SINK_NAME} but it did not appear.")
    return SINK_NAME


def unload_null_sink() -> None:
    path = module_id_path()
    module_id = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if module_id:
        _pactl("unload-module", module_id, check=False)
        path.unlink(missing_ok=True)
        return
    data = pactl_json("list modules")
    if not isinstance(data, list):
        return
    for mod in data:
        args = str(mod.get("argument") or "")
        if f"sink_name={SINK_NAME}" in args:
            _pactl("unload-module", str(mod.get("index")), check=False)
            return


def monitor_name() -> str:
    return f"{SINK_NAME}.monitor"
