"""Era skins for the Cascade EQ rack — CSS + cairo palettes."""

from __future__ import annotations

import copy

DEFAULT_SKIN = "1980s"
_ACTIVE = DEFAULT_SKIN


def _rgb(r: int, g: int, b: int) -> tuple[float, float, float]:
    return (r / 255.0, g / 255.0, b / 255.0)


def _merge(base: dict, **over) -> dict:
    out = copy.deepcopy(base)
    css = over.pop("css", {})
    cr = over.pop("cr", {})
    out.update(over)
    out["css"].update(css)
    out["cr"].update(cr)
    return out


def _css(c: dict) -> str:
    r = c.get("radius", "2px")
    return f"""
window.cascade-skin {{
  background-color: {c["window_bg"]};
  color: {c["window_fg"]};
  font-family: {c["font"]};
  font-size: 17px;
}}
headerbar, .top-bar, windowcontrols, menubar, .cascade-menubar {{
  background-image: linear-gradient(180deg, {c["header0"]} 0%, {c["header1"]} 40%, {c["header2"]} 100%);
  color: {c["header_fg"]};
  border-bottom: 1px solid {c["header_line"]};
  box-shadow: inset 0 1px 0 {c["header_hi"]};
  min-height: 48px;
}}
headerbar button, menubar, menubar > menuitem {{ color: {c["header_fg"]}; }}
menubar {{
  min-height: 40px;
  padding: 2px 8px;
}}
menubar > menuitem {{
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 1.8px;
  padding: 6px 16px;
}}
popover contents, popover.menu contents {{
  background-color: {c["drop_bg"]};
  color: {c["drop_fg"]};
}}
.cascade-title {{
  font-size: 26px;
  letter-spacing: 0.6px;
  font-weight: 700;
  color: {c["title"]};
  text-shadow: 0px 0px 10px {c["title_glow"]};
}}
.cascade-sub {{
  font-size: 15px;
  letter-spacing: 2.8px;
  color: {c["sub"]};
  text-shadow: 0px 0px 10px {c["sub_glow"]};
}}
.status-ok {{
  font-size: 18px;
  color: {c["status_ok"]};
  letter-spacing: 1px;
  font-family: "Ubuntu Mono", monospace;
  text-shadow: 0px 0px 10px {c["status_glow"]}, 0px 0px 18px {c["status_glow"]};
}}
.status-off {{ font-size: 18px; color: {c["status_off"]}; letter-spacing: 1px; font-family: "Ubuntu Mono", monospace; }}
.eq-scale {{ min-height: 168px; padding-top: 4px; }}
.eq-label {{ font-size: 1.15rem; letter-spacing: 0.4px; color: {c["eq_label"]}; }}
.eq-wave-wrap, .scope-wrap, .meter-wrap {{
  background-color: {c["wrap_bg"]};
  border: 1px solid {c["wrap_border"]};
  box-shadow: inset 0 3px 10px #000000cc, 0 0 0 1px {c["wrap_edge"]}, 0 0 18px {c["wrap_glow"]};
  border-radius: {r};
}}
.rack-cabinet {{ background-color: {c["cabinet"]}; padding: 10px 8px 14px 8px; }}
.rack-unit {{
  background-color: {c["unit_bg"]};
  border: 1px solid {c["unit_border"]};
  box-shadow: 0 7px 0 {c["unit_shadow"]}, inset 0 1px 0 {c["unit_hi"]};
  margin-bottom: 8px;
  border-radius: {r};
}}
.rack-pair {{ margin-bottom: 0; }}
.rack-pair > .rack-unit {{ min-width: 0; }}
.rack-face {{
  background-image: linear-gradient(180deg, {c["face0"]} 0%, {c["face1"]} 10%, {c["face2"]} 50%, {c["face3"]} 100%);
  padding: 8px 12px 10px 12px;
  border-radius: {r};
}}
expander.rack-expander {{ color: {c["expander"]}; }}
expander.rack-expander arrow {{
  min-width: 18px;
  min-height: 18px;
  color: {c["arrow"]};
}}
expander.rack-expander title {{ padding: 2px 0 4px 0; }}
.rack-body {{ padding-top: 8px; }}
.rack-brand {{
  font-size: 22px;
  letter-spacing: 0.4px;
  font-weight: 700;
  color: {c["brand"]};
  text-shadow: 0px 0px 8px {c["title_glow"]};
}}
.rack-model {{
  font-size: 16px;
  letter-spacing: 1.8px;
  color: {c["model"]};
  text-shadow: 0px 0px 8px {c["sub_glow"]};
}}
.rack-title {{
  font-size: 17px;
  letter-spacing: 2.4px;
  color: {c["rack_title"]};
  text-shadow: 0px 0px 8px {c["sub_glow"]};
}}
.panel-label, .rack-section {{
  font-size: 15px;
  letter-spacing: 2.4px;
  color: {c["section"]};
  margin-bottom: 4px;
  text-shadow: 0px 0px 6px {c["sub_glow"]};
}}
.field-label {{
  font-size: 15px;
  letter-spacing: 1.6px;
  color: {c["field"]};
  text-shadow: 0px 0px 6px {c["sub_glow"]};
}}
.gain-readout {{
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 1.2px;
}}
.led-well {{
  background-color: {c["led_bg"]};
  border: 1px solid {c["led_border"]};
  box-shadow: inset 0 2px 6px #000000cc, 0 0 12px {c["led_glow"]};
  padding: 4px 8px;
  min-width: 260px;
  border-radius: {r};
}}
scale trough {{
  min-height: 8px; min-width: 8px;
  background-color: {c["trough"]};
  border: 1px solid #000;
  box-shadow: inset 0 2px 3px #000;
  border-radius: {r};
}}
scale highlight {{ background-color: {c["highlight"]}; border-radius: {r}; }}
scale.eq-scale trough {{ min-width: 10px; background-color: {c["trough"]}; }}
scale slider {{
  min-width: 14px; min-height: 18px;
  background-image: linear-gradient(180deg, {c["slider0"]}, {c["slider1"]});
  border: 1px solid #1a1c1e;
  box-shadow: 0 1px 2px #00000088;
  margin: -6px;
  border-radius: {r};
}}
scale value {{
  color: {c["value"]};
  font-size: 16px;
  font-family: "Ubuntu Mono", monospace;
  text-shadow: 0px 0px 8px {c["status_glow"]};
}}
switch {{
  background-color: {c["switch_bg"]};
  border: 1px solid #000;
  border-radius: {r};
  min-width: 44px;
  box-shadow: inset 0 2px 4px #000000aa;
}}
switch:checked {{ background-color: {c["switch_on"]}; border-color: {c["switch_on_border"]}; }}
dropdown, dropdown > button {{
  background-color: {c["drop_bg"]};
  color: {c["drop_fg"]};
  font-size: 16px;
  border: 1px solid {c["drop_border"]};
  border-radius: {r};
  box-shadow: inset 0 2px 4px #00000088;
  min-height: 36px;
}}
.dyn-col {{ padding: 4px 8px 0 8px; }}
button.rec-arm {{
  min-width: 96px;
  min-height: 48px;
  background-image: linear-gradient(180deg, {c["tape0"]} 0%, {c["tape1"]} 42%, {c["tape2"]} 100%);
  color: {c["rec_fg"]};
  border: 1px solid {c["unit_hi"]};
  border-bottom: 4px solid {c["unit_shadow"]};
  border-radius: {r};
  font-weight: 700;
  font-size: 15px;
  letter-spacing: 2px;
}}
button.rec-arm:checked {{
  background-image: linear-gradient(180deg, {c["rec_on0"]} 0%, {c["rec_on1"]} 48%, {c["rec_on2"]} 100%);
  color: #fff4f0;
  text-shadow: 0px 0px 10px #ff6050;
  border-color: #ff8870;
  box-shadow: 0 0 18px #ff403088;
}}
.rec-led {{ font-size: 22px; color: #3a1818; letter-spacing: 0; }}
.rec-led.hot {{ color: #ff4a38; text-shadow: 0px 0px 12px #ff3018; }}
.rec-clock {{
  font-family: "Ubuntu Mono", monospace;
  font-size: 24px;
  letter-spacing: 3px;
  color: {c["status_ok"]};
  text-shadow: 0px 0px 10px {c["status_glow"]}, 0px 0px 18px {c["status_glow"]};
}}
.rec-path {{
  font-family: "Ubuntu Mono", monospace;
  font-size: 16px;
  color: {c["model"]};
}}
button.tape-key {{
  min-width: 76px;
  min-height: 48px;
  background-image: linear-gradient(180deg, {c["tape0"]} 0%, {c["tape1"]} 42%, {c["tape2"]} 100%);
  color: {c["tape_fg"]};
  border: 1px solid {c["unit_hi"]};
  border-bottom: 4px solid {c["unit_shadow"]};
  border-radius: {r};
  font-weight: 700;
  letter-spacing: 1.5px;
  font-size: 15px;
}}
button.tape-key:hover {{ background-image: linear-gradient(180deg, {c["slider0"]}, {c["slider1"]}); }}
button.tape-key:checked, button.tape-key.hot {{
  background-image: linear-gradient(180deg, {c["tape_hot0"]} 0%, {c["tape_hot1"]} 48%, {c["tape_hot2"]} 100%);
  color: #ffffff;
  text-shadow: 0px 0px 10px {c["title_glow"]};
  border-color: {c["wrap_edge"]};
  box-shadow: 0 0 18px {c["wrap_glow"]};
}}
button.tape-play {{ min-width: 100px; }}
button.tape-load {{
  min-width: 86px;
  letter-spacing: 1.2px;
}}
button.tape-mode {{
  min-width: 90px;
  background-image: linear-gradient(180deg, {c["tape_mode0"]} 0%, {c["tape_mode1"]} 100%);
  color: {c["tape_mode_fg"]};
}}
button.tape-mode:checked {{
  color: {c["title"]};
  background-image: linear-gradient(180deg, {c["tape_mode_on0"]} 0%, {c["tape_mode_on1"]} 100%);
  box-shadow: inset 0 0 12px {c["led_glow"]}, 0 0 10px {c["wrap_glow"]};
  border-color: {c["wrap_edge"]};
}}
button.out-sel {{
  min-width: 124px;
  min-height: 42px;
  padding: 6px 10px;
  background-image: linear-gradient(180deg, #c9b45c 0%, #a88832 45%, #7a6424 100%);
  color: #2c240c;
  border: 1px solid #5a4a18;
  border-bottom: 4px solid #3a3010;
  border-radius: 3px;
  font-weight: 800;
  letter-spacing: 1px;
  font-size: 15px;
}}
button.out-sel:hover {{
  background-image: linear-gradient(180deg, #d8c46a 0%, #b8983c 100%);
}}
button.out-sel.hot {{
  background-image: linear-gradient(180deg, #efe07a 0%, #e0c040 42%, #c4a028 100%);
  color: #241c08;
  text-shadow: 0 1px 0 #fff6c0;
  border-color: #d4b430;
  box-shadow: inset 0 1px 0 #fff3b8, 0 0 12px #c9a02888;
}}
button.out-sel.dim {{
  opacity: 0.42;
}}
.tape-keybed {{
  background-image: linear-gradient(180deg, {c["keybed0"]} 0%, {c["keybed1"]} 100%);
  border: 1px solid #000;
  box-shadow: inset 0 3px 8px #000000cc;
  padding: 8px 8px 10px 8px;
  border-radius: {r};
}}
entry {{
  background-color: {c["entry_bg"]};
  color: {c["entry_fg"]};
  caret-color: {c["entry_fg"]};
  border: 1px solid {c["entry_border"]};
  box-shadow: inset 0 2px 6px #000000aa, 0 0 8px {c["wrap_glow"]};
  font-family: "Ubuntu Mono", monospace;
  font-size: 16px;
  min-height: 36px;
  border-radius: {r};
}}
scrollbar slider {{ background: {c["scroll"]}; min-width: 8px; }}
scrollbar trough {{ background: {c["scroll_trough"]}; }}
"""


