# NWS NBM Viewer

A production-grade, real-time map viewer for the NOAA/NWS **National Blend of
Models (NBM v4.2+)** — deterministic elements, probabilistic percentiles, and
exceedance thresholds, rendered as WebP map tiles from the raw GRIB2 stream,
served through a caching edge and a MapLibre GL front end.

NBM fuses the GFS, GEFS, NAM, NAMNest, SREF, HRRR and international ensemble
guidance into a gridded CONUS/Alaska/Hawaii/Puerto Rico/Guam/Oceanic mosaic
published **hourly**. This viewer consumes it anonymously from the NOAA Open
Data Dissemination (NODD) S3 buckets, with a NOMADS HTTP fallback, and keeps
the map warm, current and honest about failures — 24/7.

## What "production-ready" means here

| Concern                  | Implementation                                                              |
| ------------------------ | --------------------------------------------------------------------------- |
| Always-current runs      | Background **cycle poller** (APScheduler) discovers every NOAA run within ~10 min; a shared *run pointer* makes `/runs/latest` a pure cache read |
| First-paint speed        | **Warm-up renderer** pre-bakes headline hours/zooms for a fresh cycle in the background; the UI shows real progress |
| Tile delivery            | WebP, immutable URLs (`/cycle/fhour/x/y`), edge micro-cache (1 d) + backend disk cache (72 h), stale-on-error |
| Upstream turbulence      | Bounded retries with exponential backoff + `Retry-After` honouring; throttle cooldown; HTTP 503+`Retry-After` passthrough instead of melting |
| Compression              | gzip (universal) and brotli (edge image) for JSON/JS/CSS — **never** re-encoded WebP/PNG |
| Failure UX               | Per-panel error boundaries with retry, de-duplicated toasts for feed delay / throttling, tile 404s coalesced into one message |
| State across replicas    | Optional Redis meta cache (`CACHE_BACKEND=redis`); poller leadership via `flock` so N tasks run exactly **one** NOAA loop |
| Disk hygiene             | Hourly retention sweep: 72 h latest-cycle, 30 d historical, TTL-capped writes |
| Ops surface              | `/health/live`, `/health/ready`, `/api/v1/poller/status|poll|evict`, `X-Cache-Status` at the edge |
| CI                       | black + flake8 + 144-test pytest, tsc + ESLint + `next build` + headless leak audits, `nginx -t` image build, compose validation |

## Architecture

```
                       ┌─────────────────────────────────────────────────────┐
 browser ─── 80/443 ──▶│  Edge gateway  (docker/nginx.Dockerfile)            │
                       │  gzip+brotli · tiles 1 d micro-cache · stale-on-err │
                       │  API micro-cache 10-15 s · CORS preflight · CSP     │
                       └───────────────┬─────────────────────┬───────────────┘
                                       │ /*                  │ /api/v1/*
                              ┌────────▼────────┐   ┌────────▼──────────────────────┐
                              │  frontend        │   │  backend (FastAPI, N workers) │
                              │  Next.js 14      │   │  tiles · runs · products      │
                              │  MapLibre GL     │◀──┤  /api/health proxy (rewrite)  │
                              │  timeline engine │   │  APScheduler: cycle poll      │
                              └──────────────────┘   │  warm-up · 72 h retention   │
                                                     └───┬───────────────┬─────────┘
                                                 diskcache│        Redis  │ (optional, multi-host)
                                            /data/tilecache└────┬──────────┘
                                                                │ boto3 (anon) / httpx
                                                    ┌───────────▼───────────────────┐
                                                    │ NODD S3: noaa-nbm-grib2-pds,  │
                                                    │ noaa-nbm-pds + NOMADS HTTP    │
                                                    └───────────────────────────────┘
```

| Path               | Tech                                                              |
| ------------------ | ----------------------------------------------------------------- |
| `/backend`         | FastAPI, rio-tiler/rasterio, boto3, diskcache/Redis, APScheduler  |
| `/frontend`        | Next.js 14 (App Router), TypeScript, Tailwind, MapLibre GL       |
| `/docker`          | Multi-stage dev+prod images; gateway compiles ngx_brotli at build |
| `docker-compose.prod.yml` | Gateway, backend×4 workers, frontend, Redis, optional poller daemon |
| `/deploy`          | systemd units (VPS) + ECS task definitions (AWS) + nginx site conf |

## Quick start (development)

