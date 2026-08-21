"""JSON-line client for the Cascade EQ daemon."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .paths import log_path, socket_path


class ClientError(RuntimeError):
    pass


def _send_raw(msg: dict, timeout: float = 3.0) -> dict:
    path = socket_path()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    if not data:
        raise ClientError("Empty reply from daemon")
    reply = json.loads(data.decode("utf-8"))
    if not isinstance(reply, dict):
        raise ClientError("Daemon returned invalid JSON")
    return reply


def ping() -> bool:
    try:
        reply = _send_raw({"cmd": "ping"}, timeout=0.4)
        return bool(reply.get("ok"))
    except (OSError, ClientError, json.JSONDecodeError):
        return False


def request(msg: dict, timeout: float = 8.0) -> dict:
    try:
        reply = _send_raw(msg, timeout=timeout)
    except (OSError, json.JSONDecodeError) as exc:
        raise ClientError(f"Could not talk to the daemon: {exc}") from exc
    if not reply.get("ok", True) and "error" in reply:
        raise ClientError(str(reply["error"]))
    return reply


def start_daemon(wait: float = 8.0) -> None:
    if ping():
        return
    env = os.environ.copy()
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        subprocess.Popen(
            [sys.executable, "-m", "cascade_eq", "daemon", "--foreground"],
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=fh,
            start_new_session=True,
            env=env,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
    deadline = time.time() + wait
    while time.time() < deadline:
        if ping():
            return
        time.sleep(0.08)
    raise ClientError(
        f"Daemon did not start. Check {log}. Example: cascade-eq daemon --foreground"
    )


def ensure_daemon() -> None:
    start_daemon()


def meters(*, rta: bool = False) -> dict:
    empty = {
        "ok": True,
        "wave": [0.0] * 256,
        "peak_l": 0.0,
        "peak_r": 0.0,
        "rms_l": 0.0,
        "rms_r": 0.0,
        "rta": [0.0] * 16,
        "blend_gr": 0.0,
        "blend_gr_db": 0.0,
        "blend_on": True,
        "hist": [0.0] * 36,
        "form_db": -42.0,
        "ride_db": 0.0,
        "ride_on": True,
        "ride_target_db": -18.0,
        "bpm": 120.0,
        "tone_auto": False,
        "tone_scale": 1.0,
        "tone_live": {"low_db": 0.0, "mid_db": 0.0, "high_db": 0.0, "gain_db": 0.0},
    }
    try:
        return _send_raw({"cmd": "meters", "rta": bool(rta)}, timeout=0.25)
    except (OSError, ClientError, json.JSONDecodeError):
        empty["ok"] = False
        return empty
