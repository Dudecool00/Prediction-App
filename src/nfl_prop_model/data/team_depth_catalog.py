"""Bounded official depth-chart CDX inventory; every candidate remains unverified."""

import gzip
import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_games import team_source_hash
from nfl_prop_model.data.team_injury_archive import NICKNAMES
from nfl_prop_model.data.team_injury_development import (
    development_cutoffs,
    load_development_schedule,
)
from nfl_prop_model.data.team_injury_history import capture_time, cdx_url, index_rows
from nfl_prop_model.features.team_epa import HORIZONS, weekly_cutoffs

DIRECTORY = "https://www.nfl.com/teams/"
MAX_ROWS = 650
MAX_RAW_BYTES = 4_000_000
_LOCK = Lock()
POLICY = {
    "scope": "2022-2024 official depth-chart catalog sample only, no body verification or fits",
    "domains": "current NFL directory, retrospective domain assumption; pin all 32 clubs",
    "routes": "/team/depth-chart or /team/depthchart, optional slash; no query variants",
    "window": "August 15 through next January 16 for each NFL season",
    "sampling": "CDX HTTP200 HTML, collapse timestamp:8; adjacent URL groups, first capture/day",
    "candidate": "latest sampled capture strictly before cutoff, age <=72h; never model eligible",
    "cutoffs": "Tuesday 10 Chicago and kickoff minus 1h; metadata-only canonical 815 games",
    "bounds": "96 indexes, two workers, 650 rows and 200kB/index; 500kB directory; 4MB gzip total",
    "failures": "one retry; unavailable/empty/truncated supply no candidate; resume receipts",
    "limitations": "may miss domains, routes and revisions; no complete roster or starter proof",
}


def source_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes() + team_source_hash().encode()).hexdigest()


class _Directory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.team: str | None = None
        self.href: str | None = None
        self.text: list[str] = []
        self.sites: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self.href = dict(attrs).get("href") or ""
        self.text = []
        parsed = urllib.parse.urlparse(self.href)
        parts = parsed.path.strip("/").split("/")
        if (
            (not parsed.hostname or parsed.hostname in {"www.nfl.com", "nfl.com"})
            and len(parts) == 2
            and parts[0] == "teams"
        ):
            self.team = NICKNAMES.get(parts[1].split("-")[-1].upper())

    def handle_data(self, value: str) -> None:
        if self.href is not None:
            self.text.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if "View Full Site" in " ".join(self.text):
            parsed = urllib.parse.urlparse(self.href or "")
            if (
                self.team is None
                or parsed.scheme != "https"
                or not parsed.hostname
                or not parsed.hostname.endswith(".com")
                or parsed.username
                or parsed.port
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or self.team in self.sites
            ):
                raise DataQualityError("Official directory team/domain association is ambiguous")
            self.sites[self.team] = f"https://{parsed.hostname}"
            self.team = None
        self.href = None
        self.text = []


def directory_sites(payload: bytes) -> dict[str, str]:
    parser = _Directory()
    parser.feed(payload.decode("utf-8"))
    if set(parser.sites) != set(NICKNAMES.values()) or len(set(parser.sites.values())) != 32:
        raise DataQualityError("Official directory must uniquely identify all 32 clubs")
    return parser.sites


def catalog_url(site: str, season: int) -> str:
    host = re.escape(str(urllib.parse.urlparse(site).hostname).removeprefix("www."))
    return cdx_url(
        [
            ("url", site + "/team/depth"),
            ("matchType", "prefix"),
            ("from", f"{season}0815"),
            ("to", f"{season + 1}0116"),
            ("filter", "mimetype:text/html"),
            ("filter", rf"original:https?://(?:www[.])?{host}/team/depth-?chart/?$"),
            ("collapse", "timestamp:8"),
            ("fl", "timestamp,original,mimetype,statuscode,digest,length"),
            ("limit", str(MAX_ROWS + 1)),
        ]
    )


