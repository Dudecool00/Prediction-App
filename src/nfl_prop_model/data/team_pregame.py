"""Compact historical pregame source audit; untimed records never become forecasts."""

import hashlib
import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_context import load_context_cache
from nfl_prop_model.data.team_games import load_team_cache
from nfl_prop_model.features.team_epa import HORIZONS, weekly_cutoffs

RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
# City/grid forecasts, not stadium thermometers. Fixed indoor exposure is known in advance;
# retractable/canopy venues stay unknown instead of using the postgame roof field.
VENUES: dict[str, tuple[str, str, float | None]] = {
    "ATL97": ("Atlanta", "US", None),
    "BAL00": ("Baltimore", "US", 1.0),
    "BOS00": ("Foxborough", "US", 1.0),
    "BUF00": ("Orchard Park", "US", 1.0),
    "CAR00": ("Charlotte", "US", 1.0),
    "CHI98": ("Chicago", "US", 1.0),
    "CIN00": ("Cincinnati", "US", 1.0),
    "CLE00": ("Cleveland", "US", 1.0),
    "DAL00": ("Arlington", "US", None),
    "DEN00": ("Denver", "US", 1.0),
    "DET00": ("Detroit", "US", 0.0),
    "FRA00": ("Frankfurt am Main", "DE", 1.0),
    "GER00": ("Munich", "DE", 1.0),
    "GNB00": ("Green Bay", "US", 1.0),
    "HOU00": ("Houston", "US", None),
    "IND00": ("Indianapolis", "US", None),
    "JAX00": ("Jacksonville", "US", 1.0),
    "KAN00": ("Kansas City", "US", 1.0),
    "LAX01": ("Inglewood", "US", None),
    "LON00": ("London", "GB", 1.0),
    "LON02": ("London", "GB", 1.0),
    "MEX00": ("Mexico City", "MX", 1.0),
    "MIA00": ("Miami Gardens", "US", 1.0),
    "MIN01": ("Minneapolis", "US", 0.0),
    "NAS00": ("Nashville", "US", 1.0),
    "NOR00": ("New Orleans", "US", 0.0),
    "NYC01": ("East Rutherford", "US", 1.0),
    "PHI00": ("Philadelphia", "US", 1.0),
    "PHO00": ("Glendale", "US", None),
    "PIT00": ("Pittsburgh", "US", 1.0),
    "SAO00": ("Sao Paulo", "BR", 1.0),
    "SEA00": ("Seattle", "US", 1.0),
    "SFO01": ("Santa Clara", "US", 1.0),
    "TAM00": ("Tampa", "US", 1.0),
    "VEG00": ("Las Vegas", "US", 0.0),
    "WAS00": ("Landover", "US", 1.0),
}
WEATHER_POLICY = {
    "source": "Open-Meteo Previous Runs, explicit gfs_global model",
    "fields": "temperature only; wind/rain archive is missing in 2022-2023",
    "lead": "ceil((valid hour - cutoff + 6h publication buffer)/24h), minimum one day",
    "timing": (
        "fixed-lead availability bound is assumed; exact run/publication is not archived here"
    ),
    "location": "geocoded venue city; roughly 25km GFS grid, not stadium measurements",
    "exposure": "fixed indoor=0, outdoor=1, retractable/canopy=unknown",
    "refresh": "at least 24-hour-old forecast; not a claimed latest one-hour forecast",
    "schedule": "retrospective venue registry; moved 2022 CLE-BUF weather excluded",
    "excluded": "observed game weather, reanalysis, stitched historical-forecast API",
}


def _download(url: str) -> bytes:
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Prediction-App-research"})
            with urllib.request.urlopen(request, timeout=45) as response:
                payload: bytes = response.read()
                return payload
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("Unreachable download state")


