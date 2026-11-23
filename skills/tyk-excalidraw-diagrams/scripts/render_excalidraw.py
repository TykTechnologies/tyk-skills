#!/usr/bin/env python3
"""
Rasterise a .excalidraw file to PNG so you can LOOK at it before shipping it.

This is deliberately a rough approximation - fonts are Arial rather than
Excalidraw's hand-drawn faces, strokes are clean rather than sketchy, rounded lines
are smoothed with a Catmull-Rom spline, and dashed styles render solid. Closed
`line` elements are filled as polygons, so composed glyphs render as drawn. That is fine: it catches the things that actually
go wrong in generated diagrams - overlapping labels, text escaping its box,
arrows landing in the wrong place, images that failed to embed, content drifting
outside a frame. It cannot tell you how the arrow *curvature* will look, so
never rely on it to check roundness; keep roundness null instead.

    python render_excalidraw.py in.excalidraw out.png [--scale 0.3] [--crop x0,y0,x1,y1]

Scale guide: 0.13 for a whole multi-panel canvas, 0.30-0.35 for one panel
(crop to it), 0.5+ to inspect a detail. Text below ~4px effective size is
skipped, so if labels look missing, zoom in with --crop before believing it.
"""
import argparse
import base64
import io
import json
import math

from PIL import Image, ImageDraw, ImageFont

FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
_fonts = {}


def font(size, bold=False):
    key = (int(size), bold)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR,
                                             max(6, int(size)))
        except OSError:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def rgba(color, opacity):
    if color in ("transparent", None, ""):
        return None
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (int(255 * opacity / 100),)
    except ValueError:
        return None


def corner_radius(e):
    """Excalidraw's getCornerRadius: proportional (25% of the short side) for
    roundness types 1 and 2; for type 3 a fixed radius (`value`, default 32) unless
    the short side is under four times that, then proportional."""
    r = e.get("roundness")
    if not r:
        return 0
    short = min(e["width"], e["height"])
    if r.get("type") in (1, 2):
        return short * 0.25
    fixed = r.get("value") or 32
    return short * 0.25 if short <= fixed / 0.25 else fixed


def smooth(pts, closed, steps=12):
    """Catmull-Rom curve through `pts` - close to how Excalidraw draws a line or
    arrow whose roundness is set."""
    if len(pts) < 3:
        return pts
    ring = pts[:-1] if closed else pts
    n = len(ring)
    out = []
    for i in range(n if closed else n - 1):
        if closed:
            p0, p1, p2, p3 = (ring[(i + k) % n] for k in (-1, 0, 1, 2))
        else:
            p0, p1, p2, p3 = (ring[max(0, min(n - 1, i + k))] for k in (-1, 0, 1, 2))
        for s in range(steps):
            t = s / steps
            out.append(tuple(
                0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t
                       + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t * t
                       + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t ** 3)
                for j in (0, 1)))
    out.append(ring[0] if closed else ring[-1])
    return out


def extent(e):
    """Scene bounding box. Linear elements are measured from their points, which may
    sit left of or above the element's x/y."""
    if e.get("points"):
        xs = [e["x"] + q[0] for q in e["points"]]
        ys = [e["y"] + q[1] for q in e["points"]]
        return min(xs), min(ys), max(xs), max(ys)
    return e["x"], e["y"], e["x"] + e["width"], e["y"] + e["height"]


