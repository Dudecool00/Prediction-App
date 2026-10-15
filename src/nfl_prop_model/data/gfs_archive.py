"""Strict, small OPeNDAP reads of archived operational GFS forecasts."""

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.team_depth import aware

BASE = "https://tds.gdex.ucar.edu/thredds/dodsC/files/g/d084001"
TEMPERATURE = "Temperature_height_above_ground"
U_WIND = "u-component_of_wind_height_above_ground"
V_WIND = "v-component_of_wind_height_above_ground"
RAIN = "Precipitation_rate_surface"
VARIABLES = (TEMPERATURE, U_WIND, V_WIND, RAIN)
BUFFER = timedelta(hours=6, minutes=10)


def forecast_selection(kickoff: datetime, cutoff: datetime) -> dict[str, Any]:
    kickoff, cutoff = aware(kickoff), aware(cutoff)
    if cutoff >= kickoff:
        raise DataQualityError("Weather cutoff must precede kickoff")
    usable = cutoff - BUFFER - timedelta(microseconds=1)
    run = usable.replace(hour=usable.hour // 6 * 6, minute=0, second=0, microsecond=0)
    # Nearest three-hour point, ties toward the later time; at most 90 minutes away.
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    valid = epoch + timedelta(hours=3 * math.floor((kickoff - epoch).total_seconds() / 10800 + 0.5))
    lead = int((valid - run).total_seconds() // 3600)
    if lead < 3 or lead > 240 or lead % 3:
        raise DataQualityError("No admissible 3-240h archived forecast")
    filename = f"gfs.0p25.{run:%Y%m%d%H}.f{lead:03d}.grib2"
    return {
        "run_utc": run.isoformat(),
        "assumed_available_utc": (run + BUFFER).isoformat(),
        "valid_utc": valid.isoformat(),
        "lead_hours": lead,
        "valid_offset_minutes": (valid - kickoff).total_seconds() / 60,
        "url": f"{BASE}/{run.year}/{run:%Y%m%d}/{filename}",
    }


def grid_point(latitude: float, longitude: float) -> tuple[int, int]:
    if not math.isfinite(latitude + longitude) or not (
        -90 <= latitude <= 90 and -180 <= longitude <= 180
    ):
        raise DataQualityError("Invalid weather coordinates")
    row = math.floor((90 - latitude) * 4 + 0.5)
    column = math.floor((longitude % 360) * 4 + 0.5) % 1440
    return row, column


def point_url(selection: dict[str, Any], latitude: float, longitude: float) -> str:
    row, column = grid_point(latitude, longitude)
    projection = ",".join(
        f"{name}[0]" + ("[0]" if name != RAIN else "") + f"[{row}][{column}]" for name in VARIABLES
    )
    return str(selection["url"]) + ".ascii?" + quote(projection + ",reftime", safe=",")


def _blocks(metadata: str) -> dict[str, str]:
    blocks = re.findall(r"^    ([\w-]+) \{\n(.*?)^    \}", metadata, re.M | re.S)
    if len({key for key, _ in blocks}) != len(blocks):
        raise DataQualityError("Duplicate GFS metadata block")
    return dict(blocks)


def _attribute(block: str, name: str) -> str:
    values = re.findall(rf'^        \w+ {re.escape(name)} "([^"\n]*)";', block, re.M)
    if len(values) != 1:
        raise DataQualityError(f"Missing/ambiguous GFS metadata attribute: {name}")
    return str(values[0])


def validate_metadata(payload: bytes, selection: dict[str, Any]) -> dict[str, list[str]]:
    """Verify units, coordinates, parameter and forecast identity before accepting values."""
    if len(payload) > 600_000:
        raise DataQualityError("Oversized GFS metadata")
    blocks = _blocks(payload.decode("utf-8"))
    run = datetime.fromisoformat(selection["run_utc"])
    expected_time = f"Hour since {run:%Y-%m-%dT%H:%M:%SZ}"
    for axis, units in (("lat", "degrees_north"), ("lon", "degrees_east")):
        if _attribute(blocks.get(axis, ""), "units") != units:
            raise DataQualityError("GFS coordinate units changed")
    if (
        _attribute(blocks.get("reftime", ""), "units") != expected_time
        or _attribute(blocks["reftime"], "standard_name") != "forecast_reference_time"
    ):
        raise DataQualityError("GFS reference time metadata disagrees with selected run")
    result = {}
    for name, units, parameter in (
        (TEMPERATURE, "K", "VAR_0-0-0_L103"),
        (U_WIND, "m/s", "VAR_0-2-2_L103"),
        (V_WIND, "m/s", "VAR_0-2-3_L103"),
        (RAIN, "kg.m-2.s-1", "VAR_0-1-7_L1"),
    ):
        block = blocks.get(name, "")
        if (
            _attribute(block, "units") != units
            or _attribute(block, "Grib_Variable_Id") != parameter
            or _attribute(block, "Grib2_Generating_Process_Type") != "Forecast"
            or _attribute(block, "Grib2_Statistical_Process_Type") != "UnknownStatType--1"
        ):
            raise DataQualityError(f"Unsupported GFS units/parameter/process: {name}")
        coordinates = _attribute(block, "coordinates").split()
        expected_count = 4 if name == RAIN else 5
        if (
            len(coordinates) != expected_count
            or coordinates[0] != "reftime"
            or not re.fullmatch(r"time\d*", coordinates[1])
            or coordinates[-2:] != ["lat", "lon"]
        ):
            raise DataQualityError("Unsupported GFS coordinate layout")
        time_block = blocks.get(coordinates[1], "")
        if (
            _attribute(time_block, "units") != expected_time
            or _attribute(time_block, "standard_name") != "time"
        ):
            raise DataQualityError("GFS valid-time units changed")
        if name != RAIN:
            height = coordinates[2]
            if not re.fullmatch(r"height_above_ground\d*", height) or (
                _attribute(blocks.get(height, ""), "units") != "m"
            ):
                raise DataQualityError("GFS height units changed")
        result[name] = coordinates[1:]
    return result


def parse_point(
    payload: bytes,
    metadata: bytes,
    selection: dict[str, Any],
    latitude: float,
    longitude: float,
) -> dict[str, float | None]:
    coordinates = validate_metadata(metadata, selection)
    if len(payload) > 20_000:
        raise DataQualityError("Oversized GFS point response")
    parts = re.split(r"\n-{20,}\n", payload.decode("ascii"))
    if len(parts) != 2 or not parts[0].rstrip().endswith(
        str(selection["url"]).rsplit("/", 1)[1] + ";"
    ):
        raise DataQualityError("Wrong GFS point dataset")
    values: dict[str, float] = {}
    current: str | None = None
    for line in parts[1].splitlines():
        if not line.strip():
            continue
        if current is not None:
            if re.fullmatch(r"(?:\[0\])*,? ?(?:[-+\w.]+)", line) is None:
                raise DataQualityError("Malformed GFS point scalar")
            values[current] = float(line.rsplit(",", 1)[-1].strip())
            current = None
            continue
        scalar = re.fullmatch(r"reftime, (\S+)", line)
        if scalar:
            if "reftime" in values:
                raise DataQualityError("Duplicate GFS reference time")
            values["reftime"] = float(scalar[1])
            continue
        # Every projected array/map must be a single value, not a larger slice.
        label = re.fullmatch(r"([\w.-]+)((?:\[1\])+)", line)
        if label:
            current = label[1]
            if current in values:
                raise DataQualityError("Duplicate GFS point field")
            values[current] = math.nan  # replaced by its immediately following scalar
            continue
        raise DataQualityError("Malformed GFS point response")
    if current is not None:
        raise DataQualityError("Missing GFS point scalar")
    row, column = grid_point(latitude, longitude)
    if values.get("reftime") != 0:
        raise DataQualityError("GFS reference time mismatch")
    parsed: dict[str, float | None] = {}
    for name, axes in coordinates.items():
        expected = [float(selection["lead_hours"])]
        if name != RAIN:
            expected.append(2.0 if name == TEMPERATURE else 10.0)
        expected.extend([90 - row / 4, column / 4])
        if any(
            values.get(f"{name}.{axis}") != value
            for axis, value in zip(axes, expected, strict=True)
        ):
            raise DataQualityError("GFS time/height/grid mismatch")
        key = f"{name}.{name}"
        if key not in values:
            raise DataQualityError("Missing GFS point variable")
        value = values[key]
        parsed[name] = value if math.isfinite(value) else None
    t, u, v, rain = (parsed[name] for name in VARIABLES)
    if (
        (t is not None and not 150 <= t <= 350)
        or (u is not None and abs(u) > 150)
        or (v is not None and abs(v) > 150)
        or (rain is not None and not 0 <= rain <= 1)
    ):
        raise DataQualityError("GFS point outside broad physical bounds")
    return {
        "temperature_c": t - 273.15 if t is not None else None,
        "wind_speed_m_s": math.hypot(u, v) if u is not None and v is not None else None,
        "precipitation_rate_mm_h": rain * 3600 if rain is not None else None,
        "grid_latitude": 90 - row / 4,
        "grid_longitude": column / 4,
    }
