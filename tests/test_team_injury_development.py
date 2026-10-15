import base64
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nfl_prop_model.cli import main
from nfl_prop_model.data import team_injury_development as source
from nfl_prop_model.data import team_injury_development_audit as audit
from nfl_prop_model.data import team_injury_history as history
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, write_json
from nfl_prop_model.data.team_depth import SCHEDULE_FIELDS
from nfl_prop_model.data.team_injury_tables import HEADERS, parse_tables

HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]


def table(rows):
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{c}</th>" for c in HEADERS)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
        + "</tbody></table>"
    )


def player(
    slug="test-player", position="LG", status="Out", practice="Full Participation in Practice"
):
    return [f'<a href="/players/{slug}/">{slug}</a>', position, "Ankle", practice, status]


def document(season=2023, week=2, left=None, right=None):
    title = f"Official NFL Injury Report for Players - Week {week} of the {season} Season | NFL.com"
    return (
        f"<title>{title}</title><h1>{title}</h1><h1>{season} NFL Injury Report</h1>"
        f"<h2>Injuries - WEEK {week}</h2><section class='nfl-o-injury-report__unit'>"
        "<span class='nfl-c-matchup-strip__team-abbreviation'>CHI</span>"
        "<div class='nfl-c-matchup-strip__record'>(0-999)</div>"
        "<span class='nfl-c-matchup-strip__team-abbreviation'>GB</span>"
        "<div class='d3-o-section-sub-title'><span>Bears</span></div>"
        + table([player()] if left is None else left)
        + "<div class='d3-o-section-sub-title'><span>Packers</span></div>"
        + table([player("another-player", status="")] if right is None else right)
        + "</section>"
    ).encode()


def row(body, season=2023, stamp=None):
    return dict(
        zip(
            HEADER,
            [
                "com,nfl)",
                stamp or f"{season}0914180000",
                f"https://www.nfl.com/injuries/league/{season}/REG2",
                "text/html",
                "200",
                base64.b32encode(hashlib.sha1(body).digest()).decode(),
                str(len(body)),
            ],
            strict=True,
        )
    )


def index(rows):
    return json.dumps([HEADER] + [[r[k] for k in HEADER] for r in rows]).encode()


def test_table_columns_keep_practice_separate_and_never_infer_healthy_or_finality():
    body = document(
        left=[player(), player("blank-person", "QB", "", "Did Not Participate In Practice")]
    )
    result = parse_tables(body, 2023, 2)
    assert result == parse_tables(gzip.compress(body), 2023, 2)
    assert result["CHI"]["OL_observed_out_count"] == 1
    assert result["CHI"]["QB_observed_out_count"] == 0
    assert result["CHI"]["blank_game_status_rows"] == 1
    assert result["GB"]["status"] == "practice_only_or_pending"
    assert result["GB"]["observed_out_count"] is None
    assert not any(r["final_report_verified"] for r in result.values())
    questions = parse_tables(document(right=[player(status="Questionable")]), 2023, 2)
    assert questions["GB"]["observed_out_count"] == 0
    assert questions["GB"]["status"] == "designations_present"
    assert questions["GB"]["final_report_verified"] is False
    # Team records elsewhere in the captured page cannot affect derived injury facts.
    assert parse_tables(body.replace(b"(0-999)", b"(20-0)"), 2023, 2) == result


@pytest.mark.parametrize("defect", ["season", "week", "header", "label", "order", "duplicate"])
def test_wrong_body_ambiguous_attribution_or_duplicate_identity_fails(defect):
    body = document()
    if defect == "season":
        body = document(2024)
    elif defect == "week":
        body = document(week=3)
    elif defect == "header":
        body = body.replace(b"Game Status", b"Practice Estimate")
    elif defect == "label":
        body = body.replace(b"<span>Bears</span>", b"<span>Vikings</span>")
    elif defect == "order":
        label = b"<div class='d3-o-section-sub-title'><span>Packers</span></div>"
        body = body.replace(label, b"").replace(b"<table>", label + b"<table>", 1)
    else:
        body = document(left=[player(), player()])
    with pytest.raises(DataQualityError):
        parse_tables(body, 2023, 2)


