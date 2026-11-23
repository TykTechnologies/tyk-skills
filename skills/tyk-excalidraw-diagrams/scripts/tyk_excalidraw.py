#!/usr/bin/env python3
"""
tyk_excalidraw - build Tyk architecture diagrams as .excalidraw files.

Why this exists: Excalidraw's on-disk format is verbose and has a few sharp edges
that silently produce broken-looking diagrams (curved "elbow" arrows, arrows that
double back, bound text that has lost its container). This module wraps the format
so you describe a diagram in Tyk's visual language and get a valid file out.

Components are drawn as composed native Excalidraw glyphs - a hexagon for a gateway,
a cylinder for a datastore, a browser window for the Dashboard, a person for a
consumer - built only from rectangles, ellipses, diamonds, lines and text, so a
document carries no embedded images and its `files` map stays empty.

    from tyk_excalidraw import Diagram, BLUE, GREEN, DMZ, TRUSTED

    d = Diagram()
    d.zone(0, 0, 2000, 600, "DMZ / Public zone", "internet-facing", kind=DMZ)
    d.icon("tyk-gateway", 600, 300, label="Tyk-Gateway")
    d.arrow([(200, 300), (520, 300)], BLUE)
    d.save("out.excalidraw")

`save()` validates before writing and raises on anything that would render wrong.
Always rasterise the result with render_excalidraw.py and LOOK at it afterwards.
"""
import json
import math
import random

# ------------------------------------------------------------------ palette
# Flow colours - keep these consistent so a reader learns them once.
BLUE = "#1971c2"     # request / traffic flow
GREEN = "#2f9e44"    # config / control flow (usually dashed)
RED = "#e03131"      # danger: crossing a trust boundary, shared failure domain
PURPLE = "#7048e8"   # state: Redis reads/writes
TEAL = "#087f5b"     # analytics / pump flow
GREY = "#757575"     # de-emphasised annotation

# Zone tints. Excalidraw renders these at low opacity as a background wash.
DMZ = ("#ffc9c9", "DMZ / untrusted")
TRUSTED = ("#a5d8ff", "trusted network")
INTERNAL = ("#b2f2bb", "internal / k8s")
CLOUD = ("#e5dbff", "cloud / SaaS")
NEUTRAL = ("#f8f9fa", "plain")

# Note tones -> (fill, stroke, title colour)
TONES = {
    "bad":  ("#ffe3e3", "#e03131", "#c92a2a"),
    "good": ("#d3f9d8", "#2f9e44", "#2b8a3e"),
    "warn": ("#fff3bf", "#f08c00", "#e67700"),
    "info": ("#dbe4ff", "#1971c2", "#1864ab"),
    "note": ("#e5dbff", "#7048e8", "#5f3dc4"),
}

