"""Preserved weekly 2025 injury editions, separate from the frozen Week 1 pilot."""

import base64
import gzip
import hashlib
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, write_json
from nfl_prop_model.data.team_depth import aware, load_schedule_only
from nfl_prop_model.data.team_injury_archive import (
    NICKNAMES,
    POSITION_GROUPS,
    ArticleParts,
    _clean,
    _get,
    _gunzip,
    _segments,
)
from nfl_prop_model.features.team_epa import weekly_cutoffs

POLICY = {
    "scope": "canonical NFL weekly injury articles, 2025 regular season; source audit only",
    "catalog": "dated CDX prefix catalog; exclude query variants and unrelated article slugs",
    "selection": "latest whole edition strictly before each cutoff; maximum age 72 hours",
    "integrity": "exact id_ URL and original entity CDX SHA1/base32; bounded gzip decoding",
    "missing": "missing, unverified, unsupported, stale or omitted team stays unknown; no fallback",
    "timing": "archive capture proves version timing; first publication date does not",
    "bounds": "100 catalog rows, 20 editions/week, 1 MB/entity, 8 MB retained bodies, two workers",
    "outcomes": "schedule metadata only; no actual scores, game QB labels or model fits",
    "readiness": "does not establish starters, full IR rosters or clear enriched replay gates",
}
PREFIX = "www.nfl.com/news/nfl-week-"
SLUG = re.compile(
    r"https://www\.nfl\.com/news/nfl-week-(\d+)-injury-report-player-statuses"
    r"(?:-for-all-\d+-games)?(?:-2025-season)?$"
)


def cdx_url(params: list[tuple[str, str]]) -> str:
    return "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(
        params + [("output", "json"), ("filter", "statuscode:200")]
    )


def _request(url: str, limit: int) -> tuple[bytes, str]:
    try:
        return _get(url, limit)
    except OSError:
        # Retry transient transport failures once; integrity failures are never retried.
        return _get(url, limit)


def index_rows(payload: bytes, bound: int) -> list[dict[str, str]]:
    values = json.loads(payload)
    if not values:
        return []
    if len(set(values[0])) != len(values[0]):
        raise DataQualityError("Duplicate archive index columns")
    if not {"timestamp", "original", "mimetype", "statuscode", "digest"} <= set(values[0]):
        raise DataQualityError("Archive index schema changed")
    rows = [dict(zip(values[0], row, strict=True)) for row in values[1:]]
    if len(rows) > bound:
        raise DataQualityError("Archive index exceeds declared bound")
    if len({(r["timestamp"], r["original"]) for r in rows}) != len(rows):
        raise DataQualityError("Conflicting archive index versions at one timestamp")
    if any(
        not re.fullmatch(r"\d{14}", row["timestamp"])
        or row["statuscode"] != "200"
        or row["mimetype"] != "text/html"
        or not re.fullmatch(r"[A-Z2-7]{32}", row["digest"])
        for row in rows
    ):
        raise DataQualityError("Invalid archive index receipt")
    return rows


def week_cutoffs(games: pl.DataFrame) -> dict[int, list[datetime]]:
    weekly = weekly_cutoffs(games)
    return {
        week: sorted(
            {weekly[(2025, week)]} | {value - timedelta(hours=1) for value in group["kickoff_utc"]}
        )
        for (week,), group in games.partition_by("week", as_dict=True).items()
    }


def selected_rows(rows: list[dict[str, str]], cutoffs: list[datetime]) -> list[dict[str, str]]:
    selected = {}
    for cutoff in cutoffs:
        eligible = [row for row in rows if capture_time(row) < aware(cutoff)]
        if eligible:
            latest = max(eligible, key=lambda row: row["timestamp"])
            selected[latest["timestamp"]] = latest
    return [selected[key] for key in sorted(selected)]


