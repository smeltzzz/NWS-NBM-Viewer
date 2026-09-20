"""Typed, exhaustive metadata for the NBM GRIB2 products.

The NBM product names are unfortunately not a single flat list: the same GRIB
short name can occur with a different accumulation period, statistical process,
or threshold.  This module keeps those distinctions explicit so a client can
build a selector without having to know the details of GRIB2 inventory syntax.

``NBM_CATALOG`` is the API-ready tree.  The models are intentionally kept free
of raster/decoder concerns; they are also useful to the inventory and
materialisation workers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceFile = Literal["core", "qmd"]
DomainCode = Literal["co", "ak", "hi", "pr", "gu", "oc"]


class DisplayUnits(BaseModel):
    """The two display systems exposed by the frontend."""

    model_config = ConfigDict(frozen=True)

    metric: str
    imperial: str


class ForecastInterval(BaseModel):
    """An inclusive forecast-hour range and its cadence."""

    model_config = ConfigDict(frozen=True)

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    step: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "ForecastInterval":
        if self.end < self.start:
            raise ValueError("forecast interval end must not precede start")
        return self

    @property
    def start_hour(self) -> int:
        """Descriptive alias used by ingestion code."""
        return self.start

    @property
    def end_hour(self) -> int:
        return self.end

    @property
    def step_hours(self) -> int:
        return self.step

    def hours(self) -> list[int]:
        return list(range(self.start, self.end + 1, self.step))


class DomainMetadata(BaseModel):
    """An NBM publication grid/domain."""

    model_config = ConfigDict(frozen=True)

    code: DomainCode
    name: str
    description: str
    family: Literal["RR"] = "RR"
    projection: str
    epsg: int | None = None
    resolution_km: float | None = Field(default=None, gt=0)
    resolution_range_km: tuple[float, float] | None = None
    cycles: list[int]
    products: list[SourceFile] = Field(default_factory=lambda: ["core", "qmd"])
    bbox: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def validate_domain(self) -> "DomainMetadata":
        if not self.cycles or any(c < 0 or c > 23 for c in self.cycles):
            raise ValueError("domain cycles must be UTC hours in the range 0..23")
        if self.resolution_km is None and self.resolution_range_km is None:
            raise ValueError("a domain needs a resolution or resolution range")
        if self.resolution_range_km is not None:
            low, high = self.resolution_range_km
            if low <= 0 or high < low:
                raise ValueError("invalid resolution range")
        return self


class ElementMetadata(BaseModel):
    """One selectable NBM element, including its GRIB inventory selector."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    description: str
    master_source: SourceFile
    # This is intentionally the colon-delimited selector seen in .idx files,
    # rather than only a GRIB short name.  Statistical qualifiers are kept in
    # the fields below because they are not consistently printed by wgrib2.
    grib_parameter: str
    display_units: DisplayUnits
    forecast_intervals: list[ForecastInterval]
    variable: str | None = None
    statistical_process: Literal["deterministic", "percentile", "probability", "categorical"] = (
        "deterministic"
    )
    statistical_value: int | float | str | None = None
    threshold: str | None = None
    accumulation_hours: int | None = Field(default=None, gt=0)
    tags: list[str] = Field(default_factory=list)

    @property
    def source_file(self) -> SourceFile:
        """Alias matching the wording used in NBM documentation."""
        return self.master_source

    @property
    def grib_identifier(self) -> str:
        return self.grib_parameter

    @property
    def units(self) -> DisplayUnits:
        return self.display_units

    @property
    def forecast_hour_intervals(self) -> list[ForecastInterval]:
        return self.forecast_intervals


