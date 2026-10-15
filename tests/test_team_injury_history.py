import base64
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nfl_prop_model.cli import main
from nfl_prop_model.data import team_injury_coverage as coverage
from nfl_prop_model.data import team_injury_history as history
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file

HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]
ARTICLE = "https://www.nfl.com/news/nfl-week-2-injury-report-player-statuses-2025-season"


def article(body, week=2, published="2025-09-10T17:00:00Z"):
    return (
        f"<h1>NFL Week {week} injury report</h1><script type='application/ld+json'>"
        + json.dumps(
            {"@type": "NewsArticle", "datePublished": published, "dateModified": published}
        )
        + "</script><div class='nfl-c-article__body'>"
        + body
        + "</div>"
    ).encode()


def index_row(body, stamp="20250912180000", original=ARTICLE):
    return dict(
        zip(
            HEADER,
            [
                "com,nfl)",
                stamp,
                original,
                "text/html",
                "200",
                base64.b32encode(hashlib.sha1(body).digest()).decode(),
                str(len(body)),
            ],
            strict=True,
        )
    )


def index(rows):
    return json.dumps([HEADER] + [[r[key] for key in HEADER] for r in rows]).encode()


def test_selected_editions_are_strict_and_future_versions_do_not_change_old_cutoffs():
    rows = [index_row(b"earlier"), index_row(b"later", "20250914180000")]
    cutoff = datetime(2025, 9, 14, 18, tzinfo=UTC)
    assert history.selected_rows(rows, [cutoff]) == [rows[0]]
    assert history.selected_rows(rows, [cutoff + timedelta(seconds=1)]) == [rows[1]]
    assert history.selected_rows(rows + [index_row(b"future", "20250920180000")], [cutoff]) == [
        rows[0]
    ]


def test_parser_handles_january_positions_and_explicit_missingness():
    first = datetime(2025, 12, 30, 16, tzinfo=UTC)
    captured = datetime(2026, 1, 2, 20, tzinfo=UTC)
    body = article(
        "<h3>BEARS</h3><li>OUT: LG Test Guard (ankle)</li>"
        "<h3>PACKERS</h3><p>No injury designations</p>",
        18,
        "2026-01-01T17:00:00Z",
    )
    result = history.parse_weekly_article(body, 18, captured, first)
    assert result == history.parse_weekly_article(gzip.compress(body), 18, captured, first)
    assert result["CHI"]["OL_out_count"] == 1 and result["GB"]["out_count"] == 0
    unknown = history.parse_weekly_article(
        article("<h3>BEARS</h3><li>OUT: Mystery Person (knee)</li>"),
        2,
        datetime(2025, 9, 12, 20, tzinfo=UTC),
        datetime(2025, 9, 9, 15, tzinfo=UTC),
    )
    assert unknown["CHI"]["out_count"] == 1 and unknown["CHI"]["OL_out_count"] is None


@pytest.mark.parametrize(
    "body",
    [
        "<h3>BEARS</h3><p>Report pending</p>",
        "<h3>BEARS</h3><li>OUT: QB One (knee) QB Two (back)</li>",
        "<h3>BEARS</h3><li>OUT: QB One (knee</li>",
    ],
)
def test_pending_or_malformed_player_separators_cannot_become_healthy_counts(body):
    result = history.parse_weekly_article(
        article(body),
        2,
        datetime(2025, 9, 12, 20, tzinfo=UTC),
        datetime(2025, 9, 9, 15, tzinfo=UTC),
    )
    assert result["CHI"]["status"] == "partial_or_pending"
    assert result["CHI"]["out_count"] is None


def test_parser_rejects_wrong_week_backdated_metadata_and_conflicting_empty_claims():
    first = datetime(2025, 9, 9, 15, tzinfo=UTC)
    captured = datetime(2025, 9, 12, 20, tzinfo=UTC)
    with pytest.raises(DataQualityError, match="expected injury week"):
        history.parse_weekly_article(article("<h3>BEARS</h3>"), 3, captured, first)
    with pytest.raises(DataQualityError, match="dates conflict"):
        history.parse_weekly_article(
            article("<h3>BEARS</h3>", published="2024-09-10T17:00:00Z"), 2, captured, first
        )
    with pytest.raises(DataQualityError, match="conflicts"):
        history.parse_weekly_article(
            article("<h3>BEARS</h3><p>No injury designations</p><li>OUT: QB One (knee)</li>"),
            2,
            captured,
            first,
        )


