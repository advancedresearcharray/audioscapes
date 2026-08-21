"""libadwaita control panel — analog rack."""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")


def _enable_gi_cairo() -> bool:
    """GTK DrawingArea needs gi._gi_cairo; Ubuntu splits that into python3-gi-cairo."""
    try:
        gi.require_foreign("cairo")
        return True
    except (ImportError, ValueError):
        pass
    import importlib.util

    tag = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
    names = (
        f"_gi_cairo.{tag}-x86_64-linux-gnu.so",
        f"_gi_cairo.{tag}-aarch64-linux-gnu.so",
    )
    roots = [
        Path(__file__).resolve().parent.parent / ".vendor",
        Path("/usr/lib/python3/dist-packages/gi"),
        Path("/usr/lib/python3/dist-packages"),
    ]
    for root in roots:
        for name in names:
            so = root / name
            if not so.is_file():
                continue
            try:
                spec = importlib.util.spec_from_file_location("gi._gi_cairo", so)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules["gi._gi_cairo"] = mod
                spec.loader.exec_module(mod)
                gi.require_foreign("cairo")
                return True
            except Exception:
                sys.modules.pop("gi._gi_cairo", None)
    return False


_GI_CAIRO = _enable_gi_cairo()
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import APP_ID, APP_NAME
from .client import ClientError, ensure_daemon, meters, ping, request
from .dsp import BAND_LABELS, CHAIN_STAGES, MASTER_STEPS, MIX_STEPS, TONE_PROFILES, apply_tone_profile, auto_reveal_from_rta, clamp_master_db, default_blend, default_ride, default_tone, default_tone_auto, empty_bands, four_beat_seconds, keep_dly_tone, keep_mix, master_db_from_knob, master_knob_value, match_tone_preset, mix_amount, mix_eq_lifts, mix_levels, normalize_chain, normalize_tone, normalize_tone_auto, set_mix, tone_profile_names
from .paths import load_state, save_state, sessions_dir
from .presets import DIGITAL_KEYS, apply_preset, normalize_digital, preset_names
from .pulse import PulseError, current_output_role, default_sink, is_virtual_name, output_inventory, pick_hardware_sink, resolve_output_role, set_sink_port
from .record import available_formats
from .skins import SKIN_CHOICES, active as active_skin, apply as apply_skin, css_text, palette


def _cr() -> dict:
    return palette()


def _src(cr, key: str) -> None:
    c = palette()[key]
    if len(c) == 4:
        cr.set_source_rgba(*c)
    else:
        cr.set_source_rgb(*c)

def _scale(min_v: float, max_v: float, step: float, value: float, digits: int = 1) -> Gtk.Scale:
    adj = Gtk.Adjustment(lower=min_v, upper=max_v, step_increment=step, page_increment=step * 4, value=value)
    scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
    scale.set_digits(digits)
    scale.set_hexpand(True)
    scale.set_draw_value(True)
    scale.set_value_pos(Gtk.PositionType.RIGHT)
    return scale


def _db(level: float) -> float:
    return 20.0 * math.log10(max(level, 1e-9))


def _db_clip(db: float, lo: float = -36.0, hi: float = 3.0) -> float:
    return max(lo, min(hi, db))


def _level_t(level: float, lo: float = -36.0, hi: float = 3.0) -> float:
    return (_db_clip(_db(level), lo, hi) - lo) / (hi - lo)


def _glow_text(cr, x: float, y: float, text: str) -> None:
    p = palette()
    cr.save()
    cr.move_to(x, y)
    cr.text_path(text)
    if active_skin()["glow"]:
        cr.set_source_rgba(*p["fl_glow"])
        cr.set_line_width(float(p["glow_w"]))
        cr.stroke_preserve()
    cr.set_source_rgb(*p["fl"])
    cr.fill()
    cr.restore()