def render(src, out, scale=0.22, crop=None):
    doc = json.load(open(src))
    els = [e for e in doc["elements"] if not e.get("isDeleted")]
    files = doc.get("files", {})
    if not els:
        raise SystemExit("no elements to render")

    boxes = [extent(e) for e in els]
    xs = [b[0] for b in boxes] + [b[2] for b in boxes]
    ys = [b[1] for b in boxes] + [b[3] for b in boxes]
    if crop:
        minx, miny, maxx, maxy = crop
    else:
        minx, miny, maxx, maxy = min(xs) - 60, min(ys) - 60, max(xs) + 60, max(ys) + 60

    W, H = int((maxx - minx) * scale), int((maxy - miny) * scale)
    img = Image.new("RGB", (max(1, W), max(1, H)), "white")
    dr = ImageDraw.Draw(img, "RGBA")

    def T(x, y):
        return ((x - minx) * scale, (y - miny) * scale)

    cache = {}

    def get_image(fid):
        if fid not in cache:
            try:
                blob = files[fid]["dataURL"].split(",", 1)[1]
                cache[fid] = Image.open(io.BytesIO(base64.b64decode(blob))).convert("RGBA")
            except Exception:
                cache[fid] = None
        return cache[fid]

    labels = {e["containerId"]: e for e in els
              if e["type"] == "text" and e.get("containerId")}
    missing_icons = 0

    for e in els:
        kind = e["type"]
        x0, y0 = T(e["x"], e["y"])
        x1, y1 = T(e["x"] + e["width"], e["y"] + e["height"])
        op = e.get("opacity", 100)
        fill = rgba(e.get("backgroundColor"),
                    op * (0.4 if e.get("fillStyle") == "hachure" else 1))
        stroke = rgba(e.get("strokeColor"), op) or (0, 0, 0, 255)
        sw = max(1, int(e.get("strokeWidth", 2) * scale * 1.6))

        if kind == "rectangle":
            radius = max(1, corner_radius(e) * scale) if e.get("roundness") else 0
            if radius:
                dr.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill,
                                     outline=stroke, width=sw)
            else:
                dr.rectangle([x0, y0, x1, y1], fill=fill, outline=stroke, width=sw)
        elif kind == "ellipse":
            dr.ellipse([x0, y0, x1, y1], fill=fill, outline=stroke, width=sw)
        elif kind == "diamond":
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            dr.polygon([(cx, y0), (x1, cy), (cx, y1), (x0, cy)], fill=fill,
                       outline=stroke)
        elif kind == "image":
            im = get_image(e.get("fileId"))
            if im:
                w, h = max(1, int(x1 - x0)), max(1, int(y1 - y0))
                sized = im.resize((w, h))
                img.paste(sized, (int(x0), int(y0)), sized)
            else:
                missing_icons += 1
                dr.rectangle([x0, y0, x1, y1], outline=(220, 0, 0, 255), width=2)
        elif kind in ("arrow", "line"):
            pts = [T(e["x"] + p[0], e["y"] + p[1]) for p in e.get("points", [])]
            # A line whose last point returns to its first is a closed polygon,
            # which Excalidraw fills with its background colour.
            closed = (kind == "line" and len(pts) > 3
                      and e["points"][0] == e["points"][-1])
            if e.get("roundness") and len(pts) > 2:
                pts = smooth(pts, closed)
            if closed and fill:
                dr.polygon(pts, fill=fill)
            if len(pts) > 1:
                dr.line(pts, fill=stroke, width=sw, joint="curve")
                if e.get("endArrowhead"):
                    (ax, ay), (bx, by) = pts[-2], pts[-1]
                    ang = math.atan2(by - ay, bx - ax)
                    L = max(6, 9 * scale / 0.22)
                    dr.polygon([(bx, by),
                                (bx - L * math.cos(ang - 0.4), by - L * math.sin(ang - 0.4)),
                                (bx - L * math.cos(ang + 0.4), by - L * math.sin(ang + 0.4))],
                               fill=stroke)
        elif kind == "text" and not e.get("containerId"):
            fs = e.get("fontSize", 20) * scale
            if fs >= 4:
                f = font(fs, fs > 12)
                align = e.get("textAlign", "left")
                tx = x0
                if align in ("center", "right"):
                    bb = dr.multiline_textbbox((0, 0), e["text"], font=f, align=align)
                    slack = (x1 - x0) - (bb[2] - bb[0])
                    tx = x0 + (slack / 2 if align == "center" else slack)
                ty = y0
                if e.get("verticalAlign") == "middle":
                    bb = dr.multiline_textbbox((0, 0), e["text"], font=f)
                    ty = (y0 + y1) / 2 - (bb[3] + bb[1]) / 2
                dr.multiline_text((tx, ty), e["text"], font=f, fill=stroke, align=align)

        lbl = labels.get(e["id"])
        if lbl:
            fs = lbl.get("fontSize", 20) * scale
            if fs >= 4:
                f = font(fs)
                lines = lbl["text"].split("\n")
                for j, line in enumerate(lines):
                    bb = dr.textbbox((0, 0), line, font=f)
                    cx = (x0 + x1) / 2 - (bb[2] - bb[0]) / 2
                    cy = (y0 + y1) / 2 - (bb[3] - bb[1]) * len(lines) / 2 + j * fs * 1.25
                    dr.text((cx, cy), line, font=f,
                            fill=rgba(lbl.get("strokeColor"), 100) or (0, 0, 0, 255))

    img.save(out)
    print(f"{out}  {W}x{H}px  {len(els)} elements")
    if missing_icons:
        print(f"WARN: {missing_icons} image element(s) had no embedded file "
              "- drawn as red boxes")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--scale", type=float, default=0.22)
    ap.add_argument("--crop", help="x0,y0,x1,y1 in scene coordinates")
    a = ap.parse_args()
    crop = [float(v) for v in a.crop.split(",")] if a.crop else None
    render(a.src, a.out, a.scale, crop)


if __name__ == "__main__":
    main()