```bash
npm run setup          # installs frontend (npm workspaces) + backend .venv
cp .env.example .env
npm run dev          # backend :8000 (reload) + frontend :3000
```

- Map viewer: <http://127.0.0.1:3000>
- OpenAPI docs: <http://127.0.0.1:8000/docs>
- Hot containers instead of local installs: `npm run docker:up`

## Production deployment

### Option A — Docker Compose (single host / VM) — recommended

```bash
git clone https://github.com/smeltzzz/NWS-NBM-Viewer && cd NWS-NBM-Viewer
cp .env.example .env          # set CORS_ORIGINS + your domain bits
docker compose -f docker-compose.prod.yml up -d --build
curl -I localhost/api/v1/runs/latest   # X-Cache-Status: MISS then HIT
```

What you get, in one command:

- **gateway** — nginx 1.27 + ngx_brotli compiled from source against the exact
  base version (the image runs `nginx -t` at build time), port `${GATEWAY_PORT:-80}`;
  tile micro-cache with stale-on-error and background refresh.
- **backend** — prod image: 4 uvicorn workers, non-root, healthchecked, shared
  `tile-cache-prod` volume; the poller runs inside the winning worker.
- **frontend** — `output: "standalone"` Next.js prod image; `/api/health`
  proxied to the backend.
- **redis** — shared pointer/meta cache, `allkeys-lru` (every key is re-derivable).
- *(optional)* dedicated poller daemon: `docker compose -f docker-compose.prod.yml
  --profile poller up -d` — still elects the leader through the same lock file,
  so running it alongside the in-worker scheduler is safe and is how you keep
  warm-up render bursts off the request path.

TLS: front with your existing LB/CDN, or add a `443` server block + certs to a
`docker/nginx.conf` drop-in; the edge already forwards `X-Forwarded-Proto` and
emits HSTS only for https requests.

### Option B — bare Linux VPS (systemd + distro nginx)

```bash
sudo cp deploy/systemd/nbm-*.service /etc/systemd/system/
sudo install -m 0640 -o root -g nbm deploy/nbm-viewer.env.example /etc/nbm-viewer/nbm.env
sudo cp deploy/nginx/nbm-viewer.conf /etc/nginx/conf.d/     # optional brotli: see header
sudo systemctl daemon-reload
sudo systemctl enable --now nbm-api nbm-frontend
journalctl -fu nbm-api
```

The API unit embeds the 24/7 loop (poller + warm-up + retention) via the
in-process scheduler; `deploy/systemd/nbm-poller.service` exists if you prefer
the loop as its own unit. Hardened units (ProtectSystem, MemoryMax, fd limits).

### Option C — AWS ECS Fargate + ALB

`deploy/aws/README.md` — one ALB, path rules (`/api/v1/*` → backend:8000,
`/*` → frontend:3000), EFS for the tile cache (flock keeps one poller across
tasks), ElastiCache Redis for the pointer store, ready-made task definitions
in `deploy/aws/ecs/`.

## 24/7 operations

### Run freshness model

The backend publishes a **run pointer** per domain+product:

```jsonc
GET /api/v1/runs/latest?domain=conus&product=nbrnpcp
{
  "run": { "date": "20260920", "cycle": 14, "available_forecast_hours": [0,1,2,…] },
  "pointer": {
    "cycle": "2026092014",          // poller-authoritative
    "state": "ingesting",            // ingesting | complete
    "source": "poller",              // poller (background) | live (on-demand)
    "age_seconds": 240,
    "stale": false,                  // NOAA hasn't published newer in a while
    "degraded": false,               // last live check failed
    "warmup": { "status": "running", "total": 176, "rendered": 61, "cached_hits": 40, "percent": 57 }
  }
}
```

- The **poller** ticks every `POLLER_INTERVAL_MINUTES` (10): probes the S3
  listing for a newer cycle for every enabled domain/product, publishes the
  pointer, then **warms up** the fresh cycle (headline `POLLER_WARMUP_ZOOMS`
  tiles at `POLLER_WARMUP_HOURS`), capped at `POLLER_WARMUP_CONCURRENCY` renders.
- A cycle is **complete** when its expected products stopped growing for
  `POLLER_COMPLETE_WINDOW_HOURS`.
- If the poller is off/silent, `/runs/latest` falls back to a live S3
  discovery and *publishes* what it saw; if NOAA itself errors, it serves the
  last known run flagged `stale`/`degraded` — the UI toasts the condition
  instead of white-screening.