def _json(url: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(_download(url))
    if result.get("error"):
        raise DataQualityError(f"Pregame source error: {result.get('reason')}")
    return result


def forecast_lead(valid: datetime, cutoff: datetime) -> tuple[int, datetime]:
    """Select an older lead with a conservative release buffer; never use day zero."""
    if valid.tzinfo is None or cutoff.tzinfo is None or cutoff >= valid + timedelta(hours=1):
        raise DataQualityError("Weather times must be aware and pregame")
    lead = max(1, math.ceil((valid - cutoff + timedelta(hours=6)).total_seconds() / 86400))
    if lead > 7:
        raise DataQualityError("Forecast cutoff requires weather beyond the seven-day archive")
    bound = valid - timedelta(days=lead) + timedelta(hours=6)
    if bound >= cutoff:
        lead += 1
        bound -= timedelta(days=1)
    if lead > 7 or bound >= cutoff:
        raise DataQualityError("No admissible fixed-lead weather forecast")
    return lead, bound


def _source(directory: Path, kind: str, season: int) -> dict[str, Any]:
    filename = "roster_weekly" if kind == "weekly_rosters" else kind
    url = f"{RELEASE}/{kind}/{filename}_{season}.parquet"
    payload = _download(url)
    frame = pl.read_parquet(io.BytesIO(payload))
    metadata: dict[str, Any] = {
        "url": url,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "download_bytes": len(payload),
        "upstream_sha256": hashlib.sha256(payload).hexdigest(),
        "upstream_rows": frame.height,
        "upstream_schema": {key: str(value) for key, value in frame.schema.items()},
    }
    if kind == "weekly_rosters":
        selected = frame.filter(pl.col("position") == "QB").select(
            "season", "week", "game_type", "team", "gsis_id", "status"
        )
        metadata.update(timestamp_column=None, reason="Weekly roster has no publication timestamps")
    elif kind == "depth_charts":
        if "dt" in frame.columns:
            selected = frame.filter(pl.col("pos_abb") == "QB").select(
                "dt", "team", "gsis_id", "pos_rank"
            )
            stamps = frame["dt"].drop_nulls()
            metadata.update(
                timestamp_column="dt", first_timestamp=stamps.min(), last_timestamp=stamps.max()
            )
        else:
            selected = frame.filter(pl.col("depth_position") == "QB").select(
                "season", "week", "game_type", "club_code", "gsis_id", "depth_team"
            )
            metadata.update(
                timestamp_column=None, reason="Week labels do not prove publication time"
            )
    else:
        columns = ["season", "week", "game_type", "team", "gsis_id", "position", "report_status"]
        metadata["timestamp_column"] = "date_modified" if "date_modified" in frame.columns else None
        if metadata["timestamp_column"]:
            columns.append("date_modified")
        else:
            metadata["reason"] = (
                "No modification/publication time; audit only, excluded from features"
            )
        selected = frame.select(columns)
    metadata["artifact"] = store_frame(directory, f"{kind}_{season}", selected)
    return metadata


def fetch_pregame_audit(data_dir: Path) -> dict[str, Any]:
    """Reuse existing injuries; persist only QB depth rows, 2025 injury audit, and venue IDs."""
    directory = data_dir / "raw" / "team_pregame"
    path = directory / "audit.json"
    if path.exists():
        audit = read_json(path)
        for metadata in audit["new_sources"].values():
            load_frame(directory, metadata["artifact"])
        load_frame(directory, audit["venues"])
        _finish_timing_audit(directory, audit)
        return audit
    _, games, team_manifest = load_team_cache(data_dir)
    _, injuries, context_manifest = load_context_cache(data_dir)
    new = {}
    for season in (2022, 2023, 2024, 2025):
        print(f"Auditing depth charts {season}", flush=True)
        new[f"depth_charts_{season}"] = _source(directory, "depth_charts", season)
    new["injuries_2025"] = _source(directory, "injuries", 2025)
    payload = _download("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv")
    venues = (
        pl.read_csv(io.BytesIO(payload), infer_schema_length=None)
        .filter(pl.col("season").is_between(2022, 2025) & (pl.col("game_type") == "REG"))
        .select("game_id", "season", "home_team", "away_team", "stadium_id", "stadium")
    )
    require_keys(venues, ["game_id"], "pregame venue metadata")
    canonical = games.filter(pl.col("season").is_between(2022, 2025)).select(
        "game_id", "season", "home_team", "away_team"
    )
    if venues.join(canonical, on=canonical.columns, how="anti").height:
        raise DataQualityError("Pregame venue/game identity mismatch")
    if set(venues["stadium_id"].drop_nulls()) - set(VENUES):
        raise DataQualityError("Unknown historical venue: audit registry before weather fetch")
    audit = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "new_sources": new,
        "reused_injuries": {
            "manifest_sha256": hashlib.sha256(
                json.dumps(context_manifest, sort_keys=True).encode()
            ).hexdigest(),
            "seasons": [2022, 2023, 2024],
            "rows": injuries.filter(pl.col("season").is_between(2022, 2024)).height,
            "timing": "modification +24h assumption; overwritten history is not reconstructed",
        },
        "team_pipeline_sha256": team_manifest["pipeline_source_sha256"],
        "venues": store_frame(directory, "venues", venues),
        "venue_source": "https://github.com/nflverse/nfldata/blob/master/data/games.csv",
        "venue_upstream_sha256": hashlib.sha256(payload).hexdigest(),
    }
    _finish_timing_audit(directory, audit)
    return audit


