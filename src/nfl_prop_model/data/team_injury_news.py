"""Dated development news editions using the frozen weekly-article definition."""

import gzip
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, write_json
from nfl_prop_model.data.team_injury_archive import _gunzip
from nfl_prop_model.data.team_injury_development import (
    SEASONS,
    _check_rows,
    _load_week,
    _week_url,
    development_cutoffs,
    load_development_schedule,
)
from nfl_prop_model.data.team_injury_history import (
    _digest,
    _load_index,
    _request,
    _save_index,
    cdx_url,
    index_rows,
    selected_rows,
)

DEFAULT_NEWS_SEASONS = (2023, 2024)
PREFIXES = ("www.nfl.com/news/week-", "www.nfl.com/news/nfl-week-")
SLUG = re.compile(
    r"https://www\.nfl\.com/news/(?:nfl-)?week-(\d+)-(?:nfl-)?injury-report"
    r"(?:-for-(202[234])-(?:nfl-)?season|-player-statuses(?:-for-all-\d+-games)?)$"
)
POLICY = {
    "scope": "2023-2024 development by default; optional 2022 initialization; news sources only",
    "definition": "reuse frozen 2025 weekly-article parser; formal designations or explicit none",
    "catalog": "bounded canonical and tracking-link catalogs; each variant separately proven",
    "timing": "capture strictly before Tuesday 10 Chicago / kickoff minus 1h; age <=72h",
    "selection": "latest whole edition; no fallback past failed, unsupported or omitted sections",
    "integrity": "exact id_ URL, original entity CDX digest, bounded gzip and local checksums",
    "missing": "N/A, missing sections, pending notes and malformed lists do not imply healthy",
    "bounds": "100 rows/catalog, 8 query URLs/week, 80 versions/URL, 1MB/entity, 8MB bodies",
    "outcomes": "six schedule fields only; all games retained; no actual scores or model fits",
    "readiness": "source qualification only; does not clear model comparison or enriched replay",
}


def news_schedule(
    data_dir: Path,
    seasons: tuple[int, ...] = DEFAULT_NEWS_SEASONS,
) -> tuple[pl.DataFrame, dict[str, str]]:
    if not seasons or not set(seasons) <= set(SEASONS) or len(set(seasons)) != len(seasons):
        raise ValueError("News audit seasons must be unique choices from 2022, 2023, 2024")
    games, metadata = load_development_schedule(data_dir)
    games = games.filter(pl.col("season").is_in(seasons))
    if set(games["season"]) != set(seasons):
        raise DataQualityError("News schedule requested season missing")
    development_cutoffs(games)
    return games, metadata


def news_path(data_dir: Path, games: pl.DataFrame) -> Path:
    development_cutoffs(games)
    signature = hashlib.sha256(games.sort("game_id").write_json().encode()).hexdigest()
    return data_dir / "raw" / "team_injury_news" / signature[:12] / "manifest.json"


def catalog_url(season: int, prefix: str) -> str:
    return cdx_url(
        [
            ("url", prefix),
            ("matchType", "prefix"),
            ("from", f"{season}0801"),
            ("to", f"{season + 1}0115"),
            ("filter", "original:.*injury-report.*"),
            ("filter", "!original:.*[?].*"),
            ("collapse", "urlkey"),
            ("limit", "101"),
        ]
    )


def catalog_articles(rows: list[dict[str, str]], season: int, prefix: str) -> dict[int, str]:
    result: dict[int, str] = {}
    for row in rows:
        original = row["original"]
        if (
            not f"{season}0801" <= row["timestamp"][:8] <= f"{season + 1}0115"
            or not original.startswith("https://" + prefix)
            or "?" in original
        ):
            raise DataQualityError("News catalog URL/date window mismatch")
        match = SLUG.fullmatch(original)
        if not match or (match[2] and int(match[2]) != season) or not 1 <= int(match[1]) <= 18:
            continue
        week = int(match[1])
        if week in result and result[week] != original:
            raise DataQualityError("Ambiguous weekly news URL")
        result[week] = original
    return result


def _catalogs(path: Path, seasons: list[int]) -> dict[int, dict[int, str]]:
    report = read_json(path)
    result: dict[int, dict[int, str]] = {s: {} for s in seasons}
    if [(c["season"], c["prefix"]) for c in report["catalogs"]] != [
        (s, p) for s in seasons for p in PREFIXES
    ]:
        raise DataQualityError("News catalog cohort changed")
    for c in report["catalogs"]:
        season, prefix = c["season"], c["prefix"]
        if c["url"] != catalog_url(season, prefix):
            raise DataQualityError("News catalog request changed")
        folder = path.parent / str(season) / f"catalog-{PREFIXES.index(prefix)}"
        articles = catalog_articles(_load_index(folder, c["index"], 100), season, prefix)
        for week, url in articles.items():
            if week in result[season] and result[season][week] != url:
                raise DataQualityError("Multiple canonical news URLs for one season/week")
            result[season][week] = url
    return result


