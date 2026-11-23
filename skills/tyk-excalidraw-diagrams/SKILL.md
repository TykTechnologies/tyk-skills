---
name: tyk-excalidraw-diagrams
description: Draw editable Excalidraw architecture diagrams of Tyk deployments with native glyphs for gateway, Redis, MDCB, Pump, Dashboard, Portal, Operator and more, in Tyk's house style - zone bands, validated arrow routes, legends, comparison tables and callouts. Use when asked to draw, diagram or compare a Tyk topology (data and control planes, MDCB, Redis placement, DMZ, multi-region, Kubernetes, hybrid or Tyk Cloud) or for a .excalidraw file. Not for sizing the infrastructure, which is the `tyk-infra-sizing` skill.
---

# Tyk Excalidraw Diagrams

Produce a `.excalidraw` file that opens and edits in excalidraw.com, with one consistent glyph
per Tyk component and Tyk's house visual language. The output is a working file, not a
picture: the team reopens it, moves things and keeps it as the record of the deployment.

Every component is a composed native Excalidraw glyph (a hexagon for a gateway, a cylinder for
Redis or PostgreSQL, a browser window for the Dashboard or Portal, a person for a consumer),
built only from rectangles, ellipses, diamonds, lines and text and grouped to move as one. The
document embeds no images, so its `files` map is empty and every glyph can be recoloured,
resized or relabelled. Inline renderers (Mermaid, ASCII, an inline SVG) give a one-off picture
without the zones, routes and editable record; always produce a file with
`scripts/tyk_excalidraw.py`, and render it to PNG when something inline is wanted.

What it needs: Python 3 for the library; Pillow (`requirements-diagrams.txt` at the plugin
root) for the PNG preview. No Tyk installation is needed.

## Ask first

| Question | Default |
| --- | --- |
| Which zones exist (DMZ, trusted, internal, cloud), and what sits in each? | Ask: the zones are the diagram |
| How many data planes, and where does the control plane live (beside one plane, own site, Tyk Cloud)? | One self-managed install, no MDCB |
| Where does each Redis sit, and is any Redis shared across zones? | One Redis per data plane, inside its zone |
| Is anything compared side by side, and on what axis (cost, blast radius, segregation)? | A single topology |
| Where does the file go? | `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/docs/` |

Draw the deployment the user plans to run. When a placement is unclear, ask; when an
assumption remains, label it on the diagram.

## Workflow

1. **Pin down the topology**: zones, what sits in each, and where Redis and the control plane
   live. For a comparison, settle the axis first, because it decides the layout.
2. **Write a generator script** in the project folder that imports the library, so layout
   constants stay in one place across iterations:
   ```python
   import sys; sys.path.insert(0, "<this skill>/scripts")
   from tyk_excalidraw import Diagram, BLUE, GREEN, PURPLE, TEAL, DMZ, TRUSTED, INTERNAL, CLOUD

   d = Diagram()
   d.zone(1000, 300, 2020, 620, "DMZ / Public zone", "internet-facing", kind=DMZ)
   d.group_box(1610, 460, 400, 400, "Data plane - public")   # hachure sub-box
   d.icon("tyk-gateway", 1840, 670, 130, label="Tyk Gateway")
   d.icon("redis", 2310, 505, 140, label="Redis", label_above=True)
   d.arrow([(1500, 660), (1700, 660)], BLUE)                  # straight
   d.arrow([(2185, 600), (2260, 600), (2260, 505)], PURPLE)   # elbow, validated
   d.note(90, 2400, 2940, 430, "Why this matters", "...", tone="bad")
   d.save("out.excalidraw")
   ```
   Methods: `zone`, `group_box`, `rect`, `ellipse`, `icon`, `text`, `arrow`, `note`, `legend`,
   `table`, `save`; signatures are in the module docstrings.
   `python3 scripts/tyk_excalidraw.py` lists every glyph with its form, fill and monogram.
3. **Save.** `Diagram.save()` validates the document and raises on anything that renders wrong.
4. **Render and look at it.** Not optional:
   ```bash
   python3 scripts/render_excalidraw.py out.excalidraw preview.png --scale 0.14
   python3 scripts/render_excalidraw.py out.excalidraw panel.png --scale 0.32 --crop 0,100,3100,2400
   ```
   Open the PNG. Overlapping labels, text escaping a box, arrows ending in the wrong place and
   drifting content are invisible in the JSON and obvious in the image. Two or three rounds are
   normal.
5. **Deliver** the `.excalidraw` file, and a preview PNG when useful. Say the preview is
   approximate: the rasteriser uses Arial and clean strokes, so Excalidraw itself looks better.

`scripts/example_hybrid_dataplane.py` is a complete worked diagram (Tyk Cloud control plane,
self-managed data plane on Kubernetes) in about 80 lines. Run it to check the toolchain before
starting something large:
`python3 scripts/example_hybrid_dataplane.py /tmp/hybrid.excalidraw`.

## References

