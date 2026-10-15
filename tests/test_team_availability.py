import base64
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nfl_prop_model.cli import main
from nfl_prop_model.data import team_availability as audit
from nfl_prop_model.data import team_injury_archive as injuries
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_depth import (
    DEPTH_URL,
    SCHEDULE_FIELDS,
    DepthSnapshots,
    load_schedule_only,
)
from nfl_prop_model.data.team_games import team_source_hash


@pytest.fixture
def depth_frames():
    inventory = pl.DataFrame(
        {
            "dt": ["2025-09-01T07:00:00Z", "2025-09-02T07:00:00Z", "2025-09-03T07:00:00Z"],
            "team": ["CHI"] * 3,
            "chart_rows": [50, 50, 49],
            "qb_rows": [2, 2, 0],
        }
    )
    qb = pl.DataFrame(
        {
            "dt": ["2025-09-01T07:00:00Z"] * 2 + ["2025-09-02T07:00:00Z"] * 2,
            "team": ["CHI"] * 4,
            "espn_id": ["11", "22", "22", "33"],
            "pos_slot": [9] * 4,
            "pos_rank": [1, 2, 1, 2],
        }
    )
    return qb, inventory


def article(body, *, modified="2025-09-03T18:00:00Z"):
    metadata = {
        "@type": "NewsArticle",
        "datePublished": "2025-09-03T17:00:00Z",
        "dateModified": modified,
    }
    return (
        "<h1>NFL Week 1 injury report</h1><script type='application/ld+json'>"
        + json.dumps(metadata)
        + "</script><ul><li>OUT: QB Navigation Person (knee)</li></ul>"
        + "<div class='nfl-c-article__body'>"
        + body
        + "</div>"
    ).encode()


def test_depth_cutoff_is_strict_and_later_snapshot_cannot_change_earlier_leader(depth_frames):
    qb, inventory = depth_frames
    history = DepthSnapshots(qb, inventory)
    equal = datetime(2025, 9, 3, 7, tzinfo=UTC)
    assert history.as_of("CHI", equal)["listed_qb_espn_id"] == "11"
    assert history.as_of("CHI", equal + timedelta(seconds=1))["listed_qb_espn_id"] == "22"
    early = datetime(2025, 9, 2, 15, tzinfo=UTC)
    assert history.as_of("CHI", early)["listed_qb_espn_id"] == "11"
    assert (
        DepthSnapshots(qb, inventory, delay_hours=0).as_of("CHI", early)["listed_qb_espn_id"]
        == "22"
    )
    changed = qb.with_columns(
        pl.when(pl.col("dt") == "2025-09-02T07:00:00Z")
        .then(pl.lit("999"))
        .otherwise(pl.col("espn_id"))
        .alias("espn_id")
    )
    # Poison only the later leader; preserve unique row identities.
    changed = changed.with_columns(
        pl.when((pl.col("dt") == "2025-09-02T07:00:00Z") & (pl.col("pos_rank") == 2))
        .then(pl.lit("888"))
        .otherwise(pl.col("espn_id"))
        .alias("espn_id")
    )
    assert DepthSnapshots(changed, inventory).as_of("CHI", early) == history.as_of("CHI", early)


def test_empty_ambiguous_and_stale_depth_never_fall_back(depth_frames):
    qb, inventory = depth_frames
    history = DepthSnapshots(qb, inventory)
    empty = history.as_of("CHI", datetime(2025, 9, 4, 8, tzinfo=UTC))
    assert empty["listed_qb_espn_id"] is None and empty["reason"] == "empty QB snapshot"
    ambiguous = qb.with_columns(
        pl.when(pl.col("dt") == "2025-09-02T07:00:00Z")
        .then(1)
        .otherwise(pl.col("pos_rank"))
        .alias("pos_rank")
    )
    result = DepthSnapshots(ambiguous, inventory).as_of("CHI", datetime(2025, 9, 3, 8, tzinfo=UTC))
    assert result["listed_qb_espn_id"] is None and "ambiguous" in result["reason"]
    assert history.as_of("CHI", datetime(2025, 9, 10, tzinfo=UTC))["reason"] == "stale snapshot"
    assert history.as_of("DAL", datetime(2025, 9, 2, 15, tzinfo=UTC))["status"] == "unavailable"


def test_depth_inventory_integrity_and_aware_january_times(depth_frames):
    qb, inventory = depth_frames
    with pytest.raises(DataQualityError, match="explicit ISO"):
        DepthSnapshots(qb.with_columns(pl.col("dt").str.replace("Z", "")), inventory)
    with pytest.raises(DataQualityError, match="counts mismatch"):
        DepthSnapshots(qb.head(3), inventory)
    with pytest.raises(DataQualityError, match="duplicate"):
        DepthSnapshots(pl.concat([qb, qb.head(1)]), inventory)
    history = DepthSnapshots(qb, inventory)
    with pytest.raises(DataQualityError, match="timezone-aware"):
        history.as_of("CHI", datetime(2025, 9, 2))
    january_qb = qb.head(1).with_columns(pl.lit("2026-01-02T02:00:00-05:00").alias("dt"))
    january_inventory = inventory.head(1).with_columns(
        pl.lit("2026-01-02T02:00:00-05:00").alias("dt"),
        pl.lit(1, dtype=inventory.schema["qb_rows"]).alias("qb_rows"),
    )
    assert (
        DepthSnapshots(january_qb, january_inventory).as_of(
            "CHI", datetime(2026, 1, 3, 15, tzinfo=UTC)
        )["listed_qb_espn_id"]
        == "11"
    )


