"""On-demand, versioned game-window forecasts; historical lookups never fetch observations."""

import hashlib
import io
import json
import math
import urllib.parse
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_games import TEAM_ALIASES, load_team_cache, schedule_games
from nfl_prop_model.data.team_pregame import _download, _json
from nfl_prop_model.features.team_epa import HORIZONS, weekly_cutoffs

SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
RUN_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
FIELDS = ("temperature_2m", "wind_speed_10m", "wind_gusts_10m", "precipitation")
UNITS = dict(zip(FIELDS, ("°C", "m/s", "m/s", "mm"), strict=True))
WEATHER_CAPTURE_POLICY = {
    "provider": "Open-Meteo, explicit GFS global single run",
    "run": "latest six-hour UTC cycle at least 6h10m before request; receipt proves capture",
    "window": "floored kickoff hour through kickoff+4h, five hourly forecast values",
    "location": "ESPN scheduled venue city, geocoded by Open-Meteo; city/grid approximation",
    "fields": list(FIELDS),
    "units": UNITS,
    "roof": "outside weather only; no inferred retractable-roof decision or score adjustment",
    "historical": "latest matching receipt strictly before cutoff; otherwise explicit missing",
    "storage": "only five selected hours and source digests; full point response discarded",
    "schedule": "fresh live venue/kickoff check; historical schedule revisions unreconstructed",
}
COUNTRIES = {
    "USA": "US",
    "United States": "US",
    "England": "GB",
    "United Kingdom": "GB",
    "Germany": "DE",
    "Ireland": "IE",
    "Brazil": "BR",
    "Spain": "ES",
    "Mexico": "MX",
    "Australia": "AU",
    "Canada": "CA",
}
VENUE_TEAM_ALIASES = {**TEAM_ALIASES, "WSH": "WAS"}
STATES = {
    "AZ": "Arizona",
    "CA": "California",
    "CO": "Colorado",
    "FL": "Florida",
    "GA": "Georgia",
    "IL": "Illinois",
    "IN": "Indiana",
    "LA": "Louisiana",
    "MA": "Massachusetts",
    "MD": "Maryland",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MO": "Missouri",
    "NC": "North Carolina",
    "NJ": "New Jersey",
    "NV": "Nevada",
    "NY": "New York",
    "OH": "Ohio",
    "PA": "Pennsylvania",
    "TN": "Tennessee",
    "TX": "Texas",
    "WA": "Washington",
    "WI": "Wisconsin",
}
WEATHER_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "game_id": pl.String,
    **{
        name: pl.Datetime("us", "UTC")
        for name in ("kickoff_utc", "valid_time_utc", "captured_at_utc", "model_run_utc")
    },
    **{name: pl.Float64 for name in FIELDS},
}


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataQualityError("Weather times must be timezone-aware")
    return value.astimezone(UTC)