class CairoDraw(Gtk.DrawingArea if _GI_CAIRO else Gtk.Picture):
    """Cairo faceplate. Draws in place when gi cairo is available; otherwise copies a bitmap."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__()
        self._w = max(2, int(width))
        self._h = max(2, int(height))
        self._surf = None
        self.set_size_request(self._w, self._h)
        if _GI_CAIRO:
            self.set_draw_func(self._on_draw)
        else:
            self.set_content_fit(Gtk.ContentFit.FILL)

    def _on_draw(self, _area, cr, width: int, height: int) -> None:
        self._paint(cr, max(2, int(width)), max(2, int(height)))

    def _paint_size(self) -> tuple[int, int]:
        return self._w, self._h

    def _render(self) -> None:
        if _GI_CAIRO:
            self.queue_draw()
            return
        width, height = self._paint_size()
        width = max(2, int(width))
        height = max(2, int(height))
        if self._surf is None or self._surf.get_width() != width or self._surf.get_height() != height:
            self._surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(self._surf)
        self._paint(cr, width, height)
        self._surf.flush()
        data = self._surf.get_data()
        try:
            payload = GLib.Bytes.new(data)
        except (TypeError, ValueError):
            payload = GLib.Bytes.new(bytes(data))
        self.set_paintable(
            Gdk.MemoryTexture.new(
                width,
                height,
                Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                payload,
                self._surf.get_stride(),
            )
        )


class BarMeterView(CairoDraw):
    """Live output waveform plus amplitude histogram."""

    COLS = 36
    SEGS = 14

    def __init__(self) -> None:
        self.bars = [0.0] * self.COLS
        self.hold = [0.0] * self.COLS
        self.trace: list[float] = [0.0] * 256
        self.hist: list[float] = [0.0] * 36
        self.target_db = -18.0
        self.form_db = -42.0
        self.ride_db = 0.0
        super().__init__(560, 168)
        self.set_hexpand(True)
        self._render()

    def update(
        self,
        wave: list[float],
        hist: list[float] | None = None,
        target_db: float = -18.0,
        form_db: float | None = None,
        ride_db: float = 0.0,
    ) -> None:
        if wave:
            self.trace = [max(-1.0, min(1.0, float(v))) for v in wave]
            n = len(self.trace)
            for i in range(self.COLS):
                start = i * n // self.COLS
                end = max(start + 1, (i + 1) * n // self.COLS)
                mag = 0.0
                for v in self.trace[start:end]:
                    mag = max(mag, abs(float(v)))
                self.bars[i] = min(1.0, 0.45 * self.bars[i] + 0.55 * mag)
                self.hold[i] = max(mag, self.hold[i] * 0.90)
                if self.hold[i] < 0.02:
                    self.hold[i] = 0.0
        if hist:
            next_h = [max(0.0, min(1.0, float(v))) for v in hist]
            if len(next_h) < 8:
                next_h = next_h + [0.0] * (8 - len(next_h))
            if len(self.hist) != len(next_h):
                self.hist = next_h
            else:
                self.hist = [0.55 * a + 0.45 * b for a, b in zip(self.hist, next_h, strict=True)]
        self.target_db = float(target_db)
        if form_db is not None:
            self.form_db = float(form_db)
        self.ride_db = float(ride_db)
        sig = (
            tuple(round(v, 2) for v in self.bars[::3]),
            round(self.form_db),
            round(self.ride_db, 1),
            round(sum(self.hist), 2),
        )
        if sig == getattr(self, "_sig", None):
            return
        self._sig = sig
        self._render()

    def _paint(self, cr, width: int, height: int) -> None:
        _pana_glass(cr, 0, 0, width, height)
        pad_x, pad_top, pad_bot = 10, 28, 18
        hist_w = max(52.0, width * 0.16)
        gap = 8.0
        wave_w = width - pad_x * 2 - hist_w - gap
        wave_h = height - pad_top - pad_bot
        hx = pad_x + wave_w + gap
        self._paint_wave(cr, pad_x, pad_top, wave_w, wave_h)
        self._paint_hist(cr, hx, pad_top, hist_w, wave_h)
        cr.select_font_face("Ubuntu Mono" if not _analogish() else "Cantarell")
        cr.set_font_size(14 if _analogish() else 16)
        if _analogish():
            _src(cr, "silk")
            cr.move_to(12, 20)
            cr.show_text("WAVE")
            cr.move_to(width - hist_w - 4, 20)
            cr.show_text("HIST")
        else:
            _glow_text(cr, 10, 20, "WAVE")
            _glow_text(cr, width - hist_w - 2, 20, "HIST")
        tag = f"{self.form_db:+.0f}" if self.form_db > -40 else "—"
        ride = f"{self.ride_db:+.1f}"
        cr.set_font_size(13)
        if _analogish():
            _src(cr, "silk")
            cr.move_to(72, 20)
            cr.show_text(f"{tag} dB   RIDE {ride}")
        else:
            _glow_text(cr, 78, 20, f"{tag} dB   RIDE {ride}")

    def _scope_well(self, cr, x: float, y: float, w: float, h: float) -> None:
        cr.rectangle(x, y, w, h)
        if _analogish():
            cr.set_source_rgb(0.10, 0.09, 0.07)
        else:
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.42)
        cr.fill()
        cr.rectangle(x, y, w, h)
        _src(cr, "glass_edge")
        cr.set_line_width(1.0)
        cr.stroke()

    def _paint_wave(self, cr, x: float, y: float, w: float, h: float) -> None:
        self._scope_well(cr, x, y, w, h)
        mid = y + h * 0.5
        cr.set_source_rgba(1, 1, 1, 0.12 if not _analogish() else 0.18)
        cr.set_line_width(1.0)
        cr.move_to(x + 1, mid)
        cr.line_to(x + w - 1, mid)
        cr.stroke()
        trace = self.trace or [0.0]
        n = max(2, len(trace))
        cr.set_line_width(1.6)
        _src(cr, "bar_ok")
        cr.move_to(x + 1, mid - trace[0] * (h * 0.46))
        for i, sample in enumerate(trace):
            px = x + 1 + (i / (n - 1)) * max(1.0, w - 2)
            py = mid - float(sample) * (h * 0.46)
            cr.line_to(px, py)
        cr.stroke_preserve()
        cr.line_to(x + w - 1, mid)
        cr.line_to(x + 1, mid)
        cr.close_path()
        r, g, b = palette()["bar_ok"][:3]
        cr.set_source_rgba(r, g, b, 0.22)
        cr.fill()

    def _paint_hist(self, cr, x: float, y: float, w: float, h: float) -> None:
        from .meters import HIST_HI_DB, HIST_LO_DB

        self._scope_well(cr, x, y, w, h)
        bins = self.hist or [0.0]
        n = max(1, len(bins))
        row_h = h / n
        for i, mag in enumerate(bins):
            t = i / max(1, n - 1)
            by = y + h - (i + 1) * row_h
            bw = max(1.0, (w - 6) * float(mag))
            if t < 0.62:
                _src(cr, "bar_ok")
            elif t < 0.82:
                _src(cr, "bar_warn")
            else:
                _src(cr, "bar_hot")
            cr.rectangle(x + 3, by + 0.4, bw, max(1.0, row_h - 0.8))
            cr.fill()
        tgt = (self.target_db - HIST_LO_DB) / max(1.0, HIST_HI_DB - HIST_LO_DB)
        ty = y + h - tgt * h
        _src(cr, "bar_hold")
        cr.set_line_width(1.4)
        cr.move_to(x + 1, ty)
        cr.line_to(x + w - 1, ty)
        cr.stroke()


class NeedleView(CairoDraw):
    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.level = 0.0
        self.peak = 0.0
        super().__init__(210, 168)
        self._render()

    def update(self, peak: float, rms: float) -> None:
        target = min(1.0, 0.65 * rms + 0.35 * peak)
        nxt = self.level * 0.72 + target * 0.28
        pk = max(min(1.0, peak), self.peak * 0.84)
        dirty = abs(nxt - self.level) >= 0.004 or abs(pk - self.peak) >= 0.004
        self.level = nxt
        self.peak = pk
        if dirty:
            self._render()

    def _paint(self, cr, width: int, height: int) -> None:
        _pana_glass(cr, 0, 0, width, height)
        if _analogish():
            r = min(width, height) * 0.38
            _vu_round(cr, width * 0.5, height * 0.48, r, _level_t(self.level), self.channel)
            _pana_lamp(cr, 12, height - 28, "PEAK", _db(self.peak) >= -0.5, "red")
            return
        _pana_fl(cr, 12, 24, f"VU  {self.channel}", 16)
        _pana_vu(cr, 12, 42, width - 24, 16, self.level, self.channel)
        cr.select_font_face("Cantarell")
        cr.set_font_size(11)
        _src(cr, "scale")
        cr.move_to(12, 76)
        cr.show_text("-20       -10        -5        0        +3   +6  dB")
        _pana_vu(cr, 12, 90, width - 24, 16, self.peak, "PK")
        _pana_lamp(cr, 12, height - 30, "PEAK", _db(self.peak) >= -0.5, "red")
        _pana_lamp(cr, 86, height - 30, "NORM", _db(self.level) < -0.5)
        _pana_fl(cr, width - 86, height - 22, f"{_db(self.level):+5.1f}", 16)


class EqWaveView(CairoDraw):
    """Draggable digital EQ waveform across 16 graphic bands."""

    LO, HI = -12.0, 12.0
    PAD_L, PAD_R, PAD_T, PAD_B = 48, 16, 18, 32

    def __init__(self, on_change) -> None:
        self._on_change = on_change
        self.gains = [0.0] * 16
        self.rta = [0.0] * 16
        self.lifts = [0.0] * 16
        self.auto_on = False
        self._active = -1
        super().__init__(920, 236)
        self.set_hexpand(True)
        try:
            self.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        except Exception:
            pass
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        click = Gtk.GestureClick()
        click.connect("pressed", self._pressed)
        self.add_controller(click)
        self._render()

    def set_bands(self, bands: list[float]) -> None:
        self.gains = [max(self.LO, min(self.HI, float(v))) for v in (bands + [0.0] * 16)[:16]]
        self._render()

    def bands(self) -> list[float]:
        return list(self.gains)

    def _curve(self) -> list[float]:
        if self.auto_on:
            return mix_eq_lifts(self.gains, self.lifts)
        return list(self.gains)

    def set_lifts(self, lifts: list[float]) -> None:
        next_lifts = [max(0.0, min(12.0, float(v))) for v in (list(lifts) + [0.0] * 16)[:16]]
        if self.lifts and max(abs(a - b) for a, b in zip(self.lifts, next_lifts)) < 0.05:
            self.lifts = next_lifts
            return
        self.lifts = next_lifts
        self._render()

    def set_rta(self, levels: list[float]) -> None:
        next_rta = [max(0.0, min(1.0, float(v))) for v in (list(levels) + [0.0] * 16)[:16]]
        if self.rta and max(abs(a - b) for a, b in zip(self.rta, next_rta)) < 0.012:
            self.rta = next_rta
            return
        self.rta = next_rta
        self._render()

    def _paint_size(self) -> tuple[int, int]:
        return (
            max(self._w, self.get_width() or self._w),
            max(self._h, self.get_height() or self._h),
        )

    def _plot(self) -> tuple[float, float, float, float]:
        width = max(self._w, self.get_width() or self._w)
        height = max(self._h, self.get_height() or self._h)
        left, top = self.PAD_L, self.PAD_T
        inner_w = max(8, width - self.PAD_L - self.PAD_R)
        inner_h = max(8, height - self.PAD_T - self.PAD_B)
        return left, top, inner_w, inner_h

    def _xy_to_band(self, x: float, y: float) -> tuple[int, float]:
        left, top, inner_w, inner_h = self._plot()
        n = max(1, len(self.gains) - 1)
        i = int(round((x - left) / inner_w * n))
        i = max(0, min(15, i))
        db = self.HI - (y - top) / inner_h * (self.HI - self.LO)
        db = max(self.LO, min(self.HI, round(db * 2) / 2))
        return i, db

    def _pressed(self, gesture, n_press: int, x: float, y: float) -> None:
        if n_press == 2:
            i, _db = self._xy_to_band(x, y)
            self.gains[i] = 0.0
            self._active = i
            self._render()
            self._on_change()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _drag_begin(self, _g, x: float, y: float) -> None:
        self._sx, self._sy = x, y
        self._apply_xy(x, y)

    def _drag_update(self, _g, dx: float, dy: float) -> None:
        self._apply_xy(self._sx + dx, self._sy + dy)

    def _drag_end(self, *_args) -> None:
        self._active = -1
        self._render()

    def _apply_xy(self, x: float, y: float) -> None:
        i, db = self._xy_to_band(x, y)
        self._active = i
        if self.gains[i] != db:
            self.gains[i] = db
            self._render()
            self._on_change()
        else:
            self._render()

    def _y_for(self, db: float, top: float, inner_h: float) -> float:
        return top + (self.HI - db) / (self.HI - self.LO) * inner_h

    def _paint(self, cr, width: int, height: int) -> None:
        _pana_glass(cr, 0, 0, width, height)
        left, top, inner_w, inner_h = self._plot()
        curve = self._curve()
        n = len(curve)
        zero_y = self._y_for(0.0, top, inner_h)
        self._paint_rta(cr, left, top, inner_w, inner_h, n)
        if _analogish():
            cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(12)
            _src(cr, "silk")
            cr.move_to(left + 4, 16)
            cr.show_text("GRAPHIC  EQUALIZER  ·  AUTO / 4 BEATS")
            slot_w = max(8, inner_w / n * 0.38)
            for i, db in enumerate(curve):
                x = left + i / max(1, n - 1) * inner_w
                y = self._y_for(db, top, inner_h)
                _src(cr, "inner")
                cr.rectangle(x - slot_w / 2, top, slot_w, inner_h)
                cr.fill()
                mag = self.rta[i] if i < len(self.rta) else 0.0
                if mag > 0.03:
                    rh = inner_h * mag
                    cr.set_source_rgba(0.25, 0.95, 0.75, 0.45)
                    cr.rectangle(x - slot_w / 2, top + inner_h - rh, slot_w, rh)
                    cr.fill()
                _src(cr, "edge")
                cr.set_line_width(1)
                cr.rectangle(x - slot_w / 2, top, slot_w, inner_h)
                cr.stroke()
                _src(cr, "eq_active") if i == self._active else _src(cr, "knob0")
                cr.rectangle(x - slot_w, y - 7, slot_w * 2, 14)
                cr.fill()
                _src(cr, "silk")
                cr.move_to(x - 10, top + inner_h + 16)
                cr.show_text(BAND_LABELS[i] if i < len(BAND_LABELS) else "")
            _src(cr, "silk")
            cr.move_to(6, self._y_for(12, top, inner_h) + 4)
            cr.show_text("+12")
            cr.move_to(10, zero_y + 4)
            cr.show_text("0")
            cr.move_to(6, self._y_for(-12, top, inner_h) + 4)
            cr.show_text("-12")
            return
        if _form() == "matrix":
            cr.select_font_face("Ubuntu Mono")
            cr.set_font_size(12)
            _src(cr, "eq_grid")
            for i, db in enumerate(curve):
                x0 = left + i / n * inner_w
                bw = inner_w / n - 3
                y = self._y_for(db, top, inner_h)
                _src(cr, "eq_bar")
                cr.rectangle(x0, min(y, zero_y), max(2, bw), abs(zero_y - y))
                cr.fill()
                _src(cr, "eq_node")
                cr.rectangle(x0, y - 3, bw, 6)
                cr.fill()
                _glow_text(cr, x0, top + inner_h + 16, BAND_LABELS[i] if i < len(BAND_LABELS) else "")
            _glow_text(cr, left + 4, 16, "EQ  MATRIX")
            return
        _src(cr, "eq_grid")
        cr.set_line_width(1)
        for db in (-12, -6, 0, 6, 12):
            y = self._y_for(db, top, inner_h)
            cr.move_to(left, y)
            cr.line_to(left + inner_w, y)
            cr.stroke()
        for i in range(n):
            x = left + i / max(1, n - 1) * inner_w
            cr.move_to(x, top)
            cr.line_to(x, top + inner_h)
            cr.stroke()
        _src(cr, "eq_zero")
        cr.set_dash([4, 4])
        cr.move_to(left, zero_y)
        cr.line_to(left + inner_w, zero_y)
        cr.stroke()
        cr.set_dash([])

        samples = 180
        pts: list[tuple[float, float]] = []
        for s in range(samples):
            t = s / (samples - 1) * (n - 1)
            i0 = int(t)
            i1 = min(n - 1, i0 + 1)
            f = t - i0
            mu = (1 - math.cos(f * math.pi)) * 0.5
            db = curve[i0] * (1 - mu) + curve[i1] * mu
            pts.append((left + t / (n - 1) * inner_w, self._y_for(db, top, inner_h)))

        _src(cr, "eq_fill")
        cr.move_to(pts[0][0], zero_y)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(pts[-1][0], zero_y)
        cr.close_path()
        cr.fill()

        _src(cr, "eq_bar")
        cr.set_line_width(1)
        for i, db in enumerate(curve):
            x0 = left + i / max(1, n - 1) * inner_w
            x1 = left + (i + 0.92) / max(1, n - 1) * inner_w if i < n - 1 else x0 + inner_w / n
            y = self._y_for(db, top, inner_h)
            cr.rectangle(x0, min(y, zero_y), max(2, x1 - x0), abs(zero_y - y))
            cr.fill()

        _src(cr, "eq_glow")
        cr.set_line_width(6)
        cr.move_to(*pts[0])
        for x, y in pts[1:]:
            cr.line_to(x, y)
        cr.stroke()
        _src(cr, "eq_line")
        cr.set_line_width(2.0)
        cr.move_to(*pts[0])
        for x, y in pts[1:]:
            cr.line_to(x, y)
        cr.stroke()

        cr.select_font_face("Ubuntu Mono")
        cr.set_font_size(14)
        for i, db in enumerate(curve):
            x = left + i / max(1, n - 1) * inner_w
            y = self._y_for(db, top, inner_h)
            _src(cr, "eq_stem")
            cr.set_line_width(1)
            cr.move_to(x, zero_y)
            cr.line_to(x, y)
            cr.stroke()
            r = 6.2 if i == self._active else 4.8
            _src(cr, "eq_active") if i == self._active else _src(cr, "eq_node")
            cr.arc(x, y, r, 0, math.tau)
            cr.fill()
            cr.set_source_rgb(0.02, 0.05, 0.07)
            cr.arc(x, y, 1.6, 0, math.tau)
            cr.fill()
            _glow_text(cr, x - 12, top + inner_h + 16, BAND_LABELS[i] if i < len(BAND_LABELS) else "")

        cr.set_font_size(14)
        _glow_text(cr, 6, self._y_for(12, top, inner_h) + 4, "+12")
        _glow_text(cr, 10, zero_y + 4, "0")
        _glow_text(cr, 6, self._y_for(-12, top, inner_h) + 4, "-12")
        live = max(self.rta) > 0.04
        if self.auto_on:
            caption = "EQ  WAVE  ·  RTA live  ·  AUTO compressing low / mid / high up"
        elif live:
            caption = "EQ  WAVE  ·  RTA live  ·  AUTO lifts low / mid / high"
        else:
            caption = "EQ  WAVE  ·  drag to shape  ·  AUTO compresses the full spectrum up"
        _glow_text(
            cr,
            left + 4,
            16,
            caption,
        )

    def _paint_rta(self, cr, left: float, top: float, inner_w: float, inner_h: float, n: int) -> None:
        if max(self.rta, default=0.0) < 0.02:
            return
        slot = inner_w / max(1, n)
        for i, mag in enumerate(self.rta):
            h = inner_h * mag
            x = left + i * slot + slot * 0.18
            w = slot * 0.64
            y = top + inner_h - h
            if mag > 0.82:
                cr.set_source_rgba(1.0, 0.45, 0.2, 0.38)
            elif mag > 0.62:
                cr.set_source_rgba(1.0, 0.82, 0.25, 0.32)
            else:
                cr.set_source_rgba(0.2, 0.95, 0.75, 0.28)
            cr.rectangle(x, y, w, h)
            cr.fill()


def _rrect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w / 2, h / 2)
    if r < 0.5:
        cr.rectangle(x, y, w, h)
        return
    cr.new_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _form() -> str:
    return str(active_skin().get("form", "rack"))


def _analogish() -> bool:
    return _form() in {"cathedral", "console", "teak", "receiver", "tube"}


def _wood_grain(cr, x: float, y: float, w: float, h: float) -> None:
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    cr.set_line_width(1.15)
    for i in range(int(x), int(x + w) + 6, 6):
        phase = i * 0.19
        cr.set_source_rgba(0.10, 0.05, 0.02, 0.09 + 0.07 * abs(math.sin(phase)))
        cr.move_to(i, y)
        cr.curve_to(i + 12, y + h * 0.33, i - 10, y + h * 0.66, i + 7, y + h)
        cr.stroke()
    cr.restore()


def _vu_round(cr, cx: float, cy: float, r: float, level: float, name: str = "") -> None:
    p = palette()
    t = max(0.0, min(1.0, float(level)))
    ring = cairo.RadialGradient(cx - r * 0.25, cy - r * 0.3, 1, cx, cy, r)
    ring.add_color_stop_rgb(0, *p["knob0"])
    ring.add_color_stop_rgb(1, *p["knob1"])
    cr.set_source(ring)
    cr.arc(cx, cy, r, 0, math.tau)
    cr.fill()
    cr.set_source_rgb(0.93, 0.86, 0.68)
    cr.arc(cx, cy, r * 0.82, 0, math.tau)
    cr.fill()
    cr.set_source_rgb(0.12, 0.08, 0.04)
    cr.set_line_width(1.0)
    a0, a1 = math.radians(210), math.radians(330)
    for i in range(9):
        u = i / 8
        a = a0 + u * (a1 - a0)
        inner = r * (0.52 if i % 2 == 0 else 0.58)
        if u >= 0.72:
            cr.set_source_rgb(0.75, 0.12, 0.08)
        else:
            cr.set_source_rgb(0.12, 0.08, 0.04)
        cr.move_to(cx + math.cos(a) * inner, cy + math.sin(a) * inner)
        cr.line_to(cx + math.cos(a) * r * 0.74, cy + math.sin(a) * r * 0.74)
        cr.stroke()
    ang = a0 + t * (a1 - a0)
    cr.set_source_rgb(0.12, 0.05, 0.02)
    cr.set_line_width(1.6)
    cr.move_to(cx, cy)
    cr.line_to(cx + math.cos(ang) * r * 0.70, cy + math.sin(ang) * r * 0.70)
    cr.stroke()
    cr.arc(cx, cy, 3.2, 0, math.tau)
    cr.fill()
    if name:
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(9, r * 0.22))
        _src(cr, "silk")
        tw = cr.text_extents(name)[4]
        cr.move_to(cx - tw / 2, cy + r + 12)
        cr.show_text(name)


def _pana_chassis(cr, width: int, height: int) -> None:
    p = palette()
    skin = active_skin()
    form = _form()
    if form == "cathedral":
        arch = min(58.0, height * 0.30)
        cr.move_to(0, arch)
        cr.curve_to(width * 0.18, -8, width * 0.82, -8, width, arch)
        cr.line_to(width, height)
        cr.line_to(0, height)
        cr.close_path()
        g = cairo.LinearGradient(0, 0, 0, height)
        g.add_color_stop_rgb(0, *p["chassis0"])
        g.add_color_stop_rgb(0.45, *p["chassis1"])
        g.add_color_stop_rgb(1, *p["chassis3"])
        cr.set_source(g)
        cr.fill_preserve()
        _src(cr, "edge")
        cr.set_line_width(3)
        cr.stroke()
        _wood_grain(cr, 0, 0, width, height)
        if skin["screws"]:
            _pana_screw(cr, 16, arch + 10, 4.4)
            _pana_screw(cr, width - 16, arch + 10, 4.4)
        return
    if form == "console":
        _rrect(cr, 0, 0, width, height, min(18, height / 5))
        g = cairo.LinearGradient(0, 0, 0, height)
        g.add_color_stop_rgb(0, *p["chassis0"])
        g.add_color_stop_rgb(0.46, *p["chassis1"])
        g.add_color_stop_rgb(0.47, *p["chassis2"])
        g.add_color_stop_rgb(1, *p["chassis3"])
        cr.set_source(g)
        cr.fill_preserve()
        _src(cr, "edge")
        cr.set_line_width(4)
        cr.stroke()
        return
    if form in {"teak", "receiver"}:
        side = 34 if form == "teak" else 26
        g = cairo.LinearGradient(0, 0, 0, height)
        g.add_color_stop_rgb(0, *p["chassis0"])
        g.add_color_stop_rgb(0.12, *p["chassis1"])
        g.add_color_stop_rgb(1, *p["chassis3"])
        cr.set_source(g)
        cr.rectangle(side, 0, width - side * 2, height)
        cr.fill()
        if skin["hair"]:
            cr.set_line_width(1)
            hair = p["hair"]
            for i in range(0, height, 2):
                cr.set_source_rgba(hair[0], hair[1], hair[2], hair[3] * (1.0 + 0.5 * ((i // 2) % 3 == 0)))
                cr.move_to(side, i + 0.5)
                cr.line_to(width - side, i + 0.5)
                cr.stroke()
        cr.set_source_rgb(*p["chassis2"])
        cr.rectangle(0, 0, side, height)
        cr.fill()
        cr.rectangle(width - side, 0, side, height)
        cr.fill()
        _wood_grain(cr, 0, 0, side, height)
        _wood_grain(cr, width - side, 0, side, height)
        _src(cr, "edge")
        cr.set_line_width(2)
        cr.rectangle(1, 1, width - 2, height - 2)
        cr.stroke()
        return
    if form == "mesh":
        pad = 7
        cr.set_source_rgba(*p["glass_sheen"][:3], 0.22)
        _rrect(cr, pad - 4, pad - 4, width - 2 * pad + 8, height - 2 * pad + 8, 22)
        cr.fill()
        _rrect(cr, pad, pad, width - 2 * pad, height - 2 * pad, 18)
        g = cairo.LinearGradient(0, 0, 0, height)
        g.add_color_stop_rgb(0, *p["chassis0"])
        g.add_color_stop_rgb(1, *p["chassis3"])
        cr.set_source(g)
        cr.fill_preserve()
        _src(cr, "edge")
        cr.set_line_width(2.4)
        cr.stroke()
        return
    if form == "matrix":
        cr.set_source_rgb(*p["chassis3"])
        cr.rectangle(0, 0, width, height)
        cr.fill()
        cr.set_source_rgba(*p["edge"], 0.18)
        cr.set_line_width(1)
        for i in range(0, width, 10):
            cr.move_to(i + 0.5, 0)
            cr.line_to(i + 0.5, height)
            cr.stroke()
        for i in range(0, height, 10):
            cr.move_to(0, i + 0.5)
            cr.line_to(width, i + 0.5)
            cr.stroke()
        _src(cr, "edge")
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.stroke()
        return
    if form == "mini":
        cr.set_source_rgb(*p["chassis3"])
        cr.rectangle(0, 0, width, height)
        cr.fill()
        g = cairo.LinearGradient(0, 0, 0, height)
        g.add_color_stop_rgb(0, *p["chassis0"])
        g.add_color_stop_rgb(0.08, *p["chassis1"])
        g.add_color_stop_rgb(1, *p["chassis3"])
        cr.set_source(g)
        cr.rectangle(6, 6, width - 12, height - 12)
        cr.fill()
        _src(cr, "edge")
        cr.set_line_width(1)
        cr.rectangle(8, 8, width - 16, height - 16)
        cr.stroke()
        return
    if form == "tube":
        _rrect(cr, 0, 0, width, height, 16)
        g = cairo.LinearGradient(0, 0, 0, height)
        g.add_color_stop_rgb(0, *p["chassis0"])
        g.add_color_stop_rgb(1, *p["chassis3"])
        cr.set_source(g)
        cr.fill_preserve()
        _src(cr, "edge")
        cr.set_line_width(8)
        cr.stroke()
        _rrect(cr, 10, 10, width - 20, height - 20, 10)
        cr.set_line_width(1)
        cr.stroke()
        if skin["screws"]:
            for cx, cy in ((18, 18), (width - 18, 18), (18, height - 18), (width - 18, height - 18)):
                _pana_screw(cr, cx, cy, 4.2)
        return
    g = cairo.LinearGradient(0, 0, 0, height)
    g.add_color_stop_rgb(0, *p["chassis0"])
    g.add_color_stop_rgb(0.08, *p["chassis1"])
    g.add_color_stop_rgb(0.5, *p["chassis2"])
    g.add_color_stop_rgb(1, *p["chassis3"])
    cr.set_source(g)
    cr.rectangle(0, 0, width, height)
    cr.fill()
    if skin["hair"]:
        cr.set_line_width(1)
        hair = p["hair"]
        for i in range(0, height, 2):
            a = hair[3] * (1.0 + 0.6 * ((i // 2) % 3 == 0))
            cr.set_source_rgba(hair[0], hair[1], hair[2], a)
            cr.move_to(0, i + 0.5)
            cr.line_to(width, i + 0.5)
            cr.stroke()
    _src(cr, "edge")
    cr.set_line_width(2)
    cr.rectangle(1, 1, width - 2, height - 2)
    cr.stroke()
    _src(cr, "inner")
    cr.set_line_width(1)
    cr.rectangle(3, 3, width - 6, height - 6)
    cr.stroke()
    if skin["screws"]:
        for cx, cy in ((10, 10), (width - 10, 10), (10, height - 10), (width - 10, height - 10)):
            _pana_screw(cr, cx, cy)


def _pana_screw(cr, cx: float, cy: float, r: float = 4.0) -> None:
    _src(cr, "screw")
    cr.arc(cx, cy, r, 0, math.tau)
    cr.fill()
    _src(cr, "screw_in")
    cr.arc(cx, cy, r - 1.3, 0, math.tau)
    cr.fill()
    _src(cr, "screw_slot")
    cr.set_line_width(1.1)
    cr.move_to(cx - r * 0.5, cy)
    cr.line_to(cx + r * 0.5, cy)
    cr.stroke()


def _pana_header(cr, width: int, model: str, subtitle: str, right: str = "", power: bool = True) -> None:
    brand = str(active_skin()["brand_name"])
    form = _form()
    slant = cairo.FONT_SLANT_ITALIC if form == "cathedral" else cairo.FONT_SLANT_NORMAL
    face = "Ubuntu Mono" if form == "matrix" else "Cantarell"
    cr.select_font_face(face, slant, cairo.FONT_WEIGHT_BOLD)
    compact = width < 680
    cr.set_font_size(18 if compact else 22)
    _src(cr, "brand")
    cr.move_to(16 if compact else 18, 27)
    cr.show_text(brand)
    name_x = 16 + min(200, 11 * len(brand))
    cr.set_font_size(13 if compact else 14)
    _src(cr, "model")
    cr.move_to(name_x if compact else max(152, name_x + 8), 26)
    cr.show_text(model)
    if not compact:
        cr.set_font_size(13)
        _src(cr, "sub")
        cr.move_to(max(236, name_x + 90), 27)
        cr.show_text(subtitle)
        if right:
            cr.move_to(width - 292, 27)
            cr.show_text(right)
    _src(cr, "power_lbl")
    cr.set_font_size(11)
    cr.move_to(width - 88, 18)
    cr.show_text("ON" if _analogish() else "POWER")
    if power:
        _src(cr, "power_on")
        cr.arc(width - 22, 24, 6.4 if _analogish() else 5.8, 0, math.tau)
        cr.fill()
        _src(cr, "power_glow")
        cr.arc(width - 22, 24, 9, 0, math.tau)
        cr.fill()
    else:
        _src(cr, "power_off")
        cr.arc(width - 22, 24, 5.8, 0, math.tau)
        cr.fill()


def _pana_glass(cr, x: float, y: float, w: float, h: float) -> None:
    p = palette()
    form = _form()
    rad = 14.0 if form == "mesh" else (0.0 if form == "matrix" else (10.0 if _analogish() else float(active_skin()["radius"])))
    _rrect(cr, x, y, w, h, rad)
    glass = cairo.LinearGradient(x, y, x, y + h)
    if _analogish():
        glass.add_color_stop_rgb(0, 0.94, 0.88, 0.70)
        glass.add_color_stop_rgb(0.55, 0.88, 0.80, 0.58)
        glass.add_color_stop_rgb(1, 0.80, 0.70, 0.48)
    else:
        glass.add_color_stop_rgb(0, *p["glass0"])
        glass.add_color_stop_rgb(0.5, *p["glass1"])
        glass.add_color_stop_rgb(1, *p["glass2"])
    cr.set_source(glass)
    cr.fill_preserve()
    _src(cr, "glass_edge")
    cr.set_line_width(3 if _analogish() else 2)
    cr.stroke()
    if form == "matrix":
        cr.set_source_rgba(*p["fl"], 0.06)
        cr.set_line_width(1)
        for i in range(int(y), int(y + h), 3):
            cr.move_to(x, i + 0.5)
            cr.line_to(x + w, i + 0.5)
            cr.stroke()
        return
    if not _analogish():
        _src(cr, "glass_sheen")
        cr.rectangle(x + 4, y + 3, max(1, w - 8), 12)
        cr.fill()


def _pana_fl(cr, x: float, y: float, text: str, size: float = 16, dim: bool = False) -> None:
    p = palette()
    cr.select_font_face("Ubuntu Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size)
    cr.move_to(x, y)
    cr.text_path(text)
    if dim:
        cr.set_source_rgba(*p["fl_dim"])
        cr.fill()
        return
    if active_skin()["glow"]:
        cr.set_source_rgba(*p["fl_glow"])
        cr.set_line_width(float(p["glow_w"]))
        cr.stroke_preserve()
    cr.set_source_rgb(*p["fl"])
    cr.fill()


def _pana_lamp(cr, x: float, y: float, label: str, on: bool, color: str = "cyan") -> None:
    form = _form()
    _src(cr, "lamp_well")
    if form == "matrix":
        cr.rectangle(x, y, 12, 8)
        cr.fill()
    elif form == "mesh":
        cr.move_to(x + 6, y)
        cr.line_to(x + 12, y + 4)
        cr.line_to(x + 6, y + 8)
        cr.line_to(x, y + 4)
        cr.close_path()
        cr.fill()
    elif _analogish():
        cr.arc(x + 6, y + 4, 5, 0, math.tau)
        cr.fill()
    else:
        _rrect(cr, x, y, 13, 8, 1)
        cr.fill()
    if on:
        if color == "red":
            _src(cr, "lamp_red")
        elif color == "amber":
            _src(cr, "lamp_amber")
        else:
            _src(cr, "lamp_cyan")
        if _analogish() or form == "mesh":
            cr.arc(x + 6, y + 4, 3.6, 0, math.tau)
            cr.fill()
        else:
            cr.rectangle(x + 1.6, y + 1.6, 9.8, 5)
            cr.fill()
    cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(10)
    _src(cr, "lamp_on") if on else _src(cr, "lamp_off")
    cr.move_to(x + 16, y + 8)
    cr.show_text(label)


def _pana_vu(cr, x: float, y: float, w: float, h: float, level: float, name: str, amount: float | None = None) -> None:
    t = _level_t(level) if amount is None else max(0.0, min(1.0, float(amount)))
    form = _form()
    if _analogish():
        if h >= 40 and w >= 70:
            _vu_round(cr, x + w * 0.55, y + h * 0.48, min(w, h) * 0.42, t, name)
            return
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(10)
        _src(cr, "silk")
        cr.move_to(x, y + h * 0.75)
        cr.show_text(name)
        cr.set_line_width(1.2)
        cr.move_to(x + 18, y + h * 0.55)
        cr.line_to(x + w, y + h * 0.55)
        cr.stroke()
        px = x + 18 + t * max(8, w - 22)
        cr.set_line_width(2)
        cr.move_to(px, y + 1)
        cr.line_to(px, y + h - 1)
        cr.stroke()
        return
    cr.select_font_face("Ubuntu Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(11)
    _src(cr, "vu_name")
    cr.move_to(x, y + 10)
    cr.show_text(name)
    segs = 22
    gap = 3.2 if form == "matrix" else (1.2 if form == "mesh" else 2.0)
    sw = (w - 20 - (segs - 1) * gap) / segs
    for i in range(segs):
        sx = x + 20 + i * (sw + gap)
        on = t >= (i + 0.5) / segs
        if i >= segs - 3:
            _src(cr, "vu_hot") if on else _src(cr, "vu_hot_off")
        elif i >= segs - 7:
            _src(cr, "vu_warn") if on else _src(cr, "vu_warn_off")
        else:
            _src(cr, "vu_ok") if on else _src(cr, "vu_ok_off")
        if form == "mesh":
            _rrect(cr, sx, y, sw, h, h / 2)
            cr.fill()
        else:
            cr.rectangle(sx, y, sw, h)
            cr.fill()


def _pana_knob_scale(cr, cx: float, cy: float, r: float, steps: int, bipolar: bool = False) -> None:
    """Even tick marks numbered 1–N around a 270° rotary sweep."""
    n = max(2, int(steps))
    cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(13 if bipolar else 7.5)
    mid = (n - 1) // 2
    for i in range(n):
        t = i / (n - 1)
        ang = math.pi * 0.75 + t * math.pi * 1.5
        c, s = math.cos(ang), math.sin(ang)
        if bipolar:
            if t < 0.5:
                cr.set_source_rgba(0.62, 0.84, 1.0, 0.75)
            elif t > 0.5:
                cr.set_source_rgba(0.22, 0.58, 1.0, 0.95)
            else:
                cr.set_source_rgba(0.88, 0.94, 1.0, 1.0)
            cr.set_line_width(2.2 if i in (0, mid, n - 1) else 1.35)
        else:
            _src(cr, "knob_ring")
            cr.set_line_width(1.15)
        cr.move_to(cx + c * r * 1.10, cy + s * r * 1.10)
        cr.line_to(cx + c * r * 1.28, cy + s * r * 1.28)
        cr.stroke()
        if bipolar and i not in (0, mid, n - 1):
            continue
        if bipolar:
            num = {0: "−12", mid: "0", n - 1: "+12"}[i]
        else:
            num = str(i + 1)
            _src(cr, "knob_lbl")
        ext = cr.text_extents(num)
        rr = r * (1.62 if bipolar else 1.54)
        cr.move_to(
            cx + c * rr - ext.width / 2 - ext.x_bearing,
            cy + s * rr - ext.height / 2 - ext.y_bearing,
        )
        cr.show_text(num)


def _pana_gain_backlight(cr, cx: float, cy: float, r: float, pos: float) -> None:
    mag = abs(pos - 0.5) * 2.0
    if mag < 0.02:
        return
    if pos < 0.5:
        rgb = (0.62, 0.84, 1.0)
        peak = 0.16 + 0.38 * mag
    else:
        rgb = (0.18, 0.52, 1.0)
        peak = 0.22 + 0.52 * mag
    glow = cairo.RadialGradient(cx, cy, r * 0.12, cx, cy, r * 1.95)
    glow.add_color_stop_rgba(0.0, *rgb, peak)
    glow.add_color_stop_rgba(0.42, *rgb, peak * 0.42)
    glow.add_color_stop_rgba(1.0, *rgb, 0.0)
    cr.set_source(glow)
    cr.arc(cx, cy, r * 1.95, 0, math.tau)
    cr.fill()


def _pana_gain_trace(cr, cx: float, cy: float, r: float, pos: float) -> None:
    start = math.pi * 0.75
    sweep = math.pi * 1.5
    zero = start + 0.5 * sweep
    end = start + sweep
    ang = start + pos * sweep
    mag = abs(pos - 0.5) * 2.0
    rr = r * 1.09
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(2.6)
    cr.set_source_rgba(0.62, 0.84, 1.0, 0.28)
    cr.arc(cx, cy, rr, start, zero)
    cr.stroke()
    cr.set_source_rgba(0.20, 0.56, 1.0, 0.42)
    cr.arc(cx, cy, rr, zero, end)
    cr.stroke()
    if mag < 0.03:
        return
    cr.set_line_width(4.6)
    if pos < 0.5:
        cr.set_source_rgba(0.72, 0.90, 1.0, 0.40 + 0.60 * mag)
        cr.arc(cx, cy, rr, ang, zero)
        cr.stroke()
    else:
        cr.set_source_rgba(0.18, 0.62, 1.0, 0.50 + 0.50 * mag)
        cr.arc(cx, cy, rr, zero, ang)
        cr.stroke()


def _pana_knob(
    cr, cx: float, cy: float, r: float, pos: float, label: str = "", steps: int = 0, bipolar: bool = False
) -> None:
    p = palette()
    form = _form()
    pos = max(0.0, min(1.0, float(pos)))
    ang = math.pi * 0.75 + pos * math.pi * 1.5
    if bipolar:
        _pana_gain_backlight(cr, cx, cy, r, pos)
    if form == "matrix":
        cr.set_source_rgb(*p["knob1"])
        cr.rectangle(cx - r, cy - r, r * 2, r * 2)
        cr.fill_preserve()
        _src(cr, "edge")
        cr.set_line_width(1)
        cr.stroke()
        _src(cr, "knob_ptr")
        cr.set_line_width(2)
        cr.move_to(cx, cy)
        cr.line_to(cx + math.cos(ang) * r * 0.7, cy + math.sin(ang) * r * 0.7)
        cr.stroke()
    elif form == "mesh":
        _src(cr, "glass_edge")
        cr.set_line_width(3)
        cr.arc(cx, cy, r, 0, math.tau)
        cr.stroke()
        _src(cr, "lamp_cyan")
        cr.arc(cx + math.cos(ang) * r * 0.62, cy + math.sin(ang) * r * 0.62, 3.2, 0, math.tau)
        cr.fill()
    else:
        g = cairo.RadialGradient(cx - r * 0.3, cy - r * 0.3, 1, cx, cy, r)
        g.add_color_stop_rgb(0, *p["knob0"])
        g.add_color_stop_rgb(1, *p["knob1"])
        cr.set_source(g)
        cr.arc(cx, cy, r, 0, math.tau)
        cr.fill()
        _src(cr, "knob_ring")
        cr.set_line_width(1.2 if not _analogish() else 2.2)
        cr.arc(cx, cy, r, 0, math.tau)
        cr.stroke()
        if not _analogish():
            _src(cr, "knob_cap")
            cr.arc(cx, cy, r * 0.55, 0, math.tau)
            cr.fill()
        _src(cr, "knob_ptr")
        cr.set_line_width(1.6)
        cr.move_to(cx, cy)
        cr.line_to(cx + math.cos(ang) * r * 0.78, cy + math.sin(ang) * r * 0.78)
        cr.stroke()
    if bipolar:
        _pana_gain_trace(cr, cx, cy, r, pos)
    if steps >= 2:
        _pana_knob_scale(cr, cx, cy, r, steps, bipolar=bipolar)
    if label:
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(15 if bipolar else 10)
        if bipolar:
            cr.set_source_rgb(0.88, 0.93, 1.0)
        else:
            _src(cr, "knob_lbl")
        tw = cr.text_extents(label)[4]
        cr.move_to(cx - tw / 2, cy + r + (26 if bipolar else (22 if steps >= 2 else 16)))
        cr.show_text(label)


def _pana_switch(cr, x: float, y: float, label: str, on: bool) -> None:
    _src(cr, "sw_body")
    _rrect(cr, x, y, 34, 22, 2)
    cr.fill_preserve()
    _src(cr, "sw_edge")
    cr.set_line_width(1)
    cr.stroke()
    knob_y = y + (4 if on else 10)
    _src(cr, "sw_knob")
    _rrect(cr, x + 4, knob_y, 26, 10, 1)
    cr.fill()
    cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(9)
    _src(cr, "sw_on") if on else _src(cr, "sw_off")
    cr.move_to(x + 6, y + 38)
    cr.show_text(label)


class RackKnobView(CairoDraw):
    """SONIC-RAK rotary dial. Mix knobs snap to 12 even ticks (1–12)."""

    def __init__(
        self, label: str, value: float = 1.0, on_change=None, steps: int = MIX_STEPS, bipolar: bool = False
    ) -> None:
        self.label = label
        self.steps = max(0, int(steps))
        self.bipolar = bool(bipolar)
        self._on_change = on_change
        self._drag0 = 0.0
        if self.bipolar:
            width, height = 148, 168
        else:
            width, height = (116, 136) if self.steps >= 2 else (72, 88)
        self.value = self._snap(value)
        super().__init__(width, height)
        try:
            self.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        except Exception:
            pass
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        self.add_controller(drag)
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)
        self._render()

    def _snap(self, value: float) -> float:
        v = max(0.0, min(1.0, float(value)))
        if self.steps < 2:
            return v
        n = self.steps - 1
        return round(v * n) / n

    def set_value(self, value: float) -> None:
        self.value = self._snap(value)
        self._render()

    def _apply(self, value: float) -> None:
        v = self._snap(value)
        if abs(v - self.value) < 1e-9:
            return
        self.value = v
        self._render()
        if self._on_change:
            self._on_change(self.value)

    def _drag_begin(self, _g, _x, _y) -> None:
        self._drag0 = self.value

    def _drag_update(self, _g, _x, oy: float) -> None:
        span = 110.0 if self.steps >= 2 else 72.0
        self._apply(self._drag0 - oy / span)

    def _scroll(self, _c, _dx: float, dy: float) -> bool:
        if self.steps < 2:
            self._apply(self.value - dy * 0.05)
            return True
        if dy == 0:
            return True
        n = self.steps - 1
        idx = int(round(self.value * n)) + (-1 if dy > 0 else 1)
        self._apply(max(0, min(n, idx)) / n)
        return True

    def _paint(self, cr, width: int, height: int) -> None:
        _src(cr, "chassis3")
        cr.rectangle(0, 0, width, height)
        cr.fill()
        if self.steps >= 2:
            r = min(width, height) * (0.20 if self.bipolar else 0.22)
            _pana_knob(
                cr,
                width / 2,
                height / 2 - (8 if self.bipolar else 6),
                r,
                self.value,
                self.label,
                steps=self.steps,
                bipolar=self.bipolar,
            )
        else:
            _pana_knob(cr, width / 2, height / 2 - 8, min(width, height) * 0.30, self.value, self.label)


class RackFaderView(CairoDraw):
    """Vertical master fader for the enhancer rack."""

    def __init__(self, label: str, value: float = 1.0, on_change=None) -> None:
        self.label = label
        self._on_change = on_change
        self._drag0 = 0.0
        self.value = max(0.0, min(1.0, float(value)))
        super().__init__(56, 148)
        try:
            self.set_cursor(Gdk.Cursor.new_from_name("ns-resize"))
        except Exception:
            pass
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        self.add_controller(drag)
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)
        self._render()

    def set_value(self, value: float) -> None:
        self.value = max(0.0, min(1.0, float(value)))
        self._render()

    def _apply(self, value: float) -> None:
        v = max(0.0, min(1.0, float(value)))
        if abs(v - self.value) < 1e-4:
            return
        self.value = v
        self._render()
        if self._on_change:
            self._on_change(self.value)

    def _drag_begin(self, _g, _x, _y) -> None:
        self._drag0 = self.value

    def _drag_update(self, _g, _x, oy: float) -> None:
        self._apply(self._drag0 - oy / 120.0)

    def _scroll(self, _c, _dx: float, dy: float) -> bool:
        self._apply(self.value - dy * 0.04)
        return True

    def _paint(self, cr, width: int, height: int) -> None:
        p = palette()
        _src(cr, "chassis3")
        cr.rectangle(0, 0, width, height)
        cr.fill()
        slot_x = width / 2 - 5
        slot_y = 14
        slot_h = height - 36
        cr.set_source_rgb(0.08, 0.08, 0.09)
        cr.rectangle(slot_x, slot_y, 10, slot_h)
        cr.fill()
        _src(cr, "edge")
        cr.set_line_width(1)
        cr.rectangle(slot_x, slot_y, 10, slot_h)
        cr.stroke()
        for i in range(11):
            ty = slot_y + slot_h * (1 - i / 10)
            cr.set_source_rgb(*p["fl"][:3] if i in (0, 5, 10) else (0.35, 0.35, 0.32))
            cr.set_line_width(1)
            cr.move_to(slot_x - 8, ty)
            cr.line_to(slot_x - 2, ty)
            cr.move_to(slot_x + 12, ty)
            cr.line_to(slot_x + 18, ty)
            cr.stroke()
        cap_y = slot_y + slot_h * (1.0 - self.value)
        cap_w, cap_h = 22, 10
        g = cairo.LinearGradient(0, cap_y - cap_h / 2, 0, cap_y + cap_h / 2)
        g.add_color_stop_rgb(0, *p["knob0"])
        g.add_color_stop_rgb(1, *p["knob_cap"])
        cr.set_source(g)
        cr.rectangle(width / 2 - cap_w / 2, cap_y - cap_h / 2, cap_w, cap_h)
        cr.fill_preserve()
        _src(cr, "edge")
        cr.set_line_width(1)
        cr.stroke()
        cr.select_font_face("Ubuntu Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(9)
        cr.set_source_rgb(*p["fl"])
        ext = cr.text_extents(self.label)
        cr.move_to(width / 2 - ext.width / 2 - ext.x_bearing, height - 6)
        cr.show_text(self.label)


class CassetteDeckView(CairoDraw):
    """SONIC-RAK dual cassette faceplate."""

    # IEC 60094 compact cassette is 100.4 × 63.8 mm.
    _CASSETTE_ASPECT = 100.4 / 63.8

    def __init__(self, on_select=None) -> None:
        self.paths: list[Path | None] = [None, None]
        self.active = 0
        self.playing = False
        self.paused = False
        self.frac = 0.0
        self.pos = 0.0
        self.peak_l = 0.0
        self.peak_r = 0.0
        self.dolby = True
        self.auto_rev = True
        self.recording = False
        self.dubbing = False
        self.dub_armed = False
        self.rec_sec = 0.0
        self.angle = 0.0
        self._last_deck = None
        super().__init__(980, 560)
        self.set_hexpand(True)
        click = Gtk.GestureClick()
        click.connect("pressed", self._click)
        self.add_controller(click)
        self._on_select = on_select
        self._render()

    def _paint_size(self) -> tuple[int, int]:
        return (
            max(self._w, self.get_width() or self._w),
            max(self._h, self.get_height() or self._h),
        )

    def _click(self, _g, _n, x: float, y: float) -> None:
        width = max(self._w, self.get_width() or self._w)
        height = max(self._h, self.get_height() or self._h)
        well_x, well_y, well_h, well_w, gap = self._geom(width, height)
        if well_y <= y <= well_y + well_h:
            mid = well_x + well_w + gap / 2
            self.active = 0 if x < mid else 1
            if self._on_select:
                self._on_select(self.active)
            self._render()

    def _geom(self, width: int, height: int) -> tuple[float, float, float, float, float]:
        header_h, display_h, bottom_h, gap = 36.0, 92.0, 76.0, 14.0
        well_y = header_h + display_h + 8
        avail_h = max(200.0, height - well_y - bottom_h - 8)
        avail_pair = max(280.0, width - 24)
        frame_x, frame_top, frame_bot, glass = 8.0, 24.0, 16.0, 4.0
        max_well_w = (avail_pair - gap) / 2
        max_cass_w = max(80.0, max_well_w - 2 * frame_x - 2 * glass)
        max_cass_h = max(50.0, avail_h - frame_top - frame_bot - 2 * glass)
        cass_h = min(max_cass_h, max_cass_w / self._CASSETTE_ASPECT)
        cass_w = cass_h * self._CASSETTE_ASPECT
        well_w = cass_w + 2 * frame_x + 2 * glass
        well_h = cass_h + frame_top + frame_bot + 2 * glass
        well_x = (width - (2 * well_w + gap)) / 2
        return well_x, well_y, well_h, well_w, gap

    def set_deck(
        self,
        paths: list[Path | None],
        active: int,
        playing: bool,
        frac: float,
        paused: bool = False,
        peak_l: float = 0.0,
        peak_r: float = 0.0,
        pos: float = 0.0,
        dolby: bool = True,
        auto_rev: bool = True,
        recording: bool = False,
        rec_sec: float = 0.0,
        dubbing: bool = False,
        dub_armed: bool = False,
    ) -> None:
        self.paths = (list(paths) + [None, None])[:2]
        self.active = 0 if active == 0 else 1
        self.playing = playing
        self.paused = paused
        self.frac = max(0.0, min(1.0, float(frac)))
        self.peak_l = float(peak_l)
        self.peak_r = float(peak_r)
        self.pos = max(0.0, float(pos))
        self.dolby = bool(dolby)
        self.auto_rev = bool(auto_rev)
        self.recording = bool(recording)
        self.rec_sec = max(0.0, float(rec_sec))
        self.dubbing = bool(dubbing)
        self.dub_armed = bool(dub_armed)
        spinning = playing or dubbing or self.rec_sec > 0.15
        if spinning:
            spin = 0.36 if self.dubbing else 0.18
            self.angle = (self.angle + spin) % math.tau
        snapshot = (
            tuple(str(p) if p else "" for p in self.paths),
            self.active,
            playing,
            paused,
            round(self.frac, 3) if spinning else 0,
            round(self.peak_l, 2) if spinning else 0,
            round(self.peak_r, 2) if spinning else 0,
            round(self.pos, 1) if spinning else 0,
            self.dolby,
            self.auto_rev,
            self.recording,
            round(self.rec_sec, 1),
            self.dubbing,
            self.dub_armed,
            round(self.angle, 2) if spinning else 0,
        )
        if snapshot == self._last_deck:
            return
        self._last_deck = snapshot
        self._render()

    def _paint(self, cr, width: int, height: int) -> None:
        self._chassis(cr, width, height)
        well_x, well_y, well_h, well_w, gap = self._geom(width, height)
        self._header(cr, width)
        self._display(cr, 10, 42, width - 20, 88)
        for i in range(2):
            x = well_x + i * (well_w + gap)
            self._door(cr, x, well_y, well_w, well_h, i)
        self._bottom(cr, 10, well_y + well_h + 6, width - 20, height - (well_y + well_h + 10))

    def _chassis(self, cr, width: int, height: int) -> None:
        _pana_chassis(cr, width, height)

    def _screw(self, cr, cx: float, cy: float, r: float = 4.0) -> None:
        _pana_screw(cr, cx, cy, r)

    def _fl(self, cr, x: float, y: float, text: str, size: float = 16, dim: bool = False) -> None:
        _pana_fl(cr, x, y, text, size, dim)

    def _lamp(self, cr, x: float, y: float, label: str, on: bool, color: str = "cyan") -> None:
        _pana_lamp(cr, x, y, label, on, color)

    def _vu(self, cr, x: float, y: float, w: float, h: float, level: float, name: str) -> None:
        _pana_vu(cr, x, y, w, h, level, name)

    def _header(self, cr, width: int) -> None:
        _pana_header(
            cr,
            width,
            "RS-TR575",
            "STEREO DOUBLE CASSETTE DECK",
            "DOLBY B-C NR   ·   AUTO REVERSE   ·   Hi-Fi",
            power=True,
        )

    def _display(self, cr, x: float, y: float, w: float, h: float) -> None:
        _pana_glass(cr, x, y, w, h)
        loaded = [p is not None for p in self.paths]
        live = self.playing or self.recording
        self._vu(cr, x + 12, y + 10, w * 0.42, 12, self.peak_l if live else 0.0, "L")
        self._vu(cr, x + 12, y + 26, w * 0.42, 12, self.peak_r if live else 0.0, "R")
        mins = int(self.pos) // 60
        secs = int(self.pos) % 60
        counter = f"{mins:02d}{secs:02d}"
        idle = "0000"
        if self.recording:
            rm = int(self.rec_sec) // 60
            rs = int(self.rec_sec) % 60
            self._fl(cr, x + w * 0.48, y + 32, f"REC    {rm:02d}{rs:02d}", 20)
            self._fl(cr, x + w * 0.72, y + 32, f"DECK   {counter if live else idle}", 20, dim=not live)
        else:
            self._fl(cr, x + w * 0.48, y + 32, f"DECK1  {counter if self.active == 0 else idle}", 20, dim=self.active != 0)
            self._fl(cr, x + w * 0.72, y + 32, f"DECK2  {counter if self.active == 1 else idle}", 20, dim=self.active != 1)
        cr.set_font_size(11)
        _src(cr, "scale")
        cr.select_font_face("Cantarell")
        cr.move_to(x + 12, y + 54)
        cr.show_text("-20       -10        -5        0        +3   +6  dB")
        row = y + 66
        self._lamp(cr, x + 12, row, "PLAY", self.playing)
        self._lamp(cr, x + 78, row, "PAUSE", self.paused)
        self._lamp(cr, x + 154, row, "REC", self.recording, "red")
        self._lamp(cr, x + 214, row, "DOLBY B NR", self.dolby and any(loaded))
        self._lamp(cr, x + 328, row, "CrO2", any(loaded), "amber")
        self._lamp(cr, x + 392, row, "AUTO REV", self.auto_rev)
        self._lamp(
            cr,
            x + 488,
            row,
            "HIGH SPEED DUB",
            self.dubbing or (self.dub_armed and bool(self.paths[0] and self.paths[1])),
            "amber",
        )
        self._lamp(cr, x + 638, row, "MPX FILTER", self.dolby)
        self._lamp(cr, x + 744, row, "NORM", True)
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(10)
        _src(cr, "silk")
        cr.move_to(x + w - 188, y + 18)
        cr.show_text("REC COUNTER" if self.recording else "TAPE COUNTER")

    def _door(self, cr, x: float, y: float, w: float, h: float, index: int) -> None:
        selected = index == self.active
        form = _form()
        rad = 16 if form in {"cathedral", "tube", "console"} else (18 if form == "mesh" else (0 if form == "matrix" else 5))
        _rrect(cr, x, y, w, h, rad)
        bezel = cairo.LinearGradient(x, y, x, y + h)
        bezel.add_color_stop_rgb(0, *palette()["chassis0"])
        bezel.add_color_stop_rgb(0.08, *palette()["chassis1"])
        bezel.add_color_stop_rgb(1, *palette()["chassis3"])
        cr.set_source(bezel)
        cr.fill_preserve()
        _src(cr, "door_sel") if selected else _src(cr, "door_idle")
        cr.set_line_width(1.6 if selected else 1.0)
        cr.stroke()
        if form in {"cathedral", "teak", "receiver", "tube"}:
            _wood_grain(cr, x, y, w, h)
        ix, iy, iw, ih = x + 8, y + 24, w - 16, h - 40
        if _analogish():
            cr.save()
            cr.translate(ix + iw / 2, iy + ih / 2)
            cr.scale(max(1.0, iw / 2), max(1.0, ih / 2))
            cr.arc(0, 0, 1, 0, math.tau)
            cr.set_source_rgb(0.08, 0.06, 0.04)
            cr.fill()
            cr.restore()
            playing_here = self.playing and selected
            self._reel(cr, ix + iw * 0.32, iy + ih * 0.55, 18, playing_here)
            self._reel(cr, ix + iw * 0.68, iy + ih * 0.55, 18, playing_here)
            cr.select_font_face("Cantarell", cairo.FONT_SLANT_ITALIC if form == "cathedral" else cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(13)
            _src(cr, "door_title")
            cr.move_to(x + 10, y + 16)
            cr.show_text(f"{'WELL' if form == 'tube' else 'DECK'}  {index + 1}")
            return
        if form == "matrix":
            _src(cr, "inner")
            cr.rectangle(ix, iy, iw, ih)
            cr.fill()
            cr.select_font_face("Ubuntu Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(13)
            _src(cr, "door_title")
            cr.move_to(x + 10, y + 16)
            cr.show_text(f"BAY {index + 1}")
            self._ctk_sa(cr, ix + 4, iy + 4, iw - 8, ih - 8, self.paths[index], index)
            return
        _rrect(cr, ix, iy, iw, ih, 14 if form == "mesh" else 3)
        cr.set_source_rgb(0.04, 0.045, 0.05)
        cr.fill_preserve()
        cr.set_source_rgba(0.5, 0.85, 0.9, 0.08)
        cr.fill_preserve()
        cr.set_source_rgb(0.12, 0.14, 0.15)
        cr.set_line_width(1)
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.04)
        cr.rectangle(ix + 4, iy + 3, iw - 8, 8)
        cr.fill()
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(13)
        cr.set_source_rgb(0.92, 0.96, 0.98)
        cr.move_to(x + 10, y + 16)
        cr.show_text(f"DECK  {index + 1}")
        cr.set_font_size(10)
        cr.set_source_rgb(0.78, 0.86, 0.88)
        cr.move_to(x + 92, y + 16)
        cr.show_text("AUTO REVERSE   PLAY")
        playing_here = self.playing and selected
        cr.set_source_rgb(0.25, 1.0, 0.78) if playing_here else cr.set_source_rgb(0.32, 0.36, 0.36)
        cr.move_to(x + w - 52, y + 16)
        cr.show_text("◄")
        cr.set_source_rgb(0.25, 1.0, 0.78) if playing_here else cr.set_source_rgb(0.32, 0.36, 0.36)
        cr.move_to(x + w - 34, y + 16)
        cr.show_text("►")
        self._ctk_sa(cr, ix + 4, iy + 4, iw - 8, ih - 8, self.paths[index], index)
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(10)
        cr.set_source_rgb(0.78, 0.82, 0.86)
        cr.move_to(x + 10, y + h - 6)
        cr.show_text("EJECT")
        cr.rectangle(x + 42, y + h - 12, 22, 7)
        cr.set_source_rgb(0.2, 0.21, 0.22)
        cr.fill_preserve()
        cr.set_source_rgb(0.45, 0.46, 0.48)
        cr.set_line_width(0.8)
        cr.stroke()
        self._screw(cr, x + 6, y + 6, 3.2)
        self._screw(cr, x + w - 6, y + 6, 3.2)

    def _bottom(self, cr, x: float, y: float, w: float, h: float) -> None:
        if h < 40:
            return
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(10)
        _src(cr, "silk")
        cr.move_to(x + 4, y + 12)
        cr.show_text("REC LEVEL")
        self._knob(cr, x + 28, y + 42, 16, 0.42)
        self._knob(cr, x + 68, y + 42, 16, 0.42)
        cr.set_font_size(9)
        cr.move_to(x + 20, y + h - 4)
        cr.show_text("L")
        cr.move_to(x + 62, y + h - 4)
        cr.show_text("R")
        cr.set_font_size(10)
        cr.move_to(x + 108, y + 12)
        cr.show_text("PHONES")
        cr.set_source_rgb(0.08, 0.08, 0.09)
        cr.arc(x + 128, y + 40, 9, 0, math.tau)
        cr.fill()
        cr.set_source_rgb(0.35, 0.36, 0.38)
        cr.set_line_width(1.4)
        cr.arc(x + 128, y + 40, 7, 0, math.tau)
        cr.stroke()
        cr.arc(x + 128, y + 40, 2.2, 0, math.tau)
        cr.fill()
        self._knob(cr, x + 168, y + 42, 11, 0.25)
        _src(cr, "silk")
        cr.move_to(x + 156, y + 12)
        cr.show_text("LEVEL")
        cr.move_to(x + 210, y + 12)
        cr.show_text("DUBBING")
        self._switch(cr, x + 214, y + 30, "NORM", True)
        self._switch(cr, x + 258, y + 30, "HIGH", bool(self.paths[0] and self.paths[1]) and False)
        _src(cr, "silk")
        cr.move_to(x + 320, y + 12)
        cr.show_text("SYNCHRO START")
        self._switch(cr, x + 330, y + 30, "ON", False)
        _src(cr, "silk")
        cr.move_to(x + 420, y + 12)
        cr.show_text("BIAS")
        self._switch(cr, x + 418, y + 30, "AUTO", True)
        _src(cr, "silk")
        cr.move_to(x + 490, y + 12)
        cr.show_text("TAPE")
        self._switch(cr, x + 488, y + 30, "CrO2", any(p is not None for p in self.paths))
        _src(cr, "silk")
        cr.move_to(x + 560, y + 12)
        cr.show_text("NR")
        self._switch(cr, x + 552, y + 30, "B", self.dolby)
        self._switch(cr, x + 590, y + 30, "C", False)
        _src(cr, "silk")
        cr.move_to(x + 650, y + 12)
        cr.show_text("TIMER")
        self._switch(cr, x + 648, y + 30, "OFF", True)
        _src(cr, "silk")
        cr.set_font_size(10)
        cr.move_to(x + w - 228, y + 38)
        cr.show_text("HEADPHONES   8 Ω   ·   LINE IN/OUT")
        cr.move_to(x + w - 228, y + 54)
        cr.show_text("RS-TR575   ·   cascade_eq")

    def _switch(self, cr, x: float, y: float, label: str, on: bool) -> None:
        _pana_switch(cr, x, y, label, on)

    def _knob(self, cr, cx: float, cy: float, r: float, pos: float) -> None:
        _pana_knob(cr, cx, cy, r, pos)

    def _ctk_sa(self, cr, x: float, y: float, w: float, h: float, path: Path | None, index: int) -> None:
        # IEC 60094 compact cassette is 100.4 × 63.8 mm.
        aspect = 100.4 / 63.8
        ch = min(h, w / aspect)
        cw = ch * aspect
        x = x + (w - cw) / 2
        y = y + (h - ch) / 2
        w, h = cw, ch
        if path is None:
            self._empty_well(cr, x, y, w, h)
            return
        r = max(2.2, h * 0.045)
        _rrect(cr, x, y, w, h, r)
        shell = cairo.LinearGradient(x, y, x, y + h)
        shell.add_color_stop_rgb(0, 0.16, 0.14, 0.13)
        shell.add_color_stop_rgb(0.08, 0.07, 0.065, 0.06)
        shell.add_color_stop_rgb(0.55, 0.045, 0.04, 0.038)
        shell.add_color_stop_rgb(1, 0.02, 0.02, 0.02)
        cr.set_source(shell)
        cr.fill_preserve()
        cr.set_source_rgb(0.28, 0.24, 0.20)
        cr.set_line_width(1.1)
        cr.stroke()
        cr.set_source_rgba(1, 0.92, 0.75, 0.07)
        cr.rectangle(x + 3, y + 2, w - 6, max(2, h * 0.06))
        cr.fill()
        # Write-protect tabs on the top edge (opposite the tape path).
        cr.set_source_rgb(0.10, 0.09, 0.08)
        tab_w, tab_h = w * 0.07, h * 0.045
        cr.rectangle(x + w * 0.08, y - 1, tab_w, tab_h)
        cr.rectangle(x + w * 0.85, y - 1, tab_w, tab_h)
        cr.fill()
        # Five Philips screws on the shell corners and bottom center.
        for sx, sy in (
            (x + w * 0.07, y + h * 0.10),
            (x + w * 0.93, y + h * 0.10),
            (x + w * 0.07, y + h * 0.90),
            (x + w * 0.93, y + h * 0.90),
            (x + w * 0.50, y + h * 0.90),
        ):
            cr.set_source_rgb(0.38, 0.36, 0.32)
            cr.arc(sx, sy, max(1.6, h * 0.028), 0, math.tau)
            cr.fill()
            cr.set_source_rgb(0.16, 0.15, 0.14)
            cr.arc(sx, sy, max(0.8, h * 0.014), 0, math.tau)
            cr.fill()
            cr.set_source_rgb(0.08, 0.08, 0.07)
            cr.set_line_width(0.8)
            cr.move_to(sx - h * 0.016, sy)
            cr.line_to(sx + h * 0.016, sy)
            cr.stroke()

        # CTK SA gold foil A-side label.
        lx, ly, lw, lh = x + w * 0.08, y + h * 0.07, w * 0.84, h * 0.36
        _rrect(cr, lx, ly, lw, lh, 2.2)
        foil = cairo.LinearGradient(lx, ly, lx + lw, ly + lh)
        foil.add_color_stop_rgb(0, 0.93, 0.78, 0.42)
        foil.add_color_stop_rgb(0.35, 0.82, 0.62, 0.28)
        foil.add_color_stop_rgb(0.7, 0.70, 0.50, 0.22)
        foil.add_color_stop_rgb(1, 0.55, 0.38, 0.14)
        cr.set_source(foil)
        cr.fill()
        cr.set_source_rgba(1.0, 0.92, 0.62, 0.35)
        cr.rectangle(lx, ly, lw, 4)
        cr.fill()
        cr.set_source_rgb(0.10, 0.07, 0.04)
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(11, h * 0.16))
        cr.move_to(lx + lw * 0.04, ly + lh * 0.48)
        cr.show_text("CTK")
        cr.set_font_size(max(18, h * 0.28))
        sa = "SA"
        tw = cr.text_extents(sa)[4]
        cr.move_to(lx + lw - tw - lw * 0.06, ly + lh * 0.58)
        cr.show_text(sa)
        cr.set_font_size(max(7, h * 0.09))
        cr.move_to(lx + lw * 0.04, ly + lh * 0.78)
        cr.show_text("HIGH BIAS")
        cr.set_font_size(max(6.5, h * 0.08))
        cr.move_to(lx + lw * 0.42, ly + lh * 0.78)
        cr.show_text("IEC TYPE II   HIGH POSITION")
        cr.rectangle(lx + lw * 0.04, ly + lh * 0.84, lw * 0.16, lh * 0.14)
        cr.set_line_width(0.9)
        cr.stroke()
        cr.set_font_size(max(7, h * 0.09))
        cr.move_to(lx + lw * 0.055, ly + lh * 0.96)
        cr.show_text("C90")

        # Lined write-on strip (the real SA had this under the foil).
        sx, sy, sw, sh = x + w * 0.10, y + h * 0.44, w * 0.80, h * 0.10
        cr.set_source_rgb(0.93, 0.90, 0.82)
        cr.rectangle(sx, sy, sw, sh)
        cr.fill()
        cr.set_source_rgb(0.62, 0.55, 0.42)
        cr.set_line_width(0.6)
        cr.move_to(sx + 4, sy + sh * 0.72)
        cr.line_to(sx + sw - 4, sy + sh * 0.72)
        cr.stroke()
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_ITALIC, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(max(8, h * 0.09))
        cr.set_source_rgb(0.18, 0.12, 0.08)
        cr.move_to(sx + 6, sy + sh * 0.68)
        cr.show_text(path.stem.upper()[:24])

        # Twin round windows and oxide pancakes.
        frac = self.frac if index == self.active else 0.08
        spinning = index == self.active and self.playing
        hub_y = y + h * 0.72
        win_r = h * 0.175
        left_x, right_x = x + w * 0.31, x + w * 0.69
        for hx, pack in ((left_x, 1.0 - frac), (right_x, frac)):
            cr.set_source_rgb(0.12, 0.13, 0.13)
            cr.arc(hx, hub_y, win_r, 0, math.tau)
            cr.fill()
            cr.set_source_rgb(0.05, 0.05, 0.05)
            cr.set_line_width(1.2)
            cr.arc(hx, hub_y, win_r, 0, math.tau)
            cr.stroke()
            tape_r = win_r * (0.38 + 0.50 * max(0.06, pack))
            self._reel(cr, hx, hub_y, tape_r, spinning, hub_r=win_r * 0.34)

        # Tape path along the head edge (bottom of the shell).
        path_y = y + h * 0.93
        cr.set_source_rgb(0.28, 0.16, 0.08)
        cr.set_line_width(max(1.4, h * 0.025))
        cr.move_to(x + w * 0.18, path_y)
        cr.line_to(x + w * 0.82, path_y)
        cr.stroke()
        cr.set_source_rgb(0.82, 0.82, 0.78)
        for rx in (x + w * 0.22, x + w * 0.78):
            cr.arc(rx, path_y, h * 0.035, 0, math.tau)
            cr.fill()
        cr.set_source_rgb(0.55, 0.42, 0.28)
        cr.rectangle(x + w * 0.46, path_y - h * 0.03, w * 0.08, h * 0.06)
        cr.fill()
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(6, h * 0.07))
        cr.set_source_rgb(0.45, 0.40, 0.34)
        cr.move_to(x + w * 0.08, y + h * 0.98)
        cr.show_text("MADE IN JAPAN")

    def _empty_well(self, cr, x: float, y: float, w: float, h: float) -> None:
        _rrect(cr, x, y, w, h, 4)
        cr.set_source_rgb(0.04, 0.045, 0.05)
        cr.fill_preserve()
        cr.set_source_rgb(0.14, 0.15, 0.16)
        cr.set_line_width(1)
        cr.stroke()
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(10, h * 0.12))
        cr.set_source_rgb(0.42, 0.48, 0.50)
        cr.move_to(x + w * 0.18, y + h * 0.22)
        cr.show_text("INSERT  CASSETTE")
        for hx in (x + w * 0.31, x + w * 0.69):
            self._reel(cr, hx, y + h * 0.62, 8, False, empty=True)

    def _reel(self, cr, cx: float, cy: float, radius: float, spin: bool, empty: bool = False, hub_r: float | None = None) -> None:
        if empty:
            cr.set_source_rgb(0.22, 0.23, 0.24)
            cr.arc(cx, cy, 11, 0, math.tau)
            cr.fill()
            cr.set_source_rgb(0.08, 0.08, 0.09)
            cr.arc(cx, cy, 4.5, 0, math.tau)
            cr.fill()
            ang = 0.3
            cr.set_line_width(1.4)
            cr.set_source_rgb(0.12, 0.12, 0.13)
            for i in range(3):
                a = ang + i * math.tau / 3
                cr.move_to(cx, cy)
                cr.line_to(cx + math.cos(a) * 8, cy + math.sin(a) * 8)
                cr.stroke()
            return
        # Super Avilyn oxide pancake.
        cr.set_source_rgb(0.28, 0.16, 0.07)
        cr.arc(cx, cy, radius, 0, math.tau)
        cr.fill()
        cr.set_source_rgb(0.18, 0.10, 0.04)
        cr.set_line_width(1.0)
        rings = max(2, int(radius / 4))
        for i in range(1, rings):
            cr.arc(cx, cy, radius * i / rings, 0, math.tau)
            cr.stroke()
        hr = hub_r if hub_r is not None else max(6.5, radius * 0.42)
        cr.set_source_rgb(0.10, 0.10, 0.10)
        cr.arc(cx, cy, hr, 0, math.tau)
        cr.fill()
        cr.set_source_rgb(0.22, 0.22, 0.22)
        cr.arc(cx, cy, hr * 0.92, 0, math.tau)
        cr.stroke()
        # Compact-cassette 3-prong hub.
        ang = self.angle if spin else 0.35
        cr.set_source_rgb(0.06, 0.06, 0.06)
        for i in range(3):
            a0 = ang + i * math.tau / 3
            cr.new_path()
            cr.arc(cx, cy, hr * 0.78, a0 - 0.38, a0 + 0.38)
            cr.arc_negative(cx, cy, hr * 0.22, a0 + 0.38, a0 - 0.38)
            cr.close_path()
            cr.fill()
        cr.set_source_rgb(0.08, 0.08, 0.08)
        cr.arc(cx, cy, hr * 0.16, 0, math.tau)
        cr.fill()


class VintageLa2aView(CairoDraw):
    """SONIC-RAK compressor or preamp faceplate."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.on = False
        self.peak_red = 0.45
        self.gain = 0.5
        self.limit = False
        self.hpf_on = False
        self.hpf = 0.15
        self.level = 0.0
        self.gr = 0.0
        self.preset_label = "SAFE"
        self.air_on = False
        self.lpf_on = False
        self.echo_on = False
        self.room_on = False
        self.pong_on = False
        self.chop_on = False
        self.rev_on = False
        self.crush_on = False
        self.auto_on = False
        self.enhance = ""
        self.gap = 0.0
        self.upward = False
        self.mix_wet = 1.0
        self.mix_dry = 0.0
        super().__init__(440, 132)
        self.set_hexpand(True)
        self._render()

    def update(self, **kw) -> None:
        for key, value in kw.items():
            setattr(self, key, value)
        last = getattr(self, "_painted", None)
        if last is None:
            self._painted = dict(kw)
            self._render()
            return
        dirty = False
        for key, value in kw.items():
            old = last.get(key, object())
            if isinstance(value, float) and isinstance(old, (int, float)):
                if abs(float(old) - float(value)) < 0.02:
                    continue
            elif old == value:
                continue
            dirty = True
            break
        if dirty:
            self._painted = {**last, **kw}
            self._render()

    def _paint(self, cr, width: int, height: int) -> None:
        _pana_chassis(cr, width, height)
        if self.kind == "comp":
            _pana_header(cr, width, "SH-8038", "DYNAMIC PROCESSOR", "COMP / LIMIT  ·  Hi-Fi", power=self.on)
        elif self.kind == "exp":
            _pana_header(cr, width, "SH-8046", "DYNAMIC EXPANDER", "GATE / EXPAND  ·  Hi-Fi", power=self.on)
        elif self.kind == "fx":
            _pana_header(cr, width, "SH-8055", "DIGITAL EFFECT", "FILTER / ECHO / AIR", power=self.on)
        elif self.kind == "delay":
            _pana_header(cr, width, "SH-8066", "ENHANCER", "NR / CLARITY / LEVEL", power=self.on)
        else:
            _pana_header(cr, width, "SU-V6", "STEREO PREAMPLIFIER", "GAIN STAGE  ·  RUMBLE", power=True)
        meter_w = max(220, width - 24)
        _pana_glass(cr, 12, 40, meter_w, height - 52)
        lamp_x = 12 + meter_w
        if self.kind == "comp":
            gr = self.gr if self.on else 0.0
            _pana_vu(cr, 24, 52, meter_w - 28, 12, 0.0, "GR", amount=gr)
            _pana_vu(cr, 24, 70, meter_w - 28, 12, self.level, "IN")
            _pana_fl(cr, 24, 108, self.preset_label, 18, dim=not self.on)
            _pana_fl(cr, 118, 108, "LIMIT" if self.limit else "COMP", 14, dim=not self.on)
            _pana_lamp(cr, lamp_x - 220, 100, "PROCESS", self.on)
            _pana_lamp(cr, lamp_x - 122, 100, "LIMIT", self.limit, "amber")
            _pana_lamp(cr, lamp_x - 50, 100, "GR", self.on and gr > 0.08)
        elif self.kind == "exp":
            gr = self.gr if self.on else 0.0
            _pana_vu(cr, 24, 52, meter_w - 28, 12, 0.0, "GR", amount=gr)
            _pana_vu(cr, 24, 70, meter_w - 28, 12, self.level, "IN")
            _pana_fl(cr, 24, 108, self.preset_label, 18, dim=not self.on)
            _pana_fl(cr, 118, 108, "UP" if self.upward else "GATE", 14, dim=not self.on)
            _pana_lamp(cr, lamp_x - 220, 100, "PROCESS", self.on)
            _pana_lamp(cr, lamp_x - 122, 100, "UP", self.upward, "amber")
            _pana_lamp(cr, lamp_x - 50, 100, "GATE", self.on and not self.upward)
        elif self.kind == "fx":
            amount = self.gain if self.on else 0.0
            _pana_vu(cr, 24, 52, meter_w - 28, 12, amount, "FX")
            _pana_vu(cr, 24, 70, meter_w - 28, 12, self.level, "IN")
            _pana_fl(cr, 24, 108, self.preset_label, 16, dim=not self.on)
            _pana_lamp(cr, lamp_x - 250, 100, "HPF", self.hpf_on, "amber")
            _pana_lamp(cr, lamp_x - 188, 100, "LPF", self.lpf_on, "amber")
            _pana_lamp(cr, lamp_x - 126, 100, "AIR", self.air_on)
            _pana_lamp(cr, lamp_x - 72, 100, "ECHO", self.echo_on)
            _pana_lamp(cr, lamp_x - 8, 100, "ROOM", self.room_on, "amber")
        elif self.kind == "delay":
            amount = self.gain if self.on else 0.0
            gap = self.gap if self.on and self.auto_on else 0.0
            mode = str(self.enhance or "")
            vu = {"noise": "NR", "clarity": "CLR", "level": "VOL"}.get(mode, "ENH")
            _pana_vu(cr, 24, 52, meter_w - 28, 12, gap if self.auto_on else amount, vu)
            _pana_vu(cr, 24, 70, meter_w - 28, 12, self.level, "IN")
            _pana_fl(cr, 24, 108, self.preset_label, 16, dim=not self.on)
            _pana_lamp(cr, lamp_x - 250, 100, "NR", mode == "noise")
            _pana_lamp(cr, lamp_x - 172, 100, "CLEAR", mode == "clarity")
            _pana_lamp(cr, lamp_x - 90, 100, "LEVEL", mode == "level", "amber")
            _pana_lamp(cr, lamp_x - 20, 100, "CRUSH", self.crush_on)
        else:
            _pana_vu(cr, 24, 52, meter_w - 28, 12, self.level, "L")
            _pana_vu(cr, 24, 70, meter_w - 28, 12, self.level, "R")
            _pana_fl(cr, 24, 108, self.preset_label, 18)
            _pana_fl(cr, 118, 108, f"{self.gain * 24 - 12:+.1f} dB", 14)
            _pana_lamp(cr, lamp_x - 220, 100, "LINE", True)
            _pana_lamp(cr, lamp_x - 122, 100, "HPF", self.hpf_on, "amber")
            _pana_lamp(cr, lamp_x - 50, 100, "AIR", self.air_on)


class CascadeWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title=APP_NAME)
        self.add_css_class("cascade-skin")
        self.set_default_size(1180, 920)
        self._suppress = False
        self._apply_src = 0
        self.state = load_state()
        self._chain_order = normalize_chain(self.state.get("chain_order"))
        self._order_dd: dict[str, Gtk.DropDown] = {}
        self._player = None
        self._tapes: list[Path | None] = [None, None]
        self._tape_well = 0
        self._split_tracks: list[dict] = []
        self._suppress_preview = False
        self._dubbing = False
        self._auto_until = 0.0
        self._auto_on = False
        self._auto_bars = 0
        self._live_bpm = 120.0
        self._auto_listen: list[list[float]] = []
        self.state["auto_eq"] = {"enabled": False, "lift": empty_bands()}
        self._tone_auto_on = bool(normalize_tone_auto(self.state.get("tone_auto")).get("enabled"))
        self._last_rec_path = ""
        self._expanders: dict[str, Gtk.Expander] = {}
        self._meter_n = 0
        self._brand_labels: list[Gtk.Label] = []
        sid = apply_skin(self.state.get("skin"))

        self._css = Gtk.CssProvider()
        self._css.load_from_data(css_text().encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._install_menu(app)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        t = Gtk.Label(label=str(active_skin()["brand_name"]))
        t.add_css_class("cascade-title")
        s = Gtk.Label(label=str(active_skin()["tagline"]))
        s.add_css_class("cascade-sub")
        self._title_main = t
        self._title_sub = s
        titles.append(t)
        titles.append(s)
        header.set_title_widget(titles)
        toolbar.add_top_bar(self._menubar)
        toolbar.add_top_bar(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        cabinet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        cabinet.add_css_class("rack-cabinet")
        scroll.set_child(cabinet)
        toolbar.set_content(scroll)
        self.set_content(toolbar)
        self._header = header
        self._scroll = scroll
        self._cabinet = cabinet

        self.enable = Gtk.Switch()
        self.enable.set_valign(Gtk.Align.CENTER)
        self.enable.connect("notify::active", self._on_enable)
        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("status-off")
        led = Gtk.Box()
        led.add_css_class("led-well")
        led.append(self.status)
        power = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pwr = Gtk.Label(label="POWER")
        pwr.add_css_class("field-label")
        power.append(pwr)
        power.append(self.enable)
        power.append(led)

        blend_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bl = Gtk.Label(label="BLEND")
        bl.add_css_class("field-label")
        blend_row.append(bl)
        self.blend = Gtk.Switch()
        self.blend.set_valign(Gtk.Align.CENTER)
        self.blend.set_active(bool((self.state.get("blend") or default_blend()).get("enabled", True)))
        self.blend.connect("notify::active", self._on_blend)
        blend_row.append(self.blend)
        self.blend_gr = Gtk.Label(label="GR  0.0", xalign=0)
        self.blend_gr.add_css_class("field-label")
        self.blend_gr.set_hexpand(True)
        blend_row.append(self.blend_gr)
        self.blend_note = Gtk.Label(xalign=0)
        self.blend_note.add_css_class("field-label")
        self.blend_note.set_wrap(True)
        self.blend_note.set_text("Ducks FX and enhance as the output nears the red.")

        ride_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rl = Gtk.Label(label="RIDE")
        rl.add_css_class("field-label")
        ride_row.append(rl)
        self.ride = Gtk.Switch()
        self.ride.set_valign(Gtk.Align.CENTER)
        self.ride.set_active(bool((self.state.get("ride") or default_ride()).get("enabled", True)))
        self.ride.connect("notify::active", self._on_ride)
        ride_row.append(self.ride)
        self.ride_db = Gtk.Label(label="RIDE  0.0", xalign=0)
        self.ride_db.add_css_class("field-label")
        self.ride_db.set_hexpand(True)
        ride_row.append(self.ride_db)
        self.ride_note = Gtk.Label(xalign=0)
        self.ride_note.add_css_class("field-label")
        self.ride_note.set_wrap(True)
        self.ride_note.set_text("Watches the waveform histogram and rides output level to -18 dB RMS.")

        from .profiles import profile_names as _profile_names

        profile_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        prl = Gtk.Label(label="PROFILE", xalign=0)
        prl.add_css_class("field-label")
        profile_row.append(prl)
        self.profile = Gtk.DropDown.new_from_strings(_profile_names() + ["CUSTOM"])
        self.profile.set_hexpand(True)
        chosen = str(self.state.get("profile") or "Clean")
        names = _profile_names() + ["CUSTOM"]
        self.profile.set_selected(names.index(chosen) if chosen in names else names.index("CUSTOM"))
        self.profile.connect("notify::selected", self._on_profile)
        profile_row.append(self.profile)
        self.profile_note = Gtk.Label(xalign=0)
        self.profile_note.add_css_class("field-label")
        self.profile_note.set_wrap(True)
        from .profiles import PROFILES as _PROFILES

        note = (_PROFILES.get(chosen) or {}).get("note") or "Pick an intent — the whole rack follows."
        self.profile_note.set_text(str(note))

        meters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.wave = BarMeterView()
        wave_frame = Gtk.Box()
        wave_frame.add_css_class("scope-wrap")
        wave_frame.append(self.wave)
        meters.append(wave_frame)
        self.needle_l = NeedleView("L")
        self.needle_r = NeedleView("R")
        nwrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for needle in (self.needle_l, self.needle_r):
            boxn = Gtk.Box()
            boxn.add_css_class("meter-wrap")
            boxn.append(needle)
            nwrap.append(boxn)
        meters.append(nwrap)
        mon_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        mon_inner.append(power)
        mon_inner.append(blend_row)
        mon_inner.append(self.blend_note)
        mon_inner.append(ride_row)
        mon_inner.append(self.ride_note)
        mon_inner.append(profile_row)
        mon_inner.append(self.profile_note)
        mon_inner.append(meters)
        tone_state = normalize_tone(self.state.get("tone"))
        tone_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.tone_knobs = {}
        self.tone_readouts = {}
        for key, label in (("low_db", "LOW"), ("mid_db", "MID"), ("high_db", "HIGH")):
            col, knob, readout = self._db_knob(label, tone_state[key], lambda v, k=key: self._on_tone_gain(k, v))
            self.tone_knobs[key] = knob
            self.tone_readouts[key] = readout
            tone_row.append(col)
        gain_col, self.master_knob, self.master_readout = self._db_knob(
            "GAIN",
            float(self.state.get("master_db", 0)),
            self._on_master_gain,
        )
        tone_row.append(gain_col)
        tone_ctrl = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tone_ctrl.set_valign(Gtk.Align.CENTER)
        tlab = Gtk.Label(label="TONE", xalign=0.5)
        tlab.add_css_class("field-label")
        tone_ctrl.append(tlab)
        tnames = tone_profile_names() + ["Custom"]
        self.tone_preset = Gtk.DropDown.new_from_strings(tnames)
        chosen_tone = str(self.state.get("tone_preset") or match_tone_preset(tone_state, self.state.get("master_db", 0)))
        self.tone_preset.set_selected(tnames.index(chosen_tone) if chosen_tone in tnames else tnames.index("Custom"))
        self.tone_preset.set_size_request(128, -1)
        self.tone_preset.connect("notify::selected", self._on_tone_preset)
        tone_ctrl.append(self.tone_preset)
        self.btn_tone_auto = self._tape_key("AUTO", self._on_tone_auto)
        self.btn_tone_auto.add_css_class("tape-load")
        if self._tone_auto_on:
            self.btn_tone_auto.add_css_class("hot")
        tone_ctrl.append(self.btn_tone_auto)
        tone_row.append(tone_ctrl)
        out_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        out_col.set_valign(Gtk.Align.CENTER)
        self.out_btns = {}
        for role, label in (
            ("speakers", "SPEAKERS"),
            ("usb", "USB AUDIO"),
            ("headphones", "HEADPHONES"),
        ):
            btn = Gtk.Button(label=label)
            btn.add_css_class("out-sel")
            btn.connect("clicked", lambda *_a, r=role: self._on_output_role(r))
            self.out_btns[role] = btn
            out_col.append(btn)
        tone_row.append(out_col)
        de_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        de_wrap.set_valign(Gtk.Align.CENTER)
        de_lab = Gtk.Label(label="DIGITAL", xalign=0.5)
        de_lab.add_css_class("field-label")
        de_wrap.append(de_lab)
        de_grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        de_row = None
        self.de_btns = {}
        for i, (key, label) in enumerate(DIGITAL_KEYS):
            if i % 2 == 0:
                de_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                de_grid.append(de_row)
            btn = Gtk.Button(label=label)
            btn.add_css_class("out-sel")
            btn.connect("clicked", lambda *_a, k=key: self._on_digital(k))
            self.de_btns[key] = btn
            de_row.append(btn)
        de_wrap.append(de_grid)
        self.de_note = Gtk.Label(xalign=0.5)
        self.de_note.add_css_class("field-label")
        self.de_note.set_text("POST  ·  off")
        de_wrap.append(self.de_note)
        tone_row.append(de_wrap)
        self._sync_digital_buttons()
        mon_inner.append(tone_row)
        cabinet.append(self._rack_unit("310", "STEREO MONITOR  ·  TONE  ·  GAIN  ·  OUTPUT  ·  DIGITAL  ·  WAVE", mon_inner))

        self.preset = Gtk.DropDown.new_from_strings(preset_names() + ["Custom"])
        self.preset.set_hexpand(True)
        self.preset.connect("notify::selected", self._on_preset)
        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        pl = Gtk.Label(label="PRESET", xalign=0)
        pl.add_css_class("field-label")
        preset_row.append(pl)
        preset_row.append(self.preset)
        self.btn_auto_eq = self._tape_key("AUTO", self._on_eq_auto)
        self.btn_auto_eq.add_css_class("tape-load")
        preset_row.append(self.btn_auto_eq)
        self._append_order(preset_row, "eq")

        self.eq_wave = EqWaveView(self._on_eq_changed)
        eq_frame = Gtk.Box()
        eq_frame.add_css_class("eq-wave-wrap")
        eq_frame.set_hexpand(True)
        eq_frame.append(self.eq_wave)
        eq_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        eq_inner.append(preset_row)
        eq_inner.append(eq_frame)
        cabinet.append(self._rack_unit("316", "GRAPHIC EQUALIZER", eq_inner))

        pair = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pair.add_css_class("rack-pair")
        pair.set_homogeneous(True)
        pair.set_hexpand(True)
        pair.append(self._rack_unit("330", "DYNAMIC PROCESSOR", self._build_dynamics()))
        pair.append(self._rack_unit("335", "DYNAMIC EXPANDER", self._build_expander()))
        cabinet.append(pair)
        pair2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pair2.add_css_class("rack-pair")
        pair2.set_homogeneous(True)
        pair2.set_hexpand(True)
        pair2.append(self._rack_unit("340", "STEREO PREAMP", self._build_preamp()))
        pair2.append(self._rack_unit("350", "DIGITAL EFFECT", self._build_fx()))
        cabinet.append(pair2)
        cabinet.append(self._rack_unit("355", "NOISE FILTER  ·  AUDIO CLARITY  ·  AUTO VOLUME", self._build_delay()))
        cabinet.append(self._rack_unit("370", "RS-TR575  ·  CASSETTE / REC / MIX", self._build_cassette()))

        self._load_into_widgets(self.state)
        self.connect("close-request", self._on_close_player)
        mix = None
        try:
            from .player import latest_radio_mix

            mix = latest_radio_mix()
        except Exception:
            mix = None
        if mix:
            self._tapes[0] = mix
            self.cassette.set_deck(self._tapes, 0, False, 0.0)
            self.cassette_note.set_text(f"Deck 1  {mix.name}")
        GLib.timeout_add(700, self._refresh_status)
        GLib.timeout_add(33, self._tick_meters)
        self._refresh_status()
        self._apply_skin(sid, persist=False)

    def _install_menu(self, app: Adw.Application) -> None:
        close_act = Gio.SimpleAction.new("close", None)
        close_act.connect("activate", lambda *_a: self.close())
        self.add_action(close_act)
        current = apply_skin(self.state.get("skin"))
        skin_act = Gio.SimpleAction.new_stateful(
            "skin", GLib.VariantType.new("s"), GLib.Variant.new_string(current)
        )
        skin_act.connect("activate", self._on_skin_action)
        self.add_action(skin_act)
        profile_act = Gio.SimpleAction.new("profile", GLib.VariantType.new("s"))
        profile_act.connect("activate", self._on_profile_action)
        self.add_action(profile_act)
        if app.lookup_action("quit") is None:
            quit_act = Gio.SimpleAction.new("quit", None)
            quit_act.connect("activate", lambda *_a: app.quit())
            app.add_action(quit_act)
            app.set_accels_for_action("app.quit", ["<Control>q"])

        root = Gio.Menu()
        file_m = Gio.Menu()
        file_m.append("Close", "win.close")
        file_m.append("Quit", "app.quit")
        root.append_submenu("FILE", file_m)
        skin_m = Gio.Menu()
        for sid, label in SKIN_CHOICES:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.skin", GLib.Variant.new_string(sid))
            skin_m.append_item(item)
        root.append_submenu("SKIN", skin_m)
        from .profiles import profile_names

        profile_m = Gio.Menu()
        for name in profile_names():
            item = Gio.MenuItem.new(name, None)
            item.set_action_and_target_value("win.profile", GLib.Variant.new_string(name))
            profile_m.append_item(item)
        root.append_submenu("PROFILE", profile_m)
        self._menubar = Gtk.PopoverMenuBar.new_from_model(root)
        self._menubar.add_css_class("cascade-menubar")
        self._menubar.set_hexpand(True)

    def _on_skin_action(self, action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if param is None:
            return
        action.set_state(param)
        self._apply_skin(param.get_string())

    def _apply_skin(self, skin_id: str, persist: bool = True) -> None:
        sid = apply_skin(skin_id)
        skin = active_skin()
        self._css.load_from_data(css_text().encode("utf-8"))
        scheme = Adw.ColorScheme.FORCE_LIGHT if skin["light"] else Adw.ColorScheme.FORCE_DARK
        Adw.StyleManager.get_default().set_color_scheme(scheme)
        self._title_main.set_text(str(skin["brand_name"]))
        self._title_sub.set_text(str(skin["tagline"]))
        for lab in self._brand_labels:
            lab.set_text(str(skin["brand_name"]))
        for view in (
            getattr(self, "wave", None),
            getattr(self, "needle_l", None),
            getattr(self, "needle_r", None),
            getattr(self, "eq_wave", None),
            getattr(self, "cassette", None),
            getattr(self, "la2a_comp", None),
            getattr(self, "la2a_exp", None),
            getattr(self, "la2a_pre", None),
            getattr(self, "la2a_fx", None),
            getattr(self, "la2a_dly", None),
            getattr(self, "exp_mix_knob", None),
            getattr(self, "fx_mix_knob", None),
            getattr(self, "dly_mix_knob", None),
            getattr(self, "dly_master_fader", None),
            getattr(self, "dly_clarity_knob", None),
            getattr(self, "dly_color_knob", None),
            getattr(self, "dyn_mix_knob", None),
            getattr(self, "pre_mix_knob", None),
        ):
            if view is not None and hasattr(view, "_render"):
                view._render()
        if persist:
            self.state["skin"] = sid
            save_state(self.state)

    def _rack_unit(self, model: str, title: str, inner: Gtk.Widget) -> Gtk.Box:
        unit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        unit.add_css_class("rack-unit")
        unit.set_hexpand(True)
        face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        face.add_css_class("rack-face")
        face.set_hexpand(True)
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        strip.set_hexpand(True)
        brand = Gtk.Label(label=str(active_skin()["brand_name"]), xalign=0)
        brand.add_css_class("rack-brand")
        self._brand_labels.append(brand)
        names = {
            "310": "RS-310",
            "316": "SH-GE90",
            "330": "SH-8038",
            "335": "SH-8046",
            "340": "SU-V6",
            "350": "SH-8055",
            "355": "SH-8066",
            "370": "RS-TR575",
        }
        mdl = Gtk.Label(label=names.get(model, f"RS-{model}"), xalign=0)
        mdl.add_css_class("rack-model")
        ttl = Gtk.Label(label=title, xalign=1)
        ttl.add_css_class("rack-title")
        ttl.set_hexpand(True)
        ttl.set_ellipsize(Pango.EllipsizeMode.END)
        strip.append(brand)
        strip.append(mdl)
        strip.append(ttl)
        expander = Gtk.Expander()
        expander.add_css_class("rack-expander")
        expander.set_hexpand(True)
        expander.set_label_widget(strip)
        try:
            expander.set_resize_toplevel(False)
        except AttributeError:
            pass
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.add_css_class("rack-body")
        body.append(inner)
        expander.set_child(body)
        opened = bool((self.state.get("rack_expanded") or {}).get(model, model == "310"))
        expander.set_expanded(opened)
        expander.connect("notify::expanded", self._on_rack_toggle, model)
        click = Gtk.GestureClick()
        click.connect(
            "released",
            lambda *_a, exp=expander: exp.set_expanded(not exp.get_expanded()),
        )
        strip.add_controller(click)
        self._expanders[model] = expander
        face.append(expander)
        unit.append(face)
        return unit

    def _save_full_rack_png(self, path: str) -> None:
        """Render the whole cabinet (every unit) to a PNG, not just the viewport."""
        from gi.repository import Graphene

        for exp in self._expanders.values():
            exp.set_expanded(True)
        ctx = GLib.MainContext.default()
        for _ in range(80):
            if not ctx.iteration(False):
                break

        chunks: list[Path] = []
        tmp_dir = Path(path).resolve().parent
        header = getattr(self, "_header", None)
        menubar = getattr(self, "_menubar", None)
        parts = [w for w in (menubar, header, self._cabinet) if w is not None]
        for i, widget in enumerate(parts):
            width = max(1, widget.get_width())
            if widget is self._cabinet:
                measured = widget.measure(Gtk.Orientation.VERTICAL, width)
                height = max(widget.get_height(), int(measured[1]), int(measured[0]), 1)
                widget.allocate(width, height, -1, None)
            else:
                height = max(1, widget.get_height())
            snapshot = Gtk.Snapshot()
            Gtk.Widget.do_snapshot(widget, snapshot)
            node = snapshot.to_node()
            if node is None:
                raise RuntimeError(f"snapshot failed for {widget.get_name()}")
            renderer = widget.get_native().get_renderer()
            rect = Graphene.Rect().init(0.0, 0.0, float(width), float(height))
            texture = renderer.render_texture(node, rect)
            part = tmp_dir / f".rack-part-{i}.png"
            if not texture.save_to_png(str(part)):
                raise RuntimeError(f"png save failed for {part}")
            chunks.append(part)

        from PIL import Image

        images = [Image.open(p).convert("RGB") for p in chunks]
        width = max(im.width for im in images)
        height = sum(im.height for im in images)
        stacked = Image.new("RGB", (width, height), (8, 10, 12))
        y = 0
        for im in images:
            stacked.paste(im, (0, y))
            y += im.height
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        stacked.save(dest, "PNG", optimize=True)
        for part in chunks:
            part.unlink(missing_ok=True)

    def _on_rack_toggle(self, expander: Gtk.Expander, _pspec, model: str) -> None:
        rec = dict(self.state.get("rack_expanded") or {})
        rec[model] = bool(expander.get_expanded())
        self.state["rack_expanded"] = rec
        save_state(self.state)

    def _unit_open(self, model: str) -> bool:
        exp = self._expanders.get(model)
        return True if exp is None else bool(exp.get_expanded())

    def _group(self, title: str) -> tuple[Gtk.Box, Gtk.Box]:
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        lab = Gtk.Label(label=title, xalign=0)
        lab.add_css_class("rack-section")
        frame.append(lab)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        frame.append(box)
        return frame, box

    def _labeled(self, box: Gtk.Box, text: str, widget: Gtk.Widget) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl = Gtk.Label(label=text.upper(), xalign=0)
        lbl.add_css_class("field-label")
        lbl.set_size_request(190, -1)
        row.append(lbl)
        row.append(widget)
        box.append(row)

    def _pos(self, scale: Gtk.Scale) -> float:
        adj = scale.get_adjustment()
        lo, hi = adj.get_lower(), adj.get_upper()
        if hi <= lo:
            return 0.0
        return float((scale.get_value() - lo) / (hi - lo))

    def _dyn_range(self, value: float, lo: float, hi: float) -> float:
        if hi <= lo:
            return 0.0
        return max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))

    def _refresh_la2a(self, peak: float = 0.0, rms: float = 0.0, gap: float | None = None, live: bool = False) -> None:
        if not hasattr(self, "la2a_comp"):
            return
        if not live or self._unit_open("330"):
            c = getattr(self, "_dyn_comp", None) or (self.state.get("compressor") or {})
            mix_dry, mix_wet = mix_levels(c)
            on = bool(c.get("enabled")) and mix_wet > 0.04
            thr = float(c.get("threshold_db", -18))
            rms_db = _db(max(rms, 1e-9))
            gr = min(1.0, max(0.0, (rms_db - thr) / 24.0)) if on else 0.0
            self.la2a_comp.update(
                on=on,
                mix_dry=mix_dry,
                mix_wet=mix_wet,
                peak_red=self._dyn_range(thr, -36, 0),
                gain=self._dyn_range(float(c.get("makeup_db", 0)), -6, 18),
                limit=str(c.get("mode", "rms")).lower() == "peak" or float(c.get("ratio", 4)) >= 10,
                level=_level_t(peak),
                gr=self.la2a_comp.gr * 0.62 + gr * 0.38,
                preset_label=str(getattr(self, "_dyn_name", "SAFE")),
            )
        if hasattr(self, "la2a_exp") and (not live or self._unit_open("335")):
            e = getattr(self, "_expander", None) or (self.state.get("expander") or {})
            mix_dry, mix_wet = mix_levels(e)
            on = bool(e.get("enabled")) and mix_wet > 0.04
            thr = float(e.get("threshold_db", -40))
            rms_db = _db(max(rms, 1e-9))
            gr = min(1.0, max(0.0, (thr - rms_db) / 24.0)) if on else 0.0
            self.la2a_exp.update(
                on=on,
                mix_dry=mix_dry,
                mix_wet=mix_wet,
                upward=str(e.get("em", "down")).lower() == "up",
                level=_level_t(peak),
                gr=self.la2a_exp.gr * 0.62 + gr * 0.38,
                preset_label=str(getattr(self, "_exp_name", "OPEN")),
            )
        if hasattr(self, "la2a_pre") and (not live or self._unit_open("340")):
            pre = getattr(self, "_pre", None) or (self.state.get("pre") or {})
            post = getattr(self, "_post", None) or (self.state.get("post") or {})
            mix_dry, mix_wet = mix_levels(post)
            preamp_db = float(getattr(self, "_preamp_db", self.state.get("preamp_db", 0)))
            self.la2a_pre.update(
                on=mix_wet > 0.04,
                mix_dry=mix_dry,
                mix_wet=mix_wet,
                gain=self._dyn_range(preamp_db, -12, 12),
                hpf_on=bool(pre.get("enabled")) and mix_wet > 0.04,
                hpf=self._dyn_range(float(pre.get("hpf_hz", 40)), 20, 200),
                level=_level_t(peak),
                preset_label=str(getattr(self, "_pre_name", "FLAT")),
                air_on=bool(post.get("enabled", True)) and abs(float(post.get("air_db", 0))) >= 0.1 and mix_wet > 0.04,
            )
        if hasattr(self, "la2a_fx") and (not live or self._unit_open("350")):
            fx = getattr(self, "_fx", None) or (self.state.get("fx") or {})
            mix_dry, mix_wet = mix_levels(fx)
            live = mix_wet > 0.04
            echo_on = live and bool(fx.get("echo_on"))
            room_on = live and bool(fx.get("room_on"))
            air_on = live and bool(fx.get("air_on")) and abs(float(fx.get("air_db", 0))) >= 0.1
            hpf_on = live and bool(fx.get("hpf_on"))
            lpf_on = live and bool(fx.get("lpf_on"))
            on = echo_on or room_on or air_on or hpf_on or lpf_on
            amount = mix_wet if on else 0.0
            self.la2a_fx.update(
                on=on,
                mix_dry=mix_dry,
                mix_wet=mix_wet,
                hpf_on=hpf_on,
                lpf_on=lpf_on,
                air_on=air_on,
                echo_on=echo_on,
                room_on=room_on,
                gain=min(1.0, amount),
                level=_level_t(peak),
                preset_label=str(getattr(self, "_fx_name", "BYPASS")),
            )
        if hasattr(self, "la2a_dly") and (not live or self._unit_open("355")):
            dly = getattr(self, "_delay", None) or (self.state.get("delay") or {})
            mix_dry, mix_wet = mix_levels(dly)
            live = mix_wet > 0.04
            auto_on = live and bool(dly.get("auto_on"))
            enhance = str(dly.get("enhance") or "") if auto_on else ""
            pong_on = live and bool(dly.get("pong_on"))
            chop_on = live and bool(dly.get("chop_on"))
            rev_on = live and bool(dly.get("rev_on"))
            crush_on = live and bool(dly.get("crush_on"))
            sift_on = live and bool(dly.get("sift_on"))
            on = auto_on or pong_on or chop_on or rev_on or crush_on or sift_on
            amount = mix_wet if on else 0.0
            if gap is not None:
                self._fx2_gap = float(gap)
            gap_amt = float(getattr(self, "_fx2_gap", 0.0)) if auto_on else 0.0
            self.la2a_dly.update(
                on=on,
                mix_dry=mix_dry,
                mix_wet=mix_wet,
                auto_on=auto_on,
                enhance=enhance,
                pong_on=pong_on,
                chop_on=chop_on,
                rev_on=rev_on,
                crush_on=crush_on,
                gap=gap_amt,
                gain=min(1.0, amount),
                level=_level_t(peak),
                preset_label=str(getattr(self, "_dly_name", "BYPASS")),
            )

    def _build_dynamics(self) -> Gtk.Box:
        from .presets import dyn_preset_names, match_dyn_preset

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.la2a_comp = VintageLa2aView("comp")
        face = Gtk.Box()
        face.add_css_class("scope-wrap")
        face.append(self.la2a_comp)
        inner.append(face)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("tape-keybed")
        preset_l = Gtk.Label(label="PRESET", xalign=0)
        preset_l.add_css_class("field-label")
        row.append(preset_l)
        names = dyn_preset_names() + ["CUSTOM"]
        self.dyn_preset = Gtk.DropDown.new_from_strings(names)
        self._dyn_comp = dict(self.state.get("compressor") or {})
        self._dyn_lim = dict(self.state.get("limiter") or {})
        self._dyn_name = match_dyn_preset(self._dyn_comp, self._dyn_lim)
        self.dyn_preset.set_selected(
            names.index(self._dyn_name) if self._dyn_name in names else names.index("CUSTOM")
        )
        self.dyn_preset.connect("notify::selected", self._on_dyn_preset)
        row.append(self.dyn_preset)
        self._append_order(row, "dyn")
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        self.dyn_mix_knob = self._mix_dial(self._dyn_comp, self._on_dyn_mix)
        row.append(self.dyn_mix_knob)
        inner.append(row)
        return inner

    def _on_dyn_preset(self, *_args) -> None:
        if self._suppress:
            return
        from .presets import dyn_preset_mapping, dyn_preset_names

        names = dyn_preset_names() + ["CUSTOM"]
        idx = int(self.dyn_preset.get_selected())
        name = names[idx] if 0 <= idx < len(names) else "SAFE"
        if name == "CUSTOM":
            return
        self._dyn_name = name
        old = dict(self._dyn_comp or {})
        self._dyn_comp, self._dyn_lim = dyn_preset_mapping(name)
        keep_mix(self._dyn_comp, old)
        self._dirty()

    def _build_expander(self) -> Gtk.Box:
        from .presets import exp_preset_names, match_exp_preset

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.la2a_exp = VintageLa2aView("exp")
        face = Gtk.Box()
        face.add_css_class("scope-wrap")
        face.append(self.la2a_exp)
        inner.append(face)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("tape-keybed")
        preset_l = Gtk.Label(label="PRESET", xalign=0)
        preset_l.add_css_class("field-label")
        row.append(preset_l)
        names = exp_preset_names() + ["CUSTOM"]
        self.exp_preset = Gtk.DropDown.new_from_strings(names)
        self._expander = dict(self.state.get("expander") or {})
        self._exp_name = match_exp_preset(self._expander)
        self.exp_preset.set_selected(
            names.index(self._exp_name) if self._exp_name in names else names.index("CUSTOM")
        )
        self.exp_preset.connect("notify::selected", self._on_exp_preset)
        row.append(self.exp_preset)
        self._append_order(row, "exp")
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        self.exp_mix_knob = self._mix_dial(self._expander, self._on_exp_mix)
        row.append(self.exp_mix_knob)
        inner.append(row)
        return inner

    def _on_exp_preset(self, *_args) -> None:
        if self._suppress:
            return
        from .presets import exp_preset_mapping, exp_preset_names

        names = exp_preset_names() + ["CUSTOM"]
        idx = int(self.exp_preset.get_selected())
        name = names[idx] if 0 <= idx < len(names) else "OPEN"
        if name == "CUSTOM":
            return
        self._exp_name = name
        old = dict(self._expander or {})
        self._expander = exp_preset_mapping(name)
        keep_mix(self._expander, old)
        self._dirty()

    def _build_preamp(self) -> Gtk.Box:
        from .presets import match_pre_preset, pre_preset_names

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.la2a_pre = VintageLa2aView("pre")
        self.la2a_pre.preset_label = "FLAT"
        face = Gtk.Box()
        face.add_css_class("scope-wrap")
        face.append(self.la2a_pre)
        inner.append(face)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("tape-keybed")
        preset_l = Gtk.Label(label="PRESET", xalign=0)
        preset_l.add_css_class("field-label")
        row.append(preset_l)
        names = pre_preset_names() + ["CUSTOM"]
        self.pre_preset = Gtk.DropDown.new_from_strings(names)
        self._preamp_db = float(self.state.get("preamp_db", 0))
        self._pre = dict(self.state.get("pre") or {})
        self._post = dict(self.state.get("post") or {})
        self._pre_name = match_pre_preset(self._preamp_db, self._pre, self._post)
        self.pre_preset.set_selected(
            names.index(self._pre_name) if self._pre_name in names else names.index("CUSTOM")
        )
        self.pre_preset.connect("notify::selected", self._on_pre_preset)
        row.append(self.pre_preset)
        self._append_order(row, "pre")
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        self.pre_mix_knob = self._mix_dial(self._post, self._on_pre_mix)
        row.append(self.pre_mix_knob)
        inner.append(row)
        return inner

    def _on_pre_preset(self, *_args) -> None:
        if self._suppress:
            return
        from .presets import pre_preset_mapping, pre_preset_names

        names = pre_preset_names() + ["CUSTOM"]
        idx = int(self.pre_preset.get_selected())
        name = names[idx] if 0 <= idx < len(names) else "FLAT"
        if name == "CUSTOM":
            return
        self._pre_name = name
        old = dict(self._post or {})
        self._preamp_db, self._pre, self._post = pre_preset_mapping(name)
        keep_mix(self._post, old)
        self._dirty()

    def _build_fx(self) -> Gtk.Box:
        from .presets import fx_preset_names, match_fx_preset

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.la2a_fx = VintageLa2aView("fx")
        face = Gtk.Box()
        face.add_css_class("scope-wrap")
        face.append(self.la2a_fx)
        inner.append(face)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("tape-keybed")
        preset_l = Gtk.Label(label="PRESET", xalign=0)
        preset_l.add_css_class("field-label")
        row.append(preset_l)
        names = fx_preset_names() + ["CUSTOM"]
        self.fx_preset = Gtk.DropDown.new_from_strings(names)
        self._fx = dict(self.state.get("fx") or {})
        self._fx_name = match_fx_preset(self._fx)
        self.fx_preset.set_selected(
            names.index(self._fx_name) if self._fx_name in names else names.index("CUSTOM")
        )
        self.fx_preset.connect("notify::selected", self._on_fx_preset)
        row.append(self.fx_preset)
        self._append_order(row, "fx")
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        self.fx_mix_knob = self._mix_dial(self._fx, self._on_fx_mix)
        row.append(self.fx_mix_knob)
        inner.append(row)
        return inner

    def _on_fx_preset(self, *_args) -> None:
        if self._suppress:
            return
        from .presets import fx_preset_mapping, fx_preset_names

        names = fx_preset_names() + ["CUSTOM"]
        idx = int(self.fx_preset.get_selected())
        name = names[idx] if 0 <= idx < len(names) else "BYPASS"
        if name == "CUSTOM":
            return
        self._fx_name = name
        old = dict(self._fx or {})
        self._fx = fx_preset_mapping(name)
        keep_mix(self._fx, old)
        self._dirty()

    def _build_delay(self) -> Gtk.Box:
        from .presets import delay_preset_names, match_delay_preset

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.la2a_dly = VintageLa2aView("delay")
        face = Gtk.Box()
        face.add_css_class("scope-wrap")
        face.set_hexpand(True)
        face.append(self.la2a_dly)
        self._delay = dict(self.state.get("delay") or {})
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.append(face)
        self.dly_master_fader = RackFaderView(
            "MASTER",
            float(self._delay.get("master", 1.0)),
            self._on_dly_master,
        )
        top.append(self.dly_master_fader)
        inner.append(top)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("tape-keybed")
        preset_l = Gtk.Label(label="PRESET", xalign=0)
        preset_l.add_css_class("field-label")
        row.append(preset_l)
        names = delay_preset_names() + ["CUSTOM"]
        self.dly_preset = Gtk.DropDown.new_from_strings(names)
        self._dly_name = match_delay_preset(self._delay)
        self.dly_preset.set_selected(
            names.index(self._dly_name) if self._dly_name in names else names.index("CUSTOM")
        )
        self.dly_preset.connect("notify::selected", self._on_dly_preset)
        row.append(self.dly_preset)
        self._append_order(row, "fx2")
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        self.dly_clarity_knob = RackKnobView(
            "CLARITY",
            float(self._delay.get("clarity", 0.55)),
            self._on_dly_clarity,
            steps=0,
        )
        self.dly_color_knob = RackKnobView(
            "COLOR",
            float(self._delay.get("color", 0.4)),
            self._on_dly_color,
            steps=0,
        )
        row.append(self.dly_clarity_knob)
        row.append(self.dly_color_knob)
        self.dly_mix_knob = self._mix_dial(self._delay, self._on_dly_mix)
        row.append(self.dly_mix_knob)
        inner.append(row)
        return inner

    def _on_dly_preset(self, *_args) -> None:
        if self._suppress:
            return
        from .presets import delay_preset_mapping, delay_preset_names

        names = delay_preset_names() + ["CUSTOM"]
        idx = int(self.dly_preset.get_selected())
        name = names[idx] if 0 <= idx < len(names) else "BYPASS"
        if name == "CUSTOM":
            return
        self._dly_name = name
        old = dict(self._delay or {})
        self._delay = delay_preset_mapping(name)
        keep_mix(self._delay, old)
        keep_dly_tone(self._delay, old)
        self._sync_dly_tone()
        self._dirty()

    def _mix_dial(self, rec: dict, on_change) -> RackKnobView:
        return RackKnobView("MIX", mix_amount(rec), on_change, steps=MIX_STEPS)

    def _db_knob(self, label: str, db: float, on_change):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_valign(Gtk.Align.CENTER)
        knob = RackKnobView(label, master_knob_value(db), on_change, steps=MASTER_STEPS, bipolar=True)
        readout = Gtk.Label(xalign=0.5)
        readout.add_css_class("field-label")
        readout.add_css_class("gain-readout")
        readout.set_text(f"{clamp_master_db(db):+.0f} dB")
        col.append(knob)
        col.append(readout)
        return col, knob, readout

    def _append_order(self, row: Gtk.Box, key: str) -> None:
        lab = Gtk.Label(label="ORDER", xalign=0)
        lab.add_css_class("field-label")
        row.append(lab)
        names = [str(i) for i in range(1, len(CHAIN_STAGES) + 1)]
        dd = Gtk.DropDown.new_from_strings(names)
        dd.set_hexpand(False)
        dd.set_size_request(72, -1)
        dd.set_selected(self._chain_order.index(key))
        dd.connect("notify::selected", lambda *_a, k=key: self._on_chain_slot(k))
        self._order_dd[key] = dd
        row.append(dd)

    def _sync_order_dropdowns(self) -> None:
        for key, dd in self._order_dd.items():
            dd.set_selected(self._chain_order.index(key))

    def _on_chain_slot(self, key: str) -> None:
        if self._suppress:
            return
        dd = self._order_dd.get(key)
        if dd is None:
            return
        new_i = int(dd.get_selected())
        old_i = self._chain_order.index(key)
        if new_i == old_i or not (0 <= new_i < len(self._chain_order)):
            return
        order = list(self._chain_order)
        order[old_i], order[new_i] = order[new_i], order[old_i]
        self._chain_order = order
        self._suppress = True
        self._sync_order_dropdowns()
        self._suppress = False
        self._dirty()

    def _on_exp_mix(self, value: float) -> None:
        if self._suppress:
            return
        self._expander = dict(self._expander or {})
        set_mix(self._expander, value)
        self._dirty()

    def _on_fx_mix(self, value: float) -> None:
        if self._suppress:
            return
        self._fx = dict(self._fx or {})
        set_mix(self._fx, value)
        self._dirty()

    def _on_dly_mix(self, value: float) -> None:
        if self._suppress:
            return
        self._delay = dict(self._delay or {})
        set_mix(self._delay, value)
        self._dirty()

    def _on_dly_master(self, value: float) -> None:
        if self._suppress:
            return
        self._delay = dict(self._delay or {})
        self._delay["master"] = max(0.0, min(1.0, float(value)))
        self._dirty()

    def _on_dly_clarity(self, value: float) -> None:
        if self._suppress:
            return
        self._delay = dict(self._delay or {})
        self._delay["clarity"] = max(0.0, min(1.0, float(value)))
        self._dirty()

    def _on_dly_color(self, value: float) -> None:
        if self._suppress:
            return
        self._delay = dict(self._delay or {})
        self._delay["color"] = max(0.0, min(1.0, float(value)))
        self._dirty()

    def _sync_dly_tone(self) -> None:
        dly = self._delay or {}
        if hasattr(self, "dly_master_fader"):
            self.dly_master_fader.set_value(float(dly.get("master", 1.0)))
        if hasattr(self, "dly_clarity_knob"):
            self.dly_clarity_knob.set_value(float(dly.get("clarity", 0.55)))
        if hasattr(self, "dly_color_knob"):
            self.dly_color_knob.set_value(float(dly.get("color", 0.4)))

    def _on_dyn_mix(self, value: float) -> None:
        if self._suppress:
            return
        self._dyn_comp = dict(self._dyn_comp or {})
        set_mix(self._dyn_comp, value)
        self._dirty()

    def _on_pre_mix(self, value: float) -> None:
        if self._suppress:
            return
        self._post = dict(self._post or {})
        set_mix(self._post, value)
        self._dirty()

    def _rec_format(self):
        idx = int(self.rec_format.get_selected())
        if 0 <= idx < len(self._rec_formats):
            return self._rec_formats[idx]
        return self._rec_formats[0]

    def _save_rec_prefs(self) -> None:
        rec = dict(self.state.get("record") or {})
        fmt = self._rec_format()
        rec["format"] = fmt.key
        typed = self.rec_path.get_text().strip()
        if typed:
            p = Path(typed).expanduser()
            rec["directory"] = str(p if p.is_dir() else p.parent)
        self.state["record"] = rec
        save_state(self.state)

    def _on_rec_format(self, *_args) -> None:
        if self._suppress:
            return
        self._save_rec_prefs()
        if self.rec_arm.get_active():
            return
        fmt = self._rec_format()
        text = self.rec_path.get_text().strip()
        if text:
            p = Path(text).expanduser()
            if p.suffix:
                self.rec_path.set_text(str(p.with_suffix(fmt.ext)))
                return
        self._fill_rec_path()

    def _fill_rec_path(self) -> None:
        rec = self.state.get("record") or {}
        folder = Path(rec.get("directory") or (Path.home() / "Music" / "Cascade EQ"))
        fmt = self._rec_format()
        self.rec_path.set_text(str(folder / f"cascade-{fmt.ext.lstrip('.')}{fmt.ext}"))

    def _browse_rec(self, *_args) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Record processed audio")
        fmt = self._rec_format()
        dialog.set_initial_name(f"cascade{fmt.ext}")
        dialog.save(self, None, self._on_rec_chosen)

    def _on_rec_chosen(self, dialog, result) -> None:
        try:
            gio_file = dialog.save_finish(result)
        except GLib.Error:
            return
        if gio_file is None:
            return
        path = gio_file.get_path()
        if path:
            self.rec_path.set_text(path)
            self._save_rec_prefs()

    def _on_rec(self, *_args) -> None:
        if self._suppress:
            return
        if self.rec_arm.get_active():
            self._rec_start()
        else:
            self._rec_stop()

    def _rec_start(self) -> None:
        self._save_rec_prefs()
        fmt = self._rec_format()
        payload = {
            "cmd": "record-start",
            "format": fmt.key,
            "path": self.rec_path.get_text().strip() or None,
            "exists": "unique",
        }
        try:
            ensure_daemon()
            if not self.enable.get_active():
                raise ClientError("Turn POWER on before recording.")
            data = request(payload, timeout=12.0)
            self.rec_path.set_text(str(data.get("path") or ""))
            self.rec_note.set_text("")
            self._set_rec_lit(True)
        except ClientError as exc:
            self._suppress = True
            self.rec_arm.set_active(False)
            self._suppress = False
            self.rec_note.set_text(str(exc))
            self._set_rec_lit(False)

    def _rec_stop(self) -> str | None:
        saved = None
        try:
            if ping():
                data = request({"cmd": "record-stop"}, timeout=12.0)
                saved = data.get("saved") or data.get("path")
                self._last_rec_path = str(saved or "")
                self.rec_note.set_text(f"Saved {saved}" if saved else "")
        except ClientError as exc:
            self.rec_note.set_text(str(exc))
        self._set_rec_lit(False)
        return str(saved) if saved else None

    def _set_rec_lit(self, on: bool) -> None:
        if on:
            self.rec_led.add_css_class("hot")
        else:
            self.rec_led.remove_css_class("hot")

    def _session_set_note(self, text: str) -> None:
        self.cassette_note.set_text(text)

    def _save_session_prefs(self, *_args) -> None:
        if self._suppress:
            return
        from .session import mix_preset_mapping, mix_preset_names

        rec = dict(self.state.get("session") or {})
        names = mix_preset_names()
        idx = int(self.session_preset.get_selected()) if hasattr(self, "session_preset") else 0
        name = names[idx] if 0 <= idx < len(names) else "POP"
        rec.update(mix_preset_mapping(name))
        rec["preset"] = name
        if self._session_file:
            rec["last"] = self._session_file
        self.state["session"] = rec
        save_state(self.state)

    def _mix_settings(self):
        from .session import MixSettings

        self._save_session_prefs()
        return MixSettings.from_mapping(self.state.get("session"))

    def _on_session_rec(self, *_args) -> None:
        if self._suppress:
            return
        if self.session_arm.get_active():
            self._session_start()
        else:
            self._session_stop()

    def _session_start(self) -> None:
        from datetime import datetime

        dest = sessions_dir() / f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}.flac"
        try:
            ensure_daemon()
            if not self.enable.get_active():
                raise ClientError("Turn POWER on before recording a session.")
            data = request(
                {
                    "cmd": "record-start",
                    "format": "flac",
                    "path": str(dest),
                    "exists": "unique",
                },
                timeout=12.0,
            )
            self._session_file = str(data.get("path") or dest)
            self._session_set_note(f"Recording session → {self._session_file}")
            self._set_rec_lit(True)
        except ClientError as exc:
            self._suppress = True
            self.session_arm.set_active(False)
            self._suppress = False
            self._session_set_note(str(exc))

    def _session_stop(self) -> None:
        saved = self._session_file
        try:
            if ping():
                data = request({"cmd": "record-stop"}, timeout=12.0)
                saved = data.get("saved") or data.get("path") or saved
        except ClientError as exc:
            self._session_set_note(str(exc))
            return
        self._set_rec_lit(False)
        if not saved:
            self._session_set_note("Session recording stopped.")
            return
        self._session_file = str(saved)
        rec = dict(self.state.get("session") or {})
        rec["last"] = self._session_file
        self.state["session"] = rec
        save_state(self.state)
        self._session_set_note(f"Saved {saved} — splitting on silence…")
        self._run_session_job(self._job_split, saved)

    def _run_session_job(self, fn, *args) -> None:
        if self._session_busy:
            self._session_set_note("Already splitting or mixing.")
            return
        self._session_busy = True

        def work():
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self._session_set_note, str(exc))
            finally:
                GLib.idle_add(self._session_unbusy)

        threading.Thread(target=work, daemon=True).start()

    def _session_unbusy(self) -> bool:
        self._session_busy = False
        return False

    def _job_split(self, path: str) -> None:
        from .session import split_session

        def prog(msg: str) -> None:
            GLib.idle_add(self._session_set_note, msg)

        data = split_session(path, progress=prog)

        def done() -> bool:
            rows = data.get("tracks") or []
            lines = [
                f"{row['index']:02d}  {row.get('camelot') or row.get('key')}  "
                f"E{row.get('energy', '—')}  {row['bpm']:.0f} BPM  {row['key']}"
                + (f"  pk {row['peak_db']:.0f}dB" if "peak_db" in row else "")
                for row in rows
            ]
            self.session_tracks.set_text("\n".join(lines) or "No tracks found.")
            self._set_preview_tracks(rows)
            self._session_set_note(
                f"{len(rows)} tracks in {data['directory']}. Preview a track, or RADIO MIX."
            )
            rec = dict(self.state.get("session") or {})
            rec["last"] = path
            rec["tracks"] = data["directory"]
            self.state["session"] = rec
            save_state(self.state)
            return False

        GLib.idle_add(done)

    def _job_mix(self, path: str, settings=None) -> None:
        from .session import mix_session

        def prog(msg: str) -> None:
            GLib.idle_add(self._session_set_note, msg)

        data = mix_session(path, progress=prog, settings=settings)

        def done() -> bool:
            played = data.get("played_bpm") or []
            glide = " → ".join(str(x) for x in played)
            order = data.get("order") or []
            if order:
                lines = [
                    f"{row['index']:02d}  {row.get('key')}  E{row.get('energy')}  "
                    f"{row.get('bpm')} BPM"
                    + (f"  {row['into']}" if row.get("into") and row.get("into") != "end" else "")
                    for row in order
                ]
                self.session_tracks.set_text("\n".join(lines))
                self._set_preview_tracks(order)
            counts = data.get("transition_counts") or {}
            xfades = ""
            if counts:
                xfades = (
                    f"  echo {counts.get('echo', 0)}  "
                    f"blend {counts.get('blend', 0)}  "
                    f"bump {counts.get('bump', 0)}  cut {counts.get('cut', 0)}"
                )
            self._session_set_note(
                f"Radio mix {data['tracks']} songs  {data.get('style', 'pop')}  "
                f"{data.get('bit_depth', 16)}-bit  start {data.get('mix_root')}  "
                f"{glide} BPM{xfades}  songs {data.get('target_db', data.get('min_db'))} dB  "
                f"peak {data.get('peak_db', data.get('max_db'))} dB "
                f"({data.get('mix_gain_db', 0):+.1f} dB)  →  {data['output']}"
            )
            out = data.get("output")
            if out and Path(out).exists():
                self._load_tape(0, Path(out))
            return False

        GLib.idle_add(done)

    def _on_session_split(self, *_args) -> None:
        path = self._session_file or (self.state.get("session") or {}).get("last")
        if not path:
            self._session_set_note("Record a session first, or REC on the cassette deck then SPLIT.")
            return
        self._session_set_note("Splitting on silence…")
        self._run_session_job(self._job_split, path)

    def _on_session_mix(self, *_args) -> None:
        rec = self.state.get("session") or {}
        path = rec.get("tracks") or self._session_file or rec.get("last")
        if not path:
            self._session_set_note("Split a session first.")
            return
        self._session_set_note(
            f"Radio mixing {self._mix_settings().style}  {str((self.state.get('session') or {}).get('preset') or 'POP')}…"
        )
        self._run_session_job(self._job_mix, path, self._mix_settings())

    def _build_cassette(self) -> Gtk.Box:
        self._rec_formats = available_formats()
        self._rec_elapsed = 0.0
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.cassette = CassetteDeckView(self._on_well_select)
        frame = Gtk.Box()
        frame.add_css_class("scope-wrap")
        frame.append(self.cassette)
        inner.append(frame)
        self.cassette_note = Gtk.Label(
            label="REC captures the mix. HIGH SPEED DUB copies Deck 1 through the rack into Deck 2 at 2×. AUTO on the EQ listens every 4 beats and lifts buried detail without changing your curve.",
            xalign=0,
        )
        self.cassette_note.add_css_class("field-label")
        self.cassette_note.set_wrap(True)
        inner.append(self.cassette_note)
        self.rec_note = self.cassette_note
        self.session_note = self.cassette_note
        self._session_busy = False
        self._session_file = (self.state.get("session") or {}).get("last")
        self.rec_led = Gtk.Label(label="●")
        self.rec_led.add_css_class("rec-led")
        self.rec_led.set_visible(False)
        self.rec_clock = Gtk.Label(label="00:00")
        self.rec_clock.add_css_class("rec-clock")
        keys = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        keys.add_css_class("tape-keybed")
        self.rec_arm = Gtk.ToggleButton(label="REC")
        self.rec_arm.add_css_class("rec-arm")
        self.rec_arm.connect("toggled", self._on_rec)
        keys.append(self.rec_arm)
        keys.append(self.rec_clock)
        self.btn_rew = self._tape_key("◄◄", self._on_tape_rew)
        self.btn_play = self._tape_key("PLAY", self._on_tape_play)
        self.btn_play.add_css_class("tape-play")
        self.btn_pause = self._tape_key("PAUSE", self._on_tape_pause)
        self.btn_stop = self._tape_key("STOP", self._on_tape_stop)
        self.btn_ff = self._tape_key("►►", self._on_tape_ff)
        for btn in (self.btn_rew, self.btn_play, self.btn_pause, self.btn_stop, self.btn_ff):
            keys.append(btn)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        keys.append(spacer)
        self.tape_dolby = Gtk.ToggleButton(label="DOLBY B")
        self.tape_dolby.add_css_class("tape-key")
        self.tape_dolby.add_css_class("tape-mode")
        self.tape_dolby.set_active(True)
        self.tape_dolby.connect("toggled", self._on_tape_mode)
        self.tape_rev = Gtk.ToggleButton(label="AUTO REV")
        self.tape_rev.add_css_class("tape-key")
        self.tape_rev.add_css_class("tape-mode")
        self.tape_rev.set_active(True)
        self.tape_rev.connect("toggled", self._on_tape_mode)
        self.tape_dub = Gtk.ToggleButton(label="HIGH SPEED DUB")
        self.tape_dub.add_css_class("tape-key")
        self.tape_dub.add_css_class("tape-mode")
        self.tape_dub.connect("toggled", self._on_tape_mode)
        keys.append(self.tape_dolby)
        keys.append(self.tape_rev)
        keys.append(self.tape_dub)
        load_a = self._tape_key("EJECT 1", lambda *_: self._browse_tape(0))
        load_b = self._tape_key("EJECT 2", lambda *_: self._browse_tape(1))
        load_a.add_css_class("tape-load")
        load_b.add_css_class("tape-load")
        keys.append(load_a)
        keys.append(load_b)
        inner.append(keys)
        ses_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ses_row.add_css_class("tape-keybed")
        self.session_arm = Gtk.ToggleButton(label="SESSION")
        self.session_arm.add_css_class("rec-arm")
        self.session_arm.connect("toggled", self._on_session_rec)
        split_btn = self._tape_key("SPLIT", self._on_session_split)
        mix_btn = self._tape_key("RADIO MIX", self._on_tape_mix)
        mix_btn.add_css_class("tape-load")
        ses_row.append(self.session_arm)
        ses_row.append(split_btn)
        ses_row.append(mix_btn)
        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        ses_row.append(spacer2)
        from .session import mix_preset_names

        preset_l = Gtk.Label(label="PRESET", xalign=0)
        preset_l.add_css_class("field-label")
        ses_row.append(preset_l)
        names = mix_preset_names()
        self.session_preset = Gtk.DropDown.new_from_strings(names)
        ses = self.state.get("session") or {}
        chosen = str(ses.get("preset") or "").upper()
        if chosen not in names:
            chosen = "HOUSE" if str(ses.get("mix_style") or "").lower() == "house" else "POP"
        self.session_preset.set_selected(names.index(chosen) if chosen in names else 0)
        self.session_preset.connect("notify::selected", self._save_session_prefs)
        ses_row.append(self.session_preset)
        inner.append(ses_row)
        preview_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        preview_row.add_css_class("tape-keybed")
        track_l = Gtk.Label(label="TRACK", xalign=0)
        track_l.add_css_class("field-label")
        preview_row.append(track_l)
        self.track_preview = Gtk.DropDown.new_from_strings(["— split a session first —"])
        self.track_preview.set_hexpand(True)
        self.track_preview.connect("notify::selected", self._on_preview_select)
        preview_row.append(self.track_preview)
        self.btn_preview = self._tape_key("PLAY TRACK", self._on_track_preview)
        self.btn_preview.add_css_class("tape-play")
        self.btn_preview.set_sensitive(False)
        preview_row.append(self.btn_preview)
        inner.append(preview_row)
        self.session_tracks = Gtk.Label(xalign=0)
        self.session_tracks.set_wrap(True)
        self.session_tracks.add_css_class("rec-path")
        inner.append(self.session_tracks)
        dest = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dest.add_css_class("tape-keybed")
        fmt_l = Gtk.Label(label="FORMAT", xalign=0)
        fmt_l.add_css_class("field-label")
        dest.append(fmt_l)
        self.rec_format = Gtk.DropDown.new_from_strings([f.label for f in self._rec_formats] or ["FLAC"])
        self.rec_format.connect("notify::selected", self._on_rec_format)
        dest.append(self.rec_format)
        self.rec_path = Gtk.Entry()
        self.rec_path.set_hexpand(True)
        self.rec_path.add_css_class("rec-path")
        self.rec_path.set_placeholder_text("~/Music/Cascade EQ/")
        dest.append(self.rec_path)
        browse = Gtk.Button(label="BROWSE")
        browse.add_css_class("tape-key")
        browse.add_css_class("tape-load")
        browse.connect("clicked", self._browse_rec)
        dest.append(browse)
        inner.append(dest)
        self._restore_preview_tracks()
        return inner

    def _tape_key(self, label: str, handler) -> Gtk.Button:
        btn = Gtk.Button(label=label)
        btn.add_css_class("tape-key")
        btn.connect("clicked", handler)
        return btn

    def _preview_label(self, row: dict) -> str:
        idx = int(row.get("index") or 0)
        key = str(row.get("camelot") or row.get("key") or "—")
        energy = row.get("energy")
        bpm = row.get("bpm")
        name = Path(str(row.get("path") or "")).name or "track"
        bits = [f"{idx:02d}" if idx else name, key]
        if energy not in (None, "", "—"):
            bits.append(f"E{energy}")
        try:
            bits.append(f"{float(bpm):.0f} BPM")
        except (TypeError, ValueError):
            pass
        bits.append(name)
        return "  ".join(bits)

    def _set_preview_tracks(self, rows: list | None) -> None:
        found: list[dict] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            path = Path(str(row.get("path") or "")).expanduser()
            if not path.is_file():
                continue
            item = dict(row)
            item["path"] = str(path)
            found.append(item)
        self._split_tracks = found
        labels = [self._preview_label(row) for row in found] or ["— split a session first —"]
        self._suppress_preview = True
        if hasattr(self, "track_preview"):
            self.track_preview.set_model(Gtk.StringList.new(labels))
            self.track_preview.set_selected(0)
        self._suppress_preview = False
        if hasattr(self, "btn_preview"):
            self.btn_preview.set_sensitive(bool(found))
        if found and hasattr(self, "cassette_note"):
            self.cassette_note.set_text(
                f"{len(found)} tracks ready  ·  pick one and PLAY TRACK"
            )

    def _restore_preview_tracks(self) -> None:
        from .session import load_track_manifest

        rec = self.state.get("session") or {}
        man = load_track_manifest(rec.get("tracks") or rec.get("last") or self._session_file)
        if man:
            self._set_preview_tracks(man.get("tracks") or [])

    def _selected_split_track(self) -> dict | None:
        if not self._split_tracks or not hasattr(self, "track_preview"):
            return None
        idx = int(self.track_preview.get_selected())
        if 0 <= idx < len(self._split_tracks):
            return self._split_tracks[idx]
        return None

    def _on_preview_select(self, *_args) -> None:
        if self._suppress or self._suppress_preview:
            return
        row = self._selected_split_track()
        if not row:
            return
        path = Path(row["path"])
        playing = self._player is not None and self._player.state() == "playing"
        if not playing:
            self._load_tape(self._tape_well, path)
        self.cassette_note.set_text(f"TRACK  {self._preview_label(row)}  — PLAY TRACK to hear it")

    def _on_track_preview(self, *_args) -> None:
        row = self._selected_split_track()
        if not row:
            self.cassette_note.set_text("Split a session first.")
            return
        path = Path(row["path"])
        if not path.is_file():
            self.cassette_note.set_text(f"Missing {path.name}")
            return
        self._load_tape(self._tape_well, path)
        if not self._prep_play():
            return
        try:
            self._player_inst().set_tape(dolby=self.tape_dolby.get_active(), rate=1.0)
            self._player_inst().play()
            self.btn_play.add_css_class("hot")
            self.btn_pause.remove_css_class("hot")
            through = "cascade_eq" if self._player_inst().through_eq() else "output"
            self.cassette_note.set_text(
                f"PLAY TRACK  {self._preview_label(row)}  →  {through}"
            )
        except Exception as exc:
            self.cassette_note.set_text(str(exc))

    def _on_tape_mode(self, *_args) -> None:
        if hasattr(self, "cassette"):
            self.cassette.dolby = self.tape_dolby.get_active()
            self.cassette.auto_rev = self.tape_rev.get_active()
            self.cassette.dub_armed = self.tape_dub.get_active()
            self.cassette._render()
        if self._player is not None and not self._dubbing:
            try:
                self._player.set_tape(dolby=self.tape_dolby.get_active(), rate=1.0)
            except Exception:
                pass

    def _player_inst(self):
        if self._player is None:
            from .player import FilePlayer

            self._player = FilePlayer()
            self._player.on_eos = self._on_tape_eos
            self._player.on_error = self._on_tape_error
        return self._player

    def _on_well_select(self, well: int) -> None:
        self._tape_well = well
        path = self._tapes[well]
        name = path.name if path else "empty"
        self.cassette_note.set_text(f"Deck {well + 1}  {name}")

    def _browse_tape(self, well: int) -> None:
        self._tape_well = well
        dialog = Gtk.FileDialog()
        dialog.set_title(f"Eject / load Deck {well + 1}")
        filt = Gtk.FileFilter()
        filt.set_name("Audio")
        filt.add_mime_type("audio/*")
        try:
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(filt)
            dialog.set_filters(store)
        except Exception:
            pass
        try:
            dialog.set_default_filter(filt)
        except Exception:
            pass
        try:
            dialog.set_initial_folder(Gio.File.new_for_path(str(sessions_dir())))
        except Exception:
            pass
        dialog.open(self, None, lambda d, r: self._on_tape_chosen(d, r, well))

    def _on_tape_chosen(self, dialog, result, well: int) -> None:
        try:
            gio_file = dialog.open_finish(result)
        except GLib.Error:
            return
        if gio_file is None:
            return
        path = gio_file.get_path()
        if path:
            self._load_tape(well, Path(path))

    def _load_tape(self, well: int, path: Path) -> None:
        path = Path(path).expanduser()
        self._tapes[well] = path
        self._tape_well = well
        try:
            player = self._player_inst()
            playing = player.state() == "playing"
            current = Path(player.path).resolve() if player.path else None
            if not playing or current is None or current == path.resolve():
                player.load(path)
                playing = player.state() == "playing"
            pos, dur = player.progress()
            frac = (pos / dur) if dur > 0 else 0.0
        except Exception as exc:
            self.cassette_note.set_text(str(exc))
            self.cassette.set_deck(self._tapes, well, False, 0.0)
            return
        self.cassette.set_deck(self._tapes, well, playing, frac)
        self.cassette_note.set_text(f"Deck {well + 1}  {path.name}")

    def _on_tape_mix(self, *_args) -> None:
        rec = self.state.get("session") or {}
        path = rec.get("tracks") or self._session_file or rec.get("last")
        if path:
            self._on_session_mix()
            return
        from .player import latest_radio_mix

        mix = latest_radio_mix()
        if not mix:
            self.cassette_note.set_text("Record a SESSION, SPLIT, then RADIO MIX — or load a mix with EJECT.")
            return
        self._load_tape(self._tape_well, mix)

    def _prep_play(self) -> bool:
        path = self._tapes[self._tape_well]
        if not path:
            self.cassette_note.set_text("Load a tape into this well first.")
            return False
        try:
            if not self.enable.get_active():
                self.enable.set_active(True)
            ensure_daemon()
            player = self._player_inst()
            if player.path != Path(path).resolve():
                player.load(path)
            return True
        except ClientError as exc:
            self.cassette_note.set_text(str(exc))
            return False
        except Exception as exc:
            self.cassette_note.set_text(str(exc))
            return False

    def _on_tape_play(self, *_args) -> None:
        if self.tape_dub.get_active():
            self._start_dub()
            return
        if not self._prep_play():
            return
        try:
            self._player_inst().set_tape(dolby=self.tape_dolby.get_active(), rate=1.0)
            self._player_inst().play()
            self.btn_play.add_css_class("hot")
            self.btn_pause.remove_css_class("hot")
            path = self._tapes[self._tape_well]
            through = "cascade_eq" if self._player_inst().through_eq() else "output"
            tape = "DOLBY B" if self.tape_dolby.get_active() else "TAPE COLOR"
            self.cassette_note.set_text(f"PLAY  {path.name if path else ''}  →  {through}  ·  {tape}")
        except Exception as exc:
            self.cassette_note.set_text(str(exc))

    def _start_dub(self) -> None:
        src = self._tapes[self._tape_well]
        if not src:
            self.cassette_note.set_text("Load a source tape, then HIGH SPEED DUB + PLAY.")
            return
        if not self._prep_play():
            return
        dest_well = 1 - self._tape_well
        try:
            player = self._player_inst()
            player.set_tape(dolby=self.tape_dolby.get_active(), rate=2.0)
            if self.rec_arm.get_active():
                self.rec_arm.set_active(False)
            self.rec_arm.set_active(True)
            if not self.rec_arm.get_active():
                player.set_tape(rate=1.0)
                return
            self._dubbing = True
            player.play()
            self.btn_play.add_css_class("hot")
            self.btn_pause.remove_css_class("hot")
            self.cassette_note.set_text(
                f"HIGH SPEED DUB  2×  {src.name}  →  Deck {dest_well + 1}  through cascade_eq"
            )
        except Exception as exc:
            self._dubbing = False
            self.cassette_note.set_text(str(exc))

    def _on_tape_pause(self, *_args) -> None:
        if self._player is None:
            return
        self._player.pause()
        self.btn_play.remove_css_class("hot")
        self.btn_pause.add_css_class("hot")
        self.cassette_note.set_text("PAUSE")

    def _on_tape_stop(self, *_args) -> None:
        aborted_dub = self._dubbing
        self._dubbing = False
        stopped_session = self.session_arm.get_active()
        if self.rec_arm.get_active():
            self.rec_arm.set_active(False)
        if stopped_session:
            self.session_arm.set_active(False)
        if self._player is not None:
            self._player.set_tape(rate=1.0, dolby=self.tape_dolby.get_active())
            self._player.stop()
        self.btn_play.remove_css_class("hot")
        self.btn_pause.remove_css_class("hot")
        self.cassette.set_deck(self._tapes, self._tape_well, False, 0.0)
        if aborted_dub:
            self.cassette_note.set_text("DUB STOPPED")
        elif not stopped_session:
            self.cassette_note.set_text("STOP")

    def _on_tape_rew(self, *_args) -> None:
        if self._player:
            self._player.seek_delta(-30)

    def _on_tape_ff(self, *_args) -> None:
        if self._player:
            self._player.seek_delta(30)

    def _on_tape_eos(self) -> bool:
        if self._dubbing:
            self._finish_dub()
            return False
        other = 1 - self._tape_well
        if self.tape_rev.get_active() and self._tapes[other]:
            self._tape_well = other
            self.cassette.set_deck(self._tapes, other, False, 0.0)
            self._on_tape_play()
            return False
        self.btn_play.remove_css_class("hot")
        self.btn_pause.remove_css_class("hot")
        self.cassette.set_deck(self._tapes, self._tape_well, False, 1.0)
        self.cassette_note.set_text("END OF TAPE")
        return False

    def _finish_dub(self) -> None:
        dest_well = 1 - self._tape_well
        self._dubbing = False
        self.btn_play.remove_css_class("hot")
        self.btn_pause.remove_css_class("hot")
        if self._player is not None:
            self._player.set_tape(rate=1.0, dolby=self.tape_dolby.get_active())
        if self.rec_arm.get_active():
            self.rec_arm.set_active(False)
        src = Path(self._last_rec_path) if self._last_rec_path else None
        self.cassette_note.set_text("Restoring 2× tape to 1×…")
        threading.Thread(target=self._restore_dub_thread, args=(src, dest_well), daemon=True).start()

    def _restore_dub_thread(self, src: Path | None, well: int) -> None:
        try:
            if src is None or not Path(src).exists():
                GLib.idle_add(self.cassette_note.set_text, "DUB finished — no recording to load.")
                return
            from .player import restore_high_speed_dub

            out = restore_high_speed_dub(Path(src), tape_rate=2.0)
            GLib.idle_add(self._load_tape, well, out)
            GLib.idle_add(
                self.cassette_note.set_text,
                f"HIGH SPEED DUB  →  Deck {well + 1}  {out.name}",
            )
        except Exception as exc:  # noqa: BLE001
            GLib.idle_add(self.cassette_note.set_text, f"DUB restore failed: {exc}")

    def _on_tape_error(self, message: str) -> bool:
        self.btn_play.remove_css_class("hot")
        self.btn_pause.remove_css_class("hot")
        self.cassette_note.set_text(str(message))
        return False

    def _on_close_player(self, *_args) -> bool:
        if self._player is not None:
            self._player.stop()
        return False

    def _load_into_widgets(self, state: dict) -> None:
        self._suppress = True
        self._chain_order = normalize_chain(state.get("chain_order"))
        names = preset_names() + ["Custom"]
        preset = state.get("preset", "Flat")
        self.preset.set_selected(names.index(preset) if preset in names else names.index("Custom"))
        self.enable.set_active(bool(state.get("enabled")))
        self.eq_wave.set_bands(state.get("bands") or [0.0] * 16)
        from .presets import (
            delay_preset_names,
            dyn_preset_names,
            exp_preset_names,
            fx_preset_names,
            match_delay_preset,
            match_dyn_preset,
            match_exp_preset,
            match_fx_preset,
            match_pre_preset,
            pre_preset_names,
        )

        names = dyn_preset_names() + ["CUSTOM"]
        self._dyn_comp = dict(state.get("compressor") or {})
        self._dyn_lim = dict(state.get("limiter") or {})
        self._dyn_name = match_dyn_preset(self._dyn_comp, self._dyn_lim)
        if hasattr(self, "dyn_preset"):
            self.dyn_preset.set_selected(
                names.index(self._dyn_name) if self._dyn_name in names else names.index("CUSTOM")
            )
            dyn_mix = mix_amount(self._dyn_comp)
            if hasattr(self, "dyn_mix_knob"):
                self.dyn_mix_knob.set_value(dyn_mix)
        self._expander = dict(state.get("expander") or {})
        self._exp_name = match_exp_preset(self._expander)
        if hasattr(self, "exp_preset"):
            enames = exp_preset_names() + ["CUSTOM"]
            self.exp_preset.set_selected(
                enames.index(self._exp_name) if self._exp_name in enames else enames.index("CUSTOM")
            )
            exp_mix = mix_amount(self._expander)
            if hasattr(self, "exp_mix_knob"):
                self.exp_mix_knob.set_value(exp_mix)
        self._preamp_db = float(state.get("preamp_db", 0))
        self._pre = dict(state.get("pre") or {})
        self._post = dict(state.get("post") or {})
        self._pre_name = match_pre_preset(self._preamp_db, self._pre, self._post)
        if hasattr(self, "pre_preset"):
            pnames = pre_preset_names() + ["CUSTOM"]
            self.pre_preset.set_selected(
                pnames.index(self._pre_name) if self._pre_name in pnames else pnames.index("CUSTOM")
            )
            pre_mix = mix_amount(self._post)
            if hasattr(self, "pre_mix_knob"):
                self.pre_mix_knob.set_value(pre_mix)
        self._fx = dict(state.get("fx") or {})
        self._fx_name = match_fx_preset(self._fx)
        if hasattr(self, "fx_preset"):
            fnames = fx_preset_names() + ["CUSTOM"]
            self.fx_preset.set_selected(
                fnames.index(self._fx_name) if self._fx_name in fnames else fnames.index("CUSTOM")
            )
            fx_mix = mix_amount(self._fx)
            if hasattr(self, "fx_mix_knob"):
                self.fx_mix_knob.set_value(fx_mix)
        self._delay = dict(state.get("delay") or {})
        self._dly_name = match_delay_preset(self._delay)
        if hasattr(self, "dly_preset"):
            dnames = delay_preset_names() + ["CUSTOM"]
            self.dly_preset.set_selected(
                dnames.index(self._dly_name) if self._dly_name in dnames else dnames.index("CUSTOM")
            )
            dly_mix = mix_amount(self._delay)
            if hasattr(self, "dly_mix_knob"):
                self.dly_mix_knob.set_value(dly_mix)
            self._sync_dly_tone()
        rec = state.get("record") or {}
        rec_key = str(rec.get("format", "flac"))
        keys = [f.key for f in self._rec_formats]
        if rec_key in keys:
            self.rec_format.set_selected(keys.index(rec_key))
        self._fill_rec_path()
        ses = state.get("session") or {}
        if hasattr(self, "session_preset"):
            from .session import mix_preset_names

            names = mix_preset_names()
            chosen = str(ses.get("preset") or "").upper()
            if chosen not in names:
                chosen = "HOUSE" if str(ses.get("mix_style") or "").lower() == "house" else "POP"
            self.session_preset.set_selected(names.index(chosen) if chosen in names else 0)
        self._sync_order_dropdowns()
        from .profiles import profile_names

        if hasattr(self, "profile"):
            pnames = profile_names() + ["CUSTOM"]
            chosen = str(state.get("profile") or "CUSTOM")
            self.profile.set_selected(pnames.index(chosen) if chosen in pnames else pnames.index("CUSTOM"))
            from .profiles import PROFILES

            if chosen in PROFILES:
                self.profile_note.set_text(str(PROFILES[chosen]["note"]))
        if hasattr(self, "blend"):
            blend = dict(state.get("blend") or default_blend())
            self.blend.set_active(bool(blend.get("enabled", True)))
        if hasattr(self, "ride"):
            ride = dict(state.get("ride") or default_ride())
            self.ride.set_active(bool(ride.get("enabled", True)))
        if hasattr(self, "master_knob"):
            db = clamp_master_db(state.get("master_db", 0.0))
            self.master_knob.set_value(master_knob_value(db))
            self._sync_master_readout(db)
        if hasattr(self, "tone_knobs"):
            tone = normalize_tone(state.get("tone"))
            for key, knob in self.tone_knobs.items():
                db = float(tone.get(key, 0.0))
                knob.set_value(master_knob_value(db))
                readout = (self.tone_readouts or {}).get(key)
                if readout is not None:
                    readout.set_text(f"{clamp_master_db(db):+.0f} dB")
        if hasattr(self, "tone_preset"):
            tnames = tone_profile_names() + ["Custom"]
            chosen = str(state.get("tone_preset") or match_tone_preset(state.get("tone"), state.get("master_db", 0)))
            self.tone_preset.set_selected(tnames.index(chosen) if chosen in tnames else tnames.index("Custom"))
        self._tone_auto_on = bool(normalize_tone_auto(state.get("tone_auto")).get("enabled"))
        if hasattr(self, "btn_tone_auto"):
            if self._tone_auto_on:
                self.btn_tone_auto.add_css_class("hot")
            else:
                self.btn_tone_auto.remove_css_class("hot")
                self.btn_tone_auto.set_label("AUTO")
        self._sync_digital_buttons(state)
        self._suppress = False
        self._refresh_la2a()

    def _read_widgets(self) -> dict:
        state = dict(self.state)
        names = preset_names() + ["Custom"]
        sel = int(self.preset.get_selected())
        state["preset"] = names[sel] if 0 <= sel < len(names) else "Custom"
        state["enabled"] = self.enable.get_active()
        state["preamp_db"] = float(getattr(self, "_preamp_db", state.get("preamp_db", 0)))
        state["bands"] = self.eq_wave.bands()
        state["dynamics_preset"] = getattr(self, "_dyn_name", "SAFE")
        state["compressor"] = dict(getattr(self, "_dyn_comp", None) or state.get("compressor") or {})
        state["limiter"] = dict(getattr(self, "_dyn_lim", None) or state.get("limiter") or {})
        state["preamp_preset"] = getattr(self, "_pre_name", "FLAT")
        state["pre"] = dict(getattr(self, "_pre", None) or state.get("pre") or {})
        state["post"] = dict(getattr(self, "_post", None) or state.get("post") or {})
        state["expander_preset"] = getattr(self, "_exp_name", "OPEN")
        state["expander"] = dict(getattr(self, "_expander", None) or state.get("expander") or {})
        state["fx_preset"] = getattr(self, "_fx_name", "BYPASS")
        state["fx"] = dict(getattr(self, "_fx", None) or state.get("fx") or {})
        state["delay_preset"] = getattr(self, "_dly_name", "BYPASS")
        state["delay"] = dict(getattr(self, "_delay", None) or state.get("delay") or {})
        state["chain_order"] = list(self._chain_order)
        if hasattr(self, "profile"):
            from .profiles import profile_names

            pnames = profile_names() + ["CUSTOM"]
            sel = int(self.profile.get_selected())
            state["profile"] = pnames[sel] if 0 <= sel < len(pnames) else "CUSTOM"
        blend = dict(state.get("blend") or default_blend())
        if hasattr(self, "blend"):
            blend["enabled"] = self.blend.get_active()
        state["blend"] = blend
        ride = dict(state.get("ride") or default_ride())
        if hasattr(self, "ride"):
            ride["enabled"] = self.ride.get_active()
        state["ride"] = ride
        auto = normalize_tone_auto(state.get("tone_auto"))
        auto["enabled"] = bool(getattr(self, "_tone_auto_on", False))
        state["tone_auto"] = auto
        if not auto["enabled"]:
            if hasattr(self, "master_knob"):
                state["master_db"] = master_db_from_knob(self.master_knob.value)
            else:
                state["master_db"] = clamp_master_db(state.get("master_db", 0.0))
            tone = normalize_tone(state.get("tone"))
            if hasattr(self, "tone_knobs"):
                for key, knob in self.tone_knobs.items():
                    tone[key] = master_db_from_knob(knob.value)
            state["tone"] = tone
            if hasattr(self, "tone_preset"):
                names = tone_profile_names() + ["Custom"]
                sel = int(self.tone_preset.get_selected())
                state["tone_preset"] = names[sel] if 0 <= sel < len(names) else "Custom"
        else:
            state["master_db"] = clamp_master_db(state.get("master_db", 0.0))
            state["tone"] = normalize_tone(state.get("tone"))
        return state

    def _on_eq_changed(self, *_args) -> None:
        if self._suppress:
            return
        names = preset_names() + ["Custom"]
        self._suppress = True
        self.preset.set_selected(names.index("Custom"))
        self._suppress = False
        self._dirty()

    def _playing_track_bpm(self) -> float | None:
        if self._player is None or self._player.state() != "playing":
            return None
        well = self._tapes[self._tape_well] if 0 <= self._tape_well < len(self._tapes) else None
        if well is None:
            return None
        name = Path(well).name
        for row in self._split_tracks:
            path = str(row.get("path") or "")
            if path and (path == str(well) or Path(path).name == name):
                try:
                    bpm = float(row.get("bpm") or 0)
                except (TypeError, ValueError):
                    bpm = 0.0
                if bpm >= 70.0:
                    return max(70.0, min(180.0, bpm))
        return None

    def _eq_auto_bpm(self) -> float:
        track = self._playing_track_bpm()
        if track is not None:
            return track
        return max(70.0, min(180.0, float(self._live_bpm or 120.0)))

    def _on_eq_auto(self, *_args) -> None:
        if self._auto_on:
            self._stop_auto_eq()
            return
        if not self.enable.get_active():
            self.enable.set_active(True)
        self._auto_on = True
        self._auto_bars = 0
        self.btn_auto_eq.set_label("LISTEN")
        self.btn_auto_eq.add_css_class("hot")
        if hasattr(self, "eq_wave"):
            self.eq_wave.auto_on = True
            self.eq_wave._render()
        self.state["auto_eq"] = {
            "enabled": True,
            "lift": [8.0, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0],
        }
        if hasattr(self, "eq_wave"):
            self.eq_wave.set_lifts(self.state["auto_eq"]["lift"])
        self._dirty()
        self._begin_auto_bar()

    def _stop_auto_eq(self) -> None:
        self._auto_on = False
        self._auto_until = 0.0
        self._auto_listen = []
        self.btn_auto_eq.set_label("AUTO")
        self.btn_auto_eq.remove_css_class("hot")
        if hasattr(self, "eq_wave"):
            self.eq_wave.auto_on = False
            self.eq_wave._render()
        self.state["auto_eq"] = {"enabled": False, "lift": empty_bands()}
        self.status.set_text("AUTO  ·  off")
        self._dirty()

    def _begin_auto_bar(self) -> None:
        bpm = self._eq_auto_bpm()
        self._auto_listen = []
        self._auto_until = time.time() + four_beat_seconds(bpm)
        bar = f"  ·  bar {self._auto_bars}" if self._auto_bars else ""
        self.status.set_text(f"AUTO  ·  compressing low/mid/high up @ {bpm:.0f} BPM{bar}")

    def _finish_auto_eq(self) -> None:
        frames = self._auto_listen
        self._auto_listen = []
        self._auto_until = 0.0
        looping = self._auto_on
        if not frames:
            if looping:
                self.status.set_text(f"AUTO  ·  waiting for signal @ {self._eq_auto_bpm():.0f} BPM")
                self._begin_auto_bar()
            else:
                self.btn_auto_eq.set_label("AUTO")
                self.btn_auto_eq.remove_css_class("hot")
                self.status.set_text("AUTO  ·  play audio through cascade_eq first")
            return
        n = 16
        avg = [sum(row[i] for row in frames) / len(frames) for i in range(n)]
        if max(avg) < 0.03:
            if looping:
                self.status.set_text(f"AUTO  ·  too quiet @ {self._eq_auto_bpm():.0f} BPM")
                self._begin_auto_bar()
            else:
                self.btn_auto_eq.set_label("AUTO")
                self.btn_auto_eq.remove_css_class("hot")
                self.status.set_text("AUTO  ·  signal too quiet")
            return
        prev = ((self.state.get("auto_eq") or {}).get("lift") or empty_bands())
        lifts = auto_reveal_from_rta(avg, prev, strength=0.9)
        self.state["auto_eq"] = {"enabled": True, "lift": lifts}
        self._dirty()
        self._auto_bars += 1
        if looping:
            self._begin_auto_bar()
        else:
            self.btn_auto_eq.set_label("AUTO")
            self.btn_auto_eq.remove_css_class("hot")
            self.status.set_text(f"AUTO  ·  revealing @ {self._eq_auto_bpm():.0f} BPM")

    def _on_preset(self, *_args) -> None:
        if self._suppress:
            return
        names = preset_names() + ["Custom"]
        sel = int(self.preset.get_selected())
        name = names[sel]
        if name == "Custom":
            return
        self.state = apply_preset(self.state, name)
        self.state["profile"] = "CUSTOM"
        enabled = self.enable.get_active()
        self._load_into_widgets(self.state)
        self.enable.set_active(enabled)
        self._schedule_apply()

    def _on_profile_action(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if param is None:
            return
        self._apply_profile_name(param.get_string())

    def _on_profile(self, *_args) -> None:
        if self._suppress:
            return
        from .profiles import profile_names

        names = profile_names() + ["CUSTOM"]
        idx = int(self.profile.get_selected())
        name = names[idx] if 0 <= idx < len(names) else "CUSTOM"
        if name == "CUSTOM":
            return
        self._apply_profile_name(name)

    def _apply_profile_name(self, name: str) -> None:
        from .profiles import PROFILES, apply_profile

        try:
            self._applying_profile = True
            self.state = apply_profile(self.state, name)
            enabled = self.enable.get_active()
            self._load_into_widgets(self.state)
            self.enable.set_active(enabled)
            official = str(self.state.get("profile") or name)
            if official in PROFILES:
                self.profile_note.set_text(str(PROFILES[official]["note"]))
            self.status.set_text(f"PROFILE  ·  {official}")
            self.status.add_css_class("status-ok")
        except KeyError as exc:
            self.status.set_text(str(exc))
        finally:
            self._applying_profile = False
        self._schedule_apply()

    def _on_enable(self, *_args) -> None:
        if self._suppress:
            return
        self._schedule_apply(immediate_enable=True)

    def _on_blend(self, *_args) -> None:
        if self._suppress:
            return
        self._schedule_apply()

    def _on_ride(self, *_args) -> None:
        if self._suppress:
            return
        self._schedule_apply()

    def _sync_master_readout(self, db: float) -> None:
        if hasattr(self, "master_readout"):
            self.master_readout.set_text(f"{clamp_master_db(db):+.0f} dB")

    def _on_master_gain(self, value: float) -> None:
        if self._suppress:
            return
        self._cancel_tone_auto_from_knob()
        self._sync_master_readout(master_db_from_knob(value))
        self._mark_tone_custom()
        self._schedule_apply()

    def _on_tone_gain(self, key: str, value: float) -> None:
        if self._suppress:
            return
        self._cancel_tone_auto_from_knob()
        db = master_db_from_knob(value)
        readout = (getattr(self, "tone_readouts", None) or {}).get(key)
        if readout is not None:
            readout.set_text(f"{clamp_master_db(db):+.0f} dB")
        self._mark_tone_custom()
        self._schedule_apply()

    def _mark_tone_custom(self) -> None:
        if not hasattr(self, "tone_preset"):
            return
        names = tone_profile_names() + ["Custom"]
        self._suppress = True
        self.tone_preset.set_selected(names.index("Custom"))
        self._suppress = False
        self.state["tone_preset"] = "Custom"

    def _cancel_tone_auto_from_knob(self) -> None:
        if not getattr(self, "_tone_auto_on", False):
            return
        self._tone_auto_on = False
        if hasattr(self, "btn_tone_auto"):
            self.btn_tone_auto.remove_css_class("hot")
            self.btn_tone_auto.set_label("AUTO")
        self.status.set_text("TONE AUTO  ·  off")

    def _on_tone_preset(self, *_args) -> None:
        if self._suppress:
            return
        names = tone_profile_names() + ["Custom"]
        idx = int(self.tone_preset.get_selected())
        name = names[idx] if 0 <= idx < len(names) else "Custom"
        if name == "Custom":
            self.state["tone_preset"] = "Custom"
            self._dirty()
            return
        self.state = apply_tone_profile(self.state, name)
        self._suppress = True
        if hasattr(self, "master_knob"):
            db = clamp_master_db(self.state.get("master_db", 0.0))
            self.master_knob.set_value(master_knob_value(db))
            self._sync_master_readout(db)
        tone = normalize_tone(self.state.get("tone"))
        for key, knob in self.tone_knobs.items():
            db = float(tone.get(key, 0.0))
            knob.set_value(master_knob_value(db))
            readout = (self.tone_readouts or {}).get(key)
            if readout is not None:
                readout.set_text(f"{clamp_master_db(db):+.0f} dB")
        self._suppress = False
        spec = TONE_PROFILES.get(name) or {}
        self.status.set_text(f"TONE  ·  {name}  ·  {spec.get('note', '')}".strip(" ·"))
        self._dirty()

    def _on_tone_auto(self, *_args) -> None:
        if self._tone_auto_on:
            self._tone_auto_on = False
            self.btn_tone_auto.remove_css_class("hot")
            self.btn_tone_auto.set_label("AUTO")
            self.status.set_text("TONE AUTO  ·  off")
            self._suppress = True
            tone = normalize_tone(self.state.get("tone"))
            for key, knob in self.tone_knobs.items():
                db = float(tone.get(key, 0.0))
                knob.set_value(master_knob_value(db))
                readout = (self.tone_readouts or {}).get(key)
                if readout is not None:
                    readout.set_text(f"{clamp_master_db(db):+.0f} dB")
            db = clamp_master_db(self.state.get("master_db", 0.0))
            self.master_knob.set_value(master_knob_value(db))
            self._sync_master_readout(db)
            self._suppress = False
            self._dirty()
            return
        if not self.enable.get_active():
            self.enable.set_active(True)
        if hasattr(self, "ride"):
            self.ride.set_active(True)
        tone = normalize_tone(self.state.get("tone"))
        for key, knob in self.tone_knobs.items():
            tone[key] = master_db_from_knob(knob.value)
        self.state["tone"] = tone
        self.state["master_db"] = master_db_from_knob(self.master_knob.value)
        self.state["tone_preset"] = match_tone_preset(tone, self.state["master_db"])
        self._tone_auto_on = True
        self.btn_tone_auto.add_css_class("hot")
        self.status.set_text("TONE AUTO  ·  riding knobs, keeping peaks under the ceiling")
        self._dirty()

    def _dirty(self, *_args) -> None:
        if self._suppress:
            return
        if not getattr(self, "_applying_profile", False) and hasattr(self, "profile"):
            from .profiles import profile_names

            names = profile_names() + ["CUSTOM"]
            if int(self.profile.get_selected()) != names.index("CUSTOM"):
                self._suppress = True
                self.profile.set_selected(names.index("CUSTOM"))
                self._suppress = False
                self.profile_note.set_text("CUSTOM  ·  rack was tweaked after the profile.")
        self._refresh_la2a()
        self._schedule_apply()

    def _schedule_apply(self, immediate_enable: bool = False) -> None:
        if self._apply_src:
            GLib.source_remove(self._apply_src)
        delay = 10 if immediate_enable else 80
        self._apply_src = GLib.timeout_add(delay, self._apply)

    def _apply(self) -> bool:
        self._apply_src = 0
        self.state = self._read_widgets()
        save_state(self.state)
        try:
            if self.enable.get_active():
                ensure_daemon()
                self.state["enabled"] = True
                save_state(self.state)
                request({"cmd": "apply", "state": self.state})
            elif ping():
                request({"cmd": "disable"})
            self.status.set_text("")
        except ClientError as exc:
            self.status.set_text(str(exc))
        self._refresh_status()
        return False

    def _tick_meters(self) -> bool:
        self._meter_n += 1
        playing = False
        paused = False
        frac = 0.0
        pos = 0.0
        if self._player is not None:
            st = self._player.state()
            playing = st == "playing"
            paused = st == "paused"
            pos, dur = self._player.progress()
            frac = (pos / dur) if dur > 0 else 0.0
        recording = False
        actually_rec = float(getattr(self, "_rec_elapsed", 0.0)) > 0.15
        if hasattr(self, "rec_arm"):
            recording = self.rec_arm.get_active() or self.session_arm.get_active()
        spinning = bool(playing or actually_rec or self._dubbing)
        busy = bool(self._auto_on or self._auto_until or spinning or getattr(self, "_tone_auto_on", False))
        if not busy and self._meter_n % 2:
            return True
        want_rta = self._unit_open("316") or bool(self._auto_on) or bool(self._auto_until)
        data = meters(rta=want_rta)
        self._live_bpm = float(data.get("bpm") or self._live_bpm or 120.0)
        if self._unit_open("310"):
            self.wave.update(
                list(data.get("wave") or []),
                hist=list(data.get("hist") or []),
                target_db=float(data.get("ride_target_db") or -18.0),
                form_db=float(data.get("form_db") or -42.0),
                ride_db=float(data.get("ride_db") or 0.0),
            )
            self.needle_l.update(float(data.get("peak_l") or 0), float(data.get("rms_l") or 0))
            self.needle_r.update(float(data.get("peak_r") or 0), float(data.get("rms_r") or 0))
            if getattr(self, "_tone_auto_on", False) and data.get("tone_auto"):
                live = data.get("tone_live") or {}
                self._suppress = True
                for key, knob in self.tone_knobs.items():
                    db = float(live.get(key, 0.0))
                    knob.set_value(master_knob_value(db))
                    readout = (self.tone_readouts or {}).get(key)
                    if readout is not None:
                        readout.set_text(f"{clamp_master_db(db):+.0f} dB")
                gdb = float(live.get("gain_db", self.state.get("master_db", 0)))
                self.master_knob.set_value(master_knob_value(gdb))
                self._sync_master_readout(gdb)
                self._suppress = False
                scale = float(data.get("tone_scale") or 1.0)
                if hasattr(self, "btn_tone_auto"):
                    self.btn_tone_auto.set_label("AUTO" if scale > 0.97 else f"AUTO {int(round(scale * 100))}")
        rta = list(data.get("rta") or [])
        if want_rta and hasattr(self, "eq_wave"):
            self.eq_wave.auto_on = bool(self._auto_on)
            lifts = ((self.state.get("auto_eq") or {}).get("lift") or empty_bands()) if self._auto_on else empty_bands()
            self.eq_wave.set_lifts(list(lifts)[:16])
            self.eq_wave.set_rta(rta)
        if self._auto_on or self._auto_until:
            if rta and max(rta) > 0.02:
                self._auto_listen.append(rta[:16])
            if self._auto_until and time.time() >= self._auto_until:
                self._finish_auto_eq()
        if self._unit_open("370") and hasattr(self, "cassette"):
            self.cassette.set_deck(
                self._tapes,
                self._tape_well,
                playing,
                frac,
                paused=paused,
                peak_l=float(data.get("peak_l") or 0),
                peak_r=float(data.get("peak_r") or 0),
                pos=pos,
                dolby=self.tape_dolby.get_active() if hasattr(self, "tape_dolby") else True,
                auto_rev=self.tape_rev.get_active() if hasattr(self, "tape_rev") else True,
                recording=recording,
                rec_sec=float(getattr(self, "_rec_elapsed", 0.0)),
                dubbing=self._dubbing,
                dub_armed=self.tape_dub.get_active() if hasattr(self, "tape_dub") else False,
            )
        peak = float(data.get("peak_l") or 0)
        rms = float(data.get("rms_l") or 0)
        if self._meter_n % 2 == 0:
            self._refresh_la2a(peak, rms, gap=float(data.get("fx2_gap") or 0), live=True)
        if hasattr(self, "blend_gr"):
            gr_db = float(data.get("blend_gr_db") or 0)
            on = data.get("blend_on")
            if on is False:
                self.blend_gr.set_text("GR  off")
            elif gr_db >= 0.05:
                self.blend_gr.set_text(f"GR  {gr_db:.1f}")
            else:
                self.blend_gr.set_text("GR  0.0")
        if hasattr(self, "ride_db"):
            amount = float(data.get("ride_db") or 0)
            on = data.get("ride_on")
            if on is False:
                self.ride_db.set_text("RIDE  off")
            else:
                self.ride_db.set_text(f"RIDE  {amount:+.1f}")
        return True

    def _on_digital(self, name: str) -> None:
        if self._suppress:
            return
        cur = normalize_digital(self.state.get("digital"))
        nxt = "bypass" if cur.get("preset") == name else name
        self.state["digital"] = normalize_digital({"preset": nxt, "mix": cur.get("mix"), "wet": cur.get("wet"), "dry": cur.get("dry")})
        self._sync_digital_buttons()
        self._schedule_apply()

    def _sync_digital_buttons(self, state: dict | None = None) -> None:
        if not getattr(self, "de_btns", None):
            return
        rec = normalize_digital((state or self.state).get("digital"))
        active = rec.get("preset") or "bypass"
        notes = {
            "bypass": "POST  ·  off",
            "floor": "POST  ·  raise the floor",
            "headroom": "POST  ·  more headroom",
            "clarity": "POST  ·  boost clarity",
            "stereo": "POST  ·  boost stereo",
            "layers": "POST  ·  add layers",
            "punch": "POST  ·  punch",
        }
        for key, btn in self.de_btns.items():
            btn.remove_css_class("hot")
            if key == active:
                btn.add_css_class("hot")
        if hasattr(self, "de_note"):
            self.de_note.set_text(notes.get(active, "POST  ·  off"))

    def _on_output_role(self, role: str) -> None:
        if self._suppress:
            return
        try:
            if ping():
                data = request({"cmd": "output", "role": role}, timeout=12.0)
                self.state["hardware_sink"] = data.get("hardware_sink")
                self.state["output_role"] = data.get("output_role") or role
                save_state(self.state)
                self._sync_output_buttons(data.get("outputs") or {})
                hw = data.get("hardware_sink") or role
                self.status.set_text(f"OUTPUT  ·  {role}  →  {hw}")
                return
            sink, port = resolve_output_role(role)
            if not sink:
                labels = {
                    "speakers": "speakers",
                    "usb": "USB audio",
                    "headphones": "headphones",
                }
                self.status.set_text(f"No {labels.get(role, role)} output is connected.")
                self._sync_output_buttons()
                return
            if port:
                set_sink_port(sink, port)
            self.state["hardware_sink"] = sink
            self.state["output_role"] = role
            save_state(self.state)
            self._sync_output_buttons()
            self.status.set_text(f"OUTPUT  ·  {role}  →  {sink}")
        except (ClientError, PulseError) as exc:
            self.status.set_text(str(exc))
            self._sync_output_buttons()

    def _sync_output_buttons(self, outputs: dict | None = None) -> None:
        if not getattr(self, "out_btns", None):
            return
        hw = self.state.get("hardware_sink")
        if not hw or is_virtual_name(str(hw)):
            hw = pick_hardware_sink()
        try:
            info = outputs if outputs is not None else output_inventory(hw)
        except Exception:  # noqa: BLE001
            info = {}
        live = None
        try:
            live = current_output_role(hw)
        except Exception:  # noqa: BLE001
            live = self.state.get("output_role")
        for role, btn in self.out_btns.items():
            rec = info.get(role) or {}
            active = bool(rec.get("active")) if rec else live == role
            present = bool(rec.get("available", True)) if rec else True
            plugged = bool(rec.get("plugged", present)) if rec else True
            btn.remove_css_class("hot")
            btn.remove_css_class("dim")
            if active:
                btn.add_css_class("hot")
            elif not present or (role != "speakers" and not plugged):
                btn.add_css_class("dim")

    def _refresh_status(self) -> bool:
        try:
            if ping():
                data = request({"cmd": "status"})
                hw = data.get("hardware_sink") or "output"
                if data.get("processing"):
                    self.status.set_text(f"LIVE  ·  cascade_eq  →  {hw}")
                    self.status.add_css_class("status-ok")
                    self.status.remove_css_class("status-off")
                else:
                    self.status.set_text("STANDBY  ·  enable to process system audio")
                    self.status.add_css_class("status-off")
                rec = data.get("record") or {}
                self._suppress = True
                self.enable.set_active(bool(data.get("enabled") or data.get("processing")))
                recording = bool(rec.get("recording"))
                rec_path = str(rec.get("path") or "")
                session_rec = recording and "session-" in Path(rec_path).name
                self.rec_arm.set_active(recording and not session_rec)
                self.session_arm.set_active(session_rec)
                self.rec_format.set_sensitive(not recording)
                self._set_rec_lit(recording)
                if recording:
                    sec = int(float(rec.get("elapsed_sec") or 0))
                    self._rec_elapsed = float(sec)
                    self.rec_clock.set_text(f"{sec // 60:02d}:{sec % 60:02d}")
                    if rec.get("path") and not session_rec:
                        self.rec_path.set_text(str(rec["path"]))
                else:
                    self._rec_elapsed = 0.0
                    self.rec_clock.set_text("00:00")
                self._suppress = False
                if data.get("hardware_sink"):
                    self.state["hardware_sink"] = data.get("hardware_sink")
                if data.get("output_role"):
                    self.state["output_role"] = data.get("output_role")
                self._sync_output_buttons(data.get("outputs") or {})
            else:
                self.status.set_text(f"BYPASS  ·  {default_sink() or '—'}")
                self.status.add_css_class("status-off")
                self._suppress = True
                self.rec_arm.set_active(False)
                self.session_arm.set_active(False)
                self._suppress = False
                self._set_rec_lit(False)
                self._rec_elapsed = 0.0
                self.rec_clock.set_text("00:00")
                self._sync_output_buttons()
        except ClientError as exc:
            self.status.set_text(str(exc))
            self._sync_output_buttons()
        return True


class CascadeApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        quit_act = Gio.SimpleAction.new("quit", None)
        quit_act.connect("activate", lambda *_a: self.quit())
        self.add_action(quit_act)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.connect("activate", self._activate)

    def _activate(self, _app) -> None:
        win = self.props.active_window
        if not win:
            win = CascadeWindow(self)
        win.present()
        shot = os.environ.get("CASCADE_EQ_SCREENSHOT", "").strip()
        if shot:

            def _take_shot() -> bool:
                try:
                    win._save_full_rack_png(shot)
                except Exception as exc:  # noqa: BLE001
                    sys.stderr.write(f"rack screenshot failed: {exc}\n")
                self.quit()
                return False

            GLib.timeout_add(900, _take_shot)


def run_gui() -> int:
    Adw.init()
    app = CascadeApp()
    return int(app.run([sys.argv[0]]))