class CategoryMetadata(BaseModel):
    """A first-level category in the catalog tree."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    description: str
    elements: list[ElementMetadata]


class ProductMetadata(BaseModel):
    """A master NBM GRIB2 file stream."""

    model_config = ConfigDict(frozen=True)

    code: SourceFile
    name: str
    description: str
    cycles: list[int]
    forecast_intervals: list[ForecastInterval]


class NbmCatalog(BaseModel):
    """Complete domain/category/element metadata tree."""

    model_config = ConfigDict(frozen=True)

    system: str = "NBM"
    version: str = "4.2+"
    products: list[ProductMetadata]
    domains: list[DomainMetadata]
    categories: list[CategoryMetadata]

    @property
    def elements(self) -> list[ElementMetadata]:
        return [element for category in self.categories for element in category.elements]

    def element_map(self) -> dict[str, ElementMetadata]:
        return {element.code: element for element in self.elements}


# Short aliases keep the schema pleasant to consume while the descriptive
# class names remain useful in generated documentation.
Domain = DomainMetadata
Category = CategoryMetadata
Element = ElementMetadata
Product = ProductMetadata
Catalog = NbmCatalog


# The operational NBM forecast-hour layout for the CONUS/core stream.  It is
# also the default shape for elements in the catalog.  The ingestion service
# still treats the .idx inventory as authoritative because NOAA can delay an
# individual hour.
CORE_FORECAST_INTERVALS = [
    ForecastInterval(start=1, end=36, step=1),
    ForecastInterval(start=39, end=192, step=3),
    ForecastInterval(start=198, end=264, step=6),
]
QMD_FORECAST_INTERVALS = [
    ForecastInterval(start=1, end=36, step=1),
    ForecastInterval(start=39, end=192, step=3),
]

_TEMP = DisplayUnits(metric="°C", imperial="°F")
_PRECIP = DisplayUnits(metric="mm", imperial="in")
_SPEED = DisplayUnits(metric="m/s", imperial="kt")
_DIRECTION = DisplayUnits(metric="°", imperial="°")
_DISTANCE = DisplayUnits(metric="m", imperial="ft")
_PERCENT = DisplayUnits(metric="%", imperial="%")
_DURATION = DisplayUnits(metric="h", imperial="h")
_INDEX = DisplayUnits(metric="index", imperial="index")
_WAVE = DisplayUnits(metric="m", imperial="ft")


def _id(variable: str, level: str, step: str | None = None) -> str:
    if step:
        return f":{variable}:{level}:{step}:"
    return f":{variable}:{level}:"


def _element(
    code: str,
    name: str,
    description: str,
    source: SourceFile,
    grib: str,
    units: DisplayUnits,
    *,
    intervals: list[ForecastInterval] | None = None,
    variable: str | None = None,
    process: Literal["deterministic", "percentile", "probability", "categorical"] = "deterministic",
    value: int | float | str | None = None,
    threshold: str | None = None,
    accumulation_hours: int | None = None,
    tags: list[str] | None = None,
) -> ElementMetadata:
    return ElementMetadata(
        code=code,
        name=name,
        description=description,
        master_source=source,
        grib_parameter=grib,
        display_units=units,
        forecast_intervals=list(
            intervals or (QMD_FORECAST_INTERVALS if source == "qmd" else CORE_FORECAST_INTERVALS)
        ),
        variable=variable,
        statistical_process=process,
        statistical_value=value,
        threshold=threshold,
        accumulation_hours=accumulation_hours,
        tags=list(tags or []),
    )


def _accumulation(
    code: str,
    name: str,
    hours: int,
    *,
    source: SourceFile = "core",
    variable: str = "APCP",
    units: DisplayUnits = _PRECIP,
    description: str | None = None,
    step_wording: str = "acc fcst",
) -> ElementMetadata:
    return _element(
        code,
        name,
        description or f"{hours}-hour accumulated {name.lower()}",
        source,
        _id(variable, "surface", f"0-{hours} hour {step_wording}"),
        units,
        variable=variable,
        accumulation_hours=hours,
        tags=["accumulation"],
    )


def _percentile(
    code: str,
    name: str,
    variable: str,
    value: int,
    units: DisplayUnits,
    level: str,
    step: str | None = None,
    *,
    source: SourceFile = "qmd",
    description: str | None = None,
) -> ElementMetadata:
    return _element(
        code,
        name,
        description or f"{value}th percentile of {name}",
        source,
        _id(variable, level, step),
        units,
        variable=variable,
        process="percentile",
        value=value,
        tags=["percentile", "probabilistic"],
    )


def _exceedance(
    code: str,
    name: str,
    variable: str,
    threshold: str,
    units: DisplayUnits,
    level: str,
    step: str,
    *,
    source: SourceFile = "qmd",
    accumulation_hours: int | None = None,
) -> ElementMetadata:
    return _element(
        code,
        name,
        f"Probability that {variable} exceeds {threshold}",
        source,
        _id(variable, level, step),
        units,
        variable=variable,
        process="probability",
        threshold=threshold,
        accumulation_hours=accumulation_hours,
        tags=["probability", "exceedance", "probabilistic"],
    )


DOMAINS: list[DomainMetadata] = [
    DomainMetadata(
        code="co",
        name="CONUS",
        description="Contiguous United States NBM grid",
        projection="Lambert Conformal Conic",
        epsg=6372,
        resolution_km=2.5,
        cycles=list(range(24)),
        bbox=(-126.0, 20.0, -66.0, 50.0),
    ),
    DomainMetadata(
        code="ak",
        name="Alaska",
        description="Alaska NBM grid",
        projection="Polar Stereographic",
        resolution_km=3.0,
        cycles=list(range(24)),
        bbox=(-180.0, 50.0, -125.0, 72.0),
    ),
    DomainMetadata(
        code="hi",
        name="Hawaii",
        description="Hawaii NBM grid",
        projection="Mercator",
        resolution_km=2.5,
        cycles=list(range(24)),
        bbox=(-161.0, 18.0, -154.0, 23.0),
    ),
    DomainMetadata(
        code="pr",
        name="Puerto Rico",
        description="Puerto Rico NBM grid",
        projection="Mercator",
        resolution_km=1.25,
        cycles=list(range(24)),
        bbox=(-69.0, 17.0, -64.0, 19.0),
    ),
    DomainMetadata(
        code="gu",
        name="Guam",
        description="Guam NBM grid",
        projection="Mercator",
        resolution_km=2.5,
        cycles=list(range(24)),
        bbox=(140.0, 10.0, 150.0, 18.0),
    ),
    DomainMetadata(
        code="oc",
        name="Oceanic",
        description="Oceanic global/regional NBM grid",
        projection="Global/Regional",
        resolution_range_km=(10.0, 13.0),
        cycles=[0, 6, 12, 18],
        bbox=(-180.0, -80.0, 180.0, 80.0),
    ),
]


TEMPERATURE_MOISTURE = CategoryMetadata(
    code="temperature_moisture",
    name="Temperature & Moisture",
    description="Near-surface temperature, moisture, and human-perceived temperature.",
    elements=[
        _element(
            "tmp",
            "2m Temperature",
            "Air temperature at 2 m above ground",
            "core",
            _id("TMP", "2 m above ground"),
            _TEMP,
            variable="TMP",
            tags=["temperature"],
        ),
        _element(
            "dpt",
            "2m Dewpoint",
            "Dewpoint temperature at 2 m above ground",
            "core",
            _id("DPT", "2 m above ground"),
            _TEMP,
            variable="DPT",
            tags=["temperature", "moisture"],
        ),
        _element(
            "rh",
            "Relative Humidity",
            "Relative humidity at 2 m above ground",
            "core",
            _id("RH", "2 m above ground"),
            _PERCENT,
            variable="RH",
            tags=["moisture"],
        ),
        _element(
            "apt",
            "Apparent Temperature",
            "Apparent/feels-like temperature at 2 m",
            "core",
            _id("APTMP", "2 m above ground"),
            _TEMP,
            variable="APTMP",
            tags=["temperature"],
        ),
        _element(
            "max",
            "Max Temperature",
            "Maximum 2 m temperature for the accumulation window",
            "core",
            _id("MAXT", "2 m above ground"),
            _TEMP,
            variable="MAXT",
            tags=["temperature", "maximum"],
        ),
        _element(
            "min",
            "Min Temperature",
            "Minimum 2 m temperature for the accumulation window",
            "core",
            _id("MINT", "2 m above ground"),
            _TEMP,
            variable="MINT",
            tags=["temperature", "minimum"],
        ),
        _element(
            "heat_index",
            "Heat Index",
            "Heat index at 2 m above ground",
            "core",
            _id("HEAT", "2 m above ground"),
            _TEMP,
            variable="HEAT",
            tags=["temperature", "derived"],
        ),
        _element(
            "wind_chill",
            "Wind Chill",
            "Wind chill temperature at 2 m above ground",
            "core",
            _id("WCHILL", "2 m above ground"),
            _TEMP,
            variable="WCHILL",
            tags=["temperature", "derived"],
        ),
    ],
)


PRECIPITATION_HYDROLOGY = CategoryMetadata(
    code="precipitation_hydrology",
    name="Precipitation & Hydrology",
    description="Deterministic precipitation accumulations and precipitation probabilities.",
    elements=[
        _accumulation("qpf_1h", "1-hour QPF", 1),
        _accumulation("qpf_6h", "6-hour QPF", 6),
        _accumulation("qpf_12h", "12-hour QPF", 12),
        _accumulation("qpf_24h", "24-hour QPF", 24),
        _accumulation("qpf_48h", "48-hour QPF", 48),
        _accumulation("qpf_72h", "72-hour QPF", 72),
        _element(
            "pop12",
            "Probability of Precipitation 12h",
            "Probability of measurable precipitation in 12 hours",
            "core",
            _id("POP12", "surface", "0-12 hour fcst"),
            _PERCENT,
            variable="POP12",
            tags=["probability"],
        ),
        _element(
            "pop06",
            "Probability of Precipitation 6h",
            "Probability of measurable precipitation in 6 hours",
            "core",
            _id("POP06", "surface", "0-6 hour fcst"),
            _PERCENT,
            variable="POP06",
            tags=["probability"],
        ),
        _element(
            "pop01",
            "Probability of Precipitation 1h",
            "Probability of measurable precipitation in 1 hour",
            "core",
            _id("POP01", "surface", "0-1 hour fcst"),
            _PERCENT,
            variable="POP01",
            tags=["probability"],
        ),
    ],
)


PQPF = CategoryMetadata(
    code="pqpf",
    name="Probabilistic QPF (PQPF)",
    description="QPF percentiles and probabilities of exceeding precipitation thresholds.",
    elements=[
        _percentile(
            "pqpf_10", "PQPF 10th percentile", "APCP", 10, _PRECIP, "surface", "0-6 hour acc fcst"
        ),
        _percentile(
            "pqpf_25", "PQPF 25th percentile", "APCP", 25, _PRECIP, "surface", "0-6 hour acc fcst"
        ),
        _percentile(
            "pqpf_50", "PQPF 50th percentile", "APCP", 50, _PRECIP, "surface", "0-6 hour acc fcst"
        ),
        _percentile(
            "pqpf_75", "PQPF 75th percentile", "APCP", 75, _PRECIP, "surface", "0-6 hour acc fcst"
        ),
        _percentile(
            "pqpf_90", "PQPF 90th percentile", "APCP", 90, _PRECIP, "surface", "0-6 hour acc fcst"
        ),
        _percentile(
            "pqpf_95", "PQPF 95th percentile", "APCP", 95, _PRECIP, "surface", "0-6 hour acc fcst"
        ),
        _exceedance(
            "qpf_gt_001",
            "QPF > 0.01 in",
            "APCP",
            "0.01 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "qpf_gt_010",
            "QPF > 0.10 in",
            "APCP",
            "0.10 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "qpf_gt_025",
            "QPF > 0.25 in",
            "APCP",
            "0.25 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "qpf_gt_050",
            "QPF > 0.50 in",
            "APCP",
            "0.50 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "qpf_gt_100",
            "QPF > 1.00 in",
            "APCP",
            "1.00 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "qpf_gt_200",
            "QPF > 2.00 in",
            "APCP",
            "2.00 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "qpf_gt_300",
            "QPF > 3.00 in",
            "APCP",
            "3.00 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
    ],
)


WINTER_WEATHER = CategoryMetadata(
    code="winter_weather",
    name="Winter Weather",
    description="Snow, ice, precipitation type, and winter-weather probabilities.",
    elements=[
        _accumulation(
            "snow_6h",
            "Snowfall Accumulation 6h",
            6,
            variable="ASNOW",
            units=_PRECIP,
            description="6-hour snowfall accumulation",
        ),
        _accumulation(
            "snow_24h",
            "Snowfall Accumulation 24h",
            24,
            variable="ASNOW",
            units=_PRECIP,
            description="24-hour snowfall accumulation",
        ),
        _accumulation(
            "snow_48h",
            "Snowfall Accumulation 48h",
            48,
            variable="ASNOW",
            units=_PRECIP,
            description="48-hour snowfall accumulation",
        ),
        _accumulation(
            "snow_72h",
            "Snowfall Accumulation 72h",
            72,
            variable="ASNOW",
            units=_PRECIP,
            description="72-hour snowfall accumulation",
        ),
        _percentile(
            "snow_p10", "Snow 10th percentile", "ASNOW", 10, _PRECIP, "surface", "0-6 hour acc fcst"
        ),
        _percentile(
            "snow_p50", "Snow 50th percentile", "ASNOW", 50, _PRECIP, "surface", "0-6 hour acc fcst"
        ),
        _percentile(
            "snow_p90", "Snow 90th percentile", "ASNOW", 90, _PRECIP, "surface", "0-6 hour acc fcst"
        ),
        _exceedance(
            "snow_gt_01",
            "Probability of snow > 0.1 in",
            "ASNOW",
            "0.1 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "snow_gt_1",
            "Probability of snow > 1.0 in",
            "ASNOW",
            "1.0 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "snow_gt_2",
            "Probability of snow > 2.0 in",
            "ASNOW",
            "2.0 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "snow_gt_4",
            "Probability of snow > 4.0 in",
            "ASNOW",
            "4.0 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "snow_gt_8",
            "Probability of snow > 8.0 in",
            "ASNOW",
            "8.0 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "snow_gt_12",
            "Probability of snow > 12.0 in",
            "ASNOW",
            "12.0 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _accumulation(
            "ice_6h",
            "Ice Accumulation 6h",
            6,
            variable="ICEACCR",
            units=_PRECIP,
            description="6-hour ice accretion",
        ),
        _accumulation(
            "ice_24h",
            "Ice Accumulation 24h",
            24,
            variable="ICEACCR",
            units=_PRECIP,
            description="24-hour ice accretion",
        ),
        _accumulation(
            "ice_48h",
            "Ice Accumulation 48h",
            48,
            variable="ICEACCR",
            units=_PRECIP,
            description="48-hour ice accretion",
        ),
        _exceedance(
            "ice_gt_001",
            "Probability of ice > 0.01 in",
            "ICEACCR",
            "0.01 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "ice_gt_01",
            "Probability of ice > 0.1 in",
            "ICEACCR",
            "0.1 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _exceedance(
            "ice_gt_025",
            "Probability of ice > 0.25 in",
            "ICEACCR",
            "0.25 in",
            _PERCENT,
            "surface",
            "0-6 hour acc fcst",
            accumulation_hours=6,
        ),
        _element(
            "ptype",
            "Categorical Precipitation Type",
            "Categorical rain, snow, sleet/ice pellets, or freezing rain",
            "core",
            _id("PTYPE", "surface"),
            _INDEX,
            variable="PTYPE",
            process="categorical",
            tags=["rain", "snow", "sleet", "freezing-rain"],
        ),
    ],
)


WIND = CategoryMetadata(
    code="wind",
    name="Wind",
    description="Near-surface wind, gusts, directions, and wind probabilities.",
    elements=[
        _element(
            "wind",
            "10m Wind Speed",
            "10 m wind speed",
            "core",
            _id("WIND", "10 m above ground"),
            _SPEED,
            variable="WIND",
            tags=["wind"],
        ),
        _element(
            "wdir",
            "10m Wind Direction",
            "10 m wind direction",
            "core",
            _id("WDIR", "10 m above ground"),
            _DIRECTION,
            variable="WDIR",
            tags=["wind"],
        ),
        _element(
            "u10",
            "U Wind Component 10m",
            "Zonal (east–west) 10 m wind vector component",
            "core",
            _id("UU", "10 m above ground"),
            _SPEED,
            variable="UU",
            tags=["wind", "vector"],
        ),
        _element(
            "v10",
            "V Wind Component 10m",
            "Meridional (north–south) 10 m wind vector component",
            "core",
            _id("VV", "10 m above ground"),
            _SPEED,
            variable="VV",
            tags=["wind", "vector"],
        ),
        _element(
            "gust",
            "Wind Gust",
            "10 m wind gust speed",
            "core",
            _id("GUST", "10 m above ground"),
            _SPEED,
            variable="GUST",
            tags=["wind"],
        ),
        _percentile(
            "wind_p10",
            "Wind speed 10th percentile",
            "WIND",
            10,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _percentile(
            "wind_p25",
            "Wind speed 25th percentile",
            "WIND",
            25,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _percentile(
            "wind_p50",
            "Wind speed 50th percentile",
            "WIND",
            50,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _percentile(
            "wind_p75",
            "Wind speed 75th percentile",
            "WIND",
            75,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _percentile(
            "wind_p90",
            "Wind speed 90th percentile",
            "WIND",
            90,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _percentile(
            "gust_p10",
            "Gust 10th percentile",
            "GUST",
            10,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _percentile(
            "gust_p25",
            "Gust 25th percentile",
            "GUST",
            25,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _percentile(
            "gust_p50",
            "Gust 50th percentile",
            "GUST",
            50,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _percentile(
            "gust_p75",
            "Gust 75th percentile",
            "GUST",
            75,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _percentile(
            "gust_p90",
            "Gust 90th percentile",
            "GUST",
            90,
            _SPEED,
            "10 m above ground",
            source="qmd",
        ),
        _exceedance(
            "gust_gt_34kt",
            "Gust > 34 kt",
            "GUST",
            "34 kt",
            _PERCENT,
            "10 m above ground",
            "instant fcst",
        ),
        _exceedance(
            "gust_gt_40kt",
            "Gust > 40 kt",
            "GUST",
            "40 kt",
            _PERCENT,
            "10 m above ground",
            "instant fcst",
        ),
        _exceedance(
            "gust_gt_50kt",
            "Gust > 50 kt",
            "GUST",
            "50 kt",
            _PERCENT,
            "10 m above ground",
            "instant fcst",
        ),
        _exceedance(
            "gust_gt_64kt",
            "Gust > 64 kt",
            "GUST",
            "64 kt",
            _PERCENT,
            "10 m above ground",
            "instant fcst",
        ),
    ],
)


SEVERE_CONVECTIVE = CategoryMetadata(
    code="severe_convective",
    name="Severe & Convective",
    description="Instability, radar-derived fields, and thunderstorm probabilities.",
    elements=[
        _element(
            "sbcape",
            "Surface-Based CAPE",
            "Surface-based convective available potential energy",
            "core",
            _id("CAPE", "surface"),
            DisplayUnits(metric="J/kg", imperial="J/kg"),
            variable="CAPE",
            tags=["convection"],
        ),
        _element(
            "mlcape",
            "Mixed-Layer CAPE",
            "Mixed-layer convective available potential energy",
            "core",
            _id("CAPE", "180-0 mb above ground"),
            DisplayUnits(metric="J/kg", imperial="J/kg"),
            variable="CAPE",
            tags=["convection"],
        ),
        _element(
            "vil",
            "Vertically Integrated Liquid",
            "Vertically integrated liquid water",
            "core",
            _id("VIL", "entire atmosphere"),
            DisplayUnits(metric="kg/m²", imperial="kg/m²"),
            variable="VIL",
            tags=["convection"],
        ),
        _element(
            "refc",
            "Composite Reflectivity",
            "Column maximum/composite radar reflectivity",
            "core",
            _id("REFC", "entire atmosphere"),
            DisplayUnits(metric="dBZ", imperial="dBZ"),
            variable="REFC",
            tags=["radar"],
        ),
        _element(
            "echo_tops",
            "Echo Tops",
            "Maximum radar echo top height",
            "core",
            _id("RETOP", "cloud top"),
            _DISTANCE,
            variable="RETOP",
            tags=["radar"],
        ),
        _element(
            "dry_thunderstorm_3h",
            "Dry Thunderstorm Probability 3h",
            "Probability of a dry thunderstorm in 3 hours",
            "qmd",
            _id("DPTSTM", "surface", "0-3 hour fcst"),
            _PERCENT,
            intervals=QMD_FORECAST_INTERVALS,
            variable="DPTSTM",
            process="probability",
            tags=["thunderstorm"],
        ),
        _element(
            "dry_thunderstorm_6h",
            "Dry Thunderstorm Probability 6h",
            "Probability of a dry thunderstorm in 6 hours",
            "qmd",
            _id("DPTSTM", "surface", "0-6 hour fcst"),
            _PERCENT,
            intervals=QMD_FORECAST_INTERVALS,
            variable="DPTSTM",
            process="probability",
            tags=["thunderstorm"],
        ),
        _element(
            "wet_thunderstorm_3h",
            "Wet Thunderstorm Probability 3h",
            "Probability of a wet thunderstorm in 3 hours",
            "qmd",
            _id("WETTSTM", "surface", "0-3 hour fcst"),
            _PERCENT,
            intervals=QMD_FORECAST_INTERVALS,
            variable="WETTSTM",
            process="probability",
            tags=["thunderstorm"],
        ),
        _element(
            "wet_thunderstorm_6h",
            "Wet Thunderstorm Probability 6h",
            "Probability of a wet thunderstorm in 6 hours",
            "qmd",
            _id("WETTSTM", "surface", "0-6 hour fcst"),
            _PERCENT,
            intervals=QMD_FORECAST_INTERVALS,
            variable="WETTSTM",
            process="probability",
            tags=["thunderstorm"],
        ),
    ],
)


AVIATION_FIRE_MARINE = CategoryMetadata(
    code="aviation_fire_marine",
    name="Aviation, Fire & Marine",
    description="Clouds, visibility, boundary layer, fire-weather, transport wind, and waves.",
    elements=[
        _element(
            "sky",
            "Sky/Cloud Cover",
            "Total cloud cover",
            "core",
            _id("TCDC", "entire atmosphere"),
            _PERCENT,
            variable="TCDC",
            tags=["cloud"],
        ),
        _element(
            "cig",
            "Ceiling Height",
            "Cloud ceiling height",
            "core",
            _id("CIG", "cloud ceiling"),
            _DISTANCE,
            variable="CIG",
            tags=["aviation"],
        ),
        _element(
            "lcb",
            "Lowest Cloud Base",
            "Height of the lowest cloud base",
            "core",
            _id("LCB", "cloud base"),
            _DISTANCE,
            variable="LCB",
            tags=["aviation"],
        ),
        _element(
            "vis",
            "Surface Visibility",
            "Surface horizontal visibility",
            "core",
            _id("VIS", "surface"),
            _DISTANCE,
            variable="VIS",
            tags=["aviation"],
        ),
        _element(
            "mht",
            "Mixing Height",
            "Planetary boundary-layer mixing height",
            "core",
            _id("MHT", "surface"),
            _DISTANCE,
            variable="MHT",
            tags=["fire-weather"],
        ),
        _element(
            "haines",
            "Haines Index",
            "Haines fire-weather stability/dryness index",
            "core",
            _id("HINDEX", "surface"),
            _INDEX,
            variable="HINDEX",
            tags=["fire-weather"],
        ),
        _element(
            "transport_wind",
            "Transport Wind",
            "Mean transport-layer wind",
            "core",
            _id("TRANSPWIND", "transport layer"),
            _SPEED,
            variable="TRANSPWIND",
            tags=["fire-weather"],
        ),
        _element(
            "swh",
            "Significant Wave Height",
            "Significant combined wave height",
            "core",
            _id("HTSGW", "surface"),
            _WAVE,
            variable="HTSGW",
            tags=["marine"],
        ),
    ],
)


NBM_CATALOG = NbmCatalog(
    products=[
        ProductMetadata(
            code="core",
            name="Core deterministic/statistical GRIB2",
            description="Hourly deterministic fields and core statistical products.",
            cycles=list(range(24)),
            forecast_intervals=CORE_FORECAST_INTERVALS,
        ),
        ProductMetadata(
            code="qmd",
            name="Quantile Mapping & Dressing (QMD)",
            description="Calibrated quantiles and threshold probabilities.",
            cycles=[0, 6, 12, 18],
            forecast_intervals=QMD_FORECAST_INTERVALS,
        ),
    ],
    domains=DOMAINS,
    categories=[
        TEMPERATURE_MOISTURE,
        PRECIPITATION_HYDROLOGY,
        PQPF,
        WINTER_WEATHER,
        WIND,
        SEVERE_CONVECTIVE,
        AVIATION_FIRE_MARINE,
    ],
)

# Convenient indexes for ingestion workers and callers that do not need the
# hierarchy.  Keep the model instances as values so callers cannot lose the
# full selector metadata.
DOMAIN_CATALOG = {domain.code: domain for domain in NBM_CATALOG.domains}
CATALOG = NBM_CATALOG
ELEMENT_CATALOG = NBM_CATALOG.element_map()
CATEGORY_CATALOG = {category.code: category for category in NBM_CATALOG.categories}


def catalog_dict() -> dict[str, object]:
    """Return a JSON-compatible copy of the complete catalog tree."""
    return NBM_CATALOG.model_dump(mode="json")


__all__ = [
    "CATEGORY_CATALOG",
    "Catalog",
    "Category",
    "Domain",
    "CATALOG",
    "CORE_FORECAST_INTERVALS",
    "DOMAINS",
    "DOMAIN_CATALOG",
    "DisplayUnits",
    "ELEMENT_CATALOG",
    "Element",
    "ElementMetadata",
    "ForecastInterval",
    "NBM_CATALOG",
    "NbmCatalog",
    "Product",
    "ProductMetadata",
    "QMD_FORECAST_INTERVALS",
    "catalog_dict",
]