def capture_source_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def pinned_run(cutoff: datetime) -> datetime:
    reference = _aware(cutoff) - timedelta(hours=6, minutes=10)
    return reference.replace(hour=reference.hour // 6 * 6, minute=0, second=0, microsecond=0)


def game_hours(kickoff: datetime) -> list[datetime]:
    first = _aware(kickoff).replace(minute=0, second=0, microsecond=0)
    return [first + timedelta(hours=i) for i in range(5)]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fresh_slate(
    now: datetime, days: int, horizon: str, target: date | None
) -> tuple[pl.DataFrame, dict[str, Any]]:
    payload = _download(SCHEDULE_URL)
    raw = pl.read_csv(io.BytesIO(payload), infer_schema_length=None)
    # Ancient schedules can lack kickoff times; they are irrelevant to live collection.
    scoped = raw.filter(
        (pl.col("season") >= now.year - 1) & pl.col("home_score").is_null()
    ).with_columns(pl.col("home_score", "away_score").cast(pl.Float64))
    games = schedule_games(scoped).join(
        raw.select("game_id", "espn", "stadium_id", "stadium"), on="game_id", validate="1:1"
    )
    lower = now + timedelta(hours=1)
    upper = (
        now + timedelta(hours=24) if horizon == "pregame_refresh" else now + timedelta(days=days)
    )
    games = games.filter((pl.col("kickoff_utc") > lower) & (pl.col("kickoff_utc") <= upper))
    if target is not None:
        games = games.filter(
            pl.col("kickoff_utc").dt.convert_time_zone("America/Chicago").dt.date() == target
        )
    if games.is_empty():
        raise DataQualityError("No scheduled games inside the requested date/forecast window")
    return games.select(
        "game_id",
        "season",
        "week",
        "home_team",
        "away_team",
        "kickoff_utc",
        "espn",
        "stadium_id",
        "stadium",
    ), {
        "url": SCHEDULE_URL,
        "retrieved_at_utc": _now().isoformat(),
        "upstream_sha256": hashlib.sha256(payload).hexdigest(),
    }


def venue_point(data_dir: Path, game: dict[str, Any]) -> dict[str, Any]:
    """Verify the actual scheduled venue, including neutral games; cache only geocoding."""
    event = str(game["espn"])
    if not event.isdigit():
        raise DataQualityError("Missing ESPN event ID for venue verification")
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=" + event
    summary = _json(url)
    competition = summary["header"]["competitions"][0]
    kickoff = datetime.fromisoformat(competition["date"].replace("Z", "+00:00"))
    teams = {
        c["homeAway"]: VENUE_TEAM_ALIASES.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
        for c in competition["competitors"]
    }
    if kickoff != game["kickoff_utc"] or any(
        teams[s] != game[f"{s}_team"] for s in ("home", "away")
    ):
        raise DataQualityError("Live venue feed disagrees with scheduled kickoff or teams")
    venue = competition.get("venue") or summary.get("gameInfo", {}).get("venue")
    if not venue:
        raise DataQualityError("Scheduled venue is unavailable")
    address = venue["address"]
    country = COUNTRIES.get(address.get("country"), address.get("country"))
    if not country or len(country) != 2:
        raise DataQualityError("Unknown venue country")
    query = urllib.parse.urlencode({"name": address["city"], "countryCode": country, "count": 10})
    cache = (
        data_dir
        / "raw"
        / "team_weather"
        / "points"
        / f"{hashlib.sha256(query.encode()).hexdigest()}.json"
    )
    if cache.exists():
        point = read_json(cache)
        if point["query"] != query or point["response_sha256"] != _digest(point["response"]):
            raise DataQualityError("Geocoding cache checksum/query mismatch")
    else:
        response = _json("https://geocoding-api.open-meteo.com/v1/search?" + query)
        point = {"query": query, "response": response, "response_sha256": _digest(response)}
        write_json(cache, point)
    candidates = [
        r for r in point["response"].get("results", []) if r.get("country_code") == country
    ]
    if not candidates:
        raise DataQualityError("Venue city geocoding returned no matching country")
    # US city names can be ambiguous. Prefer the venue's stated state before population.
    state = STATES.get(address.get("state", ""), address.get("state"))
    preferred = [
        r
        for r in candidates
        if r.get("admin1", "").lower() == str(state).lower() or r.get("admin1_code") == state
    ]
    if country == "US" and state and not preferred:
        raise DataQualityError("Venue geocoding could not verify the scheduled state")
    chosen = max(preferred or candidates, key=lambda r: r.get("population", 0))
    lat, lon = float(chosen["latitude"]), float(chosen["longitude"])
    if not math.isfinite(lat + lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise DataQualityError("Invalid venue city coordinates")
    return {
        "venue": venue["fullName"],
        "city": chosen["name"],
        "country": country,
        "latitude": lat,
        "longitude": lon,
        "precision": "city",
        "venue_source": url,
        "venue_response_sha256": _digest(
            {"date": competition["date"], "teams": teams, "venue": venue}
        ),
        "geocoding_query": query,
        "geocoding_response_sha256": point["response_sha256"],
    }


def _forecast(
    point: dict[str, Any], game: dict[str, Any], run: datetime
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "latitude": point["latitude"],
            "longitude": point["longitude"],
            "models": "gfs_global",
            "run": run.strftime("%Y-%m-%dT%H:00"),
            "hourly": ",".join(FIELDS),
            "wind_speed_unit": "ms",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
            "timezone": "UTC",
            "forecast_days": 16,
        }
    )
    url = RUN_URL + "?" + query
    response = _json(url)
    received = _aware(_now())
    if response.get("utc_offset_seconds") != 0 or response.get("hourly_units") != {
        "time": "iso8601",
        **UNITS,
    }:
        raise DataQualityError("Forecast timezone or units changed")
    if received >= game["kickoff_utc"] or run + timedelta(hours=6, minutes=10) > received:
        raise DataQualityError("Weather receipt is postgame or run is not safely available")
    hourly = response["hourly"]
    times = hourly["time"]
    if len(set(times)) != len(times) or any(len(hourly[field]) != len(times) for field in FIELDS):
        raise DataQualityError("Malformed forecast hour arrays")
    wanted = game_hours(game["kickoff_utc"])
    indexes = {t: i for i, t in enumerate(times)}
    rows = []
    for valid in wanted:
        key = valid.strftime("%Y-%m-%dT%H:00")
        if key not in indexes:
            raise DataQualityError("Game window is outside the pinned run's forecast coverage")
        values = {field: hourly[field][indexes[key]] for field in FIELDS}
        for field, value in values.items():
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or (field != "temperature_2m" and value < 0)
            ):
                raise DataQualityError(
                    "Forecast values must be finite, nonnegative except temperature, or null"
                )
        rows.append(
            {
                "game_id": game["game_id"],
                "kickoff_utc": game["kickoff_utc"],
                "valid_time_utc": valid,
                "captured_at_utc": received,
                "model_run_utc": run,
                **values,
            }
        )
    return rows, {
        "game_id": game["game_id"],
        "kickoff_utc": game["kickoff_utc"].isoformat(),
        "model_run_utc": run.isoformat(),
        "captured_at_utc": received.isoformat(),
        "request_url": url,
        "response_sha256": _digest(response),
        "point": point,
        "status": "complete" if all(r[f] is not None for r in rows for f in FIELDS) else "partial",
    }


