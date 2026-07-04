#!/usr/bin/env python3
"""Frame-Pi client: turn a collage screenshot into Inky panel pixels.

Runs on the Pi Zero W on a systemd timer. Each run it decides whether a
refresh is worth it (the species set or call-count brackets changed, and it
is not quiet hours), then crops the title and collage from the screenshot,
centres and mats them, and pushes the result to the Inky Impression 13.3".
``--preview out.png`` writes an approximate 6-ink dither instead, so the
look can be checked on any machine without the panel.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import io
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from datetime import datetime
from urllib.parse import urlencode

from PIL import Image, ImageChops, ImageDraw, ImageFont

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

PANEL_W, PANEL_H = 1200, 1600  # portrait; the panel itself is 1600x1200

# Approximate Spectra-6 inks, used only for --preview. On hardware the Inky
# library maps to the panel's real palette.
SPECTRA6 = [(236, 234, 223), (26, 26, 28), (165, 60, 56),
            (198, 176, 74), (49, 71, 130), (58, 110, 72)]

DEFAULTS = {
    "base_url": "http://birdnet.local",
    "species_source": "",   # "" = the recent API at base_url; "birdweather" = BirdWeather near a ZIP
    "zip": "",              # BirdWeather ZIP / postal code (with species_source = "birdweather")
    "bw_days": 7,           # BirdWeather lookback window, in days
    "bw_country": "us",     # geocoder country for the ZIP
    "hours": 24,
    "window_mode": "24h",   # "24h" (rolling) or "today" (since local midnight)
    "toggle_button": "a",   # Inky button (a/b/c/d) checked each run; hold to toggle mode
    "status_text_24h": "24H",      # top-right status label when mode is 24h
    "status_text_today": "TODAY",  # top-right status label when mode is today
    "image": "",            # local PNG written by the shooter
    "image_url": "",        # or a published screenshot URL
    "shoot": False,         # or capture inline (needs a browser; the Zero 2 W handles it)
    "shoot_title": None, "shoot_subtitle": None,
    "shoot_subtitle_24h": None,    # optional mode override for large headline text
    "shoot_subtitle_today": None,  # optional mode override for large headline text
    "shoot_headline_px": 42, "shoot_eyebrow_px": 18, "shoot_lowercase": False,
    "shoot_mat": 0.04, "shoot_small_floor": 0.04, "shoot_count_exp": 0.65,
    "shoot_collage_vh": 52,
    "shoot_title_gap_px": 14,
    "shoot_group_y": "center",      # flex-start | center | flex-end
    "shoot_collage_lock_center": False,
    "shoot_title_detached": False,
    "shoot_title_offset_y_px": 0,
    "shoot_pad_top_px": None,
    "shoot_pad_side_px": None,
    "shoot_pad_bottom_px": None,
    "shoot_mat_full": 0.0,         # when layout_mode=full, remove stage padding
    "shoot_collage_vh_full": 84,   # when layout_mode=full, make collage dominate
    "shoot_headline_px_full": 32,  # when layout_mode=full, keep title compact
    "shoot_eyebrow_px_full": 14,
    "shoot_title_gap_px_full": 6,
    "shoot_group_y_full": "center",  # flex-start | center | flex-end
    "shoot_collage_lock_center_full": True,
    "shoot_title_detached_full": True,
    "shoot_title_offset_y_px_full": 0,
    "shoot_pad_top_px_full": 56,
    "shoot_pad_side_px_full": 0,
    "shoot_pad_bottom_px_full": 0,
    "layout_mode": "framed",  # "framed" (A5 mat layout) or "full" (edge-to-edge panel)
    "mat": 0.0,             # extra global shrink of the content inside the A5 opening
    "rotate": 90,           # 90 or 270 if the frame hangs the other way up
    "saturation": 0.6,
    "panel": "",            # "el133uf1" forces the 13.3" driver if auto() fails
    "quiet_start": 0, "quiet_end": 0,    # 0/0 = no quiet hours
    "heal_hours": 24,
    "state": "~/.birdframe/state.json",
    "cache": "~/.birdframe",
    "timeout": 45,
    "basic_user": None, "basic_pass": None,
}

BUTTON_PINS = {"a": 5, "b": 6, "c": 16, "d": 24}


def _auth(cfg):
    if not cfg.get("basic_user"):
        return None
    raw = f"{cfg['basic_user']}:{cfg.get('basic_pass') or ''}".encode()
    return "Basic " + base64.b64encode(raw).decode()


# --- change detection -------------------------------------------------------
def slugify(sci):
    return re.sub(r"[^a-z0-9]+", "-", sci.lower()).strip("-")


def _bucket(n):
    for i, edge in enumerate((1, 2, 5, 15, 40, 100, 300, 1000)):
        if n <= edge:
            return i
    return 8


def _normalize_window_mode(mode):
    mode = str(mode or "24h").strip().lower()
    return "today" if mode == "today" else "24h"


def _normalize_layout_mode(mode):
    mode = str(mode or "framed").strip().lower()
    return "full" if mode == "full" else "framed"


def _hours_since_midnight(now_local=None):
    now_local = now_local or datetime.now()
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    secs = max(0, (now_local - midnight).total_seconds())
    # API expects integer hours. Round up so 00:01 still means "today".
    return max(1, int((secs + 3599) // 3600))


def _fetch_recent_url(base, timeout, auth=None, **params):
    url = f"{base.rstrip('/')}/avian/api/birdnet-api.php?{urlencode({**params, 'action': 'recent'})}"
    req = urllib.request.Request(url, headers={"User-Agent": "AvianVisitors-frame/1.0"})
    if auth:
        req.add_header("Authorization", auth)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000)).get("species", [])


def fetch_recent(base, hours, timeout, auth=None, window_mode="24h"):
    mode = _normalize_window_mode(window_mode)
    if mode == "today":
        try:
            return _fetch_recent_url(base, timeout, auth, today=1)
        except Exception:
            # Backward compatibility for older API deployments without today=1.
            return _fetch_recent_url(base, timeout, auth, hours=_hours_since_midnight())
    return _fetch_recent_url(base, timeout, auth, hours=max(1, int(hours)))


def signature(species):
    items = sorted((slugify(s["sci"]), _bucket(int(s.get("n") or 1))) for s in species)
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]


def fetch_species(cfg, auth=None):
    """The species list the signature is built from: the BirdNET-Pi recent API
    by default, or BirdWeather's recent detections near a ZIP when
    species_source = "birdweather"."""
    if cfg.get("species_source") == "birdweather":
        import birdweather
        return birdweather.species_for_zip(cfg["zip"], country=cfg["bw_country"], days=cfg["bw_days"])
    return fetch_recent(cfg["base_url"], cfg["hours"], cfg["timeout"], auth,
                        window_mode=cfg.get("_window_mode", cfg.get("window_mode", "24h")))


# --- image ------------------------------------------------------------------
def get_image(src, timeout, auth=None):
    if re.match(r"^https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "AvianVisitors-frame/1.0"})
        if auth:
            req.add_header("Authorization", auth)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Image.open(io.BytesIO(r.read(20_000_000))).convert("RGB")
    return Image.open(os.path.expanduser(src)).convert("RGB")


def fit_panel(img):
    if img.size != (PANEL_W, PANEL_H):
        img = img.resize((PANEL_W, PANEL_H), Image.LANCZOS)
    return img


def _paper(img):
    """Median of the four corners, robust to a stray inked corner."""
    w, h = img.size
    px = (img.getpixel(p) for p in ((4, 4), (w - 5, 4), (4, h - 5), (w - 5, h - 5)))
    return tuple(int(statistics.median(c)) for c in zip(*px))


# The mat opening is an A5 rectangle (1 : sqrt(2)) centred in the panel; the
# content floats inside it with `mat` of inner whitespace.
A5_H = PANEL_H * 0.7071           # A5 is 1/sqrt(2) of the panel height
A5_W = A5_H / 1.41421             # A5 aspect 1 : sqrt(2)


def _place(content, paper, mat):
    s = min(A5_W * (1 - mat) / content.width, A5_H * (1 - mat) / content.height)
    nw, nh = max(1, round(content.width * s)), max(1, round(content.height * s))
    content = content.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (PANEL_W, PANEL_H), paper)
    canvas.paste(content, ((PANEL_W - nw) // 2, (PANEL_H - nh) // 2))
    return canvas


def _region_bbox(img, paper, y0, y1):
    region = img.crop((0, y0, img.width, y1))
    diff = ImageChops.difference(region, Image.new("RGB", region.size, paper))
    bb = diff.convert("L").point(lambda p: 255 if p > 34 else 0).getbbox()
    return None if not bb else (bb[0], y0 + bb[1], bb[2], y0 + bb[3])


def _scale_w(img, target_w):
    s = target_w / img.width
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


def _scale_h(img, target_h):
    s = target_h / img.height
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


def _centroid_x(img, paper):
    """Horizontal centre of ink weight (what the eye reads as centred)."""
    m = ImageChops.difference(img, Image.new("RGB", img.size, paper)).convert("L")
    cols = list(m.resize((img.width, 1), Image.BOX).tobytes())
    total = sum(cols) or 1
    return sum(x * v for x, v in enumerate(cols)) / total


# Content layout inside the A5 opening: the title and collage are sized
# independently (as fractions of the opening width), so tuning one leaves the
# other untouched. gap is a fraction of the opening height.
TITLE_H_FRAC, COLLAGE_FRAC, GAP_FRAC = 0.065, 0.66, 0.1


def mat_and_center(img, mat, empty=False):
    """Crop the title and collage, size each to a fraction of the A5 opening,
    stack with a gap, and centre on the panel."""
    img = img.convert("RGB")
    paper = _paper(img)
    mask = ImageChops.difference(img, Image.new("RGB", img.size, paper))
    mask = mask.convert("L").point(lambda p: 255 if p > 34 else 0)
    full = mask.getbbox()
    if not full:
        return img
    levels = list(mask.resize((1, img.height), Image.BOX).tobytes())  # per-row content
    top, bot = full[1], full[3]
    split, run = None, 0
    for y in range(top, bot):
        if levels[y] <= 2:
            run += 1
            if run >= 60:  # split below the headline; a 60px band clears the ~30px eyebrow/headline gap so the title stays whole
                cy = y
                while cy < bot and levels[cy] <= 2:
                    cy += 1
                split = (y - run + 1, cy)
                break
        else:
            run = 0
    tb = _region_bbox(img, paper, top, split[0]) if split else None
    cb = _region_bbox(img, paper, split[1], bot + 1) if split else None
    box_w, box_h = A5_W * (1 - mat), A5_H * (1 - mat)
    # No birds yet: hold the title where a full collage would put it and float
    # the one-line note where the birds would be, so a birdless frame reads like
    # a full one with the collage removed, not a title card drifted to the middle.
    if empty and tb and cb:
        title = _scale_h(img.crop(tb), box_h * TITLE_H_FRAC)
        note = _scale_w(img.crop(cb), box_w * 0.30)
        gap = round(box_h * GAP_FRAC)
        region = box_w * COLLAGE_FRAC  # stand in for a usual-size collage
        cw = max(title.width, note.width)
        comp = Image.new("RGB", (cw, round(title.height + gap + region)), paper)
        comp.paste(title, ((cw - title.width) // 2, 0))
        ny = title.height + gap + max(0, (round(region) - note.height) // 2)
        comp.paste(note, ((cw - note.width) // 2, ny))
        canvas = Image.new("RGB", (PANEL_W, PANEL_H), paper)
        canvas.paste(comp, ((PANEL_W - comp.width) // 2, (PANEL_H - comp.height) // 2))
        return canvas
    if not (tb and cb):
        return _place(img.crop(full), paper, mat)
    title = _scale_h(img.crop(tb), box_h * TITLE_H_FRAC)
    gap = round(box_h * GAP_FRAC)
    # Size the collage to fill the room left under the fixed-size title,
    # binding on whichever of width or remaining height runs out first, so the
    # title stays a consistent size whether the collage is tall or compact
    # instead of ballooning when the collage happens to be short.
    coll = img.crop(cb)
    cs = min(box_w * COLLAGE_FRAC / coll.width, (box_h - title.height - gap) / coll.height)
    collage = coll.resize((max(1, round(coll.width * cs)), max(1, round(coll.height * cs))), Image.LANCZOS)
    ccx = _centroid_x(collage, paper)  # centre the collage by ink weight, not bbox
    half = max(ccx, collage.width - ccx)
    # A wildly off-centre collage can push the centroid-mirrored width (2*half)
    # past the A5 opening; shrink only the collage, never the fixed-size title,
    # so nothing spills under the physical mat.
    if 2 * half > box_w:
        s = box_w / (2 * half)
        collage = collage.resize((max(1, round(collage.width * s)), max(1, round(collage.height * s))), Image.LANCZOS)
        ccx = round(ccx * s)
        half = max(ccx, collage.width - ccx)
    cw = round(max(title.width, 2 * half))
    comp = Image.new("RGB", (cw, title.height + gap + collage.height), paper)
    comp.paste(title, ((cw - title.width) // 2, 0))
    comp.paste(collage, (round(cw / 2 - ccx), title.height + gap))
    canvas = Image.new("RGB", (PANEL_W, PANEL_H), paper)
    canvas.paste(comp, ((PANEL_W - comp.width) // 2, (PANEL_H - comp.height) // 2))
    return canvas


def quantize_spectra6(img):
    pal = Image.new("P", (1, 1))
    flat = [c for ink in SPECTRA6 for c in ink]
    flat += list(SPECTRA6[0]) * ((768 - len(flat)) // 3)  # pad the 256-entry palette with paper
    pal.putpalette(flat[:768])
    return img.convert("RGB").quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG).convert("RGB")


def _draw_mat_box(img):
    """Dev aid: outline the A5 mat opening so the matte and centring show."""
    x0, y0 = round((PANEL_W - A5_W) / 2), round((PANEL_H - A5_H) / 2)
    ImageDraw.Draw(img).rectangle((x0, y0, PANEL_W - x0 - 1, PANEL_H - y0 - 1),
                                  outline=(170, 60, 56), width=2)


def _status_label(cfg):
    if cfg.get("species_source") == "birdweather":
        return ""
    mode = _normalize_window_mode(cfg.get("_window_mode", cfg.get("window_mode", "24h")))
    if mode == "today":
        return str(cfg.get("status_text_today", "TODAY") or "")
    return str(cfg.get("status_text_24h", "24H") or "")


def _layout_image(cfg, img, species):
    mode = _normalize_layout_mode(cfg.get("layout_mode", "framed"))
    if mode == "full":
        return img
    return mat_and_center(img, cfg["mat"], empty=(species == []))


def _shoot_kwargs(cfg):
    mode = _normalize_layout_mode(cfg.get("layout_mode", "framed"))
    if mode == "full":
        return {
            "headline_px": cfg["shoot_headline_px_full"],
            "eyebrow_px": cfg["shoot_eyebrow_px_full"],
            "mat": cfg["shoot_mat_full"],
            "collage_vh": cfg["shoot_collage_vh_full"],
            "title_gap_px": cfg["shoot_title_gap_px_full"],
            "group_y": cfg["shoot_group_y_full"],
            "collage_lock_center": cfg["shoot_collage_lock_center_full"],
            "title_detached": cfg["shoot_title_detached_full"],
            "title_offset_y_px": cfg["shoot_title_offset_y_px_full"],
            "pad_top_px": cfg["shoot_pad_top_px_full"],
            "pad_side_px": cfg["shoot_pad_side_px_full"],
            "pad_bottom_px": cfg["shoot_pad_bottom_px_full"],
        }
    return {
        "headline_px": cfg["shoot_headline_px"],
        "eyebrow_px": cfg["shoot_eyebrow_px"],
        "mat": cfg["shoot_mat"],
        "collage_vh": cfg["shoot_collage_vh"],
        "title_gap_px": cfg["shoot_title_gap_px"],
        "group_y": cfg["shoot_group_y"],
        "collage_lock_center": cfg["shoot_collage_lock_center"],
        "title_detached": cfg["shoot_title_detached"],
        "title_offset_y_px": cfg["shoot_title_offset_y_px"],
        "pad_top_px": cfg["shoot_pad_top_px"],
        "pad_side_px": cfg["shoot_pad_side_px"],
        "pad_bottom_px": cfg["shoot_pad_bottom_px"],
    }


def _draw_status_label(img, text):
    if not text:
        return
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    # Draw a tiny, quiet mode chip in the top-right so the user can confirm
    # today's calendar window vs rolling last-24h at a glance.
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    tw, th = x1 - x0, y1 - y0
    pad = 4
    right, top = img.width - 12, 8
    box = (right - tw - 2 * pad, top, right, top + th + 2 * pad)
    draw.rectangle(box, fill=(236, 234, 223))
    draw.text((box[0] + pad, box[1] + pad), text, font=font, fill=(70, 70, 70))


# --- hardware ---------------------------------------------------------------
def push_panel(img, rotate, saturation, panel=""):
    """Rotate to the panel's landscape buffer and push. Lazy import so this
    module still loads on a machine without the Inky library."""
    if rotate not in (90, 270):
        print(f"rotate must be 90 or 270, not {rotate}; using 90", file=sys.stderr)
        rotate = 90
    if panel == "el133uf1":
        from inky.inky_el133uf1 import Inky
        dev = Inky(resolution=(1600, 1200))
    else:
        from inky.auto import auto
        dev = auto()
    buf = img.rotate(rotate, expand=True)
    if buf.size != (dev.width, dev.height):
        buf = buf.resize((dev.width, dev.height), Image.LANCZOS)
    kw = {"saturation": saturation} if "saturation" in inspect.signature(dev.set_image).parameters else {}
    dev.set_image(buf, **kw)
    dev.show()


# --- state ------------------------------------------------------------------
def load_state(path):
    try:
        with open(os.path.expanduser(path)) as f:
            return json.load(f)
    except Exception:
        return {"signature": None, "last_refresh": 0, "window_mode": None, "last_toggle": 0}


def save_state(path, sig, when, state=None):
    state = state or {}
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Use a unique temp file per writer to avoid collisions when the timer,
    # button service, and a manual run overlap.
    tmp = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
    payload = {
        "signature": sig,
        "last_refresh": when,
        "window_mode": state.get("window_mode"),
        "last_toggle": state.get("last_toggle", 0),
    }
    with open(tmp, "w") as f:
        json.dump(payload, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic: a power cut can't leave a half-written file


def _apply_button_toggle(cfg, state):
    cur = _normalize_window_mode(state.get("window_mode") or cfg.get("window_mode", "24h"))
    state["window_mode"] = cur
    btn = str(cfg.get("toggle_button") or "").strip().lower()
    if btn not in BUTTON_PINS:
        return cur
    # The dedicated watcher service owns the GPIO pin and sets _button_pressed
    # before calling run(). Timer/manual runs should never touch GPIO directly,
    # otherwise they'd collide with the watcher and fail with "GPIO busy".
    if not cfg.get("_button_pressed"):
        return cur
    now = time.time()
    if now - float(state.get("last_toggle") or 0) < 2:
        return cur
    nxt = "today" if cur == "24h" else "24h"
    state["window_mode"] = nxt
    state["last_toggle"] = now
    print(f"window mode toggled to {nxt} via button {btn.upper()}")
    return nxt


def in_quiet_hours(cfg, hour):
    s, e = cfg["quiet_start"], cfg["quiet_end"]
    if s == e:
        return False
    return s <= hour < e if s < e else hour >= s or hour < e


def _mode_subtitle(cfg):
    mode = _normalize_window_mode(cfg.get("_window_mode", cfg.get("window_mode", "24h")))
    if mode == "today":
        override = cfg.get("shoot_subtitle_today")
    else:
        override = cfg.get("shoot_subtitle_24h")
    return cfg.get("shoot_subtitle") if override is None else override


# --- run --------------------------------------------------------------------
def obtain_image(cfg, species=None):
    if cfg.get("species_source") == "birdweather":
        from shoot import shoot_birdweather
        if species is None:  # gate skipped (--no-signature): fetch the list to render
            species = fetch_species(cfg, _auth(cfg))
        out = os.path.join(os.path.expanduser(cfg["cache"]), "frame.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        look = _shoot_kwargs(cfg)
        shoot_birdweather(out, species, title=cfg["shoot_title"], subtitle=_mode_subtitle(cfg),
                          headline_px=look["headline_px"], eyebrow_px=look["eyebrow_px"],
                          mat=look["mat"], collage_vh=look["collage_vh"],
                          title_gap_px=look["title_gap_px"], group_y=look["group_y"],
                          collage_lock_center=look["collage_lock_center"],
                          title_detached=look["title_detached"],
                          title_offset_y_px=look["title_offset_y_px"],
                          pad_top_px=look["pad_top_px"], pad_side_px=look["pad_side_px"],
                          pad_bottom_px=look["pad_bottom_px"],
                          timeout_ms=cfg["timeout"] * 1000)
        return Image.open(out).convert("RGB")
    if cfg["shoot"]:
        from shoot import shoot
        out = os.path.join(os.path.expanduser(cfg["cache"]), "shot.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        mode = _normalize_window_mode(cfg.get("_window_mode", cfg.get("window_mode", "24h")))
        look = _shoot_kwargs(cfg)
        shoot(cfg["base_url"], out, title=cfg["shoot_title"], subtitle=_mode_subtitle(cfg),
              headline_px=look["headline_px"], eyebrow_px=look["eyebrow_px"],
              lowercase=cfg["shoot_lowercase"], mat=look["mat"], collage_vh=look["collage_vh"],
              title_gap_px=look["title_gap_px"], group_y=look["group_y"],
              collage_lock_center=look["collage_lock_center"],
              title_detached=look["title_detached"],
              title_offset_y_px=look["title_offset_y_px"],
              pad_top_px=look["pad_top_px"], pad_side_px=look["pad_side_px"],
              pad_bottom_px=look["pad_bottom_px"],
              small_floor=cfg["shoot_small_floor"], count_exp=cfg["shoot_count_exp"], timeout_ms=cfg["timeout"] * 1000,
              window_hours=cfg["hours"] if mode == "24h" else None,
              window_today=(mode == "today"),
              user=cfg["basic_user"], password=cfg["basic_pass"])
        return Image.open(out).convert("RGB")
    src = cfg["image_url"] or cfg["image"]
    if not src:
        raise ValueError("set image, image_url, or shoot in config")
    return get_image(src, cfg["timeout"], _auth(cfg))


def run(cfg, preview=None, force=False, use_signature=True, mat_box=False):
    now = time.time()
    state = load_state(cfg["state"])
    before_mode = _normalize_window_mode(state.get("window_mode") or cfg.get("window_mode", "24h"))
    cfg["_window_mode"] = _apply_button_toggle(cfg, state)
    cfg.pop("_button_pressed", None)
    toggled = cfg["_window_mode"] != before_mode
    sig = None
    species = None
    if use_signature:
        try:
            species = fetch_species(cfg, _auth(cfg))
            sig = signature(species)
        except Exception as e:
            print(f"signature fetch failed: {e}", file=sys.stderr)  # treat as no change
    heal_due = now - state.get("last_refresh", 0) >= cfg["heal_hours"] * 3600
    changed = toggled or (not use_signature) or (sig is not None and sig != state.get("signature"))
    if not force and not preview:
        if in_quiet_hours(cfg, datetime.now().hour):
            print("quiet hours; skip")
            return
        if not changed and not heal_due:
            print("no change; skip")
            return
        if toggled:
            print("refresh: mode toggled")
        else:
            print("refresh:", "changed" if changed else "heal")

    try:
        img = fit_panel(obtain_image(cfg, species))
    except Exception as e:
        print(f"could not get image: {e}", file=sys.stderr)  # keep last panel image
        save_state(cfg["state"], state.get("signature"), state.get("last_refresh", 0), state)
        return
    img = _layout_image(cfg, img, species)
    _draw_status_label(img, _status_label(cfg))
    if preview:
        out = quantize_spectra6(img)
        if mat_box:
            _draw_mat_box(out)
        out.save(preview)
        print(f"wrote preview {preview}")
        save_state(cfg["state"], state.get("signature"), state.get("last_refresh", 0), state)
        return
    try:
        push_panel(img, cfg["rotate"], cfg["saturation"], cfg.get("panel", ""))
    except Exception as e:
        print(f"panel push failed: {e}", file=sys.stderr)
        save_state(cfg["state"], state.get("signature"), state.get("last_refresh", 0), state)
        return
    save_state(cfg["state"], sig if sig is not None else state.get("signature"), now, state)
    print("panel updated")


def watch_button(cfg):
    btn = str(cfg.get("toggle_button") or "").strip().lower()
    if btn not in BUTTON_PINS:
        raise ValueError("set toggle_button to one of: a, b, c, d")
    try:
        from gpiozero import Button
    except Exception as e:
        raise RuntimeError(f"gpio button support unavailable: {e}")

    button = Button(BUTTON_PINS[btn], pull_up=True, bounce_time=0.05)
    print(f"watching button {btn.upper()} for immediate refreshes")
    try:
        while True:
            button.wait_for_press()
            # Run immediately while the button is still down; run() will also
            # toggle the 24h/today mode and force a redraw path for that change.
            print(f"button {btn.upper()} pressed; refreshing now")
            cfg["_button_pressed"] = True
            run(cfg, force=True, use_signature=False)
            button.wait_for_release(timeout=2)
    finally:
        button.close()


def load_config(path):
    cfg = dict(DEFAULTS)
    if path:
        with open(os.path.expanduser(path), "rb") as f:
            cfg.update(tomllib.load(f))
    cfg["window_mode"] = _normalize_window_mode(cfg.get("window_mode", "24h"))
    cfg["layout_mode"] = _normalize_layout_mode(cfg.get("layout_mode", "framed"))
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Push the collage screenshot to the Inky panel.")
    ap.add_argument("--config")
    ap.add_argument("--base-url")
    ap.add_argument("--image")
    ap.add_argument("--image-url")
    ap.add_argument("--preview", help="write a 6-ink preview PNG instead of pushing")
    ap.add_argument("--rotate", type=int)
    ap.add_argument("--watch-button", action="store_true",
                    help="watch the configured button and refresh immediately on press")
    ap.add_argument("--force", action="store_true", help="refresh even if unchanged")
    ap.add_argument("--no-signature", action="store_true", help="skip change detection")
    ap.add_argument("--mat-box", action="store_true", help="dev: outline the mat window on the preview")
    args = ap.parse_args()

    cfg = load_config(args.config)
    for key in ("base_url", "image", "image_url"):
        val = getattr(args, key)
        if val:
            cfg[key] = val
    if args.rotate is not None:
        cfg["rotate"] = args.rotate
    if args.watch_button:
        watch_button(cfg)
        return
    run(cfg, preview=args.preview, force=args.force, use_signature=not args.no_signature, mat_box=args.mat_box)


if __name__ == "__main__":
    main()