# Component glyphs -> (form, fill, stroke, monogram).
# The form is a small composed drawing that reads as the thing it stands for, in the
# manner of Excalidraw's public libraries: a hexagon for a gateway, a cylinder for a
# datastore, a browser window for a UI, a person for a consumer. Every form is built
# from native elements only (rectangle, ellipse, diamond, line, text). `None` as the
# monogram means the shape alone carries the meaning and only the caption is drawn.
# The default Tyk components share the violet family. The teal, gold and dark variants
# keep the same form and monogram in the "second estate" colours, for diagrams that
# need two visually distinct estates (on-premises vs cloud, current vs target state).
GLYPHS = {
    # Tyk components
    "tyk-gateway":       ("hexagon",   "#7048e8", "#5f3dc4", "GW"),
    "mdcb":              ("hexagon2",  "#9c36b5", "#862e9c", "MD"),
    "tyk-dashboard":     ("browser",   "#5f3dc4", "#4c2fa0", "DB"),
    "tyk-pump":          ("funnel",    "#845ef7", "#6741d9", "PM"),
    "developer-portal":  ("browser",   "#1971c2", "#1864ab", "PT"),
    "tyk-operator":      ("heptagon",  "#6741d9", "#5f3dc4", "OP"),
    # second-estate variants
    "gateway-teal":      ("hexagon",   "#087f5b", "#06694b", "GW"),
    "pump-teal":         ("funnel",    "#0ca678", "#087f5b", "PM"),
    "dashboard-teal":    ("browser",   "#099268", "#087f5b", "DB"),
    "redis-gold":        ("cylinder",  "#fab005", "#e67700", "RD"),
    "postgres-dark":     ("cylinder",  "#343a40", "#212529", "PG"),
    # datastores
    "redis":             ("cylinder",  "#c92a2a", "#a61e1e", "RD"),
    "postgres":          ("cylinder",  "#1864ab", "#134e85", "PG"),
    "mongodb":           ("cylinder",  "#2b8a3e", "#237032", "MDB"),
    # network and services
    "load-balancer":     ("balancer",  "#0c8599", "#0b7285", "LB"),
    "services":          ("stack",     "#495057", "#343a40", "SVC"),
    "identity-provider": ("shield",    "#364fc7", "#2b3fa0", "IdP"),
    "graphql":           ("graphql",   "#c2255c", "#a61e4d", None),
    "version-control":   ("commits",   "#343a40", "#212529", None),
    "api-definition":    ("document",  "#5c940d", "#4a7a0a", "DEF"),
    "security-policy":   ("padlock",   "#e8590c", "#c4470a", "POL"),
    "key":               ("key",       "#f59f00", "#e67700", None),
    # people
    "api-consumer":      ("person",    "#f76707", "#d9480f", "API"),
    "api-developers":    ("person",    "#e8590c", "#c4470a", "DEV"),
    "api-admin":         ("admin",     "#d9480f", "#b03c0c", "ADM"),
}

# Approximate advance of an upper-case Helvetica glyph, in ems. Deliberately generous:
# Excalidraw wraps bound text that is wider than its container's text area, and a
# monogram split over two lines is unreadable.
_MONO_EM = 0.95


def available_icons(icon_dir=None):
    """Names accepted by Diagram.icon(). `icon_dir` is accepted and ignored."""
    return sorted(GLYPHS)


