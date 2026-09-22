from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nfl_prop_model.cli import main
from nfl_prop_model.data.current_qbs import NFL_TEAMS
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.data.upcoming import build_upcoming_report, load_upcoming_report

AS_OF = datetime(2026, 9, 22, 12, tzinfo=UTC)


def prospective_sources(as_of=AS_OF):
    date = (as_of + timedelta(days=2)).date().isoformat()
    schedules = pl.DataFrame(
        [
            {
                "game_id": "2026_03_ARI_BUF",
                "season": 2026,
                "week": 3,
                "game_type": "REG",
                "gameday": date,
                "gametime": "20:15",
                "home_team": "BUF",
                "away_team": "ARI",
                "home_score": None,
                "away_score": None,
                "home_qb_id": "wrong-starter-field",
            }
        ],
        schema_overrides={"home_score": pl.Float64, "away_score": pl.Float64},
    )
    retrieved = as_of - timedelta(hours=1)
    rows = [
        {
            "dt": retrieved.isoformat(),
            "team": team,
            "player_name": f"Current {team}",
            "espn_id": f"espn-{team}",
            "gsis_id": f"gsis-{team}",
            "pos_abb": "QB",
            "pos_rank": 1,
        }
        for team in sorted(NFL_TEAMS)
    ]
    rows += [
        {
            **rows[0],
            "team": "BUF",
            "player_name": "Unmapped newcomer",
            "espn_id": "rookie",
            "gsis_id": None,
            "pos_rank": 2,
        },
        {
            **rows[0],
            "dt": (retrieved - timedelta(days=1)).isoformat(),
            "team": "BUF",
            "player_name": "Departed",
            "espn_id": "departed",
            "gsis_id": "gone",
        },
        {
            **rows[0],
            "dt": (retrieved + timedelta(minutes=1)).isoformat(),
            "team": "BUF",
            "player_name": "Future",
            "espn_id": "future",
            "gsis_id": "future",
        },
    ]
    manifest = {"format_version": 1, "season": 2026, "retrieved_at_utc": retrieved.isoformat()}
    return schedules, pl.DataFrame(rows), manifest


def test_candidates_keep_newcomers_use_team_snapshot_and_preserve_history():
    schedules, depth, manifest = prospective_sources()
    history = pl.DataFrame(
        {
            "player_id": ["gsis-BUF", "gone"],
            "season": [2024, 2024],
            "kickoff_utc": [datetime(2024, 9, 1, tzinfo=UTC)] * 2,
        }
    )
    original = history.clone()
    report = build_upcoming_report(schedules, depth, manifest, as_of=AS_OF, history=history)
    rows = report["candidates"]
    assert {row["player_name"] for row in rows} == {
        "Current BUF",
        "Current ARI",
        "Unmapped newcomer",
    }
    assert all(row["sources_fresh"] for row in rows)
    assert all(not row["starter_confirmed"] and not row["forecast_available"] for row in rows)
    assert next(row for row in rows if row["gsis_id"] == "gsis-BUF")["development_games"] == 1
    assert (
        next(row for row in rows if row["gsis_id"] == "gsis-ARI")["history_status"]
        == "no_development_history"
    )
    assert (
        next(row for row in rows if row["gsis_id"] is None)["history_status"]
        == "missing_gsis_mapping"
    )
    assert all(row["kickoff_utc"] == "2026-09-25T00:15:00+00:00" for row in rows)
    assert_frame_equal(history, original)
    changed = schedules.with_columns(pl.lit("other").alias("home_qb_id"))
    assert build_upcoming_report(changed, depth, manifest, as_of=AS_OF, history=history) == report


@pytest.mark.parametrize(
    "change", ["score", "one_score", "past", "started", "late", "postseason", "unknown_time"]
)
def test_unavailable_games_never_become_upcoming_candidates(change):
    schedules, depth, manifest = prospective_sources()
    if change in ("score", "one_score"):
        schedules = schedules.with_columns(pl.lit(0.0).alias("home_score"))
        if change == "score":
            schedules = schedules.with_columns(pl.lit(0.0).alias("away_score"))
    elif change in ("past", "started"):
        schedules = schedules.with_columns(
            pl.lit("2026-09-22").alias("gameday"),
            pl.lit("07:00" if change == "past" else "08:00").alias("gametime"),
        )
    elif change == "late":
        schedules = schedules.with_columns(pl.lit("2026-11-01").alias("gameday"))
    elif change == "postseason":
        schedules = schedules.with_columns(pl.lit("POST").alias("game_type"))
    else:
        schedules = schedules.with_columns(pl.lit("TBD").alias("gametime"))
    report = build_upcoming_report(schedules, depth, manifest, as_of=AS_OF)
    assert not report["candidates"]
    if change == "unknown_time":
        assert report["unknown_kickoff_game_ids"] == ["2026_03_ARI_BUF"]