def test_index_bounds_and_ambiguous_capture_identity_fail():
    row = index_row(b"body")
    with pytest.raises(DataQualityError, match="bound"):
        history.index_rows(index([row]), 0)
    with pytest.raises(DataQualityError, match="Conflicting"):
        history.index_rows(index([row, index_row(b"other")]), 20)
    assert history.SLUG.fullmatch(ARTICLE)
    assert history.SLUG.fullmatch(ARTICLE + "?campaign=Twitter_atn") is None


def test_layout_headings_and_empty_separators_cannot_misattribute_an_injury():
    body = article(
        "<p>BEARS</p><li>OUT: LG Test Guard (ankle),, RB Runner (knee),</li>"
        "<h3></h3><li>OUT: QB Unattributed Person (foot)</li>"
        "<h3>WHERE: Stadium</h3><h3>PACKERS</h3><p>No injury designations</p>"
    )
    result = history.parse_weekly_article(
        body, 2, datetime(2025, 9, 12, 20, tzinfo=UTC), datetime(2025, 9, 9, 15, tzinfo=UTC)
    )
    assert result["CHI"]["out_count"] == 2 and result["CHI"]["QB_out_count"] == 0
    assert result["GB"]["out_count"] == 0


@pytest.fixture
def sources(tmp_path, monkeypatch):
    games = pl.DataFrame(
        [
            {
                "game_id": f"2025-2-{i}",
                "season": 2025,
                "week": 2,
                "home_team": "CHI",
                "away_team": "GB",
                "kickoff_utc": datetime(2025, 9, 14, 17 + i, tzinfo=UTC),
            }
            for i in range(2)
        ]
    )
    schedule = {"manifest_sha256": "schedule-hash"}
    monkeypatch.setattr(history, "load_schedule_only", lambda _: (games, schedule))
    monkeypatch.setattr(coverage, "load_schedule_only", lambda _: (games, schedule))
    body = article(
        "<h3>BEARS</h3><li>OUT: LG Test Guard (ankle)</li>"
        "<h3>PACKERS</h3><p>No injury designations</p>"
    )
    receipt = index_row(body)
    payload = index([receipt])
    requests = []

    def get(url, limit):
        requests.append(url)
        if "/cdx/" in url:
            return payload, url
        return body, url

    monkeypatch.setattr(history, "_get", get)
    return tmp_path, games, body, receipt, requests


def test_offline_cli_cache_guards_preserve_sources_and_exclude_outcomes(sources, monkeypatch):
    data, games, _, _, requests = sources
    assert main(["team-injury-fetch", "--data-dir", str(data)]) == 0
    raw = history.history_path(data, games)
    original = {p: sha256_file(p) for p in raw.parent.rglob("*") if p.is_file()}
    calls = len(requests)
    assert main(["team-injury-fetch", "--data-dir", str(data)]) == 0
    assert len(requests) == calls
    assert (
        main(["team-injury-audit", "--data-dir", str(data), "--report-dir", str(data / "report")])
        == 0
    )
    manifest = next((data / "processed" / "team_injury_coverage").glob("*/manifest.json"))
    report = coverage.load_coverage(manifest, data)
    assert report["records"] == 8 and report["2025_fits_performed"] == 0
    assert report["enriched_replay_ready"] is False
    assert original == {p: sha256_file(p) for p in raw.parent.rglob("*") if p.is_file()}
    with pytest.raises(DataQualityError, match="no outcomes"):
        coverage.coverage_records(
            games.with_columns(pl.lit(999).alias("home_score")), {}, raw.parent
        )
    with monkeypatch.context() as changed:
        changed.setattr(coverage, "coverage_source_hash", lambda: "changed")
        with pytest.raises(DataQualityError, match="code/policy"):
            coverage.load_coverage(manifest, data)
    original_report = manifest.read_bytes()
    changed_report = read_json(manifest)
    changed_report["missing_reasons"] = []
    manifest.write_text(json.dumps(changed_report), encoding="utf-8")
    with pytest.raises(DataQualityError, match="differs from source"):
        coverage.load_coverage(manifest, data)
    manifest.write_bytes(original_report)
    artifact = manifest.parent / report["artifact"]["file"]
    artifact.write_bytes(b"bad")
    with pytest.raises(ValueError, match="checksum"):
        coverage.load_coverage(manifest, data)


