"""Outcome-free historical weather collection and an offline provenance/coverage audit."""

import gzip
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

import polars as pl

from nfl_prop_model.data.gfs_archive import (
    forecast_selection,
    parse_point,
    point_url,
    validate_metadata,
)
from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_depth import SCHEDULE_FIELDS
from nfl_prop_model.data.team_games import team_source_hash
from nfl_prop_model.data.team_pregame import VENUES
from nfl_prop_model.features.team_epa import HORIZONS, weekly_cutoffs

INTERNATIONAL_SOURCE = (
    "https://www.nfl.com/news/"
    "nfl-announces-2025-international-games-to-feature-seven-games-in-five-countries"
)
# Announced before the season; adapter keys are not invented nflverse stadium IDs.
OVERRIDES = {
    "2025_01_KC_LAC": "SAO00",
    "2025_04_MIN_PIT": "announced-dublin",
    "2025_05_MIN_CLE": "LON02",
    "2025_06_DEN_NYJ": "LON02",
    "2025_07_LA_JAX": "LON00",
    "2025_10_ATL_IND": "announced-berlin",
    "2025_11_WAS_MIA": "announced-madrid",
}
REGISTRY = {
    **VENUES,
    "announced-dublin": ("Dublin", "IE", 1.0),
    "announced-berlin": ("Berlin", "DE", 1.0),
    "announced-madrid": ("Madrid", "ES", None),
}
POLICY = {
    "source": "NCAR d084001 operational GFS 0.25 degree archive; DOI 10.5065/D65D8PWK",
    "run": "latest six-hour run with run +6h10m strictly before prediction cutoff",
    "availability": "publication buffer is an assumption; current receipt is not historical proof",
    "valid": "nearest three-hour point to kickoff, ties later; maximum 90 minutes offset",
    "fields": "2m temperature C; 10m wind magnitude m/s; instantaneous precipitation rate mm/h",
    "rain": "rate equivalent, not accumulated hourly precipitation; no rain/snow type or gusts",
    "exposure": "fixed indoor zero; outdoor one; retractable/canopy unknown, no postgame roof",
    "location": "venue city nearest 0.25 degree grid; no stadium measurement claim",
    "venues": "retrospective registry; seven 2025 overrides from preseason NFL announcement",
    "venue_source": INTERNATIONAL_SOURCE,
    "venue_publication": "2025-05-13; static announced schedule assumption, not archived edition",
    "moved_game": "2022_11_CLE_BUF weather excluded at both cutoffs",
    "cohort": "2022-2025 regular-season canonical schedule metadata only; no outcomes or fits",
    "missing": "retain all game/horizon rows, no fallback run or imputed weather",
    "limits": "600kB metadata/20kB point response; 24MB compressed raw cache; two request workers",
}
MAX_CACHE_BYTES = 24_000_000
_CACHE_LOCK = Lock()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_hash() -> str:
    paths = [Path(__file__), Path(__file__).with_name("gfs_archive.py")]
    return _digest({p.name: sha256_file(p) for p in paths} | {"venues": REGISTRY})


def schedule(data_dir: Path) -> tuple[pl.DataFrame, dict[str, str]]:
    path = data_dir / "processed" / "team_epa" / "manifest.json"
    manifest = read_json(path)
    if manifest["pipeline_source_sha256"] != team_source_hash():
        raise DataQualityError("Canonical schedule source changed")
    metadata = manifest["artifacts"]["games"]
    artifact = path.parent / metadata["file"]
    if sha256_file(artifact) != metadata["sha256"]:
        raise DataQualityError("Schedule checksum mismatch")
    games = (
        pl.scan_parquet(artifact)
        .select(SCHEDULE_FIELDS)
        .filter(pl.col("season").is_between(2022, 2025))
        .collect()
        .sort("season", "week", "game_id")
    )
    require_keys(games, ["game_id"], "historical weather schedule")
    if Counter(games["season"]) != {2022: 271, 2023: 272, 2024: 272, 2025: 272} or (
        games["kickoff_utc"].null_count()
    ):
        raise DataQualityError("Historical weather requires all 1087 scheduled games")
    venue_path = data_dir / "raw" / "team_pregame" / "audit.json"
    venue_manifest = read_json(venue_path)
    venues = load_frame(venue_path.parent, venue_manifest["venues"])
    fields = ["game_id", "season", "home_team", "away_team"]
    require_keys(venues, ["game_id"], "historical weather venues")
    if games.join(venues, on=fields, how="anti").height:
        raise DataQualityError("Weather venue/game identity mismatch")
    games = games.join(venues.select(*fields, "stadium_id"), on=fields, how="left")
    return games, {"schedule_sha256": metadata["sha256"], "venues_sha256": _digest(venue_manifest)}


