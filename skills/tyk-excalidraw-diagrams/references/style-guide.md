Last verified: 2026-11-19 against `scripts/tyk_excalidraw.py` and `scripts/render_excalidraw.py` in this skill

# Tyk diagram style guide

The house style the library draws in. Following it keeps generated diagrams consistent with
each other and readable at the scales below.

## Contents
- [Colour](#colour)
- [Type scale](#type-scale)
- [Zones and boxes](#zones-and-boxes)
- [Component glyphs](#component-glyphs)
- [The zone band](#the-zone-band)
- [Arrows and routing](#arrows-and-routing)
- [Multi-panel layouts](#multi-panel-layouts)
- [Checklist before delivering](#checklist-before-delivering)

## Colour

Flow colours carry meaning; a reader learns them once from the legend.

| Meaning | Colour | Style |
|---|---|---|
| Traffic / request flow | `#1971c2` blue | solid, width 4 |
| Config / control flow | `#2f9e44` green | dashed, width 4 |
| Redis / state | `#7048e8` purple | solid, width 4 |
| Danger: crosses a trust boundary | `#e03131` red | solid, width 5 |
| Analytics / pump | `#087f5b` teal | dotted |

Zone tints are pastel fills at **opacity 20-25**, so glyphs stay legible on top:
DMZ `#ffc9c9`, trusted `#a5d8ff`, internal/k8s `#b2f2bb`, cloud/SaaS `#e5dbff`.

Text is `#1e1e1e`. Secondary annotation is `#757575` - never lighter on white.
Coloured text needs the dark variant to stay readable: `#2b8a3e` not `#2f9e44`,
`#1864ab` not `#1971c2`, `#c92a2a` not `#e03131`.

## Type scale

Diagrams are built at a large scene scale (a panel is ~3000 units wide), so font
sizes look big in the JSON. Relative proportions are what matter.

| Role | Size |
|---|---|
| Canvas title | 110 |
| Panel / option title | 76 |
| Zone title | 42 |
| Section or box title | 34-38 |
| Glyph caption | 26-30 |
| Sub-caption, annotation, arrow label | 24-26 |

Standalone text positions from its **left edge** - Excalidraw ignores `textAlign`
for placement. `Diagram.text(..., center=True)` estimates the width and offsets for
you; that estimate is `len * size * 0.52`, so a very long single-line caption may
drift slightly. Bound labels (`rect(label=...)`) genuinely centre themselves and
are the better choice for anything inside a small box.

## Zones and boxes

Three nesting levels, in order of decreasing area:

1. **Panel frame** - transparent rounded rect, `opacity 45`, wrapping one whole
   option or site. Title sits above it, not inside.
2. **Zone band** - tinted rounded rect at `opacity 25` with the title inside the
   top-left corner and a grey subtitle under it (`internet-facing`,
   `trusted network`). Left-aligned titles keep the interior free for glyphs.
3. **Group box** - `fillStyle: "hachure"`, transparent background, for "a data
   plane" or "a gateway group" inside a zone. Label it in purple `#5f3dc4` at the
   top-left inside the box. This hachure sub-box is the strongest Tyk-house idiom.

To make two adjacent rects read as one continuous zone (for example a control plane
that lives *inside* the internal zone but sits in the left column), make their edges
flush - same y range, one starting exactly where the other ends. A gap reads as two
separate zones.

## Component glyphs

`icon()` draws each component as a small composed drawing that reads as the thing
it is, in the manner of Excalidraw's public libraries. Every glyph is built from native
elements only - rectangles, ellipses, diamonds, lines (a closed line is a filled
polygon) and free text - at roughness 1 and stroke width 2, in the component's own
fill and stroke colours. No image elements, so the `files` map stays empty.

| Glyph | Form | Monogram |
|---|---|---|
| `tyk-gateway`, `gateway-teal` | Flat-topped filled hexagon | GW |
| `mdcb` | Filled hexagon with a smaller hexagon outline inside (double border) | MD |
| `load-balancer` | Diamond with three short lines fanning out of its right vertex | LB |
| `tyk-dashboard`, `dashboard-teal`, `developer-portal` | Browser window: rounded body, darker title bar, three dots at its left | DB, PT |
| `tyk-pump`, `pump-teal` | Funnel: wide top narrowing to a neck | PM |
| `tyk-operator` | Heptagon (Kubernetes-like) with a circle outline at its centre | OP |
| `redis`, `redis-gold`, `postgres`, `postgres-dark`, `mongodb` | Cylinder: bottom cap, body, side lines, top cap | RD, PG, MDB |
| `services` | Two overlapping rounded rectangles, the back one up and to the right | SVC |
| `identity-provider` | Shield | IdP |
| `security-policy` | Padlock: rounded body under a curved double-line shackle | POL |
| `key` | Key: round bow with a hole, shaft, two teeth | caption only |
| `api-definition` | Document with a folded corner and three text lines | DEF (small, at the bottom) |
| `version-control` | Commit graph: three filled circles joined by a trunk and a branch | caption only |
| `graphql` | Hexagon outline with an inscribed triangle and dots at its corners | caption only |
| `api-consumer`, `api-developers` | Person: head over rounded shoulders | API, DEV |
| `api-admin` | Person with a gold star badge at the top right | ADM |

Every glyph fits a `size` x `size` box (default 140; `size` scales everything). Base
size 130-150 units. Caption 14 units below, centred, size 26-30. Optional sub-caption
below that in purple. The monogram is sized to stay on one line inside the form; the
caption carries the full name. All of a glyph's elements, captions included, share one
group, so they move together in the editor, and its main body element carries
`customData.tykComponent` with the glyph name.

`label_above=True` moves the caption above the glyph. Use it whenever the glyph sits
near a horizontal flow lane - otherwise the caption lands on the arrow.

Use the default set (`tyk-gateway`, `redis`, `tyk-pump`, ...) - Tyk components in the
violet family - by default. The teal/gold variants (`gateway-teal`, `pump-teal`,
`dashboard-teal`, `redis-gold`, `postgres-dark`) keep the same forms and monograms in
a second colour family, for diagrams that need two visually distinct estates - two
regions, on-premises next to cloud, or a current-state vs target-state comparison.

## The zone band

The layout that carries most Tyk topology diagrams. One band per network zone,
stacked with a small gap, each ~2000 wide by ~620 tall. Left to right:

```
API Consumer -> WAF -> Load Balancer -> [ Gateway  Gateway ] -> Backend  Backend
                                        (hachure group box)
                              Redis sits ABOVE this lane
```

Column offsets from the band's left edge that work well at 2020 wide:
consumer 90, WAF 330, LB 590, gateway 840 and 1030, Redis 1310, backends 1640/1830.

The icon row centres at band top + 360. Put Redis at row centre - 165 with
`label_above=True`: its caption then sits above the icon and its sub-caption below,
with the traffic lane running clear underneath.

Zone boundaries between bands are red dashed lines with a short red label naming
what does or does not cross them.

## Arrows and routing

- Two-point arrows may run in any direction; diagonals are fine and often clearer.
- Three or more points are treated as an orthogonal elbow route: axis-aligned legs
  only, no zero-length segments, no reversing the previous leg. The library
  validates this and explains what to fix.
- Check direction before routing. `box_right + 75` is often already *past* a column
  you meant to route to - that produces the backwards hook.
- **Control-plane channel**: run one vertical line at a fixed x in the gutter between
  the control plane and the zone bands, then branch horizontally into each zone
  across its header strip (below the zone subtitle, above the icon row), then turn
  down into the group box. Several branches sharing the vertical reads as a bus,
  which is exactly right for MDCB.
- Give an arrow a label only when there is clear space along it - `TCP 6379`,
  `outbound RPC, TLS :443` (Tyk Cloud) or `:9091` (self-managed MDCB default). Otherwise put a
  standalone text near the flow, or in the empty area under the control plane box.
- To show something crossing a boundary it shouldn't, route it down an otherwise
  empty column in red at width 5. Emptiness around it is what makes it read as wrong.

## Multi-panel layouts

Comparing options side by side: identical internal layout in each panel, ~700 units
of gutter between panels. Only the differences should differ - that is what makes
the comparison legible. Panel title above the frame, verdict `note()` below it in
the tone matching the verdict.

Under the panels: a `table()` of the deltas with `cell_tone` shading, plus any
cross-cutting callouts. Header and canvas title go top-left with the legend beneath
them, clear of the first panel's title.

## Checklist before delivering

Render and look at the PNG, then confirm:

- No text overlapping other text, an arrow, or a box edge.
- Every arrow starts and ends where it should; no hooks, no lines through glyphs.
- Nothing has drifted outside its zone or panel frame.
- Every glyph reads as its component and shows its monogram (where it has one) on
  one line, with a readable caption.
- Zone titles and captions still readable at panel scale (~0.32).
- The diagram states the architectural point without the surrounding prose.