def test_january_week18_unknown_out_position_and_unsupported_or_empty_rows():
    body = document(2023, 18, left=[player(position="MYSTERY")], right=[])
    result = parse_tables(body, 2023, 18)
    assert result["CHI"]["observed_out_count"] == 1
    assert result["CHI"]["unknown_out_positions"] == 1
    assert result["CHI"]["OL_observed_out_count"] is None
    assert result["GB"]["observed_out_count"] is None
    unknown = parse_tables(
        document(left=[player(), player("other", status="Unavailable")]), 2023, 2
    )
    assert unknown["CHI"]["unsupported_rows"] is True
    assert unknown["CHI"]["observed_out_count"] is None


def test_missing_table_and_unlinked_document_name_cannot_rebind_another_teams_players():
    body = document(left=[player(status="")])
    first_table = table([player(status="")]).encode()
    result = parse_tables(body.replace(first_table, b"", 1), 2023, 2)
    assert result["CHI"]["status"] == "practice_only_or_pending"
    assert result["CHI"]["player_rows"] is None
    assert result["CHI"]["observed_out_count"] is None
    assert result["GB"]["player_rows"] == 1
    unlinked = player()
    unlinked[0] = "Document Player"
    result = parse_tables(document(left=[unlinked]), 2023, 2)
    assert result["CHI"]["observed_out_count"] == 1
    assert result["CHI"]["OL_observed_out_count"] == 1
    with pytest.raises(DataQualityError, match="duplicate"):
        parse_tables(document(left=[unlinked, unlinked]), 2023, 2)


@pytest.fixture
def sources(tmp_path, monkeypatch):
    games = pl.DataFrame(
        [
            {
                "game_id": f"{s}-2-CHI-GB",
                "season": s,
                "week": 2,
                "home_team": "CHI",
                "away_team": "GB",
                "kickoff_utc": datetime(s, 9, d, 17, tzinfo=UTC),
            }
            for s, d in ((2022, 18), (2023, 17), (2024, 15))
        ]
    )
    schedule = {"manifest_sha256": "schedule-sha", "artifact_sha256": "artifact-sha"}
    monkeypatch.setattr(source, "load_development_schedule", lambda _: (games, schedule))
    monkeypatch.setattr(audit, "load_development_schedule", lambda _: (games, schedule))
    bodies = {s: document(s) for s in source.SEASONS}
    rows = {s: row(bodies[s], s) for s in source.SEASONS}
    requests = []

    def request(url, limit):
        requests.append(url)
        season = next(s for s in source.SEASONS if str(s) in url)
        if "/cdx/" in url:
            return index([rows[season]]), url
        return bodies[season], url

    monkeypatch.setattr(source, "_request", request)
    return tmp_path, games, bodies, rows, requests


def test_cli_resumable_cache_and_offline_audit_preserve_cohort_and_sources(sources, monkeypatch):
    data, games, _, _, requests = sources
    assert main(["team-injury-development-fetch", "--data-dir", str(data)]) == 0
    raw = source.development_path(data, games)
    original = {p: sha256_file(p) for p in raw.parent.rglob("*") if p.is_file()}
    calls = len(requests)
    assert main(["team-injury-development-fetch", "--data-dir", str(data)]) == 0
    assert len(requests) == calls
    assert (
        main(
            [
                "team-injury-development-audit",
                "--data-dir",
                str(data),
                "--report-dir",
                str(data / "report"),
            ]
        )
        == 0
    )
    manifest = next((data / "processed" / "team_injury_development").glob("*/manifest.json"))
    report = audit.load_development_audit(manifest, data)
    assert report["games"] == 3 and report["records"] == 12
    assert report["model_fits_performed"] == 0
    assert report["matched_context_comparison_ready"] is False
    assert report["enriched_replay_ready"] is False
    assert original == {p: sha256_file(p) for p in raw.parent.rglob("*") if p.is_file()}
    with pytest.raises(DataQualityError, match="no outcomes"):
        audit.development_records(
            games.with_columns(pl.lit(42).alias("home_score")), {}, raw.parent
        )
    with monkeypatch.context() as changed:
        changed.setattr(audit, "development_source_hash", lambda: "changed")
        with pytest.raises(DataQualityError, match="code/policy"):
            audit.load_development_audit(manifest, data)
    changed_report = read_json(manifest)
    changed_report["model_fits_performed"] = 1
    write_json(manifest, changed_report)
    with pytest.raises(DataQualityError, match="differs from preserved"):
        audit.load_development_audit(manifest, data)


