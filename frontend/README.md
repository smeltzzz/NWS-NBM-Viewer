# NWS NBM Viewer — Frontend

Next.js 14 (App Router) + TypeScript + Tailwind CSS + MapLibre GL map viewer.

## Structure

| Path                      | Purpose                                        |
| ------------------------- | ---------------------------------------------- |
| `app/layout.tsx`          | Root layout, metadata, `100vh`/`overflow:hidden` shell |
| `app/page.tsx`            | Single map viewport                            |
| `app/api/health/route.ts` | Frontend health route (Docker prod healthcheck) |
| `components/map/MapContainer.tsx`    | Map engine: full-screen MapLibre canvas, HiDPI, dark vector basemap, per-domain camera presets |
| `components/map/WeatherRasterLayer.tsx` | Dynamic NBM WebP raster layer (`/api/v1/tiles/...`), opacity slider binding, flicker-free time scrubbing |
| `components/map/VectorOverlays.tsx`  | State/county borders, NWS CWAA, highways, rivers, hillshade (toggleable) |
| `components/map/ProbeReadout.tsx`    | Hover HUD: debounced `/api/v1/probe/point` readout near the cursor |
| `components/map/`         | `MapShell` (state), `Toolbar`, `OverlayControls`, `Legend`, `StatusBadge`, `layerIds` |
| `src/components/timeline/` | Temporal navigation suite (see below) |
| `lib/`                    | `config`, `api`, `nbm` (domain viewports, tile URLs), `basemaps`, `types`, `format` |

## Run

```bash
# From repo root
npm run dev:frontend          # or: cd frontend && npm run dev

# Typecheck / lint / build
npm run typecheck
npm run lint
npm run build
```

## Temporal navigation suite (`src/components/timeline/`)

Controls forecast-hour scrubbing, stepping and smooth looping animation:

| File                | Purpose                                                                                                                                    |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `TimelineBar.tsx`   | Fixed bottom dock: UTC valid-time banner (`Mon 18Z 23-OCT-2026`) + local time, `F024 +24h` projection chip, non-linear scrub track with 00Z day lines, synoptic ticks and precip-accumulation-window markers, hover preview + buffering spinner |
| `PlaybackControls.tsx` | Transport buttons (±1 step, ±24 h jump, first/last frame, play/pause, fps, forward/rocking loop) and global hotkeys: `←`/`→` step, `Space` play/pause, `[`/`]` speed, `Home`/`End` first/last |
| `AnimationEngine.ts` | rAF looping engine, 1–10 fps, forward vs rocking (ping-pong) loop, travel-direction-aware preload of the next 3 frames, buffering detection, fully leak-free `destroy()` |
| `TilePreloader.ts`  | Hidden-`Image` frame preloader with a bounded LRU cache (warms the HTTP cache MapLibre reads from — no blank-frame flashes) |
| `useTimeline.ts`    | React controller binding the engine to `MapShell` state                                                                                    |
| `frames.ts`         | Canonical NBM ladder (hourly f001–f036, 3-hourly f039–f072, 6-hourly f078–f264), index⇄hour scale math, tick computation, time formatting   |
| `viewportTiles.ts`  | Current viewport → concrete `{z}/{x}/{y}` URLs for preloading (public map API only)                                                        |

### Leak verification

```bash
npm run verify:timeline   # from frontend/
```

Compiles the DOM-free timeline modules and drives an extended looping session
(8 forward + 4 rocking loops over all 80 frames at 10 fps, flaky network,
seeks, speed/context switches) against an instrumented DOM shim. It asserts
the hidden-Image population stays inside the LRU bound, at most one rAF
handle is live, and that after `destroy()`: zero live `Image` objects, zero
pending rAF callbacks, zero timers, empty tile cache, inert instance, and no
canvas contexts ever created (the only canvas is MapLibre's, disposed by
`map.remove()`).

The dev server binds `0.0.0.0:3000`. Same-origin API calls go through the
Next.js rewrite proxy (see `next.config.js`), which forwards `/api/v1/*` to
`API_PROXY_TARGET` (default `http://127.0.0.1:8000`, `http://backend:8000`
inside Docker).

## Environment

`NEXT_PUBLIC_*` variables are inlined into the browser bundle:

| Key                            | Default     |
| ------------------------------ | ----------- |
| `NEXT_PUBLIC_API_BASE_URL`     | `/api/v1`   |
| `NEXT_PUBLIC_MAP_CENTER_LON`   | `-97.5`     |
| `NEXT_PUBLIC_MAP_CENTER_LAT`   | `38.5`      |
| `NEXT_PUBLIC_MAP_DEFAULT_ZOOM` | `4`         |
| `NEXT_PUBLIC_BASEMAP`          | `dark`      |
| `API_PROXY_TARGET`             | `http://127.0.0.1:8000` |
