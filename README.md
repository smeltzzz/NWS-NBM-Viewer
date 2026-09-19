# NWS NBM Viewer

A production-grade, real-time map viewer for the NOAA/NWS **National Blend of
Models (NBM v4.2+)** — deterministic elements, probabilistic percentiles, and
exceedance thresholds, served as web-mercator tiles over a MapLibre GL viewer.

NBM fuses the GFS, GEFS, NAM, NAMNest, SREF, HRRR and international ensemble
guidance into a gridded CONUS/Alaska/Hawaii/Puerto Rico/Guam/Oceanic mosaic
published hourly. Data is consumed anonymously from the NOAA Open Data
Dissemination (NODD) S3 buckets, with a NOMADS HTTP fallback.

## Architecture

```
┌──────────────┐  tile x/y/z png   ┌──────────────────────────────┐
│   frontend   │ ────────────────▶ │           backend             │
│  Next.js 14  │   /api/v1/*       │  FastAPI + rio-tiler          │
│ MapLibre GL  │ ◀──────────────── │  DiskCache / Redis            │
└──────────────┘                   └──────────┬───────────────────┘
                                             │ boto3 (anon) / httpx
                                             ▼
                              ┌───────────────────────────────┐
                              │  NODD S3 (noaa-nbm-grib2-pds, │
                              │  noaa-nbm-pds) + NOMADS HTTP  │
                              └───────────────────────────────┘
```

| Path          | Tech                                                              |
| ------------- | ----------------------------------------------------------------- |
| `/backend`    | FastAPI, rio-tiler/rasterio, boto3, diskcache, APScheduler        |
| `/frontend`   | Next.js 14 (App Router), TypeScript, Tailwind, MapLibre GL        |
| `/docker`     | Multi-stage build images (dev hot-reload + prod standalone)       |
| `docker-compose.yml` | Backend, frontend, Redis (optional cache)                  |

## Quick start

```bash
# 1. Install frontend dependencies (npm workspaces)
npm install

# 2. Create + install the backend venv
npm run setup:backend          # or: python3 -m venv backend/.venv && ...

# 3. Run both with hot reload
npm run dev                    # backend :8000 + frontend :3000
```

- API docs: <http://127.0.0.1:8000/docs>
- Backend health/version: <http://127.0.0.1:8000/api/v1/health/live>
- Frontend map viewer: <http://127.0.0.1:3000>

### Docker

```bash
cp .env.example .env
docker compose up --build      # hot reload for both services
```

## Configuration

Copy `.env.example` → `.env`. Every key is documented there. The essentials:

| Key                  | Default                    | Purpose                          |
| -------------------- | -------------------------- | -------------------------------- |
| `S3_BUCKET_GRIB`     | `noaa-nbm-grib2-pds`       | GRIB2 + `.idx` public bucket     |
| `S3_BUCKET_COG`      | `noaa-nbm-pds`             | Cloud-Optimized GeoTIFF bucket   |
| `AWS_DEFAULT_REGION` | `us-east-1`                | NODD region                      |
| `TILE_CACHE_DIR`     | `./data/tilecache`         | DiskCache root (git-ignored)     |
| `PORT` / `HOST`      | `8000` / `0.0.0.0`         | Backend bind                     |
| `FRONTEND_PORT`      | `3000`                     | Frontend bind (compose)          |

## Status & roadmap

**Implemented (this milestone)**

- Monorepo layout: npm workspaces, root scripts, `.gitignore`, `.env.example`.
- Backend: FastAPI + CORS + versioned `/api/v1` router, health (liveness /
  readiness / deep-upstream), version metadata, NBM domain/product/element/
  percentile/exceedance catalog, forecast-layout endpoint, XYZ tile rendering
  (rio-tiler) with colormap application and DiskCache-backed caching, colour
  ramp catalog.
- Frontend: Next.js 14 App Router + TypeScript + Tailwind, full-viewport map
  layout, MapLibre GL with a demo tile source, layer/legend/forecast-hour UI,
  `/api/health` proxy route.
- Docker: dev (hot-reload) + prod (standalone) image stages and `docker-compose.yml`.

**Next milestones**

1. NODD GRIB inventory service — latest-cycle discovery + `.idx` parsing to the
   authoritative per-cycle forecast-hour matrix.
2. GRIB decode/materialise pipeline (`pygrib`/`eccodes`/`cfgrib` → Zarr/COG),
   swapping the demo surface out of `services/tiles.py:get_reader()`.
3. Redis cache backend activation and multi-worker readiness.

## License

MIT (code). NBM data is public domain, courtesy of NOAA/NWS.