def test_latest_removed_or_failed_section_never_inherits_earlier_counts(sources):
    data, games, bodies, rows, _ = sources
    path = source.fetch_development(data)
    weeks = source.load_development(path, games)
    old = weeks[(2023, 2)]["captures"][0]
    later = {**old, "timestamp": "20230916180000", "status": "unavailable"}
    weeks[(2023, 2)]["captures"].append(later)
    frame = audit.development_records(games, weeks, path.parent)
    fresh = frame.filter((pl.col("season") == 2023) & (pl.col("horizon") == "pregame_refresh"))
    assert set(fresh["reason"]) == {"unverified_edition"}
    assert fresh["observed_out_count"].null_count() == 2
    # The latest complete edition can explicitly remove designations without implying healthy.
    body = document(left=[player(status="")], right=[player("other", status="")])
    receipt = {
        **later,
        **row(body, stamp=later["timestamp"]),
        "status": "verified",
        "file": "removed.html.gz",
    }
    (path.parent / "2023/week-02" / receipt["file"]).write_bytes(gzip.compress(body))
    weeks[(2023, 2)]["captures"][-1] = receipt
    fresh = audit.development_records(games, weeks, path.parent).filter(
        (pl.col("season") == 2023) & (pl.col("horizon") == "pregame_refresh")
    )
    assert set(fresh["status"]) == {"practice_only_or_pending"}
    assert fresh["observed_out_count"].null_count() == 2
    assert not fresh["model_eligible"].any()


def test_strict_future_invariance_and_january_cutoffs(sources):
    _, games, _, rows, _ = sources
    r = rows[2023]
    cutoff = history.capture_time(r)
    assert history.selected_rows([r], [cutoff]) == []
    assert history.selected_rows([r], [cutoff + timedelta(seconds=1)]) == [r]
    future = {**r, "timestamp": "20230918190000"}
    assert history.selected_rows([r, future], [datetime(2023, 9, 17, 16, tzinfo=UTC)]) == [r]
    january = games.filter(pl.col("season") == 2023).with_columns(
        pl.lit(18).alias("week"), pl.lit(datetime(2024, 1, 7, 18, tzinfo=UTC)).alias("kickoff_utc")
    )
    times = source.development_cutoffs(january)[(2023, 18)]
    assert times[0] == datetime(2024, 1, 2, 16, tzinfo=UTC)
    assert times[-1] == datetime(2024, 1, 7, 17, tzinfo=UTC)


def test_failed_index_resumes_completed_weeks_without_claiming_absent_reports(sources, monkeypatch):
    data, games, _, _, requests = sources
    original = source._request

    def fail_one_index(url, limit):
        if "/cdx/" in url and "REG2" in url and "2023" in url:
            raise OSError("temporary archive outage")
        return original(url, limit)

    monkeypatch.setattr(source, "_request", fail_one_index)
    with pytest.raises(DataQualityError, match="Incomplete development archive"):
        source.fetch_development(data)
    path = source.development_path(data, games)
    assert not path.exists()
    assert (path.parent / "2022/week-02/manifest.json").exists()
    assert (path.parent / "2024/week-02/manifest.json").exists()
    calls = len(requests)
    monkeypatch.setattr(source, "_request", original)
    source.fetch_development(data)
    assert all("2023" in url for url in requests[calls:])
    assert set(source.load_development(path, games)) == {(s, 2) for s in source.SEASONS}


def test_absent_catalog_week_is_reported_without_fetching_later_or_live_reports(
    sources, monkeypatch
):
    data, games, _, _, requests = sources
    original = source._request

    def missing_catalog(url, limit):
        if "/cdx/" in url and "2024" in url and "matchType" in url:
            return b"[]", url
        return original(url, limit)

    monkeypatch.setattr(source, "_request", missing_catalog)
    path = source.fetch_development(data)
    weeks = source.load_development(path, games)
    assert weeks[(2024, 2)]["article"] is None
    frame = audit.development_records(games, weeks, path.parent).filter(pl.col("season") == 2024)
    assert set(frame["reason"]) == {"week_url_not_in_catalog"}
    assert frame["observed_out_count"].null_count() == 4
    assert not any("2024" in url and ("REG2" in url or "/web/" in url) for url in requests)


def test_naive_or_invalid_schedule_metadata_is_rejected(sources):
    _, games, _, _, _ = sources
    naive = games.with_columns(pl.col("kickoff_utc").dt.replace_time_zone(None))
    with pytest.raises(DataQualityError, match="metadata is invalid"):
        source.development_cutoffs(naive)
    with pytest.raises(DataQualityError, match="metadata is invalid"):
        source.development_cutoffs(games.with_columns(pl.lit(19).alias("week")))


