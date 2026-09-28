# Contex Design System

A design language derived directly from the Contex mark and banner: a pure‑white
wordmark that **dissolves into a cyan‑to‑blue particle stream** on deep midnight
navy. Context, streaming out to agents.

Use this file as the brief when designing any Contex surface with Claude. Tokens
live in [`tokens.css`](./tokens.css); [`preview.html`](./preview.html) renders
them.

---

## The one idea

Everything hangs on a single device — **the context stream**: a gradient that
runs **cyan at the source → blue as it disperses**, always left‑to‑right, like
the particles trailing off the mark. Spend your boldness here and nowhere else.
One stream moment per view (a hero, a primary action, a key metric). Everything
around it stays quiet: dark navy, white and blue‑gray text, generous space.

> If a screen has two "wow" moments, it has none. Pick the one.

---

## Color

Three colors make something read as Contex; the rest is scaffolding.

| Token | Hex | Use |
|---|---|---|
| `--brand-ink` | `#0a0e1a` | The canvas. Nearly everything sits on it. |
| `--brand-cyan` | `#2fd4e6` | The stream's source. Primary accent, focus, links. |
| `--brand-blue` | `#3e6bf6` | The stream dispersed. Secondary accent, gradient end. |
| `--stream` | cyan→blue | The signature gradient. Primary buttons, hero, key data. |

Surfaces climb by getting **lighter navy**, never by adding gray:
`--surface-0` (page) → `--surface-1` (cards) → `--surface-2` (hover) →
`--surface-3` (active/popover), hairlines in `--hairline`.

Text: `--text` (primary, near‑white), `--text-muted` (the tagline blue‑gray, for
secondary copy), `--text-subtle` (captions). Semantic colors
(`--success`/`--warning`/`--danger`) are tuned to sit on navy — use the `-quiet`
tint as the fill and the solid color for the text/icon.

**Don't:** put the cyan→blue gradient on large flat areas (it becomes wallpaper
and kills the signature). Keep it to type, thin accents, small fills, and one
hero.

---

## Typography

A rounded‑geometric display to echo the wordmark, a calm grotesque for reading,
a mono for the thing Contex actually moves — data.

- **Display — Poppins** (600/700). Headlines and the wordmark voice. Rounded,
  wide, confident. Tighten tracking (`--tracking-tight`) at large sizes.
- **Body — Hanken Grotesk** (400/500/600). Paragraphs, UI, labels. Neutral and
  legible; it gets out of the display's way.
- **Utility — JetBrains Mono** (400/500). Payloads, keys, code, metrics, and
  **eyebrows** (uppercase, `--tracking-wide`). Mono signals "this is real data."

Scale runs `--text-display-xl` → `--text-xs` (see tokens). Hero steps are fluid;
everything else is fixed. Line height: `--leading-tight` for display,
`--leading-body` (1.6) for prose.

**Eyebrows carry meaning, not decoration.** A mono uppercase label above a
section should name a real category ("MCP TOOL", "PROJECT"), never a counter like
`01 / 02` unless the content is genuinely a sequence.

---

## Shape, space, elevation

- **Radius is rounded** — it echoes the letterforms. `--radius-md` (12px) for
  cards/inputs, `--radius-lg` for panels, `--radius-pill` for badges and small
  buttons. Never zero‑radius (that's a different brand).
- **Space** is a 4px scale (`--space-1`…`--space-24`); prefer generous vertical
  rhythm — the dark canvas wants room to breathe.
- **Elevation on dark** = a lighter surface + a soft shadow (`--elev-1/2/3`).
  Interactive/accent elements get the cyan **`--glow`**; keyboard focus is the
  cyan **`--ring`** (always visible, never removed).

---

## Motion

The house motion is the **stream‑in**: content arrives translating slightly
**left → right** with a fade, staggered, on `--ease` over `--dur`. It rhymes with
the particles. Use it on load and scroll reveals; keep hovers to a quick
`--dur-fast` lift or glow. Respect `prefers-reduced-motion` (tokens already zero
the durations). Restraint: an orchestrated hero reveal beats effects scattered
across the page.

---

## Components (behavioral spec)

- **Button / primary** — `--stream` fill, `--text-on-accent` label, pill radius,
  `--glow` on hover. The page's one gradient action.
- **Button / secondary** — transparent on a `--hairline` border, cyan text;
  border brightens to `--accent` on hover.
- **Button / ghost** — text‑only, cyan; for low‑stakes actions.
- **Card** — `--surface-1`, `--hairline` border, `--radius-lg`, `--elev-1`;
  lifts to `--surface-2` on hover.
- **Input** — `--surface-1` field, `--hairline` border, cyan `--ring` on focus.
  Placeholder in `--text-subtle`.
- **Badge / pill** — `-quiet` semantic fill + solid semantic text, pill radius,
  mono or small body.
- **Code / payload** — `--surface-2` block, `--font-mono`, cyan for keys/strings
  that matter. This is where the product's substance shows; treat it as content,
  not chrome.
- **Link** — `--link`, underline on hover; never rely on color alone.

---

## Voice

Write from the user's side of the screen. Plain verbs, sentence case, active
voice, no hype. Name things by what people control, not how the system is built.
An action keeps its name through the flow (a "Publish" button yields a
"Published" toast). Errors say what happened and how to fix it, in the product's
voice. Empty states invite an action. The brand line — *"The context bus for
agents."* — sets the register: confident, technical, unadorned.

---

## Quality floor (non‑negotiable)

Responsive to mobile · visible keyboard focus (`--ring`) · reduced motion
respected · AA contrast (the muted blue‑grays are for secondary text only, never
body on `--surface-0` below AA). Build these in silently; don't announce them.

---

## Using this with Claude

Paste this file (and `tokens.css`) into the design brief. The rules that matter
most, in order: **(1)** one cyan→blue stream moment per view; **(2)** dark navy
canvas, elevation by lighter navy; **(3)** Poppins display / Hanken body /
JetBrains mono; **(4)** rounded radii; **(5)** the gradient never becomes
wallpaper.
