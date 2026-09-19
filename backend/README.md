# NWS NBM Viewer — Backend

FastAPI service providing map tiles and metadata for the NOAA/NWS National
Blend of Models (NBM v4.2+): deterministic elements, probabilistic percentiles,
and exceedance thresholds.

## Stack

| Concern      | Choice                                             |
| ------------ | -------------------------------------------------- |
| Web          | FastAPI + uvicorn                                  |
| Raster/tiles | rio-tiler + rasterio (wheels bundle GDAL/PROJ)     |
| Data access  | boto3/botocore → NOAA NODD S3 (anonymous)          |
| Fallback     | httpx → NOAA NOMADS HTTP                           |
| Cache        | DiskCache (Redis ready, `CACHE_BACKEND=redis`)     |
| Scheduling   | APScheduler (inventory refresh, optional)          |
| Config       | pydantic-settings (`.env` at repo root)            |

## Run it

```bash
# From the repo root
npm run setup:backend              # create .venv + install requirements
npm run dev:backend                # uvicorn --reload on :8000

# Or by hand
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Then visit <http://127.0.0.1:8000/docs> (Swagger) and
<http://127.0.0.1:8000/health/live>.

## Endpoints (prefix `/api/v1`)

| Method | Path                                           | Purpose                              |
| ------ | ---------------------------------------------- | ------------------------------------ |
| GET    | `/health/live`                                 | Liveness probe                       |
| GET    | `/health/ready`                                | Readiness probe (cache init)         |
| GET    | `/health`                                      | Deep health incl. NODD/NOMADS probe  |
| GET    | `/version`                                     | Service + NBM defaults metadata      |
| GET    | `/nbm/domains`                                 | Domain catalog (co/ak/hi/pr/gu/oc)   |
| GET    | `/nbm/products`                                | core / qmd product streams           |
| GET    | `/nbm/elements` / `/nbm/elements/{var}`        | Deterministic core elements          |
| GET    | `/nbm/statistics`                              | Statistical processes (aggregates)   |
| GET    | `/nbm/percentiles`                             | Direct percentile families           |
| GET    | `/nbm/exceedance`                              | Threshold-probability families       |
| GET    | `/nbm/qmd`                                     | QMD variable catalog                 |
| GET    | `/nbm/layout/{domain}/{product}`               | Forecast-hour layout                 |
| GET    | `/tiles/demo/{z}/{x}/{y}.png`                  | Demo XYZ tile (PNG)                  |
| GET    | `/tiles/preview.png`                           | Full-field preview PNG               |
| GET    | `/tiles/capabilities`                          | Tile service description             |
| GET    | `/colormaps` / `/colormaps/{name}`             | Colour ramp catalog                  |

## Environment

Copy `.env.example` (repo root) to `.env`. All keys are documented there; the
most important backend ones:

| Key                    | Default                                    |
| ---------------------- | ------------------------------------------ |
| `S3_BUCKET_GRIB`       | `noaa-nbm-grib2-pds`                       |
| `S3_BUCKET_COG`        | `noaa-nbm-pds`                             |
| `AWS_DEFAULT_REGION`   | `us-east-1`                                |
| `TILE_CACHE_DIR`       | `./data/tilecache`                         |
| `PORT`                 | `8000`                                     |
| `NOMADS_BASE_URL`      | `https://nomads.ncep.noaa.gov/...`         |
| `CACHE_BACKEND`        | `disk` (or `redis`)                        |

## Architecture notes

- **Upstream access** uses NODD's public S3 buckets anonymously (no AWS keys).
  NOMADS HTTP serves as an automatic fallback for reliability-sensitive reads.
- **Caching** is DiskCache with explicit byte budgets and TTLs; tile and preview
  responses carry `Cache-Control` headers sized to NBM's hourly cadence.
- **Colour ramps** live in `app/colormaps.py` and are mirrored by the frontend
  so legends and rendered tiles always agree.
- **GRIB decode (pygrib/eccodes/cfgrib)** is intentionally optional — see the
  commented block in `requirements.txt`. The tile renderer currently serves a
  synthetic demo surface; the materialiser swaps in real NBM rasters through
  `services/tiles.py:get_reader()`.

## Tests

```bash
cd backend && python -m pytest -q
```
