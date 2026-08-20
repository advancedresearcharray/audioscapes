"""Processed-mix file recorder: lossless and lossy GStreamer encoders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst


@dataclass(frozen=True)
class RecordFormat:
    key: str
    label: str
    ext: str
    lossy: bool
    need: tuple[str, ...]


FORMATS: dict[str, RecordFormat] = {
    "flac": RecordFormat("flac", "FLAC (lossless)", ".flac", False, ("flacenc",)),
    "wav": RecordFormat("wav", "WAV 16-bit (lossless)", ".wav", False, ("wavenc",)),
    "wav24": RecordFormat("wav24", "WAV 24-bit (lossless)", ".wav", False, ("wavenc",)),
    "wav32": RecordFormat("wav32", "WAV 32-bit float (lossless)", ".wav", False, ("wavenc",)),
    "wavpack": RecordFormat("wavpack", "WavPack (lossless)", ".wv", False, ("wavpackenc",)),
    "aiff": RecordFormat("aiff", "AIFF (lossless)", ".aiff", False, ("aiffmux",)),
    "mp3": RecordFormat("mp3", "MP3 (lossy)", ".mp3", True, ("lamemp3enc", "id3v2mux")),
    "ogg": RecordFormat("ogg", "Ogg Vorbis (lossy)", ".ogg", True, ("vorbisenc", "oggmux")),
    "opus": RecordFormat("opus", "Opus (lossy)", ".opus", True, ("opusenc", "oggmux")),
    "aac": RecordFormat("aac", "AAC M4A (lossy)", ".m4a", True, ("avenc_aac", "aacparse", "mp4mux")),
}


def _ensure_gst() -> None:
    Gst.init(None)


def available_formats() -> list[RecordFormat]:
    _ensure_gst()
    out: list[RecordFormat] = []
    for fmt in FORMATS.values():
        if all(Gst.ElementFactory.find(name) is not None for name in fmt.need):
            out.append(fmt)
    return out


def format_or_raise(key: str) -> RecordFormat:
    _ensure_gst()
    fmt = FORMATS.get(str(key).lower())
    if fmt is None:
        known = ", ".join(f.key for f in available_formats()) or ", ".join(FORMATS)
        raise ValueError(f"Unknown record format {key!r}. Use one of: {known}")
    missing = [name for name in fmt.need if Gst.ElementFactory.find(name) is None]
    if missing:
        raise RuntimeError(
            f"Format {fmt.key} needs GStreamer element(s): {', '.join(missing)}"
        )
    return fmt


def recordings_dir(preferred: str | None = None) -> Path:
    if preferred:
        return Path(preferred).expanduser()
    music = Path(os_music())
    return music / "Cascade EQ"


def os_music() -> str:
    import os

    xdg = os.environ.get("XDG_MUSIC_DIR")
    if xdg:
        return xdg
    return str(Path.home() / "Music")


def auto_filename(fmt: RecordFormat, directory: Path | None = None) -> Path:
    folder = directory or recordings_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return unique_path(folder / f"cascade-{stamp}{fmt.ext}")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 2
    while True:
        cand = parent / f"{stem}-{n}{suffix}"
        if not cand.exists():
            return cand
        n += 1


def resolve_output(
    path: str | None,
    fmt: RecordFormat,
    directory: str | None,
    exists: str = "unique",
) -> Path:
    if path:
        dest = Path(path).expanduser()
        if dest.exists() and dest.is_dir():
            dest = auto_filename(fmt, dest)
        elif dest.suffix.lower() not in {fmt.ext, fmt.ext.upper()}:
            if dest.suffix == "" or str(path).endswith("/"):
                dest.mkdir(parents=True, exist_ok=True)
                dest = auto_filename(fmt, dest)
            else:
                dest = dest.with_suffix(fmt.ext)
                dest.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.is_file():
            if exists == "overwrite":
                pass
            elif exists == "error":
                raise FileExistsError(f"{dest} already exists. Pass --force to overwrite.")
            else:
                dest = unique_path(dest)
        return dest
    return auto_filename(fmt, recordings_dir(directory))


def _add(bin: Gst.Bin, factory: str, name: str, **props):
    el = Gst.ElementFactory.make(factory, name)
    if el is None:
        raise RuntimeError(f"GStreamer element {factory} is missing")
    for key, value in props.items():
        el.set_property(key, value)
    bin.add(el)
    return el


def _link(src, dst) -> None:
    if src.link(dst):
        return
    srcpad = src.get_static_pad("src")
    sinkpad = dst.get_compatible_pad(srcpad, None)
    if sinkpad is None and dst.get_pad_template("audio_%u") is not None:
        sinkpad = dst.request_pad_simple("audio_%u")
    if sinkpad is None:
        raise RuntimeError(f"Failed to link {src.get_name()} → {dst.get_name()}")
    if srcpad.link(sinkpad) != Gst.PadLinkReturn.OK:
        raise RuntimeError(f"Failed to link {src.get_name()} → {dst.get_name()}")


def build_record_bin(path: str, fmt_key: str, bitrate: int | None = None) -> Gst.Bin:
    """Bin with a sink ghost pad: processed audio → encoded file."""
    _ensure_gst()
    fmt = format_or_raise(fmt_key)
    rec = Gst.Bin.new("recorder")
    queue = _add(rec, "queue", "rec_q")
    queue.set_property("max-size-buffers", 0)
    queue.set_property("max-size-bytes", 0)
    queue.set_property("max-size-time", 3_000_000_000)
    conv = _add(rec, "audioconvert", "rec_conv")
    resample = _add(rec, "audioresample", "rec_res")
    _link(queue, conv)
    _link(conv, resample)
    tail = _encode_chain(rec, resample, fmt, bitrate)
    sink = _add(rec, "filesink", "rec_file", location=str(path), sync=False)
    sink.set_property("async", False)
    _link(tail, sink)
    ghost = Gst.GhostPad.new("sink", queue.get_static_pad("sink"))
    ghost.set_active(True)
    rec.add_pad(ghost)
    return rec


def _kbps(bitrate: int | None, default: int) -> int:
    if bitrate is None:
        return default
    return int(bitrate) if bitrate < 10000 else int(round(bitrate / 1000))


def _encode_chain(rec: Gst.Bin, head, fmt: RecordFormat, bitrate: int | None):
    def caps(fmt_pcm: str):
        el = _add(rec, "capsfilter", "rec_caps")
        el.set_property(
            "caps",
            Gst.Caps.from_string(
                f"audio/x-raw,format={fmt_pcm},channels=2,rate=48000,layout=interleaved"
            ),
        )
        return el

    if fmt.key == "wav":
        c = caps("S16LE")
        enc = _add(rec, "wavenc", "rec_enc")
        _link(head, c)
        _link(c, enc)
        return enc
    if fmt.key == "wav24":
        c = caps("S24LE")
        enc = _add(rec, "wavenc", "rec_enc")
        _link(head, c)
        _link(c, enc)
        return enc
    if fmt.key == "wav32":
        c = caps("F32LE")
        enc = _add(rec, "wavenc", "rec_enc")
        _link(head, c)
        _link(c, enc)
        return enc
    if fmt.key == "flac":
        c = caps("S24LE")
        enc = _add(rec, "flacenc", "rec_enc")
        try:
            enc.set_property("quality", 5)
        except Exception:
            pass
        _link(head, c)
        _link(c, enc)
        return enc
    if fmt.key == "wavpack":
        c = caps("S32LE")
        enc = _add(rec, "wavpackenc", "rec_enc")
        _link(head, c)
        _link(c, enc)
        return enc
    if fmt.key == "aiff":
        c = caps("S16BE")
        mux = _add(rec, "aiffmux", "rec_mux")
        _link(head, c)
        _link(c, mux)
        return mux
    if fmt.key == "mp3":
        enc = _add(rec, "lamemp3enc", "rec_enc")
        if bitrate:
            enc.set_property("target", "bitrate")
            enc.set_property("bitrate", max(32, min(320, _kbps(bitrate, 192))))
            enc.set_property("cbr", True)
        else:
            enc.set_property("target", "quality")
            enc.set_property("quality", 2.0)
        mux = _add(rec, "id3v2mux", "rec_mux")
        _link(head, enc)
        _link(enc, mux)
        return mux
    if fmt.key == "ogg":
        enc = _add(rec, "vorbisenc", "rec_enc", quality=0.6)
        mux = _add(rec, "oggmux", "rec_mux")
        _link(head, enc)
        _link(enc, mux)
        return mux
    if fmt.key == "opus":
        bps = _kbps(bitrate, 128) * 1000
        enc = _add(rec, "opusenc", "rec_enc", bitrate=max(8000, min(510000, bps)))
        mux = _add(rec, "oggmux", "rec_mux")
        _link(head, enc)
        _link(enc, mux)
        return mux
    if fmt.key == "aac":
        bps = _kbps(bitrate, 192) * 1000
        enc = _add(rec, "avenc_aac", "rec_enc")
        try:
            enc.set_property("bitrate", max(32000, min(512000, bps)))
        except Exception:
            pass
        parse = _add(rec, "aacparse", "rec_parse")
        mux = _add(rec, "mp4mux", "rec_mux")
        _link(head, enc)
        _link(enc, parse)
        _link(parse, mux)
        return mux
    raise RuntimeError(f"No encoder chain for {fmt.key}")