def _ink_for(fill):
    """White monogram on dark fills, near-black on light ones."""
    r, g, b = (int(fill[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return "#ffffff" if 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.6 else "#1e1e1e"


def _ngon(n, r, start_deg, dy=0.0):
    """Corners of a regular n-gon of radius r around (0, dy), in glyph units."""
    return [(r * math.cos(math.radians(start_deg + 360 * k / n)),
             dy + r * math.sin(math.radians(start_deg + 360 * k / n))) for k in range(n)]


def _star(cu, cv, r_out, r_in):
    """Corners of a five-pointed star centred on (cu, cv), point up."""
    return [(cu + (r_out if k % 2 == 0 else r_in) * math.cos(math.radians(-90 + 36 * k)),
             cv + (r_out if k % 2 == 0 else r_in) * math.sin(math.radians(-90 + 36 * k)))
            for k in range(10)]


class _Pen:
    """Draws one glyph into a Diagram. Coordinates are glyph units: a 140 x 140 box
    centred on the origin, scaled to the requested size. Every element it creates
    joins the glyph's group."""

    def __init__(self, diagram, cx, cy, size, group):
        self.d, self.cx, self.cy, self.s, self.group = diagram, cx, cy, size / 140.0, group

    def _xy(self, u, v):
        return self.cx + u * self.s, self.cy + v * self.s

    def box(self, type_, u0, v0, u1, v1, fill, stroke, radius=None):
        """rectangle / ellipse / diamond spanning (u0, v0)-(u1, v1). `radius` rounds
        a rectangle: True for Excalidraw's adaptive default, a number for a fixed
        corner radius in glyph units."""
        kw = {}
        if type_ == "diamond":
            kw["roundness"] = {"type": 2}
        elif radius is True:
            kw["roundness"] = {"type": 3}
        elif radius:
            kw["roundness"] = {"type": 3, "value": radius * self.s}
        x0, y0 = self._xy(u0, v0)
        return self.d._base(type_, x0, y0, (u1 - u0) * self.s, (v1 - v0) * self.s,
                            backgroundColor=fill, strokeColor=stroke,
                            groupIds=[self.group], **kw)

    def line(self, pts, stroke, curved=False):
        """Open polyline; `curved=True` lets Excalidraw smooth it through the points."""
        return self.d._line([self._xy(u, v) for u, v in pts], stroke,
                            curved=curved, group_ids=[self.group])

    def poly(self, pts, stroke, fill="transparent"):
        """Closed polygon: a line whose last point returns to its first, so
        Excalidraw fills it with `fill`."""
        xy = [self._xy(u, v) for u, v in pts]
        return self.d._line(xy + [xy[0]], stroke, bg=fill, group_ids=[self.group])

    def mono(self, text, u, v, avail, cap, ink):
        """Free monogram centred on (u, v), sized to fit `avail` units on one line."""
        if not text:
            return None
        fs = max(1, round(min(cap, avail / (_MONO_EM * len(text))) * self.s))
        w, h = len(text) * fs * 0.62, fs * 1.25
        x, y = self._xy(u, v)
        return self.d._base("text", x - w / 2, y - h / 2, w, h, strokeColor=ink,
                            fontSize=fs, fontFamily=2, text=text, textAlign="center",
                            verticalAlign="middle", containerId=None, originalText=text,
                            lineHeight=1.25, autoResize=True, groupIds=[self.group])


# One function per form. Each draws back-to-front and returns the main body element,
# which carries customData.tykComponent.
def _form_hexagon(p, fill, stroke, mono, ink):
    body = p.poly(_ngon(6, 70, 0), stroke, fill)               # flat top
    p.mono(mono, 0, 0, 96, 40, ink)
    return body


def _form_hexagon2(p, fill, stroke, mono, ink):
    body = p.poly(_ngon(6, 70, 0), stroke, fill)
    p.poly(_ngon(6, 56, 0), ink)                                 # inner border
    p.mono(mono, 0, 0, 76, 36, ink)
    return body


def _form_balancer(p, fill, stroke, mono, ink):
    body = p.box("diamond", -70, -55, 40, 55, fill, stroke)
    for end in ((66, -30), (70, 0), (66, 30)):                   # fan out = distribute
        p.line([(38, 0), end], stroke)
    p.mono(mono, -15, 0, 52, 30, ink)
    return body


def _form_browser(p, fill, stroke, mono, ink):
    body = p.box("rectangle", -70, -58, 70, 58, fill, stroke, radius=12)
    p.box("rectangle", -70, -58, 70, -30, stroke, stroke, radius=12)   # title bar
    for u in (-56, -44, -32):
        p.box("ellipse", u - 4, -48, u + 4, -40, "#ffffff", "#ffffff")
    p.mono(mono, 0, 14, 110, 40, ink)
    return body


def _form_funnel(p, fill, stroke, mono, ink):
    body = p.poly([(-70, -56), (70, -56), (16, 10), (16, 60), (-16, 60), (-16, 10)],
                  stroke, fill)
    p.mono(mono, 0, -28, 80, 34, ink)
    return body


def _form_heptagon(p, fill, stroke, mono, ink):
    body = p.poly(_ngon(7, 70, -90, dy=3.5), stroke, fill)
    p.box("ellipse", -32, -28.5, 32, 35.5, "transparent", ink)
    p.mono(mono, 0, 3.5, 54, 30, ink)
    return body


def _form_cylinder(p, fill, stroke, mono, ink):
    p.box("ellipse", -56, 40, 56, 70, fill, stroke)                     # bottom cap
    body = p.box("rectangle", -56, -55, 56, 55, fill, fill)             # hides chord
    p.line([(-56, -55), (-56, 55)], stroke)
    p.line([(56, -55), (56, 55)], stroke)
    p.box("ellipse", -56, -70, 56, -40, fill, stroke)                   # top cap
    p.mono(mono, 0, 12, 96, 38, ink)
    return body


def _form_stack(p, fill, stroke, mono, ink):
    p.box("rectangle", -46, -62, 70, 40, fill, stroke, radius=12)       # back
    body = p.box("rectangle", -70, -40, 46, 62, fill, stroke, radius=12)
    p.mono(mono, -12, 11, 100, 34, ink)
    return body


def _form_shield(p, fill, stroke, mono, ink):
    body = p.poly([(0, -70), (60, -52), (57, 8), (34, 46), (0, 70), (-34, 46),
                   (-57, 8), (-60, -52)], stroke, fill)
    p.mono(mono, 0, -6, 88, 34, ink)
    return body


def _form_padlock(p, fill, stroke, mono, ink):
    # Shackle: two concentric arches, so it reads as a band at strokeWidth 2.
    p.line([(-36, -8), (-36, -34), (-25.5, -59.5), (0, -70), (25.5, -59.5), (36, -34),
            (36, -8)], stroke, curved=True)
    p.line([(-22, -8), (-22, -34), (-15.6, -49.6), (0, -56), (15.6, -49.6), (22, -34),
            (22, -8)], stroke, curved=True)
    body = p.box("rectangle", -54, -12, 54, 62, fill, stroke, radius=12)
    p.mono(mono, 0, 25, 92, 32, ink)
    return body


def _form_key(p, fill, stroke, mono, ink):
    p.box("rectangle", 36, 4, 46, 26, fill, stroke)                     # teeth
    p.box("rectangle", 54, 4, 64, 20, fill, stroke)
    p.box("rectangle", -20, -8, 70, 8, fill, stroke)                    # shaft
    bow = p.box("ellipse", -70, -30, -10, 30, fill, stroke)
    p.box("ellipse", -50, -9, -32, 9, "#ffffff", stroke)                # bow hole
    return bow


def _form_document(p, fill, stroke, mono, ink):
    body = p.poly([(-52, -70), (24, -70), (52, -42), (52, 70), (-52, 70)], stroke, fill)
    p.poly([(24, -70), (24, -42), (52, -42)], stroke, stroke)           # folded corner
    for v, end in ((-28, 30), (-10, 30), (8, 12)):
        p.line([(-32, v), (end, v)], ink)
    p.mono(mono, 0, 42, 84, 24, ink)
    return body


def _form_commits(p, fill, stroke, mono, ink):
    p.line([(-30, -46), (-30, 46)], stroke)
    p.line([(34, -16), (34, 8), (-30, 38)], stroke, curved=True)
    first = p.box("ellipse", -46, -62, -14, -30, fill, stroke)
    p.box("ellipse", -46, 30, -14, 62, fill, stroke)
    p.box("ellipse", 18, -32, 50, 0, fill, stroke)
    return first


def _form_graphql(p, fill, stroke, mono, ink):
    corners = _ngon(6, 60, -90)                                          # point up
    body = p.poly(corners, fill)
    triangle = corners[0::2]
    p.poly(triangle, fill)
    for u, v in triangle:
        p.box("ellipse", u - 10, v - 10, u + 10, v + 10, fill, stroke)
    return body


def _form_person(p, fill, stroke, mono, ink):
    body = p.box("rectangle", -54, -10, 54, 64, fill, stroke, radius=True)  # shoulders
    p.box("ellipse", -25, -70, 25, -20, fill, stroke)                   # head
    p.mono(mono, 0, 28, 92, 28, ink)
    return body


def _form_admin(p, fill, stroke, mono, ink):
    body = _form_person(p, fill, stroke, mono, ink)
    p.poly(_star(44, -52, 18, 8), "#e67700", "#ffd43b")                 # admin badge
    return body


_FORMS = {name[len("_form_"):]: fn for name, fn in globals().items()
          if name.startswith("_form_")}


class DiagramError(Exception):
    pass


class Diagram:
    """Accumulates elements, then writes a valid .excalidraw document."""

    def __init__(self, icon_dir=None, seed=7):
        # `icon_dir` is accepted for backwards compatibility and ignored: glyphs are
        # native shapes, so `files` stays empty.
        self.els = []
        self.files = {}
        self._rnd = random.Random(seed)
        self._n = 0
        self.warnings = []

    # -------------------------------------------------------------- internals
    def _id(self, prefix="e"):
        self._n += 1
        return f"{prefix}{self._n:05d}"

    def _base(self, type_, x, y, w, h, **kw):
        e = {
            "type": type_, "id": kw.pop("id", None) or self._id(type_[:2]),
            "x": float(x), "y": float(y), "width": float(w), "height": float(h),
            "angle": 0, "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
            "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "seed": self._rnd.randint(1, 2 ** 31),
            "version": 1, "versionNonce": self._rnd.randint(1, 2 ** 31),
            "isDeleted": False, "boundElements": [], "updated": 1761910768312,
            "link": None, "locked": False,
        }
        e.update(kw)
        self.els.append(e)
        return e

    def _bind_label(self, container, s, size=28, color="#1e1e1e"):
        """Centred text bound to a shape. Excalidraw keeps it centred on move."""
        t = self._base("text", container["x"] + 10,
                       container["y"] + container["height"] / 2 - size * 0.625,
                       container["width"] - 20, size * 1.25,
                       strokeColor=color, fontSize=size, fontFamily=2, text=s,
                       textAlign="center", verticalAlign="middle",
                       containerId=container["id"], originalText=s,
                       lineHeight=1.25, autoResize=False)
        container["boundElements"] = (container.get("boundElements") or []) + \
            [{"type": "text", "id": t["id"]}]
        return t

    def _line(self, pts, stroke="#1e1e1e", bg="transparent", curved=False,
              group_ids=None, sw=2):
        """A `line` element through absolute points. When the last point equals the
        first it is a closed polygon and Excalidraw fills it with `bg`."""
        x0, y0 = pts[0]
        rel = [[px - x0, py - y0] for px, py in pts]
        closed = len(rel) > 3 and rel[-1] == rel[0]
        xs, ys = [q[0] for q in rel], [q[1] for q in rel]
        return self._base("line", x0, y0, max(xs) - min(xs), max(ys) - min(ys),
                          strokeColor=stroke, backgroundColor=bg, strokeWidth=sw,
                          roundness={"type": 2} if curved else None, points=rel,
                          polygon=closed, startBinding=None, endBinding=None,
                          lastCommittedPoint=None, startArrowhead=None,
                          endArrowhead=None, groupIds=list(group_ids or []))

    # ---------------------------------------------------------------- shapes
    def text(self, x, y, s, size=30, color="#1e1e1e", center=False, family=2):
        """Standalone text. `center=True` treats x as the horizontal centre.

        Excalidraw ignores textAlign for positioning standalone text, so the
        width estimate below is what actually centres it.
        """
        w = max(len(line) for line in s.split("\n")) * size * 0.52
        h = size * 1.25 * len(s.split("\n"))
        return self._base("text", x - w / 2 if center else x, y, w, h,
                          strokeColor=color, fontSize=size, fontFamily=family,
                          text=s, textAlign="left", verticalAlign="top",
                          containerId=None, originalText=s, lineHeight=1.25,
                          autoResize=True)

    def rect(self, x, y, w, h, bg="transparent", stroke="#1e1e1e", op=100,
             rounded=True, fill="solid", sw=2, style="solid",
             label=None, lsize=28, lcolor="#1e1e1e"):
        r = self._base("rectangle", x, y, w, h, backgroundColor=bg,
                       strokeColor=stroke, opacity=op, fillStyle=fill,
                       strokeWidth=sw, strokeStyle=style,
                       roundness={"type": 3} if rounded else None)
        if label:
            self._bind_label(r, label, lsize, lcolor)
        return r

    def ellipse(self, x, y, w, h, bg="transparent", stroke="#1e1e1e", op=100,
                label=None, lsize=28):
        e = self._base("ellipse", x, y, w, h, backgroundColor=bg,
                       strokeColor=stroke, opacity=op)
        if label:
            self._bind_label(e, label, lsize)
        return e

    # ----------------------------------------------------------------- zones
    def zone(self, x, y, w, h, title=None, subtitle=None, kind=TRUSTED, op=25):
        """A network zone: tinted rounded rect with title top-left.

        Zones read best when the title sits inside the top-left corner rather
        than centred, leaving the interior free for the icon row.
        """
        fill = kind[0] if isinstance(kind, tuple) else kind
        r = self.rect(x, y, w, h, bg=fill, op=op, sw=2)
        if title:
            self.text(x + 24, y + 16, title, size=42)
        if subtitle:
            self.text(x + 24, y + 70, subtitle, size=26, color=GREY)
        return r

    def group_box(self, x, y, w, h, label=None, stroke="#1e1e1e",
                  lcolor="#5f3dc4", style="solid", sw=2):
        """Hachure-filled sub-box - the DMZ.excalidraw idiom for "a data plane"
        or "a gateway group" sitting inside a zone."""
        r = self.rect(x, y, w, h, bg="transparent", stroke=stroke,
                      fill="hachure", sw=sw, style=style)
        if label:
            self.text(x + 12, y + 8, label, size=26, color=lcolor)
        return r

    # ----------------------------------------------------------------- icons
    def icon(self, name, cx, cy, size=140, label=None, sub=None,
             label_above=False, lsize=28):
        """Place a component glyph centred on (cx, cy) with an optional caption.

        The glyph is a small composed drawing (see GLYPHS): a hexagon for a gateway,
        a cylinder for a datastore, a browser window for a UI and so on, built from
        native rectangles, ellipses, diamonds, lines and free text inside a
        `size` x `size` box. Every element of it, captions included, shares one
        group, so it moves as a unit in the editor; the main body element carries
        customData.tykComponent.

        `label_above=True` puts the caption above the glyph. Use it when the glyph
        sits near a horizontal flow lane - a caption below would land on the
        arrow. See references/style-guide.md.
        """
        if name not in GLYPHS:
            raise DiagramError(
                f"unknown icon {name!r}. Available: {', '.join(available_icons())}")
        form, fill, stroke, mono = GLYPHS[name]
        group = self._id("g")
        body = _FORMS[form](_Pen(self, cx, cy, size, group), fill, stroke, mono,
                            _ink_for(fill))
        body["customData"] = {"tykComponent": name}
        if label:
            ly = cy - size / 2 - 14 - lsize * 1.25 if label_above else cy + size / 2 + 14
            self.text(cx, ly, label, size=lsize, center=True)["groupIds"] = [group]
        if sub:
            sy = cy + size / 2 + 14 if label_above else cy + size / 2 + 14 + lsize * 1.4
            self.text(cx, sy, sub, size=lsize - 5, color="#5f3dc4",
                      center=True)["groupIds"] = [group]
        return cx, cy

    # ---------------------------------------------------------------- arrows
    def arrow(self, points, color=BLUE, style="solid", sw=4, head="arrow",
              label=None, lsize=26, lcolor=None, allow_backtrack=False):
        """Arrow through `points` (absolute coords).

        Two points  -> straight line, any direction (diagonals fine).
        Three plus  -> treated as an orthogonal elbow route and validated:
        every segment must be axis-aligned, non-zero, and must travel with the
        route's overall direction on its axis. Pass allow_backtrack=True for a
        deliberate U-route around an obstacle. Excalidraw draws multi-point
        arrows as smooth curves when
        `roundness` is set, so this always writes roundness=null for crisp
        corners - a curved "elbow" is the classic tell that a diagram was
        generated rather than drawn.
        """
        self._check_route(points, allow_backtrack)
        x0, y0 = points[0]
        rel = [[px - x0, py - y0] for px, py in points]
        w = max(p[0] for p in rel) - min(p[0] for p in rel)
        h = max(p[1] for p in rel) - min(p[1] for p in rel)
        a = self._base("arrow", x0, y0, w, h, strokeColor=color,
                       strokeStyle=style, strokeWidth=sw, roundness=None,
                       points=rel, startBinding=None, endBinding=None,
                       lastCommittedPoint=None, startArrowhead=None,
                       endArrowhead=head)
        if label:
            t = self._base("text", x0, y0, len(label) * lsize * 0.52, lsize * 1.25,
                           strokeColor=lcolor or color, fontSize=lsize,
                           fontFamily=2, text=label, textAlign="center",
                           verticalAlign="middle", containerId=a["id"],
                           originalText=label, lineHeight=1.25, autoResize=False,
                           backgroundColor="#ffffff")
            a["boundElements"] = [{"type": "text", "id": t["id"]}]
        return a

    #: A leg shorter than this that runs against the route's net direction is a
    #: stub, not a detour. Scene units - panels here are ~3000 wide, icons ~140,
    #: so anything under this reads as a glitch rather than a deliberate leg.
    STUB = 60

    def _check_route(self, pts, allow_backtrack=False):
        """Catch elbow routes that will render as a hook.

        The bug this exists for: a start computed as `box_right + 75` and a first
        waypoint at `column - 150`, where the column turns out to be *left* of the
        start. The route then goes back a few units, turns, and travels forward
        past its own start - Excalidraw draws a small hook at the tail, which is
        the most obvious "this was generated" tell.

        Checking only consecutive segments misses it (the stub and the leg that
        undoes it sit on different axes). Requiring strict monotonicity instead
        over-fires: the recommended control-plane route deliberately goes up out
        of a box, across a gutter, then down into its target, and that is correct.
        What separates the two is length - a genuine detour is a substantial leg,
        a bug is a stub. Hence the STUB threshold.
        """
        if len(pts) < 3:
            return
        net_x = pts[-1][0] - pts[0][0]
        net_y = pts[-1][1] - pts[0][1]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            dx, dy = bx - ax, by - ay
            if abs(dx) > 1 and abs(dy) > 1:
                self.warnings.append(
                    f"elbow route has a diagonal segment: {pts} - split it into "
                    "axis-aligned legs, or use a 2-point arrow")
            if abs(dx) < 1 and abs(dy) < 1:
                self.warnings.append(f"elbow route has a zero-length segment: {pts}")
            if allow_backtrack:
                continue
            for delta, net, axis in ((dx, net_x, "x"), (dy, net_y, "y")):
                if 1 < abs(delta) < self.STUB and abs(net) > 1 and delta * net < 0:
                    self.warnings.append(
                        f"elbow route starts with a backwards stub on {axis}: a "
                        f"leg of {delta:+.0f} against the route's overall "
                        f"{net:+.0f} draws a hook. Route: {pts}. The start point "
                        "is probably already past the waypoint - check whether "
                        "box_right is beyond the column you are routing to. Pass "
                        "allow_backtrack=True if the jog is deliberate.")

    # ------------------------------------------------------------ composites
    def note(self, x, y, w, h, title, body, tone="info", tsize=34, bsize=28):
        bg, stroke, tcol = TONES[tone]
        self.rect(x, y, w, h, bg=bg, stroke=stroke, sw=2)
        self.text(x + 28, y + 24, title, size=tsize, color=tcol)
        self.text(x + 28, y + 24 + tsize * 1.5, body, size=bsize)

    def legend(self, x, y, entries, w=2400, title="Legend"):
        """entries: list of (color, style, description)."""
        self.rect(x, y, w, 76 + len(entries) * 48, bg="#ffffff", sw=2)
        self.text(x + 30, y + 20, title, size=34)
        for i, (c, style, lab) in enumerate(entries):
            yy = y + 96 + i * 48
            self.arrow([(x + 40, yy), (x + 200, yy)], c, style=style, sw=4)
            self.text(x + 230, yy - 16, lab, size=28)

    def table(self, x, y, header, rows, widths, rh=92, hsize=30, csize=27,
              cell_tone=None, header_bg="#e5dbff"):
        """Comparison table. `cell_tone(row_index, col_index, value)` may return
        a TONES key to shade a cell - useful for making a trade-off table show
        its own conclusion at a glance."""
        cx = x
        for i, c in enumerate(header):
            self.rect(cx, y, widths[i], rh, bg=header_bg, sw=2, rounded=False,
                      label=c, lsize=hsize)
            cx += widths[i]
        for r, row in enumerate(rows):
            cx = x
            yy = y + rh * (r + 1)
            for i, cell in enumerate(row):
                tone = cell_tone(r, i, cell) if cell_tone else None
                bg = TONES[tone][0] if tone else ("#f8f9fa" if i == 0 else "#ffffff")
                self.rect(cx, yy, widths[i], rh, bg=bg, sw=1, rounded=False,
                          label=cell, lsize=csize)
                cx += widths[i]
        return y + rh * (len(rows) + 1)

    # ------------------------------------------------------------------ save
    def validate(self):
        ids = {e["id"] for e in self.els}
        errs = list(self.warnings)
        for e in self.els:
            for k in ("x", "y", "width", "height"):
                v = e[k]
                if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
                    errs.append(f"{e['id']}: bad {k}={v!r}")
            if e["type"] == "image" and e["fileId"] not in self.files:
                errs.append(f"{e['id']}: image references missing file {e['fileId']}")
            if e["type"] == "text" and e.get("containerId") and e["containerId"] not in ids:
                errs.append(f"{e['id']}: bound text has no container")
            for b in e.get("boundElements") or []:
                if b["id"] not in ids:
                    errs.append(f"{e['id']}: dangling boundElement {b['id']}")
        return errs

    def bounds(self):
        xs, ys = [], []
        for e in self.els:
            if e.get("points"):
                xs += [e["x"] + q[0] for q in e["points"]]
                ys += [e["y"] + q[1] for q in e["points"]]
            else:
                xs += [e["x"], e["x"] + e["width"]]
                ys += [e["y"], e["y"] + e["height"]]
        return min(xs), min(ys), max(xs), max(ys)

    def save(self, path, strict=True):
        errs = self.validate()
        if errs and strict:
            raise DiagramError("diagram would render incorrectly:\n  - " +
                               "\n  - ".join(errs))
        doc = {
            "type": "excalidraw", "version": 2, "source": "tyk-excalidraw-diagrams",
            "elements": self.els,
            "appState": {"gridSize": 20, "viewBackgroundColor": "#ffffff"},
            "files": self.files,
        }
        with open(path, "w") as fh:
            json.dump(doc, fh)
        x0, y0, x1, y1 = self.bounds()
        print(f"wrote {path}: {len(self.els)} elements, "
              f"bounds ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f})")
        for w in errs:
            print("WARN:", w)
        return path


if __name__ == "__main__":
    for n in available_icons():
        form, fill, _, mono = GLYPHS[n]
        print(f"{n:18} {form:9} {fill}  {mono or '-'}")