def catalog_rows(payload: bytes, site: str, season: int) -> list[dict[str, str]]:
    rows = index_rows(payload, MAX_ROWS + 1)
    host = str(urllib.parse.urlparse(site).hostname).removeprefix("www.")
    for row in rows:
        parsed = urllib.parse.urlparse(row["original"])
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.port
            or str(parsed.hostname).removeprefix("www.") != host
            or parsed.path.rstrip("/") not in {"/team/depth-chart", "/team/depthchart"}
            or parsed.query
            or parsed.fragment
            or not f"{season}0815" <= row["timestamp"][:8] <= f"{season + 1}0116"
        ):
            raise DataQualityError("Depth catalog URL/date window mismatch")
        capture_time(row)  # validate calendar date, not just its digit count
    return rows


def _download(url: str, limit: int) -> bytes:
    for attempt in range(2):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Prediction-App-research", "Accept-Encoding": "identity"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.url != url:
                    raise DataQualityError("Depth catalog redirect refused")
                payload: bytes = response.read(limit + 1)
            if len(payload) > limit:
                raise DataQualityError("Depth catalog response exceeds bound")
            return payload
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt:
                raise
    raise AssertionError("unreachable")


def _receipt(
    directory: Path, url: str, limit: int, *, offline: bool, retry_failed: bool = False
) -> bytes | None:
    path = directory / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    previous = []
    if path.exists():
        receipt = read_json(path)
        if receipt["url"] != url:
            raise DataQualityError("Depth catalog receipt URL changed")
        if receipt["status"] not in {"received", "unavailable"}:
            raise DataQualityError("Unknown depth catalog receipt status")
        if receipt["status"] == "unavailable" and (offline or not retry_failed):
            return None
        if receipt["status"] == "received":
            body = directory / receipt["file"]
            if body.name != receipt["file"] or sha256_file(body) != receipt["gzip_sha256"]:
                raise DataQualityError("Depth catalog raw checksum mismatch")
            with gzip.open(body, "rb") as handle:
                payload: bytes = handle.read(limit + 1)
            if len(payload) > limit or hashlib.sha256(payload).hexdigest() != receipt["sha256"]:
                raise DataQualityError("Depth catalog decoded checksum/size mismatch")
            return payload
        previous = receipt.pop("previous_attempts", []) + [receipt]
    if offline:
        raise DataQualityError("Depth catalog receipt missing; run fetch first")
    directory.mkdir(parents=True, exist_ok=True)
    receipt = {"url": url, "received_at_utc": datetime.now(UTC).isoformat()}
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
    with _LOCK:
        if sum(p.stat().st_size for p in directory.glob("*.gz")) + len(compressed) > MAX_RAW_BYTES:
            raise DataQualityError("Depth catalog raw budget exceeded")
        if not body.exists():
            body.write_bytes(compressed)
    receipt.update(
        status="received",
        file=body.name,
        gzip_sha256=sha256_file(body),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    write_json(path, receipt)
    return payload


def _catalogs(
    data_dir: Path, *, offline: bool, retry_failed: bool = False
) -> tuple[pl.DataFrame, dict[tuple[int, str], tuple[str, list[dict[str, str]]]], dict[str, Any]]:
    games, schedule = load_development_schedule(data_dir)
    development_cutoffs(games)
    directory = data_dir / "raw" / "team_depth_catalog"
    manifest = directory / "manifest.json"
    identity = {
        "format_version": 1,
        "source_sha256": source_hash(),
        "policy": POLICY,
        "schedule": schedule,
    }
    if manifest.exists() and read_json(manifest) != identity:
        raise DataQualityError("Depth catalog source/policy/schedule changed")
    if offline and not manifest.exists():
        raise DataQualityError("Depth catalog collection missing")
    if not offline:
        write_json(manifest, identity)
    body = _receipt(directory, DIRECTORY, 500_000, offline=offline, retry_failed=retry_failed)
    if body is None:
        raise DataQualityError("Official team directory unavailable")
    sites = directory_sites(body)
    keys = [(season, team) for season in (2022, 2023, 2024) for team in sorted(sites)]

    def read_one(key: tuple[int, str]) -> tuple[tuple[int, str], tuple[str, list[dict[str, str]]]]:
        season, team = key
        payload = _receipt(
            directory,
            catalog_url(sites[team], season),
            200_000,
            offline=offline,
            retry_failed=retry_failed,
        )
        rows = [] if payload is None else catalog_rows(payload, sites[team], season)
        status = (
            "unavailable"
            if payload is None
            else "truncated"
            if len(rows) > MAX_ROWS
            else "empty"
            if not rows
            else "indexed"
        )
        return key, (status, rows)

    result = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i, (key, value) in enumerate(pool.map(read_one, keys), 1):
            result[key] = value
            if not offline and (i % 8 == 0 or i == len(keys)):
                print(f"Depth catalog {i}/{len(keys)}", flush=True)
    identity["directory_sha256"] = hashlib.sha256(body).hexdigest()
    identity["raw_bytes"] = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
    identity["catalogs"] = [
        {
            "season": s,
            "team": t,
            "site": sites[t],
            "status": result[(s, t)][0],
            "indexed_rows": len(result[(s, t)][1]),
        }
        for s, t in keys
    ]
    return games, result, identity


def catalog_candidates(
    games: pl.DataFrame, catalogs: dict[tuple[int, str], tuple[str, list[dict[str, str]]]]
) -> pl.DataFrame:
    development_cutoffs(games)
    weekly = weekly_cutoffs(games)
    records = []
    for game in games.to_dicts():
        for horizon in HORIZONS:
            cutoff = (
                weekly[(game["season"], game["week"])]
                if horizon == "early_week"
                else game["kickoff_utc"] - timedelta(hours=1)
            )
            for side in ("home", "away"):
                team = game[side + "_team"]
                status, rows = catalogs[(game["season"], team)]
                eligible = (
                    [
                        r
                        for r in rows
                        if capture_time(r) < cutoff <= capture_time(r) + timedelta(hours=72)
                    ]
                    if status == "indexed"
                    else []
                )
                latest = (
                    max(eligible, key=lambda r: (r["timestamp"], r["original"]))
                    if eligible
                    else None
                )
                records.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season"],
                        "week": game["week"],
                        "team": team,
                        "side": side,
                        "horizon": horizon,
                        "cutoff_utc": cutoff,
                        "catalog_status": status,
                        "candidate_found": latest is not None,
                        "captured_at_utc": capture_time(latest) if latest else None,
                        "original_url": latest["original"] if latest else None,
                        "cdx_digest": latest["digest"] if latest else None,
                        "model_eligible": False,
                    }
                )
    return pl.DataFrame(records, infer_schema_length=None).with_columns(
        pl.col("captured_at_utc").cast(pl.Datetime("us", "UTC"))
    )


