#!/usr/bin/env python3
"""Local web UI for editing AvianVisitors frame settings.

The UI edits ~/.birdframe/config.toml, preserves unrelated comments and
settings, and can trigger an immediate panel refresh after saving.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlparse

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib


FRAME_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = Path.home() / ".birdframe" / "config.toml"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
ALLOWED_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

MANAGED_KEYS = [
    "base_url",
    "species_source",
    "zip",
    "bw_days",
    "bw_country",
    "hours",
    "window_mode",
    "toggle_button",
    "layout_toggle_button",
    "status_text_24h",
    "status_text_today",
    "image",
    "image_url",
    "content_mode",
    "vangogh_painting",
    "painting_scale",
    "painting_cycle",
    "painting_cycle_seconds",
    "shoot",
    "shoot_title",
    "shoot_subtitle",
    "shoot_subtitle_24h",
    "shoot_subtitle_today",
    "shoot_headline_px",
    "shoot_eyebrow_px",
    "shoot_lowercase",
    "shoot_mat",
    "shoot_small_floor",
    "shoot_count_exp",
    "shoot_collage_vh",
    "shoot_title_gap_px",
    "shoot_group_y",
    "shoot_collage_lock_center",
    "shoot_title_detached",
    "shoot_title_offset_y_px",
    "shoot_full_y_shift_px",
    "shoot_full_collage_vh",
    "shoot_full_text_y_px",
    "shoot_pad_top_px",
    "shoot_pad_side_px",
    "shoot_pad_bottom_px",
    "layout_mode",
    "mat",
    "rotate",
    "saturation",
    "panel",
    "quiet_start",
    "quiet_end",
    "heal_hours",
    "state",
    "cache",
    "timeout",
    "basic_user",
    "basic_pass",
]

SECTION_ORDER = [
    ("Source", ["base_url", "species_source", "zip", "bw_days", "bw_country", "hours", "window_mode", "image", "image_url", "content_mode", "vangogh_painting", "painting_scale", "shoot"]),
    ("Mode and buttons", ["toggle_button", "layout_toggle_button", "status_text_24h", "status_text_today", "quiet_start", "quiet_end", "heal_hours"]),
    ("Title and collage", ["shoot_title", "shoot_subtitle", "shoot_subtitle_24h", "shoot_subtitle_today", "shoot_headline_px", "shoot_eyebrow_px", "shoot_lowercase", "shoot_collage_vh", "shoot_title_gap_px", "shoot_group_y", "shoot_collage_lock_center", "shoot_title_detached", "shoot_title_offset_y_px"]),
    ("Fullscreen tweaks", ["shoot_full_y_shift_px", "shoot_full_collage_vh", "shoot_full_text_y_px", "shoot_pad_top_px", "shoot_pad_side_px", "shoot_pad_bottom_px", "mat"]),
    ("Hardware", ["rotate", "saturation", "panel", "timeout", "basic_user", "basic_pass", "state", "cache"]),
]

FIELD_DEFS: dict[str, dict[str, Any]] = {
    "base_url": {"label": "BirdNET-Pi URL", "kind": "text", "width": "wide", "help": "Where the frame screenshots from."},
    "species_source": {"label": "Species source", "kind": "select", "options": [("", "BirdNET-Pi"), ("birdweather", "BirdWeather")], "help": "Leave on BirdNET-Pi unless you want BirdWeather."},
    "zip": {"label": "ZIP / postal code", "kind": "text"},
    "bw_days": {"label": "BirdWeather lookback (days)", "kind": "number", "min": 1, "max": 30, "step": 1},
    "bw_country": {"label": "BirdWeather country", "kind": "text", "value_width": 10},
    "hours": {"label": "Rolling window (hours)", "kind": "number", "min": 1, "max": 168, "step": 1},
    "window_mode": {"label": "Frame window", "kind": "select", "options": [("24h", "24 hours"), ("today", "Today since 00:00")], "help": "Switches the detection window and the frame label."},
    "toggle_button": {"label": "Bird button", "kind": "select", "options": [("", "Off"), ("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")]},
    "layout_toggle_button": {"label": "Layout button", "kind": "select", "options": [("", "Off"), ("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")]},
    "status_text_24h": {"label": "24h badge", "kind": "text", "value_width": 12},
    "status_text_today": {"label": "Today badge", "kind": "text", "value_width": 12},
    "image": {"label": "Local image path", "kind": "text", "width": "wide"},
    "image_url": {"label": "Image URL", "kind": "text", "width": "wide"},
    "content_mode": {"label": "Display content", "kind": "select", "options": [("birds", "Birds"), ("paintings", "Paintings")], "help": "Use paintings mode to show local artwork instead of birds."},
    "vangogh_painting": {"label": "Selected painting", "kind": "text", "width": "wide", "help": "Updated by the painting gallery."},
    "painting_scale": {"label": "Painting zoom", "kind": "range", "min": 0.6, "max": 2.2, "step": 0.05, "help": "1.0 fills the frame naturally. Higher values zoom in."},
    "painting_cycle": {"label": "Cycle paintings", "kind": "checkbox", "help": "Automatically cycle through all saved paintings."},
    "painting_cycle_seconds": {"label": "Cycle interval (seconds)", "kind": "number", "min": 10, "max": 86400, "step": 1},
    "shoot": {"label": "Render on the Pi", "kind": "checkbox"},
    "shoot_title": {"label": "Title", "kind": "text", "width": "wide"},
    "shoot_subtitle": {"label": "Subtitle", "kind": "text", "width": "wide"},
    "shoot_subtitle_24h": {"label": "24h subtitle", "kind": "text", "width": "wide"},
    "shoot_subtitle_today": {"label": "Today subtitle", "kind": "text", "width": "wide"},
    "shoot_headline_px": {"label": "Headline size", "kind": "range", "min": 20, "max": 72, "step": 1},
    "shoot_eyebrow_px": {"label": "Eyebrow size", "kind": "range", "min": 10, "max": 40, "step": 1},
    "shoot_lowercase": {"label": "Lowercase title", "kind": "checkbox"},
    "shoot_mat": {"label": "Mat inset", "kind": "range", "min": 0, "max": 0.18, "step": 0.005},
    "shoot_small_floor": {"label": "Small species floor", "kind": "range", "min": 0, "max": 0.2, "step": 0.005},
    "shoot_count_exp": {"label": "Count emphasis", "kind": "range", "min": 0.2, "max": 1.2, "step": 0.01},
    "shoot_collage_vh": {"label": "Collage height", "kind": "range", "min": 20, "max": 100, "step": 1},
    "shoot_title_gap_px": {"label": "Title gap", "kind": "range", "min": 0, "max": 48, "step": 1},
    "shoot_group_y": {"label": "Vertical group alignment", "kind": "select", "options": [("flex-start", "Top"), ("center", "Center"), ("flex-end", "Bottom")]},
    "shoot_collage_lock_center": {"label": "Lock collage to center", "kind": "checkbox"},
    "shoot_title_detached": {"label": "Detach title", "kind": "checkbox"},
    "shoot_title_offset_y_px": {"label": "Title offset", "kind": "range", "min": -80, "max": 80, "step": 1},
    "shoot_full_y_shift_px": {"label": "Fullscreen block shift", "kind": "range", "min": -160, "max": 160, "step": 1},
    "shoot_full_collage_vh": {"label": "Fullscreen collage height", "kind": "range", "min": 80, "max": 220, "step": 1},
    "shoot_full_text_y_px": {"label": "Fullscreen title shift", "kind": "range", "min": -120, "max": 120, "step": 1},
    "shoot_pad_top_px": {"label": "Top padding", "kind": "range", "min": 0, "max": 120, "step": 1},
    "shoot_pad_side_px": {"label": "Side padding", "kind": "range", "min": 0, "max": 120, "step": 1},
    "shoot_pad_bottom_px": {"label": "Bottom padding", "kind": "range", "min": 0, "max": 120, "step": 1},
    "layout_mode": {"label": "Layout mode", "kind": "select", "options": [("framed", "Framed"), ("full", "Fullscreen")], "help": "Fullscreen uses the extra full-screen tuning fields."},
    "mat": {"label": "Global mat shrink", "kind": "range", "min": 0, "max": 0.12, "step": 0.005},
    "rotate": {"label": "Panel rotation", "kind": "select", "options": [("90", "90°"), ("270", "270°")]},
    "saturation": {"label": "Panel saturation", "kind": "range", "min": 0, "max": 1, "step": 0.01},
    "panel": {"label": "Panel driver", "kind": "select", "options": [("", "Auto detect"), ("el133uf1", "el133uf1")], "help": "Force the 13.3-inch driver if auto-detect fails."},
    "quiet_start": {"label": "Quiet start hour", "kind": "number", "min": 0, "max": 23, "step": 1},
    "quiet_end": {"label": "Quiet end hour", "kind": "number", "min": 0, "max": 23, "step": 1},
    "heal_hours": {"label": "Heal interval (hours)", "kind": "number", "min": 1, "max": 168, "step": 1},
    "state": {"label": "State file", "kind": "text", "width": "wide"},
    "cache": {"label": "Cache folder", "kind": "text", "width": "wide"},
    "timeout": {"label": "Timeout (seconds)", "kind": "number", "min": 5, "max": 300, "step": 1},
    "basic_user": {"label": "Basic-auth user", "kind": "text"},
    "basic_pass": {"label": "Basic-auth password", "kind": "password"},
}

FIELD_RENDER_ORDER = [name for _, names in SECTION_ORDER for name in names if name in FIELD_DEFS]

ASSIGN_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_]+)\s*=\s*(?P<value>.*?)(?P<comment>\s+#.*)?$")


def load_config(path: Path) -> dict[str, Any]:
    cfg = dict(_default_config())
    if path.exists():
        with path.open("rb") as f:
            cfg.update(tomllib.load(f))
    mode = str(cfg.get("content_mode", "birds") or "birds").strip().lower()
    cfg["content_mode"] = "paintings" if mode in {"paintings", "vangogh"} else "birds"
    try:
        cfg["painting_scale"] = max(0.6, min(2.2, float(cfg.get("painting_scale", 1.0))))
    except Exception:  # noqa: BLE001
        cfg["painting_scale"] = 1.0
    return cfg


def _default_config() -> dict[str, Any]:
    return {
        "base_url": "http://birdnet.local",
        "species_source": "",
        "zip": "",
        "bw_days": 7,
        "bw_country": "us",
        "hours": 24,
        "window_mode": "today",
        "toggle_button": "a",
        "layout_toggle_button": "b",
        "status_text_24h": "24H",
        "status_text_today": "TODAY",
        "image": "",
        "image_url": "",
        "content_mode": "birds",
        "vangogh_painting": "self_portrait_felt_hat",
        "painting_scale": 1.0,
        "painting_cycle": False,
        "painting_cycle_seconds": 300,
        "shoot": False,
        "shoot_title": None,
        "shoot_subtitle": None,
        "shoot_subtitle_24h": None,
        "shoot_subtitle_today": None,
        "shoot_headline_px": 42,
        "shoot_eyebrow_px": 18,
        "shoot_lowercase": False,
        "shoot_mat": 0.04,
        "shoot_small_floor": 0.04,
        "shoot_count_exp": 0.65,
        "shoot_collage_vh": 52,
        "shoot_title_gap_px": 14,
        "shoot_group_y": "center",
        "shoot_collage_lock_center": False,
        "shoot_title_detached": False,
        "shoot_title_offset_y_px": 0,
        "shoot_full_y_shift_px": 0,
        "shoot_full_collage_vh": 140,
        "shoot_full_text_y_px": 0,
        "shoot_pad_top_px": None,
        "shoot_pad_side_px": None,
        "shoot_pad_bottom_px": None,
        "layout_mode": "full",
        "mat": 0.0,
        "rotate": 90,
        "saturation": 0.6,
        "panel": "",
        "quiet_start": 0,
        "quiet_end": 0,
        "heal_hours": 24,
        "state": "~/.birdframe/state.json",
        "cache": "~/.birdframe",
        "timeout": 45,
        "basic_user": None,
        "basic_pass": None,
    }


def _coerce_value(name: str, raw: str | None) -> Any:
    definition = FIELD_DEFS[name]
    kind = definition["kind"]
    if kind == "checkbox":
        return raw is not None
    if raw is None:
        return None
    text = raw.strip()
    if kind in {"text", "password"}:
        return text
    if kind == "select":
        return text
    if kind == "number":
        if text == "":
            return None
        return int(text) if text.find(".") < 0 else float(text)
    if kind == "range":
        if text == "":
            return None
        return int(text) if float(text).is_integer() else float(text)
    return text


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, ".15g")
    return json.dumps(str(value), ensure_ascii=False)


def _strip_managed_keys(text: str) -> str:
    out_lines: list[str] = []
    for line in text.splitlines():
        # Always drop the managed-block marker so it never duplicates.
        if line.strip() == "# Managed by the AvianVisitors web UI":
            continue
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or stripped.startswith("["):
            out_lines.append(line)
            continue
        match = ASSIGN_RE.match(line)
        if match and match.group("key") in MANAGED_KEYS:
            continue
        out_lines.append(line)
    return "\n".join(out_lines).rstrip() + "\n"


def _render_managed_block(values: dict[str, Any]) -> str:
    lines = ["# Managed by the AvianVisitors web UI"]
    for key in MANAGED_KEYS:
        value = values.get(key)
        if value is None:
            continue
        lines.append(f"{key} = {_format_value(value)}")
    return "\n".join(lines) + "\n"


def render_config_text(original_text: str, values: dict[str, Any]) -> str:
    base = _strip_managed_keys(original_text)
    if base and not base.endswith("\n"):
        base += "\n"
    if base and not base.endswith("\n\n"):
        base += "\n"
    return base + _render_managed_block(values)


def _parse_form(form: dict[str, list[str]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key in MANAGED_KEYS:
        definition = FIELD_DEFS.get(key)
        if definition is None:
            continue
        if definition["kind"] == "checkbox":
            values[key] = key in form
        else:
            values[key] = _coerce_value(key, form.get(key, [""])[0])
    values["layout_mode"] = "full"
    return values


def _parse_basic_form(form: dict[str, list[str]], config: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("window_mode", "content_mode", "vangogh_painting", "painting_scale", "painting_cycle", "painting_cycle_seconds"):
        if key in form:
            out[key] = _coerce_value(key, form.get(key, [""])[0])
    out["painting_cycle"] = "painting_cycle" in form
    out["layout_mode"] = "full"
    return out


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "").strip()
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    return base or "painting.png"


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, list[str]], dict[str, tuple[str, bytes]]]:
    boundary_match = re.search(r"boundary=(?P<b>[^;]+)", content_type)
    if not boundary_match:
        raise ValueError("Missing multipart boundary")
    boundary = boundary_match.group("b").strip().strip('"').encode("utf-8")
    form: dict[str, list[str]] = {}
    files: dict[str, tuple[str, bytes]] = {}
    marker = b"--" + boundary
    for chunk in body.split(marker):
        chunk = chunk.strip()
        if not chunk or chunk == b"--":
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        header_blob, sep, payload = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers: dict[str, str] = {}
        for raw in header_blob.decode("utf-8", errors="ignore").split("\r\n"):
            if ":" in raw:
                k, v = raw.split(":", 1)
                headers[k.lower().strip()] = v.strip()
        disposition = headers.get("content-disposition", "")
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        content = payload.rstrip(b"\r\n")
        if filename_match and filename_match.group(1):
            files[name] = (filename_match.group(1), content)
        else:
            form.setdefault(name, []).append(content.decode("utf-8", errors="ignore"))
    return form, files


def _refresh_now(config_path: Path) -> None:
    subprocess.Popen(
        [
            sys.executable,
            str(FRAME_DIR / "display.py"),
            "--config",
            str(config_path),
            "--force",
        ],
        cwd=str(FRAME_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _field_value(config: dict[str, Any], name: str) -> Any:
    value = config.get(name)
    if value is None:
        if FIELD_DEFS[name]["kind"] == "checkbox":
            return False
        return ""
    return value


def _option_selected(current: Any, option_value: str) -> str:
    return " selected" if str(current) == option_value else ""


def _render_field(name: str, config: dict[str, Any]) -> str:
    definition = FIELD_DEFS[name]
    value = _field_value(config, name)
    help_text = definition.get("help")
    label = html.escape(definition["label"])
    field_html = ""
    kind = definition["kind"]
    width_class = definition.get("width", "")
    if kind == "select":
        options_html = []
        for option_value, option_label in definition["options"]:
            options_html.append(
                f'<option value="{html.escape(option_value)}"{_option_selected(value, option_value)}>{html.escape(option_label)}</option>'
            )
        field_html = f'<select name="{html.escape(name)}">{"".join(options_html)}</select>'
    elif kind == "checkbox":
        checked = " checked" if bool(value) else ""
        note = f'<p class="hint">{html.escape(help_text)}</p>' if help_text else ""
        return f'<div class="field"><label class="checkbox"><input type="checkbox" name="{html.escape(name)}" value="1"{checked}><span>{label}</span></label>{note}</div>'
    elif kind == "range":
        display_value = "" if value == "" else value
        field_html = (
            f'<div class="range-wrap">'
            f'<input type="range" name="{html.escape(name)}" min="{definition["min"]}" max="{definition["max"]}" step="{definition["step"]}" value="{html.escape(str(display_value))}" data-value-for="{html.escape(name)}">'
            f'<output data-output-for="{html.escape(name)}">{html.escape(str(display_value))}</output>'
            f'</div>'
        )
    else:
        input_type = "password" if kind == "password" else "text"
        attrs = []
        if "value_width" in definition:
            attrs.append(f'style="max-width:{definition["value_width"]}ch"')
        if kind == "number":
            attrs.extend([
                f'min="{definition["min"]}"',
                f'max="{definition["max"]}"',
                f'step="{definition["step"]}"',
            ])
        display_value = html.escape(str(value))
        field_html = f'<input type="{input_type}" name="{html.escape(name)}" value="{display_value}" {" ".join(attrs)}>'
    classes = ["field"]
    if width_class:
        classes.append(width_class)
    note = f'<p class="hint">{html.escape(help_text)}</p>' if help_text else ""
    return f'<div class="{" ".join(classes)}"><label>{label}</label>{field_html}{note}</div>'


def _render_section(title: str, names: Iterable[str], config: dict[str, Any]) -> str:
    cards = [ _render_field(name, config) for name in names if name in FIELD_DEFS ]
    return f'<section class="card"><h2>{html.escape(title)}</h2><div class="grid">{"".join(cards)}</div></section>'


def _render_collapsed_section(title: str, names: Iterable[str], config: dict[str, Any]) -> str:
    cards = [ _render_field(name, config) for name in names if name in FIELD_DEFS ]
    return f'<details class="card"><summary>{html.escape(title)}</summary><div class="grid">{"".join(cards)}</div></details>'


def _render_presets() -> str:
    return """
    <section class="card compact">
      <h2>Presets</h2>
      <div class="preset-row">
        <button type="button" data-preset="birds-today">Birds — Today</button>
        <button type="button" data-preset="birds-24h">Birds — 24h</button>
        <button type="button" data-preset="paintings">Paintings</button>
      </div>
      <p class="hint">Use presets to quickly switch between birds and paintings.</p>
    </section>
    """


def _render_painting_gallery(paintings: list[dict[str, Any]], selected: str) -> str:
    cards: list[str] = []
    for painting in paintings:
        key = painting["key"]
        checked = " checked" if key == selected else ""
        title = html.escape(painting["title"])
        src = html.escape(painting["preview"])
        p_scale = html.escape(str(painting.get("scale", 1.0)))
        p_x = html.escape(str(painting.get("offset_x", 0.0)))
        p_y = html.escape(str(painting.get("offset_y", 0.0)))
        local_badge = '<span class="painting-local">Local</span>' if bool(painting.get("is_local")) else ""
        cards.append(
            f'<label class="painting-card">'
            f'<input type="radio" name="vangogh_painting" value="{html.escape(key)}" data-scale="{p_scale}" data-offset-x="{p_x}" data-offset-y="{p_y}"{checked}>'
            f'<img src="{src}" alt="{title}">'
            f'<span>{title}{local_badge}</span>'
            f'</label>'
        )
    return f'<div class="painting-grid">{"".join(cards)}</div>'


def _render_painting_editor(selected: str, preview_src: str, scale: float, offset_x: float, offset_y: float) -> str:
    ox = int(round(offset_x * 100))
    oy = int(round(offset_y * 100))
    delete_style = "" if selected.startswith("local:") else ' style="display:none"'
    return f"""
    <section class=\"card compact paintings-only\">
      <h2>Image editor</h2>
      <p class=\"hint\">Adjust crop and position for the selected image. Preview updates live on this page.</p>
      <div class=\"painting-editor\">
        <div class=\"painting-preview-wrap\" id=\"painting-live-preview\">
          <img src=\"{html.escape(preview_src)}\" alt=\"Selected painting preview\" id=\"painting-live-image\">
        </div>
        <div class=\"painting-editor-controls\">
          <div class=\"field\"><label>Zoom</label><div class=\"range-wrap\"><input type=\"range\" name=\"editor_scale\" min=\"0.6\" max=\"2.2\" step=\"0.05\" value=\"{scale}\" data-value-for=\"editor_scale\"><output data-output-for=\"editor_scale\">{scale}</output></div></div>
          <div class=\"field\"><label>Move Left/Right</label><div class=\"range-wrap\"><input type=\"range\" name=\"editor_offset_x\" min=\"-100\" max=\"100\" step=\"1\" value=\"{ox}\" data-value-for=\"editor_offset_x\"><output data-output-for=\"editor_offset_x\">{ox}</output></div></div>
          <div class=\"field\"><label>Move Up/Down</label><div class=\"range-wrap\"><input type=\"range\" name=\"editor_offset_y\" min=\"-100\" max=\"100\" step=\"1\" value=\"{oy}\" data-value-for=\"editor_offset_y\"><output data-output-for=\"editor_offset_y\">{oy}</output></div></div>
        </div>
      </div>
      <form method=\"post\" action=\"/save-painting-edit\" class=\"editor-actions\">
        <input type=\"hidden\" name=\"painting_key\" id=\"editor_painting_key\" value=\"{html.escape(selected)}\">
        <input type=\"hidden\" name=\"scale\" id=\"editor_save_scale\" value=\"{scale}\">
        <input type=\"hidden\" name=\"offset_x\" id=\"editor_save_offset_x\" value=\"{ox}\">
        <input type=\"hidden\" name=\"offset_y\" id=\"editor_save_offset_y\" value=\"{oy}\">
        <button type=\"submit\" name=\"action\" value=\"save\">Save crop/position</button>
        <button class=\"primary\" type=\"submit\" name=\"action\" value=\"save_refresh\">Save and refresh now</button>
      </form>
      <form method=\"post\" action=\"/delete-painting\" class=\"editor-actions\"{delete_style}>
        <input type=\"hidden\" name=\"painting_key\" id=\"delete_painting_key\" value=\"{html.escape(selected)}\">
        <button type=\"submit\">Delete selected local image</button>
      </form>
    </section>
    """


def _config_snapshot(config: dict[str, Any]) -> str:
    lines = []
    for key in FIELD_RENDER_ORDER:
        value = _field_value(config, key)
        if value == "" and FIELD_DEFS[key]["kind"] != "checkbox":
            continue
        lines.append(f"{key} = {_format_value(value)}")
    return "\n".join(lines)


def _asset_version(path: Path) -> str:
    try:
        return str(int(path.stat().st_mtime))
    except OSError:
        return "0"


def _render_alert(message: str, error: str) -> str:
    if error:
        return f'<div class="alert error">{html.escape(error)}</div>'
    if message:
        return f'<div class="alert success">{html.escape(message)}</div>'
    return ""


def _render_header(config: dict[str, Any], subtitle: str, nav_link: str, nav_label: str) -> str:
    status_bits = []
    status_bits.append(f'<span class="status-chip">{html.escape(str(config.get("window_mode", "today")))} window</span>')
    status_bits.append(f'<span class="status-chip">{html.escape(str(config.get("content_mode", "birds")))} content</span>')
    return (
        '<header class="hero card">'
        '<div>'
        '<p class="eyebrow">AvianVisitors</p>'
        '<h1>Frame settings</h1>'
        f'<p class="lede">{html.escape(subtitle)}</p>'
        f'<p class="small"><a class="link-button" href="{nav_link}">{html.escape(nav_label)}</a></p>'
        '</div>'
        '<div class="hero-meta">'
        f'<div class="status-row">{"".join(status_bits)}</div>'
        '<p class="small">Saved to <code>~/.birdframe/config.toml</code>.</p>'
        '</div>'
        '</header>'
    )


def render_basic_page(config: dict[str, Any], paintings: list[dict[str, Any]], message: str = "", error: str = "") -> str:
    css_version = _asset_version(FRAME_DIR / "webui" / "style.css")
    js_version = _asset_version(FRAME_DIR / "webui" / "app.js")
    selected = str(config.get("vangogh_painting", "self_portrait_felt_hat"))
    gallery = _render_painting_gallery(paintings, selected)
    content_mode = _field_value(config, "content_mode")
    window_mode = _field_value(config, "window_mode")
    scale_field = _render_field("painting_scale", config)
    cycle_seconds = int(config.get("painting_cycle_seconds", 300) or 300)
    cycle_checked = " checked" if bool(config.get("painting_cycle")) else ""
    selected_item = next((p for p in paintings if p["key"] == selected), paintings[0] if paintings else None)
    preview_src = selected_item["preview"] if selected_item else ""
    selected_scale = float(selected_item.get("scale", config.get("painting_scale", 1.0)) if selected_item else config.get("painting_scale", 1.0))
    selected_x = float(selected_item.get("offset_x", 0.0)) if selected_item else 0.0
    selected_y = float(selected_item.get("offset_y", 0.0)) if selected_item else 0.0
    editor_block = _render_painting_editor(selected, preview_src, selected_scale, selected_x, selected_y)
    return f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>AvianVisitors Frame Settings</title>
    <link rel=\"stylesheet\" href=\"/static/style.css?v={css_version}\">
    <script defer src=\"/static/app.js?v={js_version}\"></script>
</head>
<body>
    <div class=\"shell\">
        {_render_header(config, "Simple editor: choose today/24h, layout, and paintings.", "/advanced", "Open advanced editor")}
        {_render_alert(message, error)}
        <form method=\"post\" action=\"/save-basic\" class=\"panel-form\" id=\"basic-form\">
            {_render_presets()}
            <section class=\"card compact\">
                <h2>Quick options</h2>
                <div class=\"grid\">
                    <div class=\"field birds-only\"><label>Frame window</label><select name=\"window_mode\"><option value=\"24h\"{" selected" if str(window_mode)=="24h" else ""}>24 hours</option><option value=\"today\"{" selected" if str(window_mode)=="today" else ""}>Today</option></select></div>
                    <div class=\"field\"><label>Display content</label><select name=\"content_mode\"><option value=\"birds\"{" selected" if str(content_mode)=="birds" else ""}>Birds</option><option value=\"paintings\"{" selected" if str(content_mode)=="paintings" else ""}>Paintings</option></select></div>
                    <div class=\"field paintings-only\"><label class=\"checkbox\"><input type=\"checkbox\" name=\"painting_cycle\" value=\"1\"{cycle_checked}><span>Cycle all saved images</span></label></div>
                    <div class=\"field paintings-only\"><label>Cycle interval (seconds)</label><input type=\"number\" name=\"painting_cycle_seconds\" min=\"10\" max=\"86400\" step=\"1\" value=\"{cycle_seconds}\"></div>
                </div>
            </section>
            <section class=\"card compact paintings-only\">
                <h2>Paintings</h2>
                <p class=\"hint\">Pick a saved painting visually and adjust zoom.</p>
                {gallery}
                <div class=\"grid\">{scale_field}</div>
            </section>
            <section class=\"card compact paintings-only\">
                <h2>Upload paintings</h2>
                <p class=\"hint\">Upload JPG, PNG, or WEBP files. They are stored on the Pi in ~/.birdframe/paintings.</p>
                <p class=\"hint\">Use the upload form below, then return here and select it.</p>
            </section>
            {editor_block}
            <div class=\"actions card compact\">
                <button class=\"primary\" type=\"submit\" name=\"action\" value=\"save_refresh\">Save and refresh now</button>
                <button type=\"submit\" name=\"action\" value=\"save\">Save only</button>
            </div>
        </form>
        <form method=\"post\" action=\"/upload-painting\" enctype=\"multipart/form-data\" class=\"card compact upload-form paintings-only\">
            <h2>Upload paintings</h2>
            <input type=\"file\" name=\"painting_file\" accept=\"image/png,image/jpeg,image/webp\" required>
            <button type=\"submit\">Upload painting</button>
        </form>
        <form method=\"post\" action=\"/force-refresh\" class=\"card compact\">
            <h2>Force refresh</h2>
            <p class=\"hint\">Immediately push the current settings to the frame, even if nothing changed.</p>
            <button class=\"primary\" type=\"submit\">Force refresh now</button>
        </form>
    </div>
</body>
</html>
"""


