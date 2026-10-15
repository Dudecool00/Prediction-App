import json
import urllib.error
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import polars as pl
import pytest

from nfl_prop_model.data import team_depth_catalog as catalog
from nfl_prop_model.data.schemas import DataQualityError


def directory_html():
    return "".join(
        f'<a href="{("https://www.nfl.com" if i % 2 else "")}/teams/'
        f'city-{name.lower()}{"/" if i % 3 else ""}">Profile</a>'
        f'<a href="https://www.{team.lower()}.example.com/">View Full Site</a>'
        for i, (name, team) in enumerate(catalog.NICKNAMES.items())
    ).encode()


def test_directory_associates_absolute_relative_and_slashless_profiles():
    sites = catalog.directory_sites(directory_html())
    assert len(sites) == 32
    assert sites["DEN"] == "https://www.den.example.com"
    assert sites["HOU"] == "https://www.hou.example.com"
    assert sites["IND"] == "https://www.ind.example.com"


@pytest.mark.parametrize("change", ["missing", "duplicate", "query"])
def test_directory_missing_or_ambiguous_domains_fail(change):
    body = directory_html()
    if change == "missing":
        body = body.replace(b"View Full Site", b"no site", 1)
    elif change == "duplicate":
        body += body
    else:
        body = body.replace(b"example.com/", b"example.com/?observed=true", 1)
    with pytest.raises(DataQualityError):
        catalog.directory_sites(body)


def row(stamp="20240906182949", url="https://www.vikings.com/team/depth-chart"):
    return {
        "timestamp": stamp,
        "original": url,
        "digest": "A" * 32,
        "mimetype": "text/html",
        "statuscode": "200",
        "length": "1000",
    }


def payload(rows):
    fields = list(row())
    return json.dumps([fields] + [[r[f] for f in fields] for r in rows]).encode()


def test_catalog_query_is_bounded_metadata_only_and_date_scoped():
    query = parse_qs(urlparse(catalog.catalog_url("https://www.vikings.com", 2024)).query)
    assert query["from"] == ["20240815"] and query["to"] == ["20250116"]
    assert query["collapse"] == ["timestamp:8"]
    assert query["limit"] == ["651"]
    assert "statuscode:200" in query["filter"]
    assert catalog.catalog_rows(payload([row()]), "https://www.vikings.com", 2024) == [row()]


@pytest.mark.parametrize("change", ["domain", "path", "query", "year", "digest"])
def test_catalog_wrong_source_or_dates_rejected(change):
    r = row()
    if change == "year":
        r["timestamp"] = "20230906182949"
    elif change == "digest":
        r["digest"] = "wrong"
    else:
        r["original"] = {
            "domain": "https://evil.example.com/team/depth-chart",
            "path": "https://www.vikings.com/team/roster",
            "query": "https://www.vikings.com/team/depth-chart?season=2024",
        }[change]
    with pytest.raises(DataQualityError):
        catalog.catalog_rows(payload([r]), "https://www.vikings.com", 2024)


def test_cutoff_equality_staleness_and_catalog_failures_remain_unverified():
    kickoff = datetime(2024, 9, 8, 17, tzinfo=UTC)
    games = pl.DataFrame(
        [
            {
                "game_id": "2024_01_MIN_NYG",
                "season": 2024,
                "week": 1,
                "home_team": "NYG",
                "away_team": "MIN",
                "kickoff_utc": kickoff,
            }
        ]
    )
    fresh = row()
    sources = {(2024, "MIN"): ("indexed", [fresh]), (2024, "NYG"): ("unavailable", [])}
    result = catalog.catalog_candidates(games, sources)
    assert result.height == 4 and result["candidate_found"].sum() == 1
    assert result["model_eligible"].sum() == 0
    cutoff = kickoff - timedelta(hours=1)
    for stamp in (cutoff, cutoff - timedelta(hours=72, seconds=1)):
        sources[(2024, "MIN")] = ("indexed", [row(stamp.strftime("%Y%m%d%H%M%S"))])
        assert catalog.catalog_candidates(games, sources)["candidate_found"].sum() == 0
    for status in ("empty", "truncated", "unavailable"):
        sources[(2024, "MIN")] = (status, [fresh])
        assert catalog.catalog_candidates(games, sources)["candidate_found"].sum() == 0
    with pytest.raises(DataQualityError, match="metadata"):
        catalog.catalog_candidates(games.with_columns(pl.lit(99).alias("home_score")), sources)


def test_catalog_receipts_resume_offline_and_reject_corruption(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(catalog, "_download", lambda url, limit: calls.append(url) or b"small")
    url = "https://example.test/cdx"
    assert catalog._receipt(tmp_path, url, 100, offline=False) == b"small"
    assert catalog._receipt(tmp_path, url, 100, offline=True) == b"small"
    assert calls == [url]
    next(tmp_path.glob("*.gz")).write_bytes(b"corrupt")
    with pytest.raises(DataQualityError, match="checksum"):
        catalog._receipt(tmp_path, url, 100, offline=True)


def test_catalog_failures_do_not_become_empty_and_budget_enforced(tmp_path, monkeypatch):
    def fail(*args):
        raise urllib.error.URLError("transport unavailable")

    monkeypatch.setattr(catalog, "_download", fail)
    url = "https://example.test/failure"
    assert catalog._receipt(tmp_path, url, 100, offline=False) is None
    monkeypatch.setattr(
        catalog, "_download", lambda *args: pytest.fail("Unavailable receipt unexpectedly retried")
    )
    assert catalog._receipt(tmp_path, url, 100, offline=True) is None
    monkeypatch.setattr(catalog, "_download", lambda *args: b"recovered")
    assert catalog._receipt(tmp_path, url, 100, offline=False, retry_failed=True) == b"recovered"
    monkeypatch.setattr(catalog, "_download", lambda *args: b"small")
    monkeypatch.setattr(catalog, "MAX_RAW_BYTES", 1)
    with pytest.raises(DataQualityError, match="budget"):
        catalog._receipt(tmp_path, "https://example.test/new", 100, offline=False)