def load_weather_capture(path: Path) -> tuple[dict[str, Any], pl.DataFrame]:
    manifest = read_json(path)
    if manifest["format_version"] != 1 or manifest["policy"] != WEATHER_CAPTURE_POLICY:
        raise DataQualityError("Unsupported weather capture policy")
    frame = load_frame(path.parent, manifest["artifact"])
    if dict(frame.schema) != WEATHER_SCHEMA:
        raise DataQualityError("Weather capture schema mismatch")
    started = _aware(datetime.fromisoformat(manifest["request_started_utc"]))
    created = _aware(datetime.fromisoformat(manifest["created_at_utc"]))
    if created < started or manifest["horizon"] not in HORIZONS:
        raise DataQualityError("Invalid weather capture time/horizon")
    receipts = manifest["games"]
    if len({r["game_id"] for r in receipts}) != len(receipts):
        raise DataQualityError("Duplicate weather game receipt")
    expected_games = set()
    for receipt in receipts:
        game = frame.filter(pl.col("game_id") == receipt["game_id"])
        kickoff = _aware(datetime.fromisoformat(receipt["kickoff_utc"]))
        if receipt["status"] == "unavailable":
            if not game.is_empty():
                raise DataQualityError("Unavailable weather receipt contains values")
            continue
        expected_games.add(receipt["game_id"])
        captured = _aware(datetime.fromisoformat(receipt["captured_at_utc"]))
        run = _aware(datetime.fromisoformat(receipt["model_run_utc"]))
        if not started <= captured <= created or captured >= kickoff or run != pinned_run(started):
            raise DataQualityError("Invalid weather receipt timing")
        if game.height != 5 or game["valid_time_utc"].to_list() != game_hours(kickoff):
            raise DataQualityError("Weather receipt hour mismatch")
        for column, expected in (
            ("kickoff_utc", kickoff),
            ("captured_at_utc", captured),
            ("model_run_utc", run),
        ):
            if game[column].to_list() != [expected] * 5:
                raise DataQualityError("Weather receipt metadata/frame mismatch")
        for field in FIELDS:
            if game.filter(
                pl.col(field).is_not_null()
                & (
                    ~pl.col(field).is_finite()
                    | ((pl.col(field) < 0) if field != "temperature_2m" else pl.lit(False))
                )
            ).height:
                raise DataQualityError("Invalid stored weather values")
        complete = all(game[field].null_count() == 0 for field in FIELDS)
        if receipt["status"] != ("complete" if complete else "partial"):
            raise DataQualityError("Weather receipt completeness mismatch")
    if set(frame["game_id"]) != expected_games:
        raise DataQualityError("Weather receipt game coverage mismatch")
    return manifest, frame