def render_advanced_page(config: dict[str, Any], message: str = "", error: str = "") -> str:
    sections = [
        _render_presets(),
        *(_render_collapsed_section(title, names, config) for title, names in SECTION_ORDER),
    ]
    snapshot = html.escape(_config_snapshot(config))
    css_version = _asset_version(FRAME_DIR / "webui" / "style.css")
    js_version = _asset_version(FRAME_DIR / "webui" / "app.js")
    alert = _render_alert(message, error)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AvianVisitors Frame Settings</title>
    <link rel="stylesheet" href="/static/style.css?v={css_version}">
    <script defer src="/static/app.js?v={js_version}"></script>
</head>
<body>
  <div class="shell">
    {_render_header(config, "Advanced editor: all frame controls.", "/", "Back to simple editor")}
    {alert}
    <form method="post" action="/save" class="panel-form">
      {"".join(sections)}
      <section class="card compact">
        <h2>Copyable config</h2>
        <p class="hint">This is the managed TOML block the UI will write back into your config file.</p>
        <textarea readonly rows="18" id="config-snapshot">{snapshot}</textarea>
      </section>
      <div class="actions card compact">
        <button class="primary" type="submit" name="action" value="save_refresh">Save and refresh now</button>
        <button type="submit" name="action" value="save">Save only</button>
        <a class="link-button" href="/">Reset view</a>
      </div>
    </form>
    <form method="post" action="/force-refresh" class="card compact">
      <h2>Force refresh</h2>
      <p class="hint">Immediately push the current settings to the frame, even if nothing changed.</p>
      <button class="primary" type="submit">Force refresh now</button>
    </form>
  </div>