_EIGHTIES = {
    "id": "1980s",
    "label": "1980s Fluorescent",
    "brand_name": "Panasonic",
    "tagline": "HI-FI COMPONENT SYSTEM",
    "light": False,
    "glow": True,
    "screws": True,
    "hair": True,
    "radius": 4.0,
    "form": "rack",
    "css": {
        "font": '"Cantarell", "Ubuntu", sans-serif',
        "radius": "2px",
        "window_bg": "#060708",
        "window_fg": "#e8eef0",
        "header0": "#3a3e44",
        "header1": "#1a1c20",
        "header2": "#0c0d10",
        "header_fg": "#f0f4f6",
        "header_hi": "#8a9098",
        "header_line": "#050506",
        "title": "#ffffff",
        "title_glow": "#80f0ff88",
        "sub": "#b8fff4",
        "sub_glow": "#50e8e088",
        "status_ok": "#b8fff8",
        "status_glow": "#50fff0",
        "status_off": "#2a4848",
        "eq_label": "#d0fff8",
        "wrap_bg": "#02090a",
        "wrap_border": "#1a4044",
        "wrap_edge": "#58a8b0",
        "wrap_glow": "#20c8c033",
        "cabinet": "#050608",
        "unit_bg": "#16181c",
        "unit_border": "#5a646c",
        "unit_shadow": "#020203",
        "unit_hi": "#a0a8b0",
        "face0": "#3e4248",
        "face1": "#1c1e22",
        "face2": "#141618",
        "face3": "#0c0d10",
        "expander": "#d0d8dc",
        "arrow": "#7ef8f0",
        "brand": "#ffffff",
        "model": "#c8f8f0",
        "rack_title": "#c8f8f0",
        "section": "#c0f0e8",
        "field": "#d0fff8",
        "led_bg": "#02090a",
        "led_border": "#1a4044",
        "led_glow": "#30e0d844",
        "trough": "#08090a",
        "highlight": "#40e0d8",
        "slider0": "#c8d0d6",
        "slider1": "#4a5058",
        "value": "#b8fff8",
        "switch_bg": "#121416",
        "switch_on": "#1a8890",
        "switch_on_border": "#90fff8",
        "drop_bg": "#181c20",
        "drop_fg": "#f0f8f8",
        "drop_border": "#3a5054",
        "rec_fg": "#ffb0a0",
        "rec_on0": "#e85848",
        "rec_on1": "#8a2018",
        "rec_on2": "#380808",
        "tape0": "#5a6068",
        "tape1": "#2c3034",
        "tape2": "#16181a",
        "tape_fg": "#f4f8f8",
        "tape_hot0": "#90e8f4",
        "tape_hot1": "#2a8898",
        "tape_hot2": "#0c3840",
        "tape_mode0": "#3a4048",
        "tape_mode1": "#181a1c",
        "tape_mode_fg": "#a8b8bc",
        "tape_mode_on0": "#246068",
        "tape_mode_on1": "#0c3038",
        "keybed0": "#1a1c20",
        "keybed1": "#0a0c0e",
        "entry_bg": "#02090a",
        "entry_fg": "#b8fff8",
        "entry_border": "#1a4044",
        "scroll": "#58a0a8",
        "scroll_trough": "#070808",
    },
    "cr": {
        "chassis0": (0.40, 0.42, 0.46),
        "chassis1": (0.22, 0.24, 0.26),
        "chassis2": (0.14, 0.15, 0.17),
        "chassis3": (0.08, 0.09, 0.10),
        "hair": (1.0, 1.0, 1.0, 0.04),
        "edge": (0.58, 0.62, 0.66),
        "inner": (0.05, 0.05, 0.06),
        "screw": (0.48, 0.50, 0.52),
        "screw_in": (0.22, 0.23, 0.24),
        "screw_slot": (0.12, 0.12, 0.13),
        "brand": (0.98, 0.99, 1.0),
        "model": (0.78, 0.96, 0.94),
        "sub": (0.82, 0.88, 0.90),
        "power_lbl": (0.78, 0.82, 0.86),
        "power_on": (0.25, 1.0, 0.48),
        "power_glow": (0.3, 1.0, 0.5, 0.42),
        "power_off": (0.18, 0.20, 0.20),
        "glass0": (0.02, 0.07, 0.08),
        "glass1": (0.01, 0.04, 0.05),
        "glass2": (0.02, 0.06, 0.07),
        "glass_edge": (0.14, 0.32, 0.36),
        "glass_sheen": (0.45, 1.0, 0.98, 0.09),
        "fl": (0.75, 1.0, 0.98),
        "fl_glow": (0.25, 1.0, 0.96, 0.30),
        "fl_dim": (0.18, 0.40, 0.42, 0.68),
        "glow_w": 5.0,
        "lamp_well": (0.08, 0.10, 0.11),
        "lamp_cyan": (0.45, 1.0, 0.96),
        "lamp_amber": (1.0, 0.84, 0.22),
        "lamp_red": (1.0, 0.38, 0.22),
        "lamp_on": (0.90, 0.96, 0.96),
        "lamp_off": (0.42, 0.48, 0.50),
        "vu_name": (0.55, 1.0, 0.96, 0.92),
        "vu_ok": (0.28, 1.0, 0.92),
        "vu_ok_off": (0.06, 0.16, 0.16),
        "vu_warn": (1.0, 0.86, 0.22),
        "vu_warn_off": (0.20, 0.16, 0.06),
        "vu_hot": (1.0, 0.34, 0.20),
        "vu_hot_off": (0.22, 0.08, 0.07),
        "knob0": (0.55, 0.56, 0.58),
        "knob1": (0.16, 0.17, 0.18),
        "knob_ring": (0.08, 0.08, 0.09),
        "knob_cap": (0.32, 0.33, 0.34),
        "knob_ptr": (0.92, 0.93, 0.94),
        "knob_lbl": (0.78, 0.82, 0.86),
        "sw_body": (0.16, 0.17, 0.18),
        "sw_edge": (0.08, 0.08, 0.09),
        "sw_knob": (0.55, 0.58, 0.60),
        "sw_on": (0.92, 1.0, 1.0),
        "sw_off": (0.50, 0.54, 0.56),
        "bar_ok": (0.18, 1.0, 0.86),
        "bar_warn": (1.0, 0.88, 0.18),
        "bar_hot": (1.0, 0.38, 0.14),
        "bar_off": (0.03, 0.08, 0.08),
        "bar_off_line": (0.0, 0.7, 0.7, 0.12),
        "bar_hold": (1.0, 0.92, 0.55),
        "eq_grid": (0.15, 0.9, 1.0, 0.16),
        "eq_zero": (0.25, 1.0, 1.0, 0.62),
        "eq_fill": (0.05, 0.92, 1.0, 0.24),
        "eq_bar": (0.05, 0.75, 0.92, 0.48),
        "eq_glow": (0.2, 0.98, 1.0, 0.32),
        "eq_line": (0.72, 1.0, 1.0),
        "eq_stem": (0.15, 0.98, 1.0, 0.48),
        "eq_node": (0.25, 0.95, 1.0),
        "eq_active": (0.92, 1.0, 1.0),
        "scale": (0.55, 0.95, 0.92, 0.95),
        "door_sel": (0.48, 0.98, 1.0),
        "door_idle": (0.30, 0.34, 0.36),
        "silk": (0.82, 0.88, 0.90),
        "door_title": (0.92, 0.96, 0.98),
    },
}