def collect_weather(
    data_dir: Path, *, horizon: str = "early_week", days: int = 7, target: date | None = None
) -> Path:
    if horizon not in HORIZONS or not 1 <= days <= 28:
        raise ValueError("Use a supported horizon and 1-28 days")
    started = _aware(_now())
    source_hash = capture_source_hash()
    if target is not None and target < started.astimezone(ZoneInfo("America/Chicago")).date():
        raise DataQualityError(
            "Past dates require team-weather-lookup; live data cannot be backdated"
        )
    games, schedule = _fresh_slate(started, days, horizon, target)
    run = pinned_run(started)
    if run < datetime(2026, 4, 2, tzinfo=UTC):
        raise DataQualityError(
            "Pinned GFS archive starts April 2026; historical coverage must be audited separately"
        )
    rows, receipts = [], []
    for game in games.to_dicts():
        try:
            point = venue_point(data_dir, game)
            values, receipt = _forecast(point, game, run)
            rows.extend(values)
            receipts.append(receipt)
        except (OSError, KeyError, ValueError) as error:
            receipts.append(
                {
                    "game_id": game["game_id"],
                    "kickoff_utc": game["kickoff_utc"].isoformat(),
                    "status": "unavailable",
                    "reason": str(error),
                }
            )
        print(f"Weather {game['game_id']}: {receipts[-1]['status']}", flush=True)
    captured = _aware(_now())
    if source_hash != capture_source_hash():
        raise DataQualityError("Weather collector source changed during capture")
    directory = (
        data_dir
        / "journal"
        / "team_weather"
        / f"snapshot-{captured.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
    )
    frame = pl.DataFrame(rows, schema=WEATHER_SCHEMA)
    manifest = {
        "format_version": 1,
        "created_at_utc": captured.isoformat(),
        "request_started_utc": started.isoformat(),
        "horizon": horizon,
        "source_sha256": source_hash,
        "policy": WEATHER_CAPTURE_POLICY,
        "schedule": schedule,
        "games": receipts,
        "artifact": store_frame(directory, "weather", frame),
    }
    path = directory / "manifest.json"
    write_json(path, manifest)
    load_weather_capture(path)
    return path