def fetch_depth_catalog(data_dir: Path, *, retry_failed: bool = False) -> dict[str, Any]:
    return _catalogs(data_dir, offline=False, retry_failed=retry_failed)[2]


def audit_depth_catalog(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    games, catalogs, source = _catalogs(data_dir, offline=True)
    frame = catalog_candidates(games, catalogs)
    directory = data_dir / "processed" / "team_depth_catalog"
    report = {
        **source,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "records": frame.height,
        "games": games.height,
        "model_fits": 0,
        "verified_snapshots": 0,
        "model_eligible_records": 0,
        "coverage": frame.group_by("season", "horizon")
        .agg(pl.len().alias("team_slots"), pl.col("candidate_found").sum())
        .sort("season", "horizon")
        .to_dicts(),
        "artifact": store_frame(directory, "candidates", frame),
    }
    write_json(directory / "manifest.json", report)
    write_json(report_dir / "results.json", report)
    pl.DataFrame(report["coverage"]).write_csv(report_dir / "coverage.csv")
    pl.DataFrame(report["catalogs"]).write_csv(report_dir / "catalogs.csv")
    (report_dir / "findings.md").write_text(
        "# Official depth-chart source inventory\n\n"
        f"{games.height} games, {frame.height} team/horizon slots; "
        f"{int(frame['candidate_found'].sum())} indexed candidates within 72h. "
        "**Zero verified chart entities, model-eligible slots or model fits.**\n\n"
        "This is a bounded, daily-collapsed index sample, not latest-version proof or complete "
        "historical coverage. Current official directory domains are a retrospective assumption; "
        "other domains/routes and within-day revisions may be missing. Empty, unavailable and "
        "truncated catalogs remain explicit. Never treat index availability as a readable chart, "
        "confirmed starter, identity mapping or active/IR roster.\n\n"
        f"Raw catalog data: {report['raw_bytes']:,} bytes. "
        "Next, inventory exact URL editions and verify a bounded set of selected entities before "
        "building any chart feature adapter. All original enriched-replay gates remain in force.\n",
        encoding="utf-8",
    )
    return report
