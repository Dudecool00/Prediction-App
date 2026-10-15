"""Individually proven tracking-link editions, retaining the canonical collection."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, write_json
from nfl_prop_model.data.team_injury_archive import _gunzip
from nfl_prop_model.data.team_injury_development import _load_week, development_cutoffs
from nfl_prop_model.data.team_injury_history import (
    _load_index,
    _request,
    _save_index,
    cdx_url,
    index_rows,
)
from nfl_prop_model.data.team_injury_news import (
    DEFAULT_NEWS_SEASONS,
    PREFIXES,
    SLUG,
    collect_article,
    fetch_news,
    load_news,
    news_path,
    news_schedule,
)

TRACKING_KEYS = {"campaign", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}


def qualified_path(data_dir: Path, games: pl.DataFrame) -> Path:
    return news_path(data_dir, games).with_name("qualified.json")


def query_catalog_url(season: int, prefix: str) -> str:
    return cdx_url(
        [
            ("url", prefix),
            ("matchType", "prefix"),
            ("from", f"{season}0801"),
            ("to", f"{season + 1}0115"),
            ("filter", "original:.*[?].*"),
            ("filter", f"original:.*for-{season}-(nfl-)?season[?].*"),
            ("collapse", "urlkey"),
            ("limit", "101"),
        ]
    )


def tracking_canonical(original: str, season: int) -> str | None:
    parsed = urlsplit(original)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    match = SLUG.fullmatch(base)
    if (
        len(original) > 2048
        or not match
        or not match[2]
        or int(match[2]) != season
        or not 1 <= int(match[1]) <= 18
        or not parsed.query
        or parsed.fragment
    ):
        return None
    try:
        fields = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=8)
    except ValueError:
        return None
    if (
        not fields
        or len(dict(fields)) != len(fields)
        or any(k not in TRACKING_KEYS or not v or len(v) > 200 for k, v in fields)
    ):
        return None
    return base


class CanonicalLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        props = dict(attrs)
        if tag == "link" and "canonical" in (props.get("rel") or "").split():
            self.links.append(props.get("href") or "")


def require_canonical(body: bytes, expected: str) -> None:
    parser = CanonicalLinks()
    html = _gunzip(body) if body.startswith(b"\x1f\x8b") else body
    parser.feed(html.decode("utf-8"))
    if parser.links != [expected]:
        raise DataQualityError("Tracking edition canonical article identity is unverified")


def _inventory(
    path: Path,
    catalogs: list[dict[str, Any]],
    seasons: list[int],
) -> list[dict[str, Any]]:
    if [(c["season"], c["prefix"]) for c in catalogs] != [
        (s, p) for s in seasons for p in PREFIXES
    ]:
        raise DataQualityError("Tracking catalog cohort changed")
    urls: dict[tuple[int, int], set[str]] = {}
    for item in catalogs:
        season, prefix = item["season"], item["prefix"]
        if item["url"] != query_catalog_url(season, prefix):
            raise DataQualityError("Tracking catalog request changed")
        folder = path.parent / str(season) / f"query-catalog-{PREFIXES.index(prefix)}"
        for row in _load_index(folder, item["index"], 100):
            if not f"{season}0801" <= row["timestamp"][:8] <= f"{season + 1}0115" or not row[
                "original"
            ].startswith("https://" + prefix):
                raise DataQualityError("Tracking catalog URL/date window mismatch")
            base = tracking_canonical(row["original"], season)
            if base:
                match = SLUG.fullmatch(base)
                assert match is not None
                urls.setdefault((season, int(match[1])), set()).add(row["original"])
    if any(len(values) > 8 for values in urls.values()):
        raise DataQualityError("Tracking catalog exceeds eight URLs for one week")
    return [
        {
            "season": s,
            "week": w,
            "article": url,
            "folder": "query-" + hashlib.sha256(url.encode()).hexdigest()[:12],
        }
        for (s, w), values in sorted(urls.items())
        for url in sorted(values)
    ]


def load_qualified(path: Path, games: pl.DataFrame) -> dict[tuple[int, int], dict[str, Any]]:
    report = read_json(path)
    canonical = path.with_name("manifest.json")
    if (
        report["format_version"] != 1
        or path != qualified_path(path.parents[3], games)
        or sha256_file(canonical) != report["canonical_manifest_sha256"]
    ):
        raise DataQualityError("Qualified news schedule/canonical source changed")
    weeks = {
        k: {**v, "captures": [{**r, "article_kind": "canonical"} for r in v["captures"]]}
        for k, v in load_news(canonical, games).items()
    }
    expected = _inventory(path, report["query_catalogs"], sorted(set(games["season"])))
    if [
        {k: r[k] for k in ("season", "week", "article", "folder")} for r in report["variants"]
    ] != expected:
        raise DataQualityError("Qualified tracking edition cohort changed")
    cutoffs = development_cutoffs(games)
    for item in report["variants"]:
        key = (item["season"], item["week"])
        if key not in weeks:
            raise DataQualityError("Tracking edition lies outside schedule cohort")
        source_path = (
            path.parent / str(key[0]) / f"week-{key[1]:02}" / item["folder"] / "manifest.json"
        )
        if sha256_file(source_path) != item["manifest_sha256"]:
            raise DataQualityError("Tracking source manifest checksum mismatch")
        source = _load_week(source_path, item["article"], cutoffs[key])
        if (source["season"], source["week"]) != key:
            raise DataQualityError("Tracking source season/week mismatch")
        base = tracking_canonical(item["article"], key[0])
        if not weeks[key]["article"]:
            weeks[key]["article"] = base
        for receipt in source["captures"]:
            clone = {**receipt, "article_kind": "tracking_variant", "expected_canonical": base}
            if "file" in clone:
                clone["file"] = item["folder"] + "/" + clone["file"]
            weeks[key]["captures"].append(clone)
    return weeks


def fetch_qualified(data_dir: Path, seasons: tuple[int, ...] = DEFAULT_NEWS_SEASONS) -> Path:
    canonical = fetch_news(data_dir, seasons)
    games, _ = news_schedule(data_dir, seasons)
    path = qualified_path(data_dir, games)
    if path.exists():
        load_qualified(path, games)
        return path
    catalogs = []
    for season in sorted(seasons, reverse=True):
        for number, prefix in enumerate(PREFIXES):
            folder = path.parent / str(season) / f"query-catalog-{number}"
            checkpoint = folder / "manifest.json"
            url = query_catalog_url(season, prefix)
            if checkpoint.exists():
                item = read_json(checkpoint)
                if (item["season"], item["prefix"], item["url"]) != (season, prefix, url):
                    raise DataQualityError("Tracking catalog checkpoint changed")
                _load_index(folder, item["index"], 100)
            else:
                raw, _ = _request(url, 100000)
                index_rows(raw, 100)
                item = {
                    "season": season,
                    "prefix": prefix,
                    "url": url,
                    "index": _save_index(folder, raw),
                    "retrieved_at_utc": datetime.now(UTC).isoformat(),
                }
                write_json(checkpoint, item)
            catalogs.append(item)
    catalogs.sort(key=lambda c: (c["season"], PREFIXES.index(c["prefix"])))
    inventory = _inventory(path, catalogs, sorted(seasons))
    cutoffs, lock = development_cutoffs(games), Lock()

    def collect(item: dict[str, Any]) -> dict[str, Any]:
        key = (item["season"], item["week"])
        try:
            folder = path.parent / str(key[0]) / f"week-{key[1]:02}" / item["folder"]
            saved = collect_article(folder, item["article"], key, cutoffs[key], path.parent, lock)
            return {**item, "manifest_sha256": sha256_file(saved)}
        except (OSError, ValueError) as error:
            return {**item, "error": str(error)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        variants = list(pool.map(collect, inventory))
    if failures := [r for r in variants if "error" in r]:
        raise DataQualityError(f"Incomplete tracking archive; resume saved URLs: {failures}")
    write_json(
        path,
        {
            "format_version": 1,
            "canonical_manifest_sha256": sha256_file(canonical),
            "query_catalogs": catalogs,
            "variants": variants,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    load_qualified(path, games)
    return path