def weather_as_of(
    data_dir: Path, game_id: str, kickoff: datetime, cutoff: datetime
) -> tuple[pl.DataFrame | None, dict[str, Any]]:
    """Never fetch on lookup or repair an earlier forecast with late receipts."""
    cutoff, kickoff = _aware(cutoff), _aware(kickoff)
    if cutoff >= kickoff:
        raise DataQualityError("Weather lookup cutoff must precede kickoff")
    candidates = []
    for path in (data_dir / "journal" / "team_weather").glob("snapshot-*/manifest.json"):
        manifest, frame = load_weather_capture(path)
        for receipt in manifest["games"]:
            if receipt["game_id"] != game_id or receipt["status"] == "unavailable":
                continue
            captured = datetime.fromisoformat(receipt["captured_at_utc"])
            if captured < cutoff and datetime.fromisoformat(receipt["kickoff_utc"]) == kickoff:
                candidates.append(
                    (captured, path, frame.filter(pl.col("game_id") == game_id), receipt)
                )
    if not candidates:
        return None, {
            "game_id": game_id,
            "status": "unavailable",
            "reason": "No matching weather receipt captured before cutoff",
        }
    _, path, frame, receipt = max(candidates, key=lambda item: (item[0], str(item[1])))
    if (
        frame.height != 5
        or frame["valid_time_utc"].to_list() != game_hours(kickoff)
        or frame.filter(pl.col("captured_at_utc") >= cutoff).height
    ):
        raise DataQualityError("Weather receipt hour/timing mismatch")
    return frame, {**receipt, "manifest": str(path), "manifest_sha256": sha256_file(path)}


def lookup_weather_date(data_dir: Path, target: date, horizon: str) -> list[dict[str, Any]]:
    if horizon not in HORIZONS:
        raise ValueError("Unknown weather horizon")
    _, games, _ = load_team_cache(data_dir)
    cutoffs = weekly_cutoffs(games)
    selected = games.filter(
        pl.col("kickoff_utc").dt.convert_time_zone("America/Chicago").dt.date() == target
    )
    if selected.is_empty():
        raise DataQualityError("No cached scheduled games on the requested Chicago date")
    results = []
    for game in selected.to_dicts():
        cutoff = (
            cutoffs[(game["season"], game["week"])]
            if horizon == "early_week"
            else game["kickoff_utc"] - timedelta(hours=1)
        )
        _, receipt = weather_as_of(data_dir, game["game_id"], game["kickoff_utc"], cutoff)
        results.append({**receipt, "cutoff_utc": cutoff.isoformat(), "horizon": horizon})
    return results


def link_weather_forecast(weather_path: Path, forecast_path: Path) -> Path:
    """A sidecar links newly captured weather without changing the frozen EPA manifest/weights."""
    source, frame = load_weather_capture(weather_path)
    report = read_json(forecast_path)
    cutoff = _aware(datetime.fromisoformat(report["as_of_utc"]))
    features = load_frame(forecast_path.parent, report["artifacts"]["features"])
    if source["horizon"] != report["horizon"]:
        raise DataQualityError("Weather and score forecast horizons differ")
    receipt_games = {r["game_id"] for r in source["games"]}
    if set(features["game_id"]) != receipt_games:
        raise DataQualityError(
            "Weather and score forecast game cohorts differ; refresh schedule/cache"
        )
    scheduled = dict(zip(features["game_id"], features["kickoff_utc"], strict=True))
    if any(
        _aware(datetime.fromisoformat(r["kickoff_utc"])) != scheduled[r["game_id"]]
        for r in source["games"]
    ):
        raise DataQualityError("Weather and score forecast kickoffs differ")
    joined = frame.join(
        features.select("game_id", pl.col("kickoff_utc").alias("expected_kickoff")),
        on="game_id",
        validate="m:1",
    )
    if joined.filter(
        (pl.col("kickoff_utc") != pl.col("expected_kickoff"))
        | (pl.col("captured_at_utc") >= cutoff)
    ).height:
        raise DataQualityError("Weather was late or kickoff changed before the score forecast")
    path = forecast_path.parent / "weather.json"
    if path.exists():
        raise DataQualityError(
            "Forecast already has a weather sidecar; preserve its earlier version"
        )
    write_json(
        path,
        {
            "format_version": 1,
            "status": "collected context; score adjustments unvalidated",
            "forecast_manifest_sha256": sha256_file(forecast_path),
            "weather_manifest": str(weather_path.resolve()),
            "weather_manifest_sha256": sha256_file(weather_path),
            "prediction_time_utc": cutoff.isoformat(),
        },
    )
    return path
