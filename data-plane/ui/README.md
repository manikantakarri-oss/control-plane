# Agent Portal UI

Next.js 14 (App Router) + React 18 + Tailwind CSS 3, exported to static files.

## Why a static export

Databricks Apps runs a single command, and that command is `uvicorn` for the
Python API. There is no second process to host a Next server, so the UI is
built to plain files and served by the same FastAPI app.

```bash
npm install
npm run build          # writes ui/out/
cp -r out ../web       # what FastAPI serves, and what gets deployed
```

FastAPI mounts `web/` at `/` **after** every `/api/*` route, so the API keeps
priority and everything else falls through to the UI.

## Versions, and why these ones

| Package | Version | Reason |
| --- | --- | --- |
| next | 14.2.x | stable App Router with `output: "export"` |
| react / react-dom | 18.3.x | broadest component-library compatibility |
| tailwindcss | 3.4.x | ecosystem plugins target 3.x |
| @radix-ui/react-tabs | 1.1.x | accessible tabs, React 18-compatible |

Pinned exactly rather than with carets: a UI that is deployed as a build
artefact should rebuild identically months later.

## Layout

```
app/layout.tsx      html shell
app/page.tsx        tabs, catalog, routing between list and chat
app/globals.css     design tokens (light + dark) and component classes
components/         AgentCard, Chat, Admin, small shared bits
lib/api.ts          typed wrapper over the existing FastAPI routes
```

## Theming

Three states, not two: **Light**, **Dark**, and **System** (the default). The
toggle in the header cycles them and remembers the choice in `localStorage`.

Colours are CSS custom properties, defined light-first on `:root`. The dark
values appear twice on purpose:

```css
:root { --canvas: #f8f8f9; ... }                     /* light */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { ... }            /* follow the OS */
}
:root[data-theme="dark"] { ... }                     /* explicit choice wins */
```

The `:not([data-theme="light"])` guard is what lets someone pick Light on a
machine set to dark; without it the media query would always win. `color-scheme`
is set the same way so native form controls and scrollbars match.

An inline script in `app/layout.tsx` applies the stored choice **before the
page paints**, so a viewer who chose Light never sees a flash of dark while
React hydrates. It is wrapped in try/catch because `localStorage` throws in
private windows, and a theme must never stop the portal loading.

`?theme=light|dark|system` overrides for a visit and is stored - handy for
sharing or screenshotting a specific appearance.

## The backend was not changed

Every endpoint in `lib/api.ts` already existed. The only Python edit was which
directory FastAPI serves the UI from.