</body>
</html>
"""


class SettingsHandler(BaseHTTPRequestHandler):
    server_version = "AvianVisitorsSettings/1.0"

    @property
    def app(self) -> "SettingsApp":
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            config = self.app.current_config()
            message = parse_qs(parsed.query).get("message", [""])[0]
            error = parse_qs(parsed.query).get("error", [""])[0]
            body = render_basic_page(config, self.app.list_paintings(), message=message, error=error).encode("utf-8")
            self._send_html(body)
            return
        if parsed.path == "/advanced":
            config = self.app.current_config()
            message = parse_qs(parsed.query).get("message", [""])[0]
            error = parse_qs(parsed.query).get("error", [""])[0]
            body = render_advanced_page(config, message=message, error=error).encode("utf-8")
            self._send_html(body)
            return
        if parsed.path.startswith("/uploads/"):
            name = unquote(parsed.path.split("/uploads/", 1)[1])
            self._send_upload(name)
            return
        if parsed.path == "/static/style.css":
            self._send_static(self.app.css_path, "text/css; charset=utf-8")
            return
        if parsed.path == "/static/app.js":
            self._send_static(self.app.js_path, "application/javascript; charset=utf-8")
            return
        if parsed.path == "/api/config":
            self._send_json(self.app.current_config())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path in {"/save", "/save-basic"}:
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length).decode("utf-8")
            form = parse_qs(payload, keep_blank_values=True)
            action = form.get("action", ["save"])[0]
            target = "/advanced" if self.path == "/save" else "/"
            try:
                if self.path == "/save":
                    self.app.save_from_form(form)
                else:
                    self.app.save_basic_from_form(form)
                if action == "save_refresh":
                    self.app.trigger_refresh()
            except Exception as exc:  # noqa: BLE001
                self._redirect(target + "?error=" + quote_plus(str(exc)), code=HTTPStatus.SEE_OTHER)
                return
            self._redirect(target + "?message=" + quote_plus("Saved settings"), code=HTTPStatus.SEE_OTHER)
            return
        if self.path == "/force-refresh":
            try:
                self.app.trigger_refresh()
            except Exception as exc:  # noqa: BLE001
                self._redirect("/?error=" + quote_plus(str(exc)), code=HTTPStatus.SEE_OTHER)
                return
            self._redirect("/?message=" + quote_plus("Frame refresh triggered"), code=HTTPStatus.SEE_OTHER)
            return
        if self.path == "/upload-painting":
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length)
            ctype = self.headers.get("Content-Type", "")
            try:
                _, files = _parse_multipart(ctype, payload)
                if "painting_file" not in files:
                    raise ValueError("No file uploaded")
                filename, content = files["painting_file"]
                self.app.save_uploaded_painting(filename, content)
            except Exception as exc:  # noqa: BLE001
                self._redirect("/?error=" + quote_plus(str(exc)), code=HTTPStatus.SEE_OTHER)
                return
            self._redirect("/?message=" + quote_plus("Uploaded painting"), code=HTTPStatus.SEE_OTHER)
            return
        if self.path == "/save-painting-edit":
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length).decode("utf-8")
            form = parse_qs(payload, keep_blank_values=True)
            key = form.get("painting_key", [""])[0]
            action = form.get("action", ["save"])[0]
            scale_raw = form.get("scale", form.get("editor_scale", ["1.0"]))[0]
            offset_x_raw = form.get("offset_x", form.get("editor_offset_x", ["0"]))[0]
            offset_y_raw = form.get("offset_y", form.get("editor_offset_y", ["0"]))[0]
            try:
                self.app.save_painting_edit(
                    key,
                    scale_raw,
                    offset_x_raw,
                    offset_y_raw,
                )
                if action == "save_refresh":
                    self.app.trigger_refresh()
            except Exception as exc:  # noqa: BLE001
                self._redirect("/?error=" + quote_plus(str(exc)), code=HTTPStatus.SEE_OTHER)
                return
            self._redirect("/?message=" + quote_plus("Saved image edit"), code=HTTPStatus.SEE_OTHER)
            return
        if self.path == "/delete-painting":
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length).decode("utf-8")
            form = parse_qs(payload, keep_blank_values=True)
            key = form.get("painting_key", [""])[0]
            try:
                self.app.delete_painting(key)
            except Exception as exc:  # noqa: BLE001
                self._redirect("/?error=" + quote_plus(str(exc)), code=HTTPStatus.SEE_OTHER)
                return
            self._redirect("/?message=" + quote_plus("Deleted painting"), code=HTTPStatus.SEE_OTHER)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _send_html(self, body: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_upload(self, name: str) -> None:
        path = self.app.paintings_dir() / _safe_filename(name)
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ext = path.suffix.lower()
        ctype = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(ext, "application/octet-stream")
        self._send_static(path, ctype)

    def _redirect(self, location: str, code: HTTPStatus = HTTPStatus.SEE_OTHER) -> None:
        self.send_response(code)
        self.send_header("Location", location)
        self.end_headers()


@dataclass
class SettingsApp:
    config_path: Path
    frame_dir: Path = FRAME_DIR

    @property
    def css_path(self) -> Path:
        return self.frame_dir / "webui" / "style.css"

    @property
    def js_path(self) -> Path:
        return self.frame_dir / "webui" / "app.js"

    def current_config(self) -> dict[str, Any]:
        cfg = load_config(self.config_path)
        # The basic form always writes window_mode, layout_mode and content_mode
        # explicitly to config, so the config is the source of truth for those.
        # We only need state for vangogh_painting, which tracks the active image
        # during slideshow cycling.
        state_path = Path(os.path.expanduser(cfg.get("state", "~/.birdframe/state.json")))
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                if state.get("vangogh_painting"):
                    cfg["vangogh_painting"] = state["vangogh_painting"]
            except Exception:  # noqa: BLE001
                pass
        return cfg

    def _cache_dir(self) -> Path:
        cfg = self.current_config()
        raw = str(cfg.get("cache", str(self.config_path.parent)) or str(self.config_path.parent))
        return Path(os.path.expanduser(raw))

    def paintings_dir(self) -> Path:
        return self._cache_dir() / "paintings"

    def painting_edits_path(self) -> Path:
        return self._cache_dir() / "painting_edits.json"

    def _load_painting_edits(self) -> dict[str, dict[str, float]]:
        path = self.painting_edits_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            return {}
        return {}

    def _save_painting_edits(self, edits: dict[str, dict[str, float]]) -> None:
        path = self.painting_edits_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(edits, indent=2, sort_keys=True))

    def list_paintings(self) -> list[dict[str, Any]]:
        from vangogh import PAINTINGS

        edits = self._load_painting_edits()
        items = [{
            "key": p["key"],
            "title": f"{p['title']} ({p['year']})",
            "preview": p["url"],
            "is_local": False,
            "scale": float(edits.get(p["key"], {}).get("scale", 1.0)),
            "offset_x": float(edits.get(p["key"], {}).get("offset_x", 0.0)),
            "offset_y": float(edits.get(p["key"], {}).get("offset_y", 0.0)),
        } for p in PAINTINGS]
        pdir = self.paintings_dir()
        if pdir.exists():
            for path in sorted(pdir.iterdir()):
                if not path.is_file() or path.suffix.lower() not in ALLOWED_UPLOAD_EXTS:
                    continue
                key = f"local:{path.name}"
                items.append({
                    "key": key,
                    "title": path.stem.replace("_", " "),
                    "preview": f"/uploads/{quote(path.name)}",
                    "is_local": True,
                    "scale": float(edits.get(key, {}).get("scale", 1.0)),
                    "offset_x": float(edits.get(key, {}).get("offset_x", 0.0)),
                    "offset_y": float(edits.get(key, {}).get("offset_y", 0.0)),
                })
        return items

    def save_uploaded_painting(self, filename: str, content: bytes) -> None:
        safe = _safe_filename(filename)
        ext = Path(safe).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            raise ValueError("Use PNG, JPG, JPEG, or WEBP files")
        if not content:
            raise ValueError("Uploaded file is empty")
        pdir = self.paintings_dir()
        pdir.mkdir(parents=True, exist_ok=True)
        stem = Path(safe).stem
        candidate = pdir / safe
        idx = 2
        while candidate.exists():
            candidate = pdir / f"{stem}-{idx}{ext}"
            idx += 1
        candidate.write_bytes(content)

    def save_painting_edit(self, key: str, scale_raw: str, offset_x_raw: str, offset_y_raw: str) -> None:
        key = str(key or "").strip()
        if not key:
            raise ValueError("Select a painting first")
        try:
            scale = max(0.6, min(2.2, float(scale_raw)))
            offset_x = max(-1.0, min(1.0, float(offset_x_raw) / 100.0))
            offset_y = max(-1.0, min(1.0, float(offset_y_raw) / 100.0))
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Invalid crop values") from exc
        edits = self._load_painting_edits()
        edits[key] = {
            "scale": scale,
            "offset_x": offset_x,
            "offset_y": offset_y,
        }
        self._save_painting_edits(edits)

        # Keep selected painting in config so refresh shows the edited image.
        current = self.current_config()
        original_text = self.config_path.read_text() if self.config_path.exists() else ""
        current["vangogh_painting"] = key
        rendered = render_config_text(original_text, current)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(rendered)
        with self.config_path.open("rb") as f:
            tomllib.load(f)

    def delete_painting(self, key: str) -> None:
        key = str(key or "").strip()
        if not key.startswith("local:"):
            raise ValueError("Only local uploaded images can be deleted")
        name = _safe_filename(key.split(":", 1)[1])
        path = self.paintings_dir() / name
        if path.exists() and path.is_file():
            path.unlink()
        edits = self._load_painting_edits()
        if key in edits:
            edits.pop(key, None)
            self._save_painting_edits(edits)

    def save_basic_from_form(self, form: dict[str, list[str]]) -> None:
        current = self.current_config()
        original_text = self.config_path.read_text() if self.config_path.exists() else ""
        updates = _parse_basic_form(form, current)
        current.update(updates)
        rendered = render_config_text(original_text, current)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(rendered)
        with self.config_path.open("rb") as f:
            tomllib.load(f)

    def save_from_form(self, form: dict[str, list[str]]) -> None:
        current = self.current_config()
        original_text = self.config_path.read_text() if self.config_path.exists() else ""
        updates = _parse_form(form)
        current.update(updates)
        rendered = render_config_text(original_text, current)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(rendered)
        with self.config_path.open("rb") as f:
            tomllib.load(f)

    def trigger_refresh(self) -> None:
        _refresh_now(self.config_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local settings web UI for the AvianVisitors frame")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to ~/.birdframe/config.toml")
    parser.add_argument("--bind", default=DEFAULT_HOST, help="Bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    args = parser.parse_args()

    app = SettingsApp(config_path=Path(os.path.expanduser(args.config)))
    server = ThreadingHTTPServer((args.bind, args.port), SettingsHandler)
    server.app = app  # type: ignore[attr-defined]
    print(f"AvianVisitors settings UI listening on http://{args.bind}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()