SKINS: dict[str, dict] = {
    "1980s": _EIGHTIES,
    "future": _merge(
        _EIGHTIES,
        id="future",
        label="Future Form",
        brand_name="CASCADE",
        tagline="QUANTUM AUDIO MESH",
        light=False,
        glow=True,
        screws=False,
        hair=False,
        radius=14.0,
        form="mesh",
        css={
            "font": '"Ubuntu", "Cantarell", sans-serif',
            "radius": "14px",
            "window_bg": "#060414",
            "window_fg": "#e8f0ff",
            "header0": "#3a2060",
            "header1": "#140828",
            "header2": "#060414",
            "header_fg": "#f0e8ff",
            "header_hi": "#c080ff",
            "header_line": "#220844",
            "title": "#f0f8ff",
            "title_glow": "#80f0ffaa",
            "sub": "#ff60d0",
            "sub_glow": "#ff40c888",
            "status_ok": "#80fff8",
            "status_glow": "#ff40e0",
            "status_off": "#3a2858",
            "eq_label": "#c8f0ff",
            "wrap_bg": "#08061a",
            "wrap_border": "#8040c0",
            "wrap_edge": "#40fff8",
            "wrap_glow": "#ff40c844",
            "cabinet": "#050312",
            "unit_bg": "#140828",
            "unit_border": "#9060e0",
            "unit_shadow": "#020010",
            "unit_hi": "#c080ff",
            "face0": "#3a2060",
            "face1": "#1a0c38",
            "face2": "#100824",
            "face3": "#080414",
            "expander": "#e0d0ff",
            "arrow": "#ff60d0",
            "brand": "#f0f8ff",
            "model": "#80fff8",
            "rack_title": "#ff80e0",
            "section": "#c0a0ff",
            "field": "#80fff8",
            "led_bg": "#08061a",
            "led_border": "#8040c0",
            "led_glow": "#ff40c866",
            "trough": "#0c0820",
            "highlight": "#ff40c8",
            "slider0": "#c0f0ff",
            "slider1": "#6020a0",
            "value": "#80fff8",
            "switch_on": "#c02090",
            "switch_on_border": "#ff80e0",
            "drop_bg": "#140828",
            "drop_fg": "#f0e8ff",
            "drop_border": "#9060e0",
            "tape0": "#4a2080",
            "tape1": "#240c48",
            "tape2": "#100824",
            "tape_hot0": "#80fff8",
            "tape_hot1": "#c02090",
            "tape_hot2": "#400830",
            "tape_mode_on0": "#c02090",
            "tape_mode_on1": "#400830",
            "keybed0": "#1a0c38",
            "keybed1": "#080414",
            "entry_bg": "#08061a",
            "entry_fg": "#80fff8",
            "entry_border": "#8040c0",
            "scroll": "#ff60d0",
            "scroll_trough": "#080414",
        },
        cr={
            "chassis0": _rgb(58, 32, 96),
            "chassis1": _rgb(26, 12, 56),
            "chassis2": _rgb(16, 8, 36),
            "chassis3": _rgb(8, 4, 20),
            "edge": _rgb(160, 96, 255),
            "brand": _rgb(240, 248, 255),
            "model": _rgb(128, 255, 248),
            "sub": _rgb(255, 96, 208),
            "power_on": _rgb(255, 64, 200),
            "power_glow": (1.0, 0.25, 0.85, 0.50),
            "glass0": (0.04, 0.02, 0.12),
            "glass1": (0.02, 0.02, 0.08),
            "glass2": (0.03, 0.04, 0.12),
            "glass_edge": _rgb(64, 255, 248),
            "glass_sheen": (1.0, 0.30, 0.85, 0.14),
            "fl": _rgb(160, 255, 255),
            "fl_glow": (1.0, 0.25, 0.85, 0.40),
            "glow_w": 7.5,
            "lamp_cyan": _rgb(64, 255, 248),
            "lamp_amber": _rgb(255, 80, 210),
            "vu_ok": _rgb(64, 255, 248),
            "vu_warn": _rgb(255, 80, 210),
            "bar_ok": _rgb(64, 255, 248),
            "bar_warn": _rgb(255, 80, 210),
            "eq_line": _rgb(180, 255, 255),
            "eq_node": _rgb(255, 80, 210),
            "eq_active": _rgb(160, 255, 255),
            "door_sel": _rgb(255, 80, 210),
            "silk": _rgb(200, 180, 255),
        },
    ),
    "digital": _merge(
        _EIGHTIES,
        id="digital",
        label="Pure Digital",
        brand_name="CASCADE EQ",
        tagline="DSP CONTROL SURFACE",
        light=False,
        glow=True,
        screws=False,
        hair=False,
        radius=0.0,
        form="matrix",
        css={
            "font": '"Ubuntu Mono", "Ubuntu", monospace',
            "radius": "0px",
            "window_bg": "#000000",
            "window_fg": "#b8ffd0",
            "header0": "#101410",
            "header1": "#000000",
            "header2": "#000000",
            "header_fg": "#80ffb0",
            "header_hi": "#203020",
            "header_line": "#00ff80",
            "title": "#d0ffe0",
            "title_glow": "#00ff8066",
            "sub": "#40ff90",
            "sub_glow": "#00ff8044",
            "status_ok": "#60ff90",
            "status_glow": "#00ff60",
            "status_off": "#14301c",
            "eq_label": "#80ffb0",
            "wrap_bg": "#000000",
            "wrap_border": "#145028",
            "wrap_edge": "#00ff80",
            "wrap_glow": "#00ff8022",
            "cabinet": "#000000",
            "unit_bg": "#050805",
            "unit_border": "#1a4028",
            "unit_shadow": "#000000",
            "unit_hi": "#00ff80",
            "face0": "#101410",
            "face1": "#050805",
            "face2": "#000000",
            "face3": "#000000",
            "expander": "#80ffb0",
            "arrow": "#00ff80",
            "brand": "#d0ffe0",
            "model": "#40ff90",
            "rack_title": "#80ffb0",
            "section": "#40ff90",
            "field": "#80ffb0",
            "led_bg": "#000000",
            "led_border": "#145028",
            "led_glow": "#00ff8044",
            "trough": "#000000",
            "highlight": "#00ff80",
            "slider0": "#80ffb0",
            "slider1": "#145028",
            "value": "#60ff90",
            "switch_bg": "#000000",
            "switch_on": "#087838",
            "switch_on_border": "#00ff80",
            "drop_bg": "#000000",
            "drop_fg": "#80ffb0",
            "drop_border": "#00ff80",
            "tape0": "#145028",
            "tape1": "#082014",
            "tape2": "#000000",
            "tape_fg": "#b8ffd0",
            "tape_hot0": "#40ff90",
            "tape_hot1": "#087838",
            "tape_hot2": "#002010",
            "tape_mode0": "#101410",
            "tape_mode1": "#000000",
            "tape_mode_on0": "#087838",
            "tape_mode_on1": "#002010",
            "keybed0": "#050805",
            "keybed1": "#000000",
            "entry_bg": "#000000",
            "entry_fg": "#60ff90",
            "entry_border": "#145028",
            "scroll": "#00ff80",
            "scroll_trough": "#000000",
        },
        cr={
            "chassis0": (0.06, 0.08, 0.06),
            "chassis1": (0.02, 0.03, 0.02),
            "chassis2": (0.00, 0.00, 0.00),
            "chassis3": (0.00, 0.00, 0.00),
            "edge": (0.00, 1.0, 0.50),
            "inner": (0.00, 0.20, 0.10),
            "brand": (0.82, 1.0, 0.88),
            "model": (0.25, 1.0, 0.56),
            "sub": (0.50, 1.0, 0.70),
            "power_on": (0.00, 1.0, 0.45),
            "glass0": (0.00, 0.00, 0.00),
            "glass1": (0.00, 0.02, 0.01),
            "glass2": (0.00, 0.00, 0.00),
            "glass_edge": (0.00, 0.80, 0.40),
            "glass_sheen": (0.00, 1.0, 0.50, 0.08),
            "fl": (0.38, 1.0, 0.56),
            "fl_glow": (0.00, 1.0, 0.40, 0.22),
            "fl_dim": (0.05, 0.22, 0.12, 0.70),
            "glow_w": 2.2,
            "lamp_cyan": (0.00, 1.0, 0.50),
            "vu_ok": (0.00, 1.0, 0.50),
            "vu_ok_off": (0.00, 0.12, 0.06),
            "bar_ok": (0.00, 1.0, 0.50),
            "bar_off": (0.00, 0.06, 0.03),
            "eq_line": (0.50, 1.0, 0.70),
            "eq_node": (0.00, 1.0, 0.50),
            "door_sel": (0.00, 1.0, 0.50),
            "silk": (0.40, 1.0, 0.62),
            "door_title": (0.70, 1.0, 0.80),
        },
    ),
}

SKIN_ORDER = ("1980s", "future", "digital")
SKIN_CHOICES = tuple((sid, SKINS[sid]["label"]) for sid in SKIN_ORDER)


def resolve(skin_id: str | None) -> str:
    if skin_id in SKINS:
        return str(skin_id)
    return DEFAULT_SKIN


def apply(skin_id: str | None) -> str:
    global _ACTIVE
    _ACTIVE = resolve(skin_id)
    return _ACTIVE


def active() -> dict:
    return SKINS[_ACTIVE]


def palette() -> dict:
    return SKINS[_ACTIVE]["cr"]


def css_text(skin_id: str | None = None) -> str:
    sid = resolve(skin_id) if skin_id is not None else _ACTIVE
    return _css(SKINS[sid]["css"])