def test_latest_omitted_team_or_unverified_edition_never_inherits_older_report(sources):
    data, games, body, row, _ = sources
    path = history.fetch_history(data)
    weeks = history.load_history(path, games)
    old = weeks[2]["captures"][0]
    later = {**old, "timestamp": "20250913180000", "status": "unavailable", "archive_url": "later"}
    weeks[2]["captures"].append(later)
    records = coverage.coverage_records(games, weeks, path.parent)
    assert records.filter(pl.col("horizon") == "pregame_refresh")["out_count"].null_count() == 4
    exact = {(later["timestamp"], row["digest"], row["original"]): body}
    recovered = coverage.coverage_records(games, weeks, path.parent, exact)
    assert recovered.filter(pl.col("horizon") == "pregame_refresh")["out_count"].null_count() == 0
    with pytest.raises(DataQualityError, match="selected archive edition"):
        coverage.coverage_records(games, weeks, path.parent, {next(iter(exact)): b"wrong body"})

    omitted = article("<h3>PACKERS</h3><p>No injury designations</p>")
    changed = {
        **later,
        **index_row(omitted, later["timestamp"]),
        "status": "verified",
        "file": "omitted.html.gz",
    }
    (path.parent / "week-02" / changed["file"]).write_bytes(gzip.compress(omitted))
    weeks[2]["captures"][-1] = changed
    missing = coverage.coverage_records(games, weeks, path.parent).filter(
        (pl.col("horizon") == "pregame_refresh") & (pl.col("team") == "CHI")
    )
    assert set(missing["reason"]) == {"team_section_missing"}
    assert missing["out_count"].null_count() == missing.height


@pytest.mark.parametrize("target", ["entity", "week_manifest"])
def test_corrupt_raw_entity_and_week_manifest_are_rejected(sources, target):
    data, games, _, _, _ = sources
    path = history.fetch_history(data)
    week_path = path.parent / "week-02" / "manifest.json"
    receipt = read_json(week_path)["captures"][0]
    entity = week_path.parent / receipt["file"]
    (entity if target == "entity" else week_path).write_bytes(b"broken")
    with pytest.raises(DataQualityError, match="checksum"):
        history.load_history(path, games)


@pytest.mark.parametrize("defect", ["redirect", "digest"])
def test_new_collection_refuses_changed_capture_and_never_infers_zero_out(
    sources, monkeypatch, defect
):
    data, games, body, row, _ = sources
    payload = index([row])

    def get(url, limit):
        if "/cdx/" in url:
            return payload, url
        return (body, ARTICLE) if defect == "redirect" else (body + b"changed", url)

    monkeypatch.setattr(history, "_get", get)
    path = history.fetch_history(data)
    weeks = history.load_history(path, games)
    assert weeks[2]["captures"][0]["status"] == "unavailable"
    records = coverage.coverage_records(games, weeks, path.parent)
    refresh = records.filter(pl.col("horizon") == "pregame_refresh")
    assert set(refresh["reason"]) == {"unverified_edition"}
    assert refresh["out_count"].null_count() == refresh.height


def test_body_storage_budget_prevents_large_writes_and_keeps_status_unknown(sources, monkeypatch):
    data, games, _, _, _ = sources
    monkeypatch.setattr(history.gzip, "compress", lambda *args, **kwargs: b"x" * 8000001)
    path = history.fetch_history(data)
    weeks = history.load_history(path, games)
    receipt = weeks[2]["captures"][0]
    assert receipt["status"] == "unavailable" and "budget" in receipt["reason"]
    assert not list(path.parent.rglob("*.html.gz"))
