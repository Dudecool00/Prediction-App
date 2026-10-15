"""Bounded, dated 2022–2024 official-table collection; no model or outcome reads."""

import gzip
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import read_json, sha256_file, write_json
from nfl_prop_model.data.team_depth import SCHEDULE_FIELDS
from nfl_prop_model.data.team_games import team_source_hash
from nfl_prop_model.data.team_injury_archive import NICKNAMES, _gunzip
from nfl_prop_model.data.team_injury_history import (
    _digest,
    _load_index,
    _request,
    _save_index,
    cdx_url,
    index_rows,
    selected_rows,
)
from nfl_prop_model.features.team_epa import weekly_cutoffs

SEASONS = (2022, 2023, 2024)
TABLE_URL = re.compile(r"https://www\.nfl\.com/injuries/league/(202[234])/REG(\d+)$", re.I)
POLICY = {
    "scope": "2022 initialization; 2023-2024 development; canonical official injury tables only",
    "cohort": "canonical 271/272/272 games; canceled 2022 BUF-CIN excluded upstream",
    "cutoffs": "Tuesday 10 Chicago and kickoff minus 1h; capture strictly earlier; age <=72h",
    "selection": "latest whole indexed edition; no fallback past failed or omitted sections",
    "integrity": "exact id_ URL and original entity CDX digest before decoding; local checksums",
    "status": "observed Out/Q/D only; blank game status and practice participation are not healthy",
    "finality": "capture does not prove final team report completeness; model eligibility false",
    "identity": "NFL slug or unique document name within edition only; no crosswalk or weights",
    "bounds": "100 catalog rows/season, 80 versions/week, 1MB/entity, 8MB bodies, two workers",
    "missing": "missing, unverified, unsupported, stale and omitted teams stay unknown",
    "outcomes": "six schedule metadata fields only; zero model fits; no 2025 data selected",
}


def load_development_schedule(data_dir: Path) -> tuple[pl.DataFrame, dict[str, str]]:
    path = data_dir / "processed" / "team_epa" / "manifest.json"
    manifest = read_json(path)
    if manifest["pipeline_source_sha256"] != team_source_hash():
        raise DataQualityError("Canonical development schedule source changed")
    metadata = manifest["artifacts"]["games"]
    artifact = path.parent / metadata["file"]
    if sha256_file(artifact) != metadata["sha256"]:
        raise DataQualityError("Development schedule artifact checksum mismatch")
    games = (
        pl.scan_parquet(artifact)
        .select(SCHEDULE_FIELDS)
        .filter(pl.col("season").is_in(SEASONS))
        .collect()
    )
    require_keys(games, ["game_id"], "development schedule metadata")
    counts = dict(games.group_by("season").len().iter_rows())
    if counts != {2022: 271, 2023: 272, 2024: 272} or games["kickoff_utc"].null_count():
        raise DataQualityError("Development audit requires canonical 2022-2024 game cohort")
    return games.sort("season", "week", "game_id"), {
        "manifest": str(path.resolve()),
        "manifest_sha256": sha256_file(path),
        "artifact_sha256": metadata["sha256"],
    }


def development_cutoffs(games: pl.DataFrame) -> dict[tuple[int, int], list[datetime]]:
    if set(games.columns) != set(SCHEDULE_FIELDS) or not set(games["season"]) <= set(SEASONS):
        raise DataQualityError("Development coverage accepts only 2022-2024 metadata, no outcomes")
    require_keys(games, ["game_id"], "development injury games")
    if (
        not games.height
        or games.schema["kickoff_utc"] != pl.Datetime("us", "UTC")
        or games.filter(
            pl.col("kickoff_utc").is_null()
            | ~pl.col("week").is_between(1, 18)
            | ~pl.col("home_team").is_in(NICKNAMES.values())
            | ~pl.col("away_team").is_in(NICKNAMES.values())
            | (pl.col("home_team") == pl.col("away_team"))
        ).height
    ):
        raise DataQualityError("Development injury schedule metadata is invalid")
    weekly = weekly_cutoffs(games)
    return {
        key: sorted({weekly[key]} | {t - timedelta(hours=1) for t in group["kickoff_utc"]})
        for key, group in games.partition_by("season", "week", as_dict=True).items()
    }


def development_path(data_dir: Path, games: pl.DataFrame) -> Path:
    key = {
        f"{s}-{w}": [c.isoformat() for c in times]
        for (s, w), times in sorted(development_cutoffs(games).items())
    }
    # Bind game/team membership too, not just the distinct kickoff windows.
    signature = hashlib.sha256(
        (json.dumps(key, sort_keys=True) + games.sort("game_id").write_json()).encode()
    ).hexdigest()
    return data_dir / "raw" / "team_injury_development" / signature[:12] / "manifest.json"


