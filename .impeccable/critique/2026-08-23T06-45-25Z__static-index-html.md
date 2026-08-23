---
target: the Donna Photoshoot generator screen
total_score: 18
max_score: 40
na_heuristics: 
p0_count: 2
p1_count: 3
timestamp: 2026-08-23T06-45-25Z
slug: static-index-html
---
⚠️ DEGRADED: single-context (this session's harness forbids spawning sub-agents unless the user asks by name)

Evidence: rendered `static/index.html` at 1440×900 against a local stub serving the real
`/api/models`, `/api/locations`, `/api/categories` and `/api/composition` payloads — 30 model
tiles, 48 location tiles, production copy. Detector: `detect.mjs`, 6 warnings. Contrast measured
in-page with alpha composited over `--bg`.

## Design Health Score

| # | Heuristic | Score | Key issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2 | Cost and model name reach the action bar, but location shows as "location set" not which one; no upload progress; Composition renders six empty rows before it is usable |
| 2 | Match System / Real World | 2 | Model cards print prompt text — "a 24 year old Indian woman. She is a…" — truncated mid-word. "Shape", "Detail", "Distance" are internal vocabulary |
| 3 | User Control and Freedom | 2 | No deselect, no reset, no cancel on an in-flight shoot, no undo after credits are spent |
| 4 | Consistency and Standards | 3 | Coherent system; three separate components (`.seg`, `.chips`, `.tools`) do the same segmented-choice job |
| 5 | Error Prevention | 2 | Credit gate and disabled button are good; no confirmation before spending, no file constraints surfaced before upload |
| 6 | Recognition Rather Than Recall | 1 | 78 tiles across two pickers with no search, no filter, no grouping by attribute. You must scroll and remember |
| 7 | Flexibility and Efficiency | 1 | No presets, no "shoot again with the same setup", no favourites, no recents, no keyboard path. Every shoot restarts a 6.4-screen scroll |
| 8 | Aesthetic and Minimalist Design | 2 | One action costs 5,789px of page. Model tiles are 3:4 full-bleed and dominate everything |
| 9 | Error Recovery | 2 | Errors land in one banner at the bottom of the page, not beside the field that caused them |
| 10 | Help and Documentation | 1 | Good inline hints, but no examples, no "what makes a good upload", no support route anywhere in the app |
| **Total** | | **18/40** | **Poor — the flow needs restructuring, not restyling** |

## Design Specificity Verdict

**LLM assessment.** The shell is now specific — copper on warm black, the layered mark, the
sidebar — but the *working surface* is a generic vertical form that any SaaS could ship. Nothing
about the composition says "this is where a jeweller art-directs a shoot". The product's entire
value is imagery, and imagery appears only as 150px picker thumbnails until after you have paid.
There is no canvas. The screen where a photographer would look at their piece does not exist.

The most category-interchangeable choice is the **long scroll wizard**. Every competitor that
solved this problem moved to a persistent workspace years ago, for a specific reason covered in
the benchmark below.

**Deterministic scan.** 6 warnings, all `warning` severity, in `static/index.html`:
- `flat-type-hierarchy` (line 27) — sizes 11/12/13/13.5/14/15/17px, a 1.5:1 total range. Seven
  steps inside one and a half octaves is not a hierarchy, it is a gradient. This is why the page
  reads flat and why nothing pulls the eye.
- `single-font` + `overused-font` (line 13) — Inter alone, carrying every role.
- `broken-image` ×3 — `<img id="previewImg">` and `<img id="rPreviewImg">` ship with no `src`.
  They are hidden until an upload, so this is a false positive in behaviour, but a real one in
  markup: an `alt` on a src-less img is what a screen reader reads out.

**Measured contrast (not from the detector).** `--faint` (`rgba(245,245,247,.4)`) composites to
**3.56:1**. It is used for `.hint` at 12px and `.comp-label` at 12px. WCAG AA needs 4.5:1. Those
are the strings carrying the most important instruction on the page — *"A product photo carries no
scale. This is the only thing telling the model how big your piece really is."* — set in the
least legible colour in the system. `--muted` at 6.78:1 is fine.

## Benchmark

| | Donna today | Photoroom / Flair / Pebblely | Midjourney web | Rawshot (your own reference) |
|---|---|---|---|---|
| Layout | Vertical wizard, 6.4 screens | Canvas centre, controls in a rail | Feed + prompt bar, settings in a drawer | Split: form left, imagery right |
| Where imagery lives | Below the fold, thumbnail-sized | The whole centre of the screen | The whole screen | Half the screen, always |
| Choosing an asset | Scroll 30 then 48 tiles | Search + filter + recents | Search + saved | Curated small set |
| Repeat a shoot | Redo everything | Duplicate / remix | Rerun with variations | — |
| Canvas | None | Yes | Yes | Yes |
| Surface | Dark | Light or neutral | Dark, but the content *is* the app | Light |

Three patterns are near-universal in this category and absent here:

1. **A canvas.** The uploaded piece and the results occupy the main area from the first second.
   Settings are secondary furniture. Donna inverts this: settings are the page, imagery is a
   footnote until generation finishes.
2. **Search over browse** once a library passes ~20 items. You have 78.
3. **Neutral or light surfaces for judging photographs.** This is not taste. A #0a0a0c field
   makes every image beside it read brighter and more contrasted than it is; a jeweller judging
   whether gold looks right on skin will misjudge it. Every colour-critical tool — Lightroom's
   soft-proof view, Capture One, Photoroom, Rawshot — defaults to neutral grey or white for the
   surface immediately around the image. Dark is right for the shell; it is wrong for the tray
   the photographs sit on.

## What's Working

- **The hints are genuinely good writing.** "A product photo carries no scale. This is the only
  thing telling the model how big your piece really is." That is a sentence that prevents a bad
  shoot. Most tools would have written "Size".
- **The credit gate is honest.** The button disables, the bar says why, and it links to top-up.
  Very few products refuse to take a click they cannot honour.
- **"Not sure? Let us compose it."** The right escape hatch in exactly the right place, and
  labelled free so nobody fears a charge.

## Priority Issues

### [P0] Model cards print raw prompt text
**Why it matters:** The first grid a customer sees reads "a 24 year old Indian woman. She is a…"
truncated mid-word, thirty times. It is internal data on display. It tells the buyer nothing they
can choose on, it exposes how the sausage is made, and it makes the product look unfinished — this
alone accounts for a large share of "it looks ugly".
**Fix:** Cards show a name and 2–3 attribute chips parsed from structure, not prose: age band,
skin tone, hair. Move the full description behind an info affordance. Give `cast.py` entries a
short `label` field rather than splitting a prompt string on its first comma.
**Command:** `/impeccable clarify`

### [P0] 78 tiles, no search, no filter
**Why it matters:** Choosing a model is now a 5-row scroll past thirty faces, then a 48-tile
location grid. Recognition-over-recall collapses: by the time you reach location you cannot see
which model you picked. It gets monotonically worse as the library grows, and you just grew it.
**Fix:** Search box on each picker; filter chips (skin tone, age, hair for models; India /
International / Indoor / Outdoor for locations); collapse to a single row of ~8 with "See all 30"
opening a dialog. Pin recently used.
**Command:** `/impeccable layout`

### [P1] One action costs 6.4 screens
**Why it matters:** The primary task — upload, pick, pick, generate — is a 5,800px scroll on a
1440×900 display. Nothing is ever visible next to anything else, so no decision can be made in
context of another. The action bar is the only fixed thing, and it summarises what you cannot see.
**Fix:** Two-pane workspace. Left rail (or right) holds the four steps as an accordion with one
open at a time; the centre holds a canvas showing the uploaded piece, then the selected model and
location as a live "shot plan", then the results in the same place. This is the structural change
the other issues hang off.
**Command:** `/impeccable shape`

### [P1] Composition renders six empty rows before it can be used
**Why it matters:** On first load, Step 4 is a card containing SHAPE, DETAIL, EXPRESSION, VIEW,
ANGLE, POSE — six labels with nothing beside them, because chips only populate after a category is
known. It reads as broken. A first-time user meets it before they have uploaded anything.
**Fix:** Collapse the whole section until a piece is uploaded, with one line: "Available once we
have read your piece." Skeleton chips if you want to show shape.
**Command:** `/impeccable onboard`

### [P1] The type hierarchy is flat and the critical copy is under-contrast
**Why it matters:** Seven font sizes spanning 11–17px means the page has no loud voice and no
quiet one. Combined with `--faint` at 3.56:1 on the hints, the most valuable sentences on the page
are the hardest to read, and the least valuable (prompt text) is the most prominent.
**Fix:** Three sizes with real contrast — 28/17/13 with weight doing the rest. Raise `--faint` to
`rgba(245,245,247,.55)` (≈5.9:1) and stop using it for anything a user must read.
**Command:** `/impeccable typeset`

### [P2] Static assets are unversioned, so shipped UI does not reach returning users
**Why it matters:** This is why your screenshot shows the old sidebar under the new CSS.
`/app.js` and `/theme.css` are served with no cache-busting and no explicit `Cache-Control`, so
browsers heuristically cache them. Every future design fix has the same problem: you ship it, and
the people who already use the product do not see it.
**Fix:** Append a build stamp to the `src`/`href` (`/app.js?v=<git sha>`), or send
`Cache-Control: no-cache` for `.html`/`.js`/`.css` from the StaticFiles mount so they revalidate.
**Command:** `/impeccable harden`

## Persona Red Flags

**Jordan (first-timer, a jeweller's marketing assistant).** Meets an empty 170px dashed rectangle
with no icon and no example of a good input. Scrolls into thirty faces described in truncated
prompt fragments and cannot tell what she is choosing between. Reaches a Composition card of six
empty labels and concludes the page is broken. Has seen no example of what comes out the other end
— there is not one finished image anywhere in the app before she spends credits.

**Alex (power user, runs 40 SKUs a week).** Cannot batch. Cannot save "Aditi + Udaipur Palace + my
default composition" as a preset. Cannot rerun last week's setup on a new piece. Has to scroll
5,800px per SKU. Forty SKUs is forty full scrolls. He will ask for an API within a week, or leave.

**Sam (keyboard and screen reader).** `.hint` and `.comp-label` fail AA at 3.56:1. Two `<img>`
elements carry `alt` with no `src`. `<main>` has no `<h1>` — the page's heading structure starts at
`<h2>` inside step rows, so the document outline has no title. Tab order runs through 78 card
buttons before reaching Generate, with no skip link.

## Minor Observations

- No `<h1>` on the generator. The page is called nothing.
- The Photoshoot / Retouch toggle floats centred with no anchor, reading as a stray control rather
  than the mode switch it is.
- The drop zone has no icon, no size or dimension guidance, and no example of a good product photo.
- `.seg`, `.chips` and `.tools` are three implementations of one segmented control.
- The sidebar's workspace name sits below the credits card as unstyled text, orphaned from both.
- The location grid loses its India / International grouping headings once scrolled — a sticky
  group label would keep position legible.
- Nothing in the app links to help or support. The reference you liked puts Academy and Support
  in the sidebar footer for a reason.

## Questions to Consider

- What if the uploaded piece never left the screen? Every decision after step 1 is about that
  object, and it disappears the moment you scroll.
- What if a jeweller could see one finished example — a real before/after — before spending a
  credit? Right now the only proof of quality is on the login page they have already passed.
- Is Composition four separate vocabularies, or is it two presets and an "advanced" disclosure?
  Six chip rows implies six decisions matter equally. They do not.
- Should the picker surfaces be light while the shell stays dark? The output is the product; the
  chrome is not.
