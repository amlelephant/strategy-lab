# Design system

The interface language is carried over from [minFit](https://github.com/amlelephant/minFit)
deliberately rather than re-invented. Two projects that look like they were
made by the same person are worth more than two projects that each look
adequate, and the discipline that keeps a workout log calm — one question per
screen, one hero number, one accent — is exactly the discipline a research tool
needs. A dashboard that shows everything at once shows nothing.

`lab/web/static/lab.css` is the single source of truth. **Components read
tokens; nothing hardcodes a hex or a spacing value.** If a value is not in the
token block at the top of that file, it does not exist yet — add it with a
reason.

---

## 1. One question per screen

| Screen | The question it answers |
|---|---|
| `/` Console | What do I want to test? |
| `/run/<id>` Result | Did it work? |
| `/strategy/<key>` | What is this algorithm claiming? |

If a screen starts answering a second question, the second one moves. This is
the most important rule; most clutter is a violation of it.

The result page's answer to "did it work?" is a **Sharpe ratio at 84px with its
t-statistic directly beneath it**. Not the return. Return is the number
everybody reaches for and it is the one that tells you least — a 78% return
over four years is meaningless without knowing what the market did and how many
independent observations produced it. Both of those are one line below.

## 2. Color

Near-monochrome canvas, layered charcoals separated by hairline borders, plus
**one accent that always means "primary action or active state"**.

### Dark (default)

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#15171C` | Page background |
| `--surface` | `#1D2027` | Cards |
| `--surface-raised` | `#262A32` | Inputs, secondary buttons |
| `--surface-pressed` | `#2E323C` | Pressed state |
| `--border` | `#2F333D` | Hairline dividers and card outlines |
| `--border-strong` | `#3C424D` | Input borders |
| `--text-primary` | `#F4F5F7` | Primary text, hero numbers |
| `--text-secondary` | `#9AA0AC` | Labels, prose |
| `--text-tertiary` | `#5B616E` | Captions, axis labels, disabled |
| `--accent` | `#6E8BFF` | Primary action, active tab, the subject series |
| `--success` | `#43D08A` | A positive figure |
| `--danger` | `#F95C6E` | A negative figure, the drawdown series |
| `--warn` | `#E5B769` | Inconclusive, warnings |

### Light

Light is **not "everything white"**. The canvas (`#E9EBEF`) is the darkest
step, cards sit lighter on it, and only `--surface-raised` is pure white — so
the depth ordering is identical in both themes and a card never dissolves into
the page. The accent darkens to `#4257CE` for contrast on a light canvas.

The theme toggle writes `data-theme` on `<html>` and persists to
`localStorage`; with nothing set, `prefers-color-scheme` decides. Charts
re-read the palette from CSS custom properties and redraw on the `lab:theme`
event, so they are never stale in the other theme.

### Color means judgement, and only where judgement is real

Green and red mean *better* and *worse*, so they appear only where the platform
knows which is which: a return, a P&L, a Sharpe. They are never decoration and
never a category marker.

On a chart, **the subject series is the accent and the benchmark is a dashed
tertiary line** — not two competing colors. The comparison should read as "this
thing against the neutral ground", not as a race between two brands.

### Accent discipline

On any screen the accent should appear once or twice. If it is doing a third
decorative job, that is a bug.

## 3. Type

Inter, with a system fallback stack. **Every number that represents data uses
`tabular-nums`**, so digits never shift when a value updates.

| Class | Size / weight | Use |
|---|---|---|
| `.hero` | 84 / 700 | The one number a screen is built around |
| `.display` | 44 / 600 | Rare secondary hero |
| `.title` | 28 / 700 | Page title |
| `.headline` | 20 / 600 | Section header |
| `.body-strong` | 17 / 600 | Emphasised row value, strategy name |
| body | 17 / 400 | Prose |
| `.callout` | 15 / 500 | Secondary controls, form values |
| `.caption` | 13 / 500 | Metadata |
| `.overline` | 12 / 600, +0.06em, uppercase | Micro-labels (SHARPE RATIO, TOTAL RETURN) |

**One hero per screen.** Never more than about four type styles on one screen.
Uppercase only for `.overline`.

## 4. Spacing, radius, motion

4px scale, `--s1` (2px) through `--s11` (64px). Screen padding `--s6` (20px);
gaps between sections `--s8`–`--s9`.

Radii are generous: 8 / 10 / 12 / 16 / 24 / full. Primary buttons are pills.

Motion is 120 / 180 / 240ms on `cubic-bezier(0.22, 1, 0.36, 1)`, and exists
only to communicate a state change. Nothing loops, nothing bounces.
`prefers-reduced-motion` collapses every transition to 1ms.

## 5. Hairlines, not shadows

Depth comes from surface-color steps and 1px borders. There are no drop shadows
in this interface. The sticky header uses a translucent background with a
backdrop blur and a hairline bottom border; that is the whole elevation story.

## 6. Charts

Hand-rolled SVG, no library — the same call minFit makes. A charting library
brings its own visual opinions about grids, tooltips, legends and color, and
reconciling those with a token system costs more than drawing two polylines.
It also means no build step and no CDN: the page works offline.

Rules that are not negotiable:

* **Four quiet horizontal guides**, value labels on the right, three date ticks
  at the ends and the middle. No boxed axes, no vertical gridlines.
* **Every chart states its scale honestly.** The drawdown chart is clamped at
  0% because headroom above the running peak does not exist, and an axis
  offering +2% drawdown is a lie. This was a real bug, caught in review.
* **Wide content scrolls inside its own container.** The page body never
  scrolls horizontally.
* **Hovering reports values.** The scrub readout sits in the section header,
  not in a floating tooltip that covers the data.

## 7. Copy

Calm and specific. No exclamation marks, no emoji, no marketing adjectives.

The interface's job is partly to stop the user over-reading a result, so the
words do real work:

> t = 1.5 over 1,125 observations — **not distinguishable from luck** on this
> sample.

Not "moderate confidence". Not "promising". The empty state on the decision log
reads "Nothing logged — this strategy took every signal it generated", which is
information, rather than "No data".

## 8. Do / don't

**Do:** one hero number · one accent · hairline dividers · tabular figures ·
generous whitespace · state the sample size next to the estimate.

**Don't:** hardcode a value in a template · use the accent decoratively · add a
shadow for depth · exceed ~4 type styles on a screen · use emoji or exclamation
marks · put more than two primary affordances on a screen · show a Sharpe
without its t.