| Need | Read |
| --- | --- |
| Colours, type scale, zone and box nesting, glyph forms, the zone band, routing, multi-panel layouts, the delivery checklist | `references/style-guide.md` |
| Library signatures and validation rules | `scripts/tyk_excalidraw.py` docstrings |
| Redis contents, single-install versus control and data plane Redis | https://tyk.io/docs/api-management/in-memory-database |
| MDCB, its control-plane components, port 9091, behaviour when the link drops | https://tyk.io/docs/api-management/mdcb |
| Tyk Cloud hybrid data planes (outbound connection on 443) | https://tyk.io/docs/tyk-cloud/environments-deployments/hybrid-gateways |
| Gateway sharding with `node_is_segmented` and tags | https://tyk.io/docs/api-management/api-sharding |
| Analytics forwarded from data planes | https://tyk.io/docs/api-management/dashboard-analytics/data-plane-pump |

## Getting the architecture right

The diagram is an argument, so make the argument visible. Checked against the pages above:

- **Redis placement is usually the subject.** Redis holds keys and sessions, OAuth tokens and
  clients, certificates, quota counters, the analytics buffer and, with the Redis rate limiter,
  rate-limit counters. Gateways that share one Redis share one failure domain: show that with a
  red line across the zone boundary rather than a paragraph. Gateway sharding
  (`db_app_conf_options.node_is_segmented` with `tags`) only decides which API definitions
  load where; it is configuration placement, not isolation.
- **A single self-managed install has no MDCB.** The Dashboard, gateways and Pump share one
  Redis, and the control plane sits inside a trusted zone. An MDCB box on a single-plane
  topology is an error, not a style choice.
- **Several data planes add MDCB** to the control plane, beside the Dashboard and a management
  gateway. MDCB uses the control plane's Redis and database (shared with the Dashboard); each
  data plane has its own Redis.
- **The MDCB link is outbound from the data plane.** Gateways (and the Hybrid Pump) open RPC
  connections to MDCB: port 9091 by default for self-managed MDCB, 443 for Tyk Cloud. TLS is a
  setting (`slave_options.use_ssl`), not automatic, and the gateway keeps a small pool of
  connections, so label it "outbound RPC, TLS" rather than "one encrypted connection". Draw the
  arrow starting at the data plane: that direction is the security argument.
- **Quotas and rate limits are per data plane**: counters are never shared between planes.
  Worth a callout on any multi-plane diagram, with "route each client to one plane".

Use `note(..., tone=)`: `bad` (red) for what breaks, `good` (green) for what a design buys,
`warn` (amber) for what is still to confirm, `info` (blue) for context. For comparisons,
`table()` with a `cell_tone` callback shades winning and losing cells.

## Pitfalls

- **Curved elbow arrows.** Excalidraw draws a multi-point arrow as a curve through its points
  whenever `roundness` is set, and as straight segments when it is `null` (Excalidraw source,
  `packages/element/src/shape.ts`). `Diagram.arrow()` always writes `roundness: null`; never set
  it back, or a route meant to turn one corner swoops across the diagram.
- **Arrows that double back.** If an elbow route's first waypoint is behind its start (a start
  at `box_right + 75`, a waypoint at `column_x - 150`), the arrow opens with a hook.
  For routes of three or more points, `save()` refuses a leg that is diagonal, has zero length,
  or is shorter than 60 units against the route's overall direction. Fix the geometry; pass
  `allow_backtrack=True` only for a deliberate jog.
- **Captions on the traffic lane.** Put anything off the request path (Redis especially) above
  the lane with `label_above=True`, or its caption lands on the flow arrow.
- **Diagonal control-plane lines.** Route control traffic up one vertical channel in the
  gutter, then in over each zone's top edge; diagonals cut through consumer and load-balancer
  glyphs.
- **Standalone text is placed by its left edge.** `text(..., center=True)` estimates the width
  as characters x size x 0.52, so a very long single line can drift; inside a box, prefer a
  bound label (`rect(label=...)`).

## Limitations

- The PNG renderer approximates Excalidraw: it smooths curves with a Catmull-Rom spline, draws
  clean strokes and renders dashed lines solid, so it shows layout faults but not Excalidraw's
  exact look. It loads Arial from the macOS system font folder; elsewhere it falls back to
  Pillow's small default font, so judge text fit on a Mac or in excalidraw.com.
- A component without a glyph is drawn with `rect(..., label=...)` or `ellipse(...)` at glyph
  size, or added to `GLYPHS` reusing an existing form; the library has no image support by
  design.
- The diagram states what it is told. It does not check the topology against a running Tyk
  stack; that is the reader's job with the pages above.

## Before reporting success

- [ ] `Diagram.save()` ran without raising, and the file opens in excalidraw.com.
- [ ] The PNG was rendered and opened; no text overlaps text, an arrow or a box edge.
- [ ] Every arrow starts and ends where it should, with no hooks and no line through a glyph.
- [ ] Nothing drifts outside its zone or panel; captions are readable at scale 0.32.
- [ ] MDCB appears only with several data planes or a remote control plane, and its arrows
      start at the data plane.
- [ ] Assumptions the user did not confirm are labelled on the diagram.
- [ ] Files were written to the project folder, not inside this plugin.

## Hand-offs

- Sizing the drawn topology (gateway counts, Redis, Pump, cost): the `tyk-infra-sizing` skill,
  which generates its own diagrams with this library.
- Licensing or which components a topology needs a licence for: the `tyk-facts` skill.
- Building the drawn topology as a runnable demo: the `tyk-demo-builder` skill.
- Installing Tyk: the `tyk-deployment` skill.
