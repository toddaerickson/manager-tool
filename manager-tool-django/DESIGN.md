# Design system

A deliberate visual identity for Manager Tool. The point is to NOT look like generic AI-generated SaaS. Tokens live in `tailwind.config.js` and compile into `static/css/tw.css`, which every template loads via `base.html`/`landing.html`; do not hardcode fonts, ad-hoc hex colors, or default radii in individual templates. After changing tokens or template classes, rebuild: `TAILWINDCSS_VERSION=v3.4.17 tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/tw.css --minify` (CI's `TestCompiledCssCoverage` fails if you forget).

## Type

- **Display / headings:** Fraunces (variable serif). Applied to every `<h1>` automatically via a base rule, plus the sidebar brand mark. Use `font-display` to opt other elements in.
- **Body / UI:** Public Sans. The default for `<body>`, so most text needs no font class.
- Not Inter, not Space Grotesk, not the system-default stack. Loaded from Google Fonts in `base.html`.

## Color

- **Neutral base:** slate (`bg-slate-50` page, `text-slate-900`, `bg-slate-900` sidebar).
- **One accent:** teal, exposed as the `accent-*` scale (`accent-700` = #0f766e). Use it for primary actions, the active nav state, and primary links. One accent, used consistently, not a rainbow.
- **Semantic status only:** red / amber / emerald for error / warning / success and the team-health traffic lights. These carry meaning; never use them decoratively.

## Shape

- Tightened radius scale (`rounded`/`rounded-md`/`rounded-lg` all render small). Cards are flat: a 1px slate border, optional `shadow-sm`, no heavy elevation.

## Forbidden (the slop checklist)

Do not introduce any of these. They are the tells of generic AI-generated UI:

- Neon purple-to-blue gradients; gradient text ("Transform your X" hero copy).
- Glassmorphism: frosted, blurred, semi-transparent cards. Glowing accents.
- Bento-grid layouts where everything is an equal rounded tile. Rounded corners on absolutely everything.
- A thick colored border on a single side of a card.
- Decorative charts (pie/bar) used as filler without real data behind them.
- Generic 3D illustrations or stock photos.
- System-default sans for all text (the absence of a type choice is itself a tell).

## Enforcing it

Tokens are the enforcement: build with `accent-*`, `font-display`, and the radius utilities rather than raw values. When a new page or partial lands, check it against the forbidden list above. If a chart appears, it must plot real tenant data.
