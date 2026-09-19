# NWS NBM Viewer — Frontend

Next.js 14 (App Router) + TypeScript + Tailwind CSS + MapLibre GL map viewer.

## Structure

| Path                      | Purpose                                        |
| ------------------------- | ---------------------------------------------- |
| `app/layout.tsx`          | Root layout, metadata, `100vh`/`overflow:hidden` shell |
| `app/page.tsx`            | Single map viewport                            |
| `app/api/health/route.ts` | Frontend health route (Docker prod healthcheck) |
| `components/map/`         | `MapShell`, `MapView`, `Toolbar`, `Legend`, `StatusBadge` |
| `lib/`                    | `config`, `api`, `nbm`, `basemaps`, `types`, `format` |

## Run

```bash
# From repo root
npm run dev:frontend          # or: cd frontend && npm run dev

# Typecheck / lint / build
npm run typecheck
npm run lint
npm run build
```

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