def _download(url: str, limit: int) -> bytes:
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Prediction-App-research"})
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.geturl() != url:
                    raise DataQualityError("Historical weather redirect refused")
                payload: bytes = response.read(limit + 1)
                if len(payload) > limit:
                    raise DataQualityError("Historical weather response exceeds bound")
                return payload
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt:
                raise
        time.sleep(1)
    raise AssertionError("unreachable")


def _receipt_path(directory: Path, url: str) -> Path:
    return directory / (hashlib.sha256(url.encode()).hexdigest() + ".json")


def _read_receipt(directory: Path, url: str, limit: int) -> bytes | None:
    receipt = read_json(_receipt_path(directory, url))
    if receipt["url"] != url:
        raise DataQualityError("Weather receipt URL mismatch")
    if receipt["status"] not in {"received", "unavailable"}:
        raise DataQualityError("Unknown weather receipt status")
    if receipt["status"] == "unavailable":
        return None
    path = directory / receipt["file"]
    if path.name != receipt["file"] or sha256_file(path) != receipt["compressed_sha256"]:
        raise DataQualityError("Weather raw checksum mismatch")
    with gzip.open(path, "rb") as handle:
        payload: bytes = handle.read(limit + 1)
    if (
        len(payload) > limit
        or len(payload) != receipt["bytes"]
        or (hashlib.sha256(payload).hexdigest() != receipt["sha256"])
    ):
        raise DataQualityError("Weather decoded checksum/size mismatch")
    return payload


def _fetch(directory: Path, url: str, limit: int, *, retry_failed: bool = False) -> bytes | None:
    path = _receipt_path(directory, url)
    previous = []
    if path.exists():
        payload = _read_receipt(directory, url, limit)
        if payload is not None or not retry_failed:
            return payload
        old = read_json(path)
        previous = old.pop("previous_attempts", []) + [old]
    directory.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {"url": url, "received_at_utc": datetime.now(UTC).isoformat()}
    if previous:
        receipt["previous_attempts"] = previous
    try:
        payload = _download(url, limit)
    except (urllib.error.URLError, TimeoutError) as error:
        receipt.update(status="unavailable", error=str(error)[:400])
        write_json(path, receipt)
        return None
    compressed = gzip.compress(payload, mtime=0)
    body = directory / (hashlib.sha256(compressed).hexdigest() + ".gz")
    with _CACHE_LOCK:
        if (
            sum(p.stat().st_size for p in directory.glob("*.gz")) + len(compressed)
            > MAX_CACHE_BYTES
        ):
            raise DataQualityError("Historical weather compressed-cache budget reached")
        if not body.exists():
            body.write_bytes(compressed)
    receipt.update(
        status="received",
        file=body.name,
        bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        compressed_sha256=sha256_file(body),
    )
    write_json(path, receipt)
    return payload


def venue_key(game: dict[str, Any]) -> str:
    return OVERRIDES.get(str(game["game_id"]), str(game["stadium_id"]))


def _points(
    data_dir: Path, directory: Path, games: pl.DataFrame, *, offline: bool
) -> dict[str, Any]:
    path = directory / "points.json"
    old = data_dir / "raw" / "team_pregame" / "weather" / "points.json"
    original = read_json(old)
    keys = sorted({venue_key(row) for row in games.to_dicts()})
    for key in keys:
        if key not in REGISTRY:
            raise DataQualityError(f"Unknown historical weather venue: {key}")
    result: dict[str, Any] = {"original_sha256": sha256_file(old), "points": {}}
    for key in keys:
        city, country, exposure = REGISTRY[key]
        if exposure != 1:
            continue
        if key in original:
            point = original[key]["result"]
        else:
            query = urllib.parse.urlencode({"name": city, "countryCode": country, "count": 10})
            url = "https://geocoding-api.open-meteo.com/v1/search?" + query
            payload = (
                _read_receipt(directory, url, 100_000)
                if offline
                else _fetch(directory, url, 100_000)
            )
            if payload is None:
                raise DataQualityError("Geocoding unavailable")
            candidates = [p for p in json.loads(payload)["results"] if p["country_code"] == country]
            if not candidates:
                raise DataQualityError("Geocoding country mismatch")
            point = max(candidates, key=lambda p: p.get("population", 0))
        if point["country_code"] != country:
            raise DataQualityError("Cached city country mismatch")
        result["points"][key] = {"latitude": point["latitude"], "longitude": point["longitude"]}
    if offline:
        if read_json(path) != result:
            raise DataQualityError("Historical weather point registry changed")
    else:
        if path.exists() and read_json(path) != result:
            raise DataQualityError("Historical weather point registry changed")
        write_json(path, result)
    return result


