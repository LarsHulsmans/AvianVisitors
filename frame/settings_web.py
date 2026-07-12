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
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote_plus, urlparse

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib


FRAME_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = Path.home() / ".birdframe" / "config.toml"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080

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
    ("Source", ["base_url", "species_source", "zip", "bw_days", "bw_country", "hours", "window_mode", "image", "image_url", "content_mode", "vangogh_painting", "shoot"]),
    ("Mode and buttons", ["layout_mode", "toggle_button", "layout_toggle_button", "status_text_24h", "status_text_today", "quiet_start", "quiet_end", "heal_hours"]),
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
    "content_mode": {"label": "Content mode", "kind": "select", "options": [("birds", "Birds"), ("vangogh", "Van Gogh art")], "help": "Van Gogh mode forces fullscreen portrait art instead of birds."},
    "vangogh_painting": {"label": "Van Gogh painting", "kind": "select", "options": [("self_portrait_felt_hat", "Self-Portrait with Grey Felt Hat"), ("dr_gachet", "Portrait of Dr. Gachet"), ("madame_ginoux", "L'Arlésienne: Madame Ginoux")], "help": "Portrait-only public-domain paintings."},
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
    return cfg


def _default_config() -> dict[str, Any]:
    return {
        "base_url": "http://birdnet.local",
        "species_source": "",
        "zip": "",
        "bw_days": 7,
        "bw_country": "us",
        "hours": 24,
        "window_mode": "24h",
        "toggle_button": "a",
        "layout_toggle_button": "b",
        "status_text_24h": "24H",
        "status_text_today": "TODAY",
        "image": "",
        "image_url": "",
        "content_mode": "birds",
        "vangogh_painting": "self_portrait_felt_hat",
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
        "layout_mode": "framed",
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
    if values.get("content_mode") == "vangogh":
        values["layout_mode"] = "full"
    return values


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
        <button type="button" data-preset="today-full">Today + fullscreen</button>
        <button type="button" data-preset="today-framed">Today + framed</button>
        <button type="button" data-preset="24h-full">24h + fullscreen</button>
        <button type="button" data-preset="24h-framed">24h + framed</button>
      </div>
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


def render_page(config: dict[str, Any], message: str = "", error: str = "") -> str:
    sections = [
        _render_presets(),
        *(_render_collapsed_section(title, names, config) for title, names in SECTION_ORDER),
    ]
    snapshot = html.escape(_config_snapshot(config))
    status_bits = []
    status_bits.append(f'<span class="status-chip">{html.escape(str(config.get("window_mode", "24h")))} window</span>')
    status_bits.append(f'<span class="status-chip">{html.escape(str(config.get("layout_mode", "framed")))} layout</span>')
    if error:
        alert = f'<div class="alert error">{html.escape(error)}</div>'
    elif message:
        alert = f'<div class="alert success">{html.escape(message)}</div>'
    else:
        alert = ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AvianVisitors Frame Settings</title>
  <link rel="stylesheet" href="/static/style.css">
  <script defer src="/static/app.js"></script>
</head>
<body>
  <div class="shell">
    <header class="hero card">
      <div>
        <p class="eyebrow">AvianVisitors</p>
        <h1>Frame settings</h1>
        <p class="lede">Adjust the frame's 24h/today window, fullscreen layout, title placement, and display tuning from the browser.</p>
      </div>
      <div class="hero-meta">
        <div class="status-row">{"".join(status_bits)}</div>
        <p class="small">Saved to <code>~/.birdframe/config.toml</code>.</p>
      </div>
    </header>
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
        if parsed.path == "/static/style.css":
            self._send_static(self.app.css_path, "text/css; charset=utf-8")
            return
        if parsed.path == "/static/app.js":
            self._send_static(self.app.js_path, "application/javascript; charset=utf-8")
            return
        if parsed.path == "/api/config":
            self._send_json(self.app.current_config())
            return
        if parsed.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        config = self.app.current_config()
        message = parse_qs(parsed.query).get("message", [""])[0]
        error = parse_qs(parsed.query).get("error", [""])[0]
        body = render_page(config, message=message, error=error).encode("utf-8")
        self._send_html(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/save":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length).decode("utf-8")
        form = parse_qs(payload, keep_blank_values=True)
        action = form.get("action", ["save"])[0]
        try:
            self.app.save_from_form(form)
            if action == "save_refresh":
                self.app.trigger_refresh()
        except Exception as exc:  # noqa: BLE001
            self._redirect("/?error=" + quote_plus(str(exc)), code=HTTPStatus.SEE_OTHER)
            return
        self._redirect("/?message=" + quote_plus("Saved settings"), code=HTTPStatus.SEE_OTHER)

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
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        return load_config(self.config_path)

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