### Operator endpoints

| Probe                          | Meaning                                                       |
| ------------------------------ | ------------------------------------------------------------- |
| `GET /health/live`             | process up (used by docker/systemd healthchecks)              |
| `GET /health/ready`            | cache + upstream config usable; includes poller heartbeat     |
| `GET /api/v1/poller/status`    | job schedule, next run, last error, warm-up progress, lock owner |
| `POST /api/v1/poller/poll`     | force one poll tick now (429 if <30 s ago; never races the leader) |
| `POST /api/v1/poller/evict`    | run the 72 h/30 d retention sweep immediately                 |

Enable these only internally: `POLLER_OPS_ENDPOINTS_ENABLED=false` in prod,
restrict by path at the edge, or front the port with auth.

### Degradation ladder (all automatic)

```
S3 503/429 → bounded retry+backoff (Retry-After ≤ 60 s honoured) → 30 s throttle cooldown
tile render fails → 503 + Retry-After, edge serves stale cache copy, UI toast "busy"
NOAA publishes slowly → pointer.stale → UI info toast, viewer keeps last cycle
poller dead → /runs/latest live-discovery (source=live), zero downtime
NOAA unreachable → last pointer flagged degraded, tiles keep serving from cache
Redis down → CacheService falls back to disk silently (health/ready reports it)
panel crash → ErrorBoundary "Try again", other panels keep working
```

## Configuration

`.env.example` is the complete, annotated reference (every backend setting,
grouped into 12 sections, 1:1 with `app.config.Settings`; CI and the docs use
the same keys). Highlights:

| Key                                    | Default   | Notes                                        |
| -------------------------------------- | --------- | -------------------------------------------- |
| `POLLER_ENABLED` / `SCHEDULER_ENABLED` | `true` (prod image) | in-process background loop        |
| `POLLER_INTERVAL_MINUTES`              | 10        | S3-listing cadence per domain+product        |
| `POLLER_WARMUP_ENABLED`                | true      | pre-render first paint for fresh cycles      |
| `CACHE_BACKEND`                        | `disk`    | `redis` when running >1 host                 |
| `CACHE_MAX_AGE_HOURS` / `CACHE_EVICTION_MINUTES` | 72 / 60 | disk retention window + hourly sweep |
| `S3_RETRY_*`, `S3_THROTTLE_COOLDOWN_SECONDS` | 3 / 0.5 s / 8 s / 30 s | graceful degradation budget     |
| `POINTER_FRESHNESS_SECONDS`            | 900       | when "current" becomes "stale"               |

## Frontend behaviour worth knowing

- Polls `/runs/latest` every **10 min** (aligned with the poller; edge-cached
  15 s, so it's ~free) and immediately on tab re-focus.
- **Touch map**: one-finger pan, pinch/double-tap zoom; pitch/rotate disabled so
  the gesture budget stays free for the timeline scrubber (safe-area-padded dock).
- Tile 404s (hours NOAA hasn't posted yet, unpublished probabilistic percentiles)
  are coalesced into one quiet toast, not hundreds of console errors.
- Wind/particles, hover probe, station meteogram (with PNG/CSV export),
  playback at 1–20 fps with tile preloading and zero measured leaks (see audits).

## Development workflow

```bash
npm run verify            # typecheck + eslint + backend pytest
npm run lint:backend      # flake8 (100-col, black-compatible policy in backend/.flake8)
npm run format:backend    # black --line-length 100
npm run typecheck         # tsc --noEmit (frontend)
npm run verify:timeline   # headless leak audit of the animation engine
```

- Backend tests: `cd backend && .venv/bin/python -m pytest -q` — 144 tests
  covering the tile pipeline (real GRIB decode), poller/pointer semantics,
  retry/Retry-After behaviour, retention, ops endpoints, and the API surface.
- Gateway config: validate without Docker via
  `python -c "import crossplane; print(crossplane.parse('docker/nginx.conf', single=True))"`
  — CI builds `docker/nginx.Dockerfile` which runs the authoritative `nginx -t`.
- CI: `.github/workflows/ci.yml` — backend (black, flake8, pytest),
  frontend (tsc, eslint, `next build`, both headless audits), infra
  (gateway image build + `nginx -t` + compose validation), plus prod
  Dockerfile target builds.

## License

MIT — NOAA data is public domain; attribution is appreciated, not required.