def _finish_timing_audit(directory: Path, audit: dict[str, Any]) -> None:
    """Derive readiness from persisted schemas/probes, not a user-editable pass flag."""
    sources = audit["new_sources"]
    for season in (2022, 2023, 2024, 2025):
        key = f"weekly_rosters_{season}"
        if key not in sources:
            print(f"Auditing weekly rosters {season}", flush=True)
            sources[key] = _source(directory, "weekly_rosters", season)
    if "weather_probes" not in audit:
        probes = {}
        for season in (2022, 2023, 2024, 2025):
            query = urllib.parse.urlencode(
                {
                    "latitude": 39.05,
                    "longitude": -94.48,
                    "start_hour": f"{season}-09-10T18:00",
                    "end_hour": f"{season}-09-10T18:00",
                    "hourly": "temperature_2m_previous_day6,wind_speed_10m_previous_day6,"
                    "precipitation_previous_day6",
                    "models": "gfs_global",
                    "timezone": "UTC",
                }
            )
            probes[str(season)] = {
                "url": PREVIOUS_RUNS + "?" + query,
                "response": _json(PREVIOUS_RUNS + "?" + query),
            }
        audit["weather_probes"] = probes
    blockers = []
    for label, keys in (
        (
            "2022-2024 expected-starter snapshots lack publication timestamps",
            [f"depth_charts_{y}" for y in (2022, 2023, 2024)],
        ),
        (
            "2022-2025 weekly rosters lack publication timestamps",
            [f"weekly_rosters_{y}" for y in (2022, 2023, 2024, 2025)],
        ),
        ("2025 injury rows lack publication/modification timestamps", ["injuries_2025"]),
    ):
        for key in keys:
            frame = load_frame(directory, sources[key]["artifact"])
            column = sources[key].get("timestamp_column")
            if (
                not column
                or column not in frame.columns
                or frame[column].null_count() == frame.height
            ):
                blockers.append(label)
                break
    for season in (2022, 2023):
        hourly = audit["weather_probes"][str(season)]["response"]["hourly"]
        if any(
            all(value is None for value in hourly[name])
            for name in ("wind_speed_10m_previous_day6", "precipitation_previous_day6")
        ):
            blockers.append(
                "2022-2023 wind/rain forecast archive absent; "
                "temperature trial is incomplete weather"
            )
            break
    audit["blockers"] = blockers
    audit["ready_for_enriched_2025"] = not blockers
    write_json(directory / "audit.json", audit)


