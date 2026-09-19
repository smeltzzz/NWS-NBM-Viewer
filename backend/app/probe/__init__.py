"""Point-query and meteogram extraction for map click inspection.

``app.probe.sampler``   lat/lon → native grid index + bilinear sample
``app.probe.service``   concurrent GRIB byte-range fetch + unit conversion
``app.probe.elements``  element plans (units, labels) independent of colormaps

HTTP surface: ``GET /api/v1/probe/point`` and ``GET /api/v1/probe/meteogram``.
"""

from __future__ import annotations

from app.probe.elements import ProbeElementPlan, probe_element_plan, probeable_elements
from app.probe.sampler import (
    GridIndex,
    GridSampler,
    SampleResult,
    clear_sampler_cache,
    get_grid_sampler,
)
from app.probe.service import (
    MeteogramPoint,
    MeteogramResponse,
    PointProbeResponse,
    ProbeService,
    get_probe_service,
    meteogram_forecast_hours,
)

__all__ = [
    "GridIndex",
    "GridSampler",
    "MeteogramPoint",
    "MeteogramResponse",
    "PointProbeResponse",
    "ProbeElementPlan",
    "ProbeService",
    "SampleResult",
    "clear_sampler_cache",
    "get_grid_sampler",
    "get_probe_service",
    "meteogram_forecast_hours",
    "probe_element_plan",
    "probeable_elements",
]