def test_archived_parser_ignores_navigation_and_matchup_metadata_preserves_unknowns():
    body = (
        "<h2>SUNDAY</h2><div class='nfl-o-ranked-item'></div><ul><li>WHERE: Venue</li></ul>"
        "<h3>BEARS</h3><ul><li>OUT: WR <a href='/players/signal-runner/'>Signal Runner</a> "
        "(back, illness), Mystery Defender (calf)</li><li>QUESTIONABLE: QB "
        "<a href='/players/test-quarterback/'>Test Quarterback</a> (knee)</li></ul>"
        "<div class='nfl-o-ranked-item'></div><ul><li>WHEN: Sunday</li></ul>"
        "<h3>PACKERS</h3><p>No injury designations</p><h2>ALREADY COMPLETED</h2>"
        "<h3>COWBOYS</h3><ul><li>OUT: QB Final Game Label (knee)</li></ul>"
    )
    result = injuries.parse_injury_article(article(body), datetime(2025, 9, 3, 20, tzinfo=UTC))
    assert set(result) == {"CHI", "GB"}
    assert result["CHI"]["out_count"] == 2 and result["CHI"]["parsed_players"] == 3
    assert (
        result["CHI"]["unknown_position_out_players"] == 1 and result["CHI"]["QB_out_count"] is None
    )
    assert result["GB"]["report_state"] == "no_designations" and result["GB"]["out_count"] == 0
    pending = injuries.parse_injury_article(
        article("<h3>BEARS</h3><p>Report pending</p>"), datetime(2025, 9, 3, 20, tzinfo=UTC)
    )
    assert (
        pending["CHI"]["out_count"] is None
        and pending["CHI"]["report_state"] == "partial_or_pending"
    )


@pytest.mark.parametrize(
    "body",
    [
        "<h3>BEARS</h3><li>OUT: QB Person (knee</li>",
        "<h3>BEARS</h3><p>No injury designations</p><li>OUT: QB Person (knee)</li>",
        "<h3>UNKNOWN TEAM</h3><p>No injury designations</p>",
    ],
)
def test_invalid_article_structures_fail_visibly(body):
    with pytest.raises(DataQualityError):
        injuries.parse_injury_article(article(body), datetime(2025, 9, 3, 20, tzinfo=UTC))


def test_archive_metadata_and_decompression_are_bounded():
    body = article("<h3>BEARS</h3><p>No injury designations</p>")
    stamp = datetime(2025, 9, 3, 20, tzinfo=UTC)
    assert injuries.parse_injury_article(body, stamp) == injuries.parse_injury_article(
        gzip.compress(body), stamp
    )
    with pytest.raises(DataQualityError, match="conflicts"):
        injuries.parse_injury_article(
            article("<h3>BEARS</h3>", modified="2025-09-04T18:00:00Z"), stamp
        )
    with pytest.raises(DataQualityError, match="1 MB"):
        injuries.parse_injury_article(gzip.compress(b"x" * 1000001), stamp)


@pytest.fixture
def archive_source(tmp_path, monkeypatch):
    body = article("<h3>BEARS</h3><p>No injury designations</p>")
    stamp = "20250903203503"
    digest = base64.b32encode(hashlib.sha1(body).digest()).decode()
    header = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]
    index = json.dumps(
        [header, ["com,nfl)", stamp, injuries.ARTICLE, "text/html", "200", digest, str(len(body))]]
    ).encode()
    archive_url = f"https://web.archive.org/web/{stamp}id_/{injuries.ARTICLE}"
    requests = []

    def get(url, limit):
        requests.append(url)
        return (index, url) if "/cdx/" in url else (body, archive_url)

    monkeypatch.setattr(injuries, "_get", get)
    return tmp_path, body, index, archive_url, requests


def test_archive_receipts_bind_capture_time_digest_and_preserve_existing_files(archive_source):
    data, _, _, _, requests = archive_source
    path = injuries.fetch_injury_pilot(data)
    captures = injuries.load_injury_pilot(path)
    original = {p: sha256_file(p) for p in path.parent.iterdir()}
    assert injuries.fetch_injury_pilot(data) == path and len(requests) == 2
    assert original == {p: sha256_file(p) for p in path.parent.iterdir()}
    stamp = captures[0][0]
    assert injuries.injury_as_of(captures, "CHI", 2025, 1, stamp)["out_count"] is None
    assert (
        injuries.injury_as_of(captures, "CHI", 2025, 1, stamp + timedelta(seconds=1))["out_count"]
        == 0
    )
    assert (
        injuries.injury_as_of(captures, "CHI", 2025, 2, stamp + timedelta(seconds=1))["out_count"]
        is None
    )
    assert (
        injuries.injury_as_of(captures, "CHI", 2024, 1, stamp + timedelta(seconds=1))["out_count"]
        is None
    )
    assert (
        injuries.injury_as_of(captures, "CHI", 2025, 1, stamp + timedelta(hours=73))["out_count"]
        is None
    )
    later = (stamp + timedelta(hours=1), {"GB": captures[0][1]["CHI"]}, "archive")
    assert (
        injuries.injury_as_of(captures + [later], "CHI", 2025, 1, stamp + timedelta(hours=2))[
            "out_count"
        ]
        is None
    )
    record = read_json(path)
    record["captures"][0]["captured_at_utc"] = "2025-09-03T19:35:03+00:00"
    write_json(path, record)
    with pytest.raises(DataQualityError, match="filename/time|preserved index"):
        injuries.load_injury_pilot(path)