def test_fresh_download_does_not_make_old_team_chart_fresh():
    schedules, depth, manifest = prospective_sources()
    depth = depth.with_columns(
        pl.lit((AS_OF - timedelta(hours=49)).isoformat()).alias("dt")
    ).filter(~pl.col("espn_id").is_in(["departed", "future"]))
    report = build_upcoming_report(schedules, depth, manifest, as_of=AS_OF)
    assert all(not row["sources_fresh"] for row in report["candidates"])
    assert all("team_chart_older_than_48h" in row["review_reasons"] for row in report["candidates"])


def test_cache_age_recalculated_and_past_knowledge_rejected():
    schedules, depth, manifest = prospective_sources()
    later = build_upcoming_report(schedules, depth, manifest, as_of=AS_OF + timedelta(hours=25))
    assert later["counts"]["candidate_rows"] == 3
    assert later["counts"]["fresh_candidate_rows"] == 0
    with pytest.raises(ValueError, match="past knowledge"):
        build_upcoming_report(schedules, depth, manifest, as_of=AS_OF - timedelta(hours=2))
    with pytest.raises(ValueError, match="time zone"):
        build_upcoming_report(schedules, depth, manifest, as_of=AS_OF.replace(tzinfo=None))


def test_invalid_sources_fail_and_ambiguous_depth_is_flagged():
    schedules, depth, manifest = prospective_sources()
    with pytest.raises(DataQualityError, match="duplicate"):
        build_upcoming_report(pl.concat([schedules, schedules]), depth, manifest, as_of=AS_OF)
    with pytest.raises(DataQualityError, match="2026"):
        build_upcoming_report(
            schedules.with_columns(pl.lit(2025).alias("season")), depth, manifest, as_of=AS_OF
        )
    with pytest.raises(DataQualityError, match="coverage"):
        build_upcoming_report(
            schedules, depth.filter(pl.col("team") != "ARI"), manifest, as_of=AS_OF
        )
    tied = depth.with_columns(
        pl.when(pl.col("espn_id") == "rookie")
        .then(pl.lit(1))
        .otherwise(pl.col("pos_rank"))
        .alias("pos_rank")
    )
    report = build_upcoming_report(schedules, tied, manifest, as_of=AS_OF)
    assert all(
        "ambiguous_top_depth_rank" in row["review_reasons"]
        for row in report["candidates"]
        if row["team"] == "BUF"
    )


def save_sources(data, as_of):
    schedules, depth, manifest = prospective_sources(as_of)
    directory = data / "raw" / "upcoming_2026"
    manifest["datasets"] = {
        key: store_frame(directory, key, frame)
        for key, frame in (("schedules", schedules), ("depth_charts", depth))
    }
    write_json(directory / "manifest.json", manifest)
    return directory, manifest


def test_offline_cli_loads_verified_cache_and_rejects_tampering(tmp_path, monkeypatch):
    import nflreadpy as nfl

    def no_network(*args, **kwargs):
        raise AssertionError("Cached command must not fetch data")

    monkeypatch.setattr(nfl, "load_schedules", no_network)
    monkeypatch.setattr(nfl, "load_depth_charts", no_network)
    data = tmp_path / "data"
    directory, manifest = save_sources(data, datetime.now(UTC))
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    report_dir = tmp_path / "report"
    assert main(["upcoming", "--data-dir", str(data), "--report-dir", str(report_dir)]) == 0
    report = read_json(report_dir / "upcoming.json")
    assert report["counts"]["candidate_rows"] == 3
    assert report["development_history_source"] is None
    assert all(
        row["history_status"] in {"history_cache_unavailable", "missing_gsis_mapping"}
        for row in report["candidates"]
    )
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    path = directory / manifest["datasets"]["schedules"]["file"]
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum"):
        load_upcoming_report(data)


def test_upcoming_ui_works_without_historical_models(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    save_sources(tmp_path, datetime.now(UTC))
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=20).run()
    app.radio(key="page").set_value("Upcoming QBs").run()
    assert not app.exception and not app.error
    assert app.metric[0].value == "1"
    assert len(app.dataframe[0].value) == 3
    assert app.selectbox(key="upcoming_qb")
    app.selectbox(key="upcoming_qb").set_value("rookie").run()
    assert not app.exception and not app.error