def fetch_development_weather(data_dir: Path) -> dict[str, Any]:
    """One valid hour per game; cache each small response for resumable bounded downloads."""
    audit = fetch_pregame_audit(data_dir)
    directory = data_dir / "raw" / "team_pregame"
    weather_dir = directory / "weather"
    if (weather_dir / "manifest.json").exists():
        manifest = read_json(weather_dir / "manifest.json")
        if manifest["policy"] != WEATHER_POLICY:
            raise DataQualityError("Weather cache policy changed; audit before regenerating")
        load_frame(weather_dir, manifest["artifact"])
        return manifest
    _, games, _ = load_team_cache(data_dir)
    games = games.filter(pl.col("season").is_between(2022, 2024))
    cutoffs = weekly_cutoffs(games)
    venues = load_frame(directory, audit["venues"])
    games = games.join(venues.select("game_id", "stadium_id"), on="game_id", validate="1:1")
    points: dict[str, dict[str, Any]] = {}
    response: dict[str, Any] | None
    if (weather_dir / "points.json").exists():
        points = read_json(weather_dir / "points.json")
    for venue in sorted(set(games["stadium_id"].drop_nulls())):
        city, country, exposure = VENUES[venue]
        if exposure != 1 or venue in points:
            continue
        query = urllib.parse.urlencode({"name": city, "countryCode": country, "count": 10})
        response = _json("https://geocoding-api.open-meteo.com/v1/search?" + query)
        candidates = [r for r in response.get("results", []) if r.get("country_code") == country]
        if not candidates:
            raise DataQualityError(f"City geocoding failed: {city}, {country}")
        chosen = max(candidates, key=lambda r: r.get("population", 0))
        points[venue] = {"query": query, "result": chosen}
        write_json(weather_dir / "points.json", points)
    records = []
    for index, game in enumerate(games.sort("game_id").to_dicts(), 1):
        exposure = VENUES.get(game["stadium_id"], ("", "", None))[2]
        if game["game_id"] == "2022_11_CLE_BUF":
            exposure = None  # Venue change was announced after the early-week cutoff.
        valid = game["kickoff_utc"].replace(minute=0, second=0, microsecond=0)
        horizons = {
            "early_week": cutoffs[(game["season"], game["week"])],
            "pregame_refresh": game["kickoff_utc"] - timedelta(hours=1),
        }
        leads = {horizon: forecast_lead(valid, cutoff) for horizon, cutoff in horizons.items()}
        response = None
        if exposure == 1:
            point = points[game["stadium_id"]]["result"]
            query = urllib.parse.urlencode(
                {
                    "latitude": point["latitude"],
                    "longitude": point["longitude"],
                    "start_hour": valid.strftime("%Y-%m-%dT%H:00"),
                    "end_hour": valid.strftime("%Y-%m-%dT%H:00"),
                    "hourly": ",".join(
                        f"temperature_2m_previous_day{n}"
                        for n in sorted({v[0] for v in leads.values()})
                    ),
                    "models": "gfs_global",
                    "timezone": "UTC",
                }
            )
            path = weather_dir / "requests" / f"{game['game_id']}.json"
            if path.exists():
                cached = read_json(path)
                if cached["query"] != query:
                    raise DataQualityError("Cached weather query changed")
                response = cached["response"]
            else:
                time.sleep(0.12)
                response = _json(PREVIOUS_RUNS + "?" + query)
                write_json(
                    path,
                    {
                        "query": query,
                        "response": response,
                        "retrieved_at_utc": datetime.now(UTC).isoformat(),
                    },
                )
            if response["hourly"]["time"] != [valid.strftime("%Y-%m-%dT%H:00")]:
                raise DataQualityError("Weather valid hour mismatch")
        for horizon in HORIZONS:
            lead, bound = leads[horizon]
            temperature = (
                None
                if response is None
                else response["hourly"][f"temperature_2m_previous_day{lead}"][0]
            )
            if temperature is not None and not math.isfinite(temperature):
                raise DataQualityError("Nonfinite archived temperature")
            records.append(
                {
                    "game_id": game["game_id"],
                    "horizon": horizon,
                    "prediction_time_utc": horizons[horizon],
                    "valid_time_utc": valid,
                    "assumed_available_bound_utc": bound if temperature is not None else None,
                    "forecast_lead_days": lead,
                    "exposure": exposure,
                    "temperature_c": temperature,
                }
            )
        if index % 40 == 0:
            print(f"Weather: {index}/{games.height} games", flush=True)
    frame = pl.DataFrame(records, infer_schema_length=None).with_columns(
        pl.col("exposure", "temperature_c").cast(pl.Float64),
        pl.col("assumed_available_bound_utc").cast(pl.Datetime("us", "UTC")),
    )
    manifest = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "policy": WEATHER_POLICY,
        "seasons": [2022, 2023, 2024],
        "artifact": store_frame(weather_dir, "weather", frame),
        "known_temperatures": frame["temperature_c"].drop_nulls().len(),
        "indoor_rows": frame.filter(pl.col("exposure") == 0).height,
        "unknown_exposure_rows": frame.filter(pl.col("exposure").is_null()).height,
        "source": "https://open-meteo.com/en/docs/previous-runs-api",
    }
    write_json(weather_dir / "manifest.json", manifest)
    return manifest