def build_rows(
    games: pl.DataFrame,
    points: dict[str, Any],
    directory: Path,
    *,
    offline: bool,
    retry_failed: bool = False,
) -> list[dict[str, Any]]:
    cutoffs = weekly_cutoffs(games)
    rows = []
    total = games.height * 2
    for game in games.to_dicts():
        key = venue_key(game)
        exposure = REGISTRY[key][2]
        for horizon in HORIZONS:
            cutoff = (
                cutoffs[(game["season"], game["week"])]
                if horizon == "early_week"
                else (game["kickoff_utc"] - timedelta(hours=1))
            )
            selection = forecast_selection(game["kickoff_utc"], cutoff)
            row = {
                **{k: v.isoformat() if isinstance(v, datetime) else v for k, v in game.items()},
                "horizon": horizon,
                "cutoff_utc": cutoff.isoformat(),
                "venue_key": key,
                "venue_corrected": str(game["game_id"]) in OVERRIDES,
                "exposure": exposure,
                **selection,
                "temperature_c": None,
                "wind_speed_m_s": None,
                "precipitation_rate_mm_h": None,
                "grid_latitude": None,
                "grid_longitude": None,
                "point_url": None,
            }
            if game["game_id"] == "2022_11_CLE_BUF":
                row["status"] = "moved_venue_excluded"
            elif exposure is None:
                row["status"] = "roof_unknown"
            elif exposure == 0:
                row["status"] = "fixed_indoor"
            else:
                point = points["points"][key]
                url = point_url(selection, point["latitude"], point["longitude"])
                row["point_url"] = url
                bodies = []
                for request_url, limit in ((selection["url"] + ".das", 600_000), (url, 20_000)):
                    bodies.append(
                        _read_receipt(directory, request_url, limit)
                        if offline
                        else _fetch(directory, request_url, limit, retry_failed=retry_failed)
                    )
                    if bodies[-1] is None:
                        break
                if None in bodies:
                    row["status"] = "source_unavailable"
                else:
                    metadata, payload = bodies
                    assert payload is not None and metadata is not None
                    row.update(parse_point(payload, metadata, selection, **point))
                    row["status"] = (
                        "complete"
                        if all(
                            row[k] is not None
                            for k in ("temperature_c", "wind_speed_m_s", "precipitation_rate_mm_h")
                        )
                        else "partial"
                    )
            rows.append(row)
        if not offline and (len(rows) % 32 == 0 or len(rows) == total):
            counts = dict(Counter(r["status"] for r in rows))
            print(
                f"Historical weather {len(rows)}/{total}: {counts}",
                flush=True,
            )
    return rows


def collect_historical_weather(data_dir: Path, *, retry_failed: bool = False) -> Path:
    games, inputs = schedule(data_dir)
    directory = data_dir / "raw" / "team_historical_weather"
    path = directory / "manifest.json"
    identity = {
        "format_version": 1,
        "source_sha256": source_hash(),
        "policy": POLICY,
        "inputs": inputs,
    }
    if path.exists() and read_json(path) != identity:
        raise DataQualityError(
            "Historical weather source/policy/input changed; use a separate data directory"
        )
    write_json(path, identity)
    points = _points(data_dir, directory, games, offline=False)
    _prefetch(games, points, directory, retry_failed=retry_failed)
    build_rows(games, points, directory, offline=True)
    return path