@pytest.mark.parametrize("defect", ["redirect", "digest"])
def test_archive_never_substitutes_live_or_different_capture(archive_source, monkeypatch, defect):
    data, body, index, archive_url, _ = archive_source
    monkeypatch.setattr(
        injuries,
        "_get",
        lambda url, limit: (
            (index, url)
            if "/cdx/" in url
            else (body, injuries.ARTICLE)
            if defect == "redirect"
            else (body + b"changed", archive_url)
        ),
    )
    with pytest.raises(DataQualityError, match="different version|digest mismatched"):
        injuries.fetch_injury_pilot(data)


def test_corrupt_compressed_article_and_index_are_rejected(archive_source):
    data, _, _, _, _ = archive_source
    path = injuries.fetch_injury_pilot(data)
    metadata = read_json(path)
    body_file = path.parent / metadata["captures"][0]["file"]
    original = body_file.read_bytes()
    body_file.write_bytes(b"bad")
    with pytest.raises(DataQualityError, match="checksum"):
        injuries.load_injury_pilot(path)
    body_file.write_bytes(original)
    (path.parent / metadata["index_file"]).write_bytes(b"bad")
    with pytest.raises(DataQualityError, match="index checksum"):
        injuries.load_injury_pilot(path)


def test_outcome_free_schedule_audit_cli_and_source_guards(
    tmp_path, depth_frames, archive_source, monkeypatch
):
    from nfl_prop_model.data import team_games
    from nfl_prop_model.modeling import team_scores

    data = archive_source[0]
    injuries.fetch_injury_pilot(data)
    qb, inventory = depth_frames
    depth_dir = data / "raw" / "team_availability" / "2025"
    write_json(
        depth_dir / "manifest.json",
        {
            "format_version": 1,
            "url": DEPTH_URL,
            "artifacts": {
                "quarterbacks": store_frame(depth_dir, "quarterbacks", qb),
                "inventory": store_frame(depth_dir, "inventory", inventory),
            },
        },
    )
    games = pl.DataFrame(
        [
            {
                "game_id": f"2025-{i}",
                "season": 2025,
                "week": i // 16 + 1,
                "home_team": "CHI",
                "away_team": "GB",
                "kickoff_utc": datetime(2025, 9, 7, 17, tzinfo=UTC) + timedelta(weeks=i // 16),
                "home_score": 999.0,
                "away_score": 998.0,
                "actual_qb": "forbidden",
            }
            for i in range(272)
        ]
    )
    team_dir = data / "processed" / "team_epa"
    write_json(
        team_dir / "manifest.json",
        {
            "pipeline_source_sha256": team_source_hash(),
            "artifacts": {"games": store_frame(team_dir, "games", games)},
        },
    )
    original_files = {
        p: sha256_file(p) for folder in (depth_dir, team_dir) for p in folder.iterdir()
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Availability audit accessed complete outcome cache or fitted scores")

    monkeypatch.setattr(team_games, "load_team_cache", forbidden)
    monkeypatch.setattr(team_scores, "predict_models", forbidden)
    metadata, _ = load_schedule_only(data)
    assert set(metadata.columns) == set(SCHEDULE_FIELDS)
    with pytest.raises(DataQualityError, match="no outcomes"):
        audit.audit_cutoffs(games, qb, inventory, [])
    report_dir = data / "report"
    assert (
        main(["team-availability-audit", "--data-dir", str(data), "--report-dir", str(report_dir)])
        == 0
    )
    path = next((data / "processed" / "team_availability").glob("*/manifest.json"))
    report = audit.load_availability_audit(path)
    assert report["records"] == 1088 and report["summary"]["2025_fits_performed"] == 0
    assert report["summary"]["full_enriched_replay_ready"] is False
    assert original_files == {
        p: sha256_file(p) for folder in (depth_dir, team_dir) for p in folder.iterdir()
    }
    saved = path.read_bytes()
    audit.save_availability_audit(data, report_dir)
    assert path.read_bytes() == saved
    with monkeypatch.context() as changed:
        changed.setattr(audit, "availability_source_hash", lambda: "changed")
        with pytest.raises(DataQualityError, match="code/policy"):
            audit.load_availability_audit(path)
    (path.parent / report["artifact"]["file"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        audit.load_availability_audit(path)