def _catalog_url(season: int) -> str:
    return cdx_url(
        [
            ("url", f"www.nfl.com/injuries/league/{season}/"),
            ("matchType", "prefix"),
            ("from", f"{season}0801"),
            ("to", f"{season + 1}0115"),
            ("collapse", "urlkey"),
            ("limit", "101"),
        ]
    )


def _catalog_articles(rows: list[dict[str, str]], season: int) -> dict[int, str]:
    articles: dict[int, str] = {}
    for row in rows:
        if not f"{season}0801" <= row["timestamp"][:8] <= f"{season + 1}0115":
            raise DataQualityError("Development catalog capture outside season window")
        match = TABLE_URL.fullmatch(row["original"])
        if not match or int(match[1]) != season or not 1 <= int(match[2]) <= 18:
            continue
        week = int(match[2])
        if week in articles and articles[week] != row["original"]:
            raise DataQualityError("Ambiguous canonical development week URL")
        articles[week] = row["original"]
    return articles


def _week_url(article: str, cutoffs: list[datetime]) -> str:
    # Keep repeated identical captures: a more recent verified capture can satisfy freshness.
    return cdx_url(
        [
            ("url", article),
            ("from", (cutoffs[0] - timedelta(days=7)).strftime("%Y%m%d")),
            ("to", cutoffs[-1].strftime("%Y%m%d")),
            ("limit", "81"),
        ]
    )


def _check_rows(rows: list[dict[str, str]], article: str, cutoffs: list[datetime]) -> None:
    start = (cutoffs[0] - timedelta(days=7)).strftime("%Y%m%d")
    end = cutoffs[-1].strftime("%Y%m%d")
    if any(r["original"] != article or not start <= r["timestamp"][:8] <= end for r in rows):
        raise DataQualityError("Development week index URL/date window mismatch")


def _load_week(path: Path, article: str | None, cutoffs: list[datetime]) -> dict[str, Any]:
    report = read_json(path)
    if report["article"] != article or report["cutoffs"] != [c.isoformat() for c in cutoffs]:
        raise DataQualityError("Development week selection inputs changed")
    rows = _load_index(path.parent, report["index"], 80) if article else []
    if article:
        if report["index_url"] != _week_url(article, cutoffs):
            raise DataQualityError("Development week index request changed")
        _check_rows(rows, article, cutoffs)
    expected = selected_rows(rows, cutoffs)
    if len(expected) != len(report["captures"]):
        raise DataQualityError("Development selected edition count mismatch")
    for row, receipt in zip(expected, report["captures"], strict=True):
        stamp, digest = row["timestamp"], row["digest"]
        if (
            any(receipt[k] != v for k, v in row.items())
            or receipt["archive_url"] != f"https://web.archive.org/web/{stamp}id_/{article}"
        ):
            raise DataQualityError("Development capture receipt does not match CDX index")
        if receipt["status"] == "unavailable":
            if not receipt.get("reason") or any(
                k in receipt for k in ("file", "body_sha256", "compressed_sha256")
            ):
                raise DataQualityError("Unverified development capture has invalid receipt")
            continue
        if receipt["status"] != "verified" or receipt["file"] != f"{stamp}-{digest}.html.gz":
            raise DataQualityError("Invalid development capture receipt state")
        file = path.parent / receipt["file"]
        if sha256_file(file) != receipt["compressed_sha256"]:
            raise DataQualityError("Development compressed capture checksum mismatch")
        body = _gunzip(file.read_bytes())
        if _digest(body) != digest or hashlib.sha256(body).hexdigest() != receipt["body_sha256"]:
            raise DataQualityError("Development original capture digest mismatch")
    return report


def load_development(path: Path, games: pl.DataFrame) -> dict[tuple[int, int], dict[str, Any]]:
    report = read_json(path)
    if (
        report["format_version"] != 1
        or path.parent.name != development_path(Path("data"), games).parent.name
    ):
        raise DataQualityError("Development injury collection selection key changed")
    catalogs = {}
    for item in report["catalogs"]:
        season = item["season"]
        if season in catalogs or season not in SEASONS or item["url"] != _catalog_url(season):
            raise DataQualityError("Invalid development catalog cohort/request")
        rows = _load_index(path.parent / str(season), item["index"], 100)
        catalogs[season] = _catalog_articles(rows, season)
    if set(catalogs) != set(SEASONS):
        raise DataQualityError("Development catalog seasons missing")
    cutoffs = development_cutoffs(games)
    if [(r["season"], r["week"]) for r in report["weeks"]] != sorted(cutoffs):
        raise DataQualityError("Development injury week cohort changed")
    result = {}
    for item in report["weeks"]:
        key = (item["season"], item["week"])
        week_path = path.parent / str(key[0]) / f"week-{key[1]:02}" / "manifest.json"
        if sha256_file(week_path) != item["manifest_sha256"]:
            raise DataQualityError("Development week manifest checksum mismatch")
        result[key] = _load_week(week_path, catalogs[key[0]].get(key[1]), cutoffs[key])
        if (result[key]["season"], result[key]["week"]) != key:
            raise DataQualityError("Development week manifest season/week mismatch")
    return result