def _prefetch(
    games: pl.DataFrame, points: dict[str, Any], directory: Path, *, retry_failed: bool
) -> None:
    cutoffs = weekly_cutoffs(games)
    metadata_requests: dict[str, dict[str, Any]] = {}
    point_requests: dict[str, str] = {}
    for game in games.to_dicts():
        key = venue_key(game)
        if REGISTRY[key][2] != 1 or game["game_id"] == "2022_11_CLE_BUF":
            continue
        point = points["points"][key]
        for cutoff in (
            cutoffs[(game["season"], game["week"])],
            game["kickoff_utc"] - timedelta(hours=1),
        ):
            selection = forecast_selection(game["kickoff_utc"], cutoff)
            metadata_url = selection["url"] + ".das"
            metadata_requests[metadata_url] = selection
            point_requests[point_url(selection, **point)] = metadata_url

    def metadata_fetch(item: tuple[str, dict[str, Any]]) -> tuple[str, bool]:
        url, selection = item
        payload = _fetch(directory, url, 600_000, retry_failed=retry_failed)
        if payload is not None:
            validate_metadata(payload, selection)
        return url, payload is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        good = {}
        for index, (url, received) in enumerate(
            pool.map(metadata_fetch, metadata_requests.items()), 1
        ):
            good[url] = received
            if index % 20 == 0 or index == len(metadata_requests):
                print(f"Weather metadata {index}/{len(metadata_requests)}", flush=True)
        requests = [url for url, meta in point_requests.items() if good[meta]]

        def point_fetch(url: str) -> None:
            _fetch(directory, url, 20_000, retry_failed=retry_failed)

        for index, _ in enumerate(pool.map(point_fetch, requests), 1):
            if index % 40 == 0 or index == len(requests):
                print(f"Weather points {index}/{len(requests)}", flush=True)


def audit_historical_weather(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    games, inputs = schedule(data_dir)
    directory = data_dir / "raw" / "team_historical_weather"
    manifest = read_json(directory / "manifest.json")
    if manifest != {
        "format_version": 1,
        "source_sha256": source_hash(),
        "policy": POLICY,
        "inputs": inputs,
    }:
        raise DataQualityError("Historical weather source/policy/input mismatch")
    points = _points(data_dir, directory, games, offline=True)
    rows = build_rows(games, points, directory, offline=True)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    require_keys(frame, ["game_id", "horizon"], "historical weather audit")
    output = data_dir / "processed" / "team_historical_weather"
    report: dict[str, Any] = {
        **manifest,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "model_fits": 0,
        "games": games.height,
        "records": len(rows),
        "status": dict(Counter(r["status"] for r in rows)),
        "coverage": frame.group_by("season", "horizon", "status")
        .len()
        .sort("season", "horizon", "status")
        .to_dicts(),
        "raw_bytes": sum(p.stat().st_size for p in directory.iterdir() if p.is_file()),
        "artifact": store_frame(output, "weather", frame),
        "replay_status": (
            "blocked: dated development roster/starter inputs and weather comparison still required"
        ),
    }
    write_json(output / "manifest.json", report)
    write_json(report_dir / "results.json", report)
    frame.group_by("season", "horizon", "status").len().sort(
        "season", "horizon", "status"
    ).write_csv(report_dir / "coverage.csv")
    text = (
        "# Historical weather source audit\n\n"
        f"{games.height} games, {len(rows)} game/horizon records, zero model fits.\n\n"
        f"Statuses: `{report['status']}`. Raw cache: {report['raw_bytes']:,} bytes.\n\n"
        "Reconstructed from checksummed responses offline. Run reference, valid time, "
        "grid cell, heights, forecast process, and units are checked for each accepted point. "
        "The six-hour-ten-minute publication delay remains an assumption; retrieval today does not "
        "prove historical publication. Each value is the nearest three-hour kickoff forecast.\n\n"
        "Temperature is Celsius, wind is m/s, and precipitation is instantaneous rate in mm/h, "
        "not hourly accumulation. Gusts and precipitation type are unavailable. Indoor and unknown "
        "roof rows retain null weather; fixed-indoor exposure is zero. "
        "Missing source rows are retained.\n\n"
        "Seven 2025 international venue assignments override the old domestic defaults using the "
        f"[preseason announcement]({INTERNATIONAL_SOURCE}). "
        "This is a static announced-venue assumption, "
        "not a reconstruction of schedule revisions. The moved 2022 CLE-BUF game remains excluded. "
        "City coordinates are approximate, not stadium measurements.\n\n"
        "No active model, frozen research result, old source manifest or replay gate was changed. "
        "Next: a compact 2023-2024 weather comparison and dated roster/starter source decision. "
        "The full enriched 2025 replay remains blocked; "
        "2025 is previously examined diagnostic data.\n\n"
        "Source: [NCAR GFS operational archive](https://gdex.ucar.edu/datasets/d084001/), "
        "DOI 10.5065/D65D8PWK, CC BY 4.0. Geocoding: "
        "[Open-Meteo](https://open-meteo.com/en/docs/geocoding-api), CC BY 4.0.\n"
    )
    (report_dir / "findings.md").write_text(text, encoding="utf-8")
    return report