def load_news(path: Path, games: pl.DataFrame) -> dict[tuple[int, int], dict[str, Any]]:
    report = read_json(path)
    if (
        report["format_version"] != 1
        or path.parent.name != news_path(Path("data"), games).parent.name
    ):
        raise DataQualityError("News collection schedule/format changed")
    articles = _catalogs(path, sorted(set(games["season"])))
    cutoffs = development_cutoffs(games)
    if [(r["season"], r["week"]) for r in report["weeks"]] != sorted(cutoffs):
        raise DataQualityError("News collection week cohort changed")
    result = {}
    for item in report["weeks"]:
        key = (item["season"], item["week"])
        week_path = path.parent / str(key[0]) / f"week-{key[1]:02}" / "manifest.json"
        if sha256_file(week_path) != item["manifest_sha256"]:
            raise DataQualityError("News week manifest checksum mismatch")
        result[key] = _load_week(week_path, articles[key[0]].get(key[1]), cutoffs[key])
        if (result[key]["season"], result[key]["week"]) != key:
            raise DataQualityError("News week season/week mismatch")
    return result


def collect_article(
    folder: Path,
    article: str | None,
    key: tuple[int, int],
    cutoffs: list[datetime],
    collection_root: Path,
    lock: Any,
) -> Path:
    """Preserve one exact original URL, independent of canonical or tracking-link form."""
    path = folder / "manifest.json"
    if path.exists():
        _load_week(path, article, cutoffs)
        return path
    report: dict[str, Any] = {
        "season": key[0],
        "week": key[1],
        "article": article,
        "cutoffs": [c.isoformat() for c in cutoffs],
        "captures": [],
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
    }
    if article:
        url = _week_url(article, cutoffs)
        checkpoint = folder / "index.json"
        if checkpoint.exists():
            saved = read_json(checkpoint)
            if saved["url"] != url:
                raise DataQualityError("News week index request changed")
            index_meta = saved["index"]
            rows = _load_index(folder, index_meta, 80)
        else:
            payload, _ = _request(url, 100000)
            rows = index_rows(payload, 80)
            index_meta = _save_index(folder, payload)
            write_json(checkpoint, {"url": url, "index": index_meta})
        _check_rows(rows, article, cutoffs)
        report.update(index=index_meta, index_url=url)
        for row in selected_rows(rows, cutoffs):
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
                        raise DataQualityError("News capture URL mismatch")
                if _digest(body) != digest:
                    raise DataQualityError("News original entity digest mismatch")
                if body.startswith(b"\x1f\x8b"):
                    _gunzip(body)
                with lock:
                    if not file.exists():
                        compressed = gzip.compress(body, mtime=0)
                        used = sum(p.stat().st_size for p in collection_root.rglob("*.html.gz"))
                        if used + len(compressed) > 8000000:
                            raise DataQualityError("News body storage budget exceeded")
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
    write_json(path, report)
    return path


def fetch_news(data_dir: Path, seasons: tuple[int, ...] = DEFAULT_NEWS_SEASONS) -> Path:
    games, schedule = news_schedule(data_dir, seasons)
    path = news_path(data_dir, games)
    if path.exists():
        load_news(path, games)
        return path
    catalogs = []
    # Fetch 2024 first, while retaining a stable ascending catalog order in the manifest.
    for season in sorted(seasons, reverse=True):
        for number, prefix in enumerate(PREFIXES):
            folder = path.parent / str(season) / f"catalog-{number}"
            checkpoint = folder / "manifest.json"
            url = catalog_url(season, prefix)
            if checkpoint.exists():
                item = read_json(checkpoint)
                if (item["season"], item["prefix"], item["url"]) != (season, prefix, url):
                    raise DataQualityError("News catalog checkpoint changed")
                rows = _load_index(folder, item["index"], 100)
            else:
                payload, _ = _request(url, 100000)
                rows = index_rows(payload, 100)
                item = {
                    "season": season,
                    "prefix": prefix,
                    "url": url,
                    "index": _save_index(folder, payload),
                    "retrieved_at_utc": datetime.now(UTC).isoformat(),
                }
                write_json(checkpoint, item)
            catalog_articles(rows, season, prefix)
            catalogs.append(item)
    catalogs.sort(key=lambda c: (c["season"], PREFIXES.index(c["prefix"])))
    selection = path.parent / "catalogs.json"
    write_json(selection, {"catalogs": catalogs})
    articles = _catalogs(selection, sorted(seasons))
    cutoffs = development_cutoffs(games)
    lock = Lock()

    def fetch_week(key: tuple[int, int]) -> dict[str, Any]:
        season, week = key
        folder = path.parent / str(season) / f"week-{week:02}"
        article = articles[season].get(week)
        week_path = collect_article(folder, article, key, cutoffs[key], path.parent, lock)
        return {"season": season, "week": week, "manifest_sha256": sha256_file(week_path)}

    def checkpoint_week(key: tuple[int, int]) -> dict[str, Any]:
        try:
            return fetch_week(key)
        except (OSError, ValueError) as error:
            return {"season": key[0], "week": key[1], "error": str(error)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        weeks = list(pool.map(checkpoint_week, sorted(cutoffs, key=lambda k: (-k[0], k[1]))))
    if failures := [r for r in weeks if "error" in r]:
        raise DataQualityError(f"Incomplete news archive; resume completed weeks: {failures}")
    write_json(
        path,
        {
            "format_version": 1,
            "schedule": schedule,
            "catalogs": catalogs,
            "weeks": sorted(weeks, key=lambda r: (r["season"], r["week"])),
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    load_news(path, games)
    return path
