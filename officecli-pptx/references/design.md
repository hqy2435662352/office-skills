# Design Principles

A deck is not a document. The audience has 3 seconds to get each slide. Before adding anything, ask: "If the audience reads only the biggest element and glances once, do they get the point?" If they have to read the bullets, the biggest element is wrong.

## Grid, margins, negative space

Standard widescreen is **33.87 × 19.05cm**. Treat it as a 12-column grid internally:

- **Edge margin ≥ 1.27cm** (0.5") on all sides.
- **Inter-block gap ≥ 0.76cm** (0.3") between cards / columns / rows — pick one value (0.76 or 1.27cm) and use it everywhere; mixed gaps look unfinished.
- **≥ 20% negative space per slide.** Filling every pixel reads as amateur.
- **Compose, don't web-center.** Whitespace is structural: a slide top-weighted with open space in the lower third is correct composition, not an empty defect. Intentional asymmetry (content left, breathing room right) reads more designed than centering everything — don't fill a gap just because it's there.
- For card grids: `usable = 33.87 − 2·margin − (N−1)·gap`, then `col_width = usable / N`. Don't hand-pick x coordinates.

## Font pairings

Pair by document register, not by novelty. "Best For" is a prompt, not a decree; a pairing outside this table is fine if it fits — these 8 are seeds, not the set.

| Header | Body | Best For |
|---|---|---|
| Georgia | Calibri | Formal business, finance, executive reports |
| Arial Black | Arial | Bold marketing, product launches |
| Calibri | Calibri Light | Clean corporate, minimal design |
| Cambria | Calibri | Traditional professional, legal, academic |
| Trebuchet MS | Calibri | Friendly tech, startups, SaaS |
| Impact | Arial | Bold headlines, event decks, keynotes |
| Palatino | Garamond | Elegant editorial, luxury, nonprofit |
| Consolas | Calibri | Developer tools, technical / engineering |

Set both fonts explicitly on every shape (`--prop font=Georgia` on titles, `--prop font=Calibri` on body), not via theme inheritance.

## Color and contrast

The columns: **Primary** (dominant — 60–70% of weight, the color you see first), **Secondary** (supporting tone), **Accent** (sparing, one-hit emphasis), **Text** (body on light fills), **Muted** (captions / axis labels / footer).

| Theme | Primary | Secondary | Accent | Text | Muted |
|---|---|---|---|---|---|
| Coral Energy | `F96167` | `F9E795` | `2F3C7E` | `333333` | `8B7E6A` |
| Midnight Executive | `1E2761` | `CADCFC` | `FFFFFF` | `333333` | `8899BB` |
| Forest & Moss | `2C5F2D` | `97BC62` | `F5F5F5` | `2D2D2D` | `6B8E6B` |
| Charcoal Minimal | `36454F` | `F2F2F2` | `212121` | `333333` | `7A8A94` |
| Warm Terracotta | `B85042` | `E7E8D1` | `A7BEAE` | `3D2B2B` | `8C7B75` |
| Berry & Cream | `6D2E46` | `A26769` | `ECE2D0` | `3D2233` | `8C6B7A` |
| Ocean Gradient | `065A82` | `1C7293` | `21295C` | `2B3A4E` | `6B8FAA` |
| Teal Trust | `028090` | `00A896` | `02C39A` | `2D3B3B` | `5E8C8C` |
| Sage Calm | `84B59F` | `69A297` | `50808E` | `2D3D35` | `7A9488` |
| Cherry Bold | `990011` | `FCF6F5` | `2F3C7E` | `333333` | `8B6B6B` |

Pick by topic, not by default — finance reads Midnight Executive, a product launch reads Coral Energy, safety / LOTO reads Cherry Bold. If the closest named theme is not quite right, blend (e.g. Forest primary + gold `D4A843` accent). Use **Text** on light fills, **Muted** for captions / axis / footer, `FFFFFF` or Secondary for body on dark fills.

On dark backgrounds, text and chart series follow the Hard rules contrast floor above.

## Chart-choice decision table

Wrong chart type kills the 3-second test:

| Data shape | Use | Avoid |
|---|---|---|
| Category comparison (A vs B vs C) | `column` (vertical) / `bar` (≥ 6 categories, horizontal) | pie (slices merge), line (no time axis) |
| Time series, 1–3 series | `line` | area (occlusion), bar (implies discrete) |
| Part-of-whole, 2–5 slices | `pie` / `doughnut` | pie with 8+ slices (unreadable) |
| Correlation / distribution | `scatter` | line (implies ordering) |
| Multiple categories × metrics, dense | stacked `column` or heatmap | one chart per metric — consolidate |
| KPI snapshot (single big number) | **Large-text shape** (60–72pt + ≤ 5-word sublabel), NOT a chart | gauge chart, tiny bar |

Rule of thumb: if > 3 series and > 8 categories, split into two charts or switch to a table.

## Animation

Use as much or as little as the brand and content call for — a formal finance deck trends to near-zero, a product launch can be more expressive. Animation is a tool, not décor. Three floors keep it from hurting the deck (none caps how much you use):

- **Purposeful** — each one reveals or emphasizes (progressive bullet reveal, a build-up chart), never decorates. If it doesn't aid comprehension, cut it.
- **Degrades gracefully** — pptx animation renders inconsistently across viewers (Keynote / Slides / web / mobile) and may not play at all, so every slide must read correctly as a *static* frame. Never hide essential content behind a reveal.
- **Verify live** — animation is runtime-only; `view html` and screenshots can't see it, so confirm in a real presentation viewer before shipping.

Taste steer (not a ban): `fade` / `appear` / a single `zoom-entrance` with snappy durations (~hundreds of ms) fit most decks; `bounce` / `swivel` / `spin` / `fly-from-edge` / dense multi-object choreography usually read amateur — reach for them only when the brand is deliberately playful.

## Layout patterns & data display

Vary layout across slides — repeating the same pattern makes every slide feel identical. These are common building blocks, not the full set — pick one per slide, or build a layout outside the table when the content calls for it:

| Pattern | When to use | Key measurement |
|---|---|---|
| **Two-column** (text left, visual right) | Concept + evidence; feature + screenshot | Each col ≈ 14-15cm; gap 1cm |
| **Icon rows** (icon in filled circle + bold header + description) | Feature lists, benefits, team roles | Icon circle 1.5-2cm; 3-4 rows max |
| **2×2 or 2×3 grid** (card tiles) | Quadrant analysis, SWOT, option comparison | Gap ≥ 0.76cm; consistent card height |
| **Half-bleed image** (full left or right half, content overlay on other side) | Hero moments, case study openers | Image 16-17cm wide; content column ≥ 14cm |
| **Large stat callout** (60-72pt number + ≤5-word sublabel below) | Single KPI, milestone, market size | Use shape, NOT a chart; sublabel 14-16pt muted |

**Data display quick rules:**
- Comparison columns (before/after, A vs B) beat a table for 2-3 options.
- Timelines and process flows: numbered step shapes + connectors, not a bullet list.

## Image treatment (only when a slide uses a photo / screenshot / logo)

**Read the image first** (open the file) and choose treatment from what you see — don't place blind from a filename.

- **Full-bleed photo** → size to COVER the region (crop edges), no border.
- **Screenshot / diagram / logo** → size to FIT (never crop content). A transparent or fit image sits on a contrasting fill — drop a colored rectangle behind it, don't let it float on white.
- **Text over a photo** → never raw on the image. Put it on a card, or lay a protection scrim between image and text (a dark rectangle at ~50–60% opacity, or a gradient fading from the text edge).
- Never stretch (distort the aspect ratio); don't overlay text on a busy screenshot.
- Prefer user-provided images / brand assets; no emoji or self-drawn art unless asked.

## Visual motif commitment

Pick ONE distinctive element (rounded image frames, section numbers in filled circles, single-side border band, diagonal accent strips) and carry it to every slide — commit across the whole deck; styling one slide and leaving the rest plain reads as abandoned. A secondary motif is fine only if it doesn't compete with the primary. Declare it in your build plan first: `## Motif: numbered circles in brand color`.

## Visual AI-tells to avoid

- **No decorative underline under slide titles.** A stripe / rule below a heading is the single most common AI-slide tell — use whitespace or a background-color change instead.
- **No rounded-corner card with a colored left-border accent stripe.** The other classic AI-slide tell — use a solid fill, a top accent band, or whitespace separation instead.
- **No emoji as iconography** unless the brand uses them — use a shape or a real icon asset.

Copy-level tells live in "Copy reads human" (see main SKILL.md).