@pytest.mark.parametrize("defect", ["redirect", "digest", "budget"])
def test_integrity_and_storage_failures_stay_unknown(sources, monkeypatch, defect):
    data, games, bodies, rows, _ = sources
    original = source._request

    def request(url, limit):
        body, resolved = original(url, limit)
        if "/cdx/" not in url:
            if defect == "redirect":
                resolved += "?redirected=1"
            elif defect == "digest":
                body += b"changed"
        return body, resolved

    monkeypatch.setattr(source, "_request", request)
    if defect == "budget":
        monkeypatch.setattr(source.gzip, "compress", lambda *args, **kwargs: b"x" * 8000001)
    path = source.fetch_development(data)
    weeks = source.load_development(path, games)
    assert all(r["status"] == "unavailable" for w in weeks.values() for r in w["captures"])
    frame = audit.development_records(games, weeks, path.parent)
    assert frame["observed_out_count"].null_count() == frame.height


@pytest.mark.parametrize("target", ["entity", "index", "week_manifest"])
def test_raw_corruption_fails(sources, target):
    data, games, _, _, _ = sources
    path = source.fetch_development(data)
    week_path = path.parent / "2023/week-02/manifest.json"
    week = read_json(week_path)
    file = (
        week_path
        if target == "week_manifest"
        else week_path.parent
        / (week["index"]["file"] if target == "index" else week["captures"][0]["file"])
    )
    file.write_bytes(b"bad")
    with pytest.raises(DataQualityError, match="checksum"):
        source.load_development(path, games)


def test_missing_catalog_stale_edition_and_matchup_disagreement_remain_unknown(sources):
    data, games, _, _, _ = sources
    path = source.fetch_development(data)
    weeks = source.load_development(path, games)
    weeks[(2022, 2)] = {"article": None, "captures": []}
    # A recent capture with practice-only rows is not enough to verify a final report.
    weeks[(2023, 2)]["captures"][0]["timestamp"] = "20230910180000"
    changed = games.with_columns(
        pl.when(pl.col("season") == 2024)
        .then(pl.lit("MIN"))
        .otherwise(pl.col("away_team"))
        .alias("away_team")
    )
    frame = audit.development_records(changed, weeks, path.parent)
    refresh = frame.filter(pl.col("horizon") == "pregame_refresh")
    assert (
        refresh.filter(pl.col("season") == 2022)["reason"].to_list()
        == ["week_url_not_in_catalog"] * 2
    )
    assert refresh.filter(pl.col("season") == 2023)["reason"].to_list() == ["stale_edition"] * 2
    assert set(refresh.filter(pl.col("season") == 2024)["reason"]) == {
        "team_section_missing",
        "schedule_matchup_mismatch",
    }
    assert refresh["observed_out_count"].null_count() == 6


def test_development_schedule_projects_only_six_metadata_fields(tmp_path, monkeypatch):
    data = [
        {
            "game_id": f"{s}-{i}",
            "season": s,
            "week": 1,
            "home_team": "CHI",
            "away_team": "GB",
            "kickoff_utc": datetime(s, 9, 10, 17, tzinfo=UTC),
            "home_score": 999,
            "away_score": -999,
        }
        for s, count in ((2022, 271), (2023, 272), (2024, 272), (2025, 272))
        for i in range(count)
    ]
    frame = pl.DataFrame(data)
    write_json(
        tmp_path / "processed/team_epa/manifest.json",
        {
            "pipeline_source_sha256": "code-sha",
            "artifacts": {"games": {"file": "games.parquet", "sha256": "artifact-sha"}},
        },
    )
    monkeypatch.setattr(source, "team_source_hash", lambda: "code-sha")
    monkeypatch.setattr(source, "sha256_file", lambda p: "artifact-sha")
    projected = []

    class ProjectOnly:
        def select(self, fields):
            projected.append(fields)
            return frame.lazy().select(fields)

    monkeypatch.setattr(source.pl, "scan_parquet", lambda _: ProjectOnly())
    games, _ = source.load_development_schedule(tmp_path)
    assert projected == [SCHEDULE_FIELDS]
    assert games.height == 815 and set(games["season"]) == {2022, 2023, 2024}
    assert set(games.columns) == set(SCHEDULE_FIELDS)