def fetch_development(data_dir: Path) -> Path:
    games, schedule = load_development_schedule(data_dir)
    cutoffs = development_cutoffs(games)
    path = development_path(data_dir, games)
    if path.exists():
        load_development(path, games)
        return path
    catalogs = []
    articles = {}
    for season in SEASONS:
        folder = path.parent / str(season)
        checkpoint = folder / "catalog.json"
        url = _catalog_url(season)
        if checkpoint.exists():
            item = read_json(checkpoint)
            if item["url"] != url or item["season"] != season:
                raise DataQualityError("Development catalog request changed")
            rows = _load_index(folder, item["index"], 100)
        else:
            raw, _ = _request(url, 100000)
            rows = index_rows(raw, 100)
            item = {
                "season": season,
                "url": url,
                "index": _save_index(folder, raw),
                "retrieved_at_utc": datetime.now(UTC).isoformat(),
            }
            write_json(checkpoint, item)
        articles[season] = _catalog_articles(rows, season)
        catalogs.append(item)
    lock = Lock()

    def fetch_week(key: tuple[int, int]) -> dict[str, Any]:
        season, week = key
        folder = path.parent / str(season) / f"week-{week:02}"
        week_path = folder / "manifest.json"
        article = articles[season].get(week)
        if week_path.exists():
            _load_week(week_path, article, cutoffs[key])
        else:
            report: dict[str, Any] = {
                "season": season,
                "week": week,
                "article": article,
                "cutoffs": [c.isoformat() for c in cutoffs[key]],
                "captures": [],
                "retrieved_at_utc": datetime.now(UTC).isoformat(),
            }
            if article:
                url = _week_url(article, cutoffs[key])
                index_path = folder / "index.json"
                if index_path.exists():
                    saved = read_json(index_path)
                    if saved["url"] != url:
                        raise DataQualityError("Development index request changed")
                    index_meta = saved["index"]
                    rows = _load_index(folder, index_meta, 80)
                else:
                    raw, _ = _request(url, 100000)
                    rows = index_rows(raw, 80)
                    index_meta = _save_index(folder, raw)
                    write_json(
                        index_path,
                        {
                            "url": url,
                            "index": index_meta,
                            "retrieved_at_utc": datetime.now(UTC).isoformat(),
                        },
                    )
                _check_rows(rows, article, cutoffs[key])
                report.update(index_url=url, index=index_meta)
                for row in selected_rows(rows, cutoffs[key]):
                    stamp, digest = row["timestamp"], row["digest"]
                    url = f"https://web.archive.org/web/{stamp}id_/{article}"
                    receipt: dict[str, Any] = {**row, "archive_url": url}
                    try:
                        file = folder / f"{stamp}-{digest}.html.gz"
                        if file.exists():
                            body = _gunzip(file.read_bytes())
                        else:
                            body, resolved = _request(url, 1000000)
                            if resolved != url:
                                raise DataQualityError("Development capture URL mismatch")
                        if _digest(body) != digest:
                            raise DataQualityError("Development original entity digest mismatch")
                        if body.startswith(b"\x1f\x8b"):
                            _gunzip(body)
                        with lock:
                            if not file.exists():
                                compressed = gzip.compress(body, mtime=0)
                                used = sum(
                                    p.stat().st_size for p in path.parent.glob("*/week-*/*.html.gz")
                                )
                                if used + len(compressed) > 8000000:
                                    raise DataQualityError(
                                        "Development capture storage budget exceeded"
                                    )
                                file.write_bytes(compressed)
                        receipt.update(
                            status="verified",
                            file=file.name,
                            body_sha256=hashlib.sha256(body).hexdigest(),
                            compressed_sha256=sha256_file(file),
                        )
                    except (OSError, ValueError) as error:
                        receipt.update(status="unavailable", reason=str(error))
                    report["captures"].append(receipt)
            write_json(week_path, report)
        return {"season": season, "week": week, "manifest_sha256": sha256_file(week_path)}

    def checkpoint_week(key: tuple[int, int]) -> dict[str, Any]:
        try:
            return fetch_week(key)
        except (OSError, ValueError) as error:
            # One failed index must not cancel independent weeks queued behind it.
            # No failure is published as an empty archive or a finalized collection.
            return {"season": key[0], "week": key[1], "error": str(error)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        weeks = list(pool.map(checkpoint_week, sorted(cutoffs)))
    if failures := [r for r in weeks if "error" in r]:
        raise DataQualityError(
            f"Incomplete development archive; resume completed weeks: {failures}"
        )
    write_json(
        path,
        {
            "format_version": 1,
            "schedule": schedule,
            "catalogs": catalogs,
            "weeks": weeks,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    load_development(path, games)
    return path