def capture_time(row: dict[str, str]) -> datetime:
    return datetime.strptime(row["timestamp"], "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def _save_index(directory: Path, payload: bytes) -> dict[str, str]:
    digest = hashlib.sha256(payload).hexdigest()
    name = "index-" + digest + ".json"
    path = directory / name
    directory.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != payload:
        raise DataQualityError("Preserved archive index changed")
    if not path.exists():
        path.write_bytes(payload)
    return {"file": name, "sha256": digest}


def _load_index(directory: Path, metadata: dict[str, str], bound: int) -> list[dict[str, str]]:
    if metadata["file"] != "index-" + metadata["sha256"] + ".json":
        raise DataQualityError("Archive index filename mismatch")
    path = directory / metadata["file"]
    if sha256_file(path) != metadata["sha256"]:
        raise DataQualityError("Archive index checksum mismatch")
    return index_rows(path.read_bytes(), bound)


def history_path(data_dir: Path, games: pl.DataFrame) -> Path:
    cutoffs = week_cutoffs(games)
    signature = hashlib.sha256(
        json.dumps(
            {str(w): [c.isoformat() for c in values] for w, values in sorted(cutoffs.items())},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return data_dir / "raw" / "team_injury_history" / "2025" / signature[:12] / "manifest.json"


def fetch_history(data_dir: Path) -> Path:
    games, schedule = load_schedule_only(data_dir)
    cutoffs = week_cutoffs(games)
    path = history_path(data_dir, games)
    directory = path.parent
    if path.exists():
        load_history(path, games)
        return path
    catalog_url = cdx_url(
        [
            ("url", PREFIX),
            ("matchType", "prefix"),
            ("from", "20250801"),
            ("to", "20260106"),
            ("filter", "original:.*injury-report.*"),
            ("collapse", "urlkey"),
            ("limit", "101"),
        ]
    )
    checkpoint = directory / "catalog.json"
    if checkpoint.exists():
        saved = read_json(checkpoint)
        if saved["url"] != catalog_url:
            raise DataQualityError("Catalog request changed")
        catalog_meta = saved["index"]
        catalog = _load_index(directory, catalog_meta, 100)
    else:
        # Recover an index preserved before an interrupted catalog checkpoint write.
        existing = list(directory.glob("index-*.json"))
        if len(existing) == 1:
            payload = existing[0].read_bytes()
        else:
            payload, _ = _request(catalog_url, 100000)
        catalog = index_rows(payload, 100)
        catalog_meta = _save_index(directory, payload)
        write_json(
            checkpoint,
            {
                "url": catalog_url,
                "index": catalog_meta,
                "validated_at_utc": datetime.now(UTC).isoformat(),
            },
        )
    if any(not "20250801" <= row["timestamp"][:8] <= "20260106" for row in catalog):
        raise DataQualityError("Catalog capture falls outside the requested season window")
    articles: dict[int, str] = {}
    for row in catalog:
        if (match := SLUG.fullmatch(row["original"])) and 1 <= int(match[1]) <= 18:
            week = int(match[1])
            if week in articles and articles[week] != row["original"]:
                raise DataQualityError("Multiple canonical injury articles for one week")
            articles[week] = row["original"]

    storage_lock = Lock()

    def fetch_week(week: int) -> dict[str, Any]:
        folder = directory / f"week-{week:02}"
        week_path = folder / "manifest.json"
        if week_path.exists():
            _load_week(week_path, cutoffs[week], articles.get(week))
            return {"week": week, "manifest_sha256": sha256_file(week_path)}
        article = articles.get(week)
        report: dict[str, Any] = {
            "week": week,
            "article": article,
            "cutoffs": [c.isoformat() for c in cutoffs[week]],
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
            "captures": [],
        }
        if article:
            url = cdx_url(
                [
                    ("url", article),
                    ("from", (cutoffs[week][0] - timedelta(days=7)).strftime("%Y%m%d")),
                    ("to", cutoffs[week][-1].strftime("%Y%m%d")),
                    ("collapse", "digest"),
                    ("limit", "21"),
                ]
            )
            raw, _ = _request(url, 100000)
            rows = index_rows(raw, 20)
            if any(row["original"] != article for row in rows):
                raise DataQualityError("Week index returned a different original URL")
            report.update(index=_save_index(folder, raw), index_url=url)
            for row in selected_rows(rows, cutoffs[week]):
                stamp, digest = row["timestamp"], row["digest"]
                archive_url = f"https://web.archive.org/web/{stamp}id_/{article}"
                receipt: dict[str, Any] = {**row, "archive_url": archive_url}
                try:
                    body, resolved = _request(archive_url, 1000000)
                    if resolved != archive_url or _digest(body) != digest:
                        raise DataQualityError("Capture URL or original entity digest mismatch")
                    file = folder / (stamp + "-" + digest + ".html.gz")
                    with storage_lock:
                        if file.exists() and _gunzip(file.read_bytes()) != body:
                            raise DataQualityError("Existing original capture changed")
                        if not file.exists():
                            compressed = gzip.compress(body, mtime=0)
                            used = sum(p.stat().st_size for p in directory.rglob("*.html.gz"))
                            if used + len(compressed) > 8000000:
                                raise DataQualityError("Retained archive bodies exceed 8 MB budget")
                            file.write_bytes(compressed)
                    receipt.update(
                        status="verified",
                        file=file.name,
                        body_sha256=hashlib.sha256(body).hexdigest(),
                        compressed_sha256=sha256_file(file),
                    )
                except (OSError, ValueError) as exc:
                    receipt.update(status="unavailable", reason=str(exc))
                report["captures"].append(receipt)
        write_json(week_path, report)
        print(f"Week {week}: {len(report['captures'])} selected archive editions", flush=True)
        return {"week": week, "manifest_sha256": sha256_file(week_path)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        weeks = list(pool.map(fetch_week, sorted(cutoffs)))
    write_json(
        path,
        {
            "format_version": 1,
            "selection_key": directory.name,
            "schedule": schedule,
            "catalog_url": catalog_url,
            "catalog": catalog_meta,
            "weeks": weeks,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    load_history(path, games)
    return path


def _digest(body: bytes) -> str:
    return base64.b32encode(hashlib.sha1(body).digest()).decode()


def _load_week(path: Path, cutoffs: list[datetime], article: str | None) -> dict[str, Any]:
    report = read_json(path)
    if report["cutoffs"] != [c.isoformat() for c in cutoffs] or report["article"] != article:
        raise DataQualityError("Weekly archive selection inputs changed")
    rows = _load_index(path.parent, report["index"], 20) if article else []
    if any(row["original"] != article for row in rows):
        raise DataQualityError("Preserved week index original URL mismatch")
    expected = selected_rows(rows, cutoffs)
    if len(expected) != len(report["captures"]):
        raise DataQualityError("Selected archive edition count changed")
    for row, receipt in zip(expected, report["captures"], strict=True):
        if any(receipt[key] != value for key, value in row.items()):
            raise DataQualityError("Capture receipt disagrees with original index")
        stamp, digest = row["timestamp"], row["digest"]
        if receipt["archive_url"] != f"https://web.archive.org/web/{stamp}id_/{article}":
            raise DataQualityError("Archive capture URL mismatch")
        if receipt["status"] == "unavailable":
            continue
        if receipt["status"] != "verified" or receipt["file"] != f"{stamp}-{digest}.html.gz":
            raise DataQualityError("Invalid capture receipt state/filename")
        file = path.parent / receipt["file"]
        if sha256_file(file) != receipt["compressed_sha256"]:
            raise DataQualityError("Compressed weekly capture checksum mismatch")
        body = _gunzip(file.read_bytes())
        if _digest(body) != digest or hashlib.sha256(body).hexdigest() != receipt["body_sha256"]:
            raise DataQualityError("Original weekly capture digest mismatch")
    return report


def load_history(path: Path, games: pl.DataFrame) -> dict[int, dict[str, Any]]:
    manifest = read_json(path)
    if manifest["format_version"] != 1:
        raise DataQualityError("Unsupported weekly injury history format")
    cutoffs = week_cutoffs(games)
    if path.parent.name != history_path(Path("data"), games).parent.name:
        raise DataQualityError("Weekly source selection key mismatch")
    catalog = _load_index(path.parent, manifest["catalog"], 100)
    articles: dict[int, str] = {}
    for row in catalog:
        if (match := SLUG.fullmatch(row["original"])) and 1 <= int(match[1]) <= 18:
            week = int(match[1])
            if week in articles and articles[week] != row["original"]:
                raise DataQualityError("Ambiguous preserved article catalog")
            articles[week] = row["original"]
    if [row["week"] for row in manifest["weeks"]] != sorted(cutoffs):
        raise DataQualityError("Weekly archive cohort mismatch")
    result = {}
    for record in manifest["weeks"]:
        week = record["week"]
        week_path = path.parent / f"week-{week:02}" / "manifest.json"
        if sha256_file(week_path) != record["manifest_sha256"]:
            raise DataQualityError("Weekly source manifest checksum mismatch")
        result[week] = _load_week(week_path, cutoffs[week], articles.get(week))
    return result


def parse_weekly_article(
    payload: bytes, week: int, captured: datetime, first_cutoff: datetime
) -> dict[str, dict[str, Any]]:
    first_cutoff, captured = aware(first_cutoff), aware(captured)
    parser = ArticleParts()
    html = _gunzip(payload) if payload.startswith(b"\x1f\x8b") else payload
    parser.feed(html.decode("utf-8"))
    if not parser.article_found or not re.search(
        rf"\bWeek\s+{week}\b.*injury report", parser.title, re.I
    ):
        raise DataQualityError("Captured body does not match the expected injury week")
    published = aware(
        datetime.fromisoformat(parser.metadata["datePublished"].replace("Z", "+00:00"))
    )
    modified = aware(datetime.fromisoformat(parser.metadata["dateModified"].replace("Z", "+00:00")))
    if not first_cutoff - timedelta(days=7) <= published <= modified <= aware(captured):
        raise DataQualityError("Archived article dates conflict with its NFL week/capture")
    groups = {**POSITION_GROUPS, "OL": POSITION_GROUPS["OL"] | {"LT", "RT", "LG", "RG"}}
    positions = set().union(*groups.values())
    sections: dict[str, dict[str, Any]] = {}
    team = None
    for part in parser.parts:
        text = part["text"]
        if part["tag"] in {"h2", "matchup"}:
            team = None
            if "ALREADY COMPLETED" in text.upper():
                break
        is_team = text.upper() in NICKNAMES and part["tag"] in {"h3", "p"}
        if part["tag"] == "h3" or is_team:
            team = NICKNAMES.get(text.upper())
            if team is None:
                # Empty/layout headings cannot transfer their following players to a prior team.
                continue
            if team in sections:
                raise DataQualityError("Duplicate injury team heading")
            sections[team] = {"players": {}, "empty": False, "notes": False}
            continue
        if team is None or part["tag"] not in {"li", "p"} or not text:
            continue
        section = sections[team]
        if text.rstrip(".").casefold() == "no injury designations":
            section["empty"] = True
            continue
        status = re.match(r"^(OUT|QUESTIONABLE|DOUBTFUL)\s*:\s*(.*)$", text, re.I)
        if status is None:
            section["notes"] = True
            continue
        try:
            segments = _segments(status[2])
        except DataQualityError:
            section["notes"] = True
            continue
        for segment in segments:
            if not segment:
                continue  # Ignore empty separators; a wholly empty status remains pending.
            if segment.count("(") > 1 or (")" in segment and segment.split(")", 1)[1].strip()):
                # Malformed separators must not silently collapse two athletes into one.
                section["notes"] = True
                continue
            first, _, rest = segment.split("(", 1)[0].strip().partition(" ")
            position = first if first in positions else None
            name = _clean(rest if position else segment.split("(", 1)[0]).casefold()
            if not name or name in section["players"]:
                raise DataQualityError("Empty or duplicate injury player within team edition")
            section["players"][name] = (status[1].upper(), position)
    if not sections:
        raise DataQualityError("No auditable injury team sections")
    result = {}
    for team, section in sections.items():
        players = list(section["players"].values())
        if players and section["empty"]:
            raise DataQualityError("Explicit empty designation statement conflicts with players")
        complete = not section["notes"] and bool(players or section["empty"])
        out = [position for status, position in players if status == "OUT"]
        result[team] = {
            "status": "no_designations"
            if complete and section["empty"]
            else "designations"
            if complete
            else "partial_or_pending",
            "out_count": len(out) if complete else None,
            "unknown_position_out_players": out.count(None),
            **{
                name + "_out_count": sum(position in values for position in out)
                if complete and None not in out
                else None
                for name, values in groups.items()
            },
        }
    return result
