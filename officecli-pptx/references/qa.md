# QA — Full Delivery Gates

**Assume there are problems.** First render is almost never correct. If you found zero issues, you were not looking hard enough.

## Delivery Gate (any failure = REJECT, do NOT deliver)

Gates 1–2b are text/schema-level (cannot see a rendered slide); Gate 3 is the only visual check. Done = every gate PASS **and** Gate 3 loop converged.

Each gate is **run a command, judge its output** — the officecli commands are identical on every OS (macOS / Linux / Windows), so no shell scripting is needed; the judging is yours.

- **Gate 1 — schema.** `officecli validate "<file>"`. Any schema error → REJECT and fix.
- **Gate 2 — overflow / format / structure.** `officecli view "<file>" issues`. If it lists *any* issue (lines tagged `[O1]`, `[C1]`, `[S1]`, …) → REJECT, fix, re-run until clean.
- **Gate 2b — leftover placeholders.** `officecli view "<file>" text`, then scan the output for `xxxx`, `lorem` / `ipsum`, `<TODO>`, `placeholder`, "this slide layout", or empty `()` / `[]`. Any hit → REJECT.

## Gate 3 — Visual audit (MANDATORY)

Pick **one** path:

**Screenshot (default)** — for vision-capable agents. Screenshot each slide in turn — `officecli view "<file>" screenshot --page 1 -o slide1.png`, then `--page 2`, … — until the page index runs past the deck (one screenshot = one slide). If it errors on page 1, use the fallback below.

**Judge every PNG against the checklist, adversarially** — "assume problems exist; finding none means you didn't look hard enough." Report one `slide N: <issue>` line per problem, or `PASS`. This step is required however you run it. **If** your harness can spawn a subagent, delegate the judging to a *fresh, independent* one — the agent that built the deck is biased toward "looks fine", a separate pair of eyes is more critical — handing it the screenshots + this checklist and the same adversarial framing. No subagent? Do exactly the same yourself.

**Fallback — HTML-text** (no vision, or screenshot failed): read `view "$FILE" html` as text. DOM cannot prove **dark-on-dark / fine overlap / arrowheads / gap-margin metrics / column alignment** — flag these as "not visually verified" rather than PASS.

**Optional `--grid N`** — only on user request for layout-rhythm, or when `view outline` shows anomalous layout distribution: `officecli view "<file>" screenshot --grid 3 -o grid.png`.

### Per-slide checklist (assume issues exist)

- **overlap** — shapes / charts / giant decorative numbers (01/02/03 100pt+) colliding
- **text overflow** — clipped at slide or shape boundary (KPI cards, narrow boxes)
- **narrow text box** — content fits technically but wraps to many short lines (1–2 words each); long sublabel in a 3cm KPI card, body line in a too-tight column
- **dark-on-dark** — fill brightness < 30% with text/icon brightness < 80% (incl. dark icons on dark without a contrasting circle)
- **image treatment** — photo stretched/distorted, text raw on a busy image (no card/scrim), screenshot or logo cropped, transparent image floating on white
- **missing arrowheads** — flowchart connectors as plain lines
- **decorative-line / title mismatch** — accent bar sized for one-line title but title wrapped to two (or vice versa)
- **footer / citation collision** — source line, page number, or footnote touching content above
- **tight margin / gap** — element within ~0.5" of slide edge, or two cards within ~0.3"
- **uneven gaps** — large empty area on one side, cramped on another (broken rhythm)
- **column / repeat-element misalignment** — KPI cards / icons off baseline or inconsistent width
- **order sanity** — sequence matches narrative (cover → agenda → dividers-before-sections → closing)

REJECT with `slide N: <issue>` lines, else "Gate 3 PASS" (HTML-text fallback adds "<unverified-items> not visually verified").

### Fix-verify loop (mandatory, max 3 cycles)

Fix → re-run Gate 3 → repeat until zero new issues; one fix often surfaces another. After 3 rounds without convergence, **stop** — likely seesaw, template-level cause, or agent misread. Report `slide N: <issue> — attempted: <fixes> — likely root: <template|design-conflict|ambiguous>` and let the user decide.

### Flush (part of the gate)

Once Gate 3 converges, end with `officecli save "<file>"` — this guarantees your edits are written to disk before delivery (use `officecli close "<file>"` instead to also release the resident on a one-shot handoff). Required final step, not optional. Always safe: never errors or loses work.
