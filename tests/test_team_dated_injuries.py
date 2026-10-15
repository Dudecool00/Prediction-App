from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_team_scores import team_sources as team_sources
from test_team_walkforward import weekly_data as weekly_data

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.features.team_walkforward import add_pregame_features
from nfl_prop_model.modeling import team_dated_injuries as study
from nfl_prop_model.modeling.team_walkforward import evaluate_weeks


def test_explicit_canonical_fetch_does_not_require_optional_tracking_indexes(tmp_path, monkeypatch):
    from nfl_prop_model.data import team_injury_news, team_injury_news_variants

    called = []

    def canonical(data_dir, seasons):
        called.append((data_dir, seasons))
        return tmp_path / "manifest.json"

    def tracking(*args):
        raise AssertionError("Canonical initialization must not depend on tracking availability")

    monkeypatch.setattr(team_injury_news, "fetch_news", canonical)
    monkeypatch.setattr(team_injury_news_variants, "fetch_qualified", tracking)
    assert (
        main(
            [
                "team-injury-news-fetch",
                "--seasons",
                "2022",
                "--canonical-only",
                "--data-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert called == [(tmp_path, (2022,))]


def reports(base):
    rows = []
    for row in base.to_dicts():
        for side in ("home", "away"):
            formal = row["horizon"] == "pregame_refresh"
            counts = {g + "_out_count": 0 if formal else None for g in study.GROUPS}
            if formal:
                counts["QB_out_count"] = int(side == "home")
                counts["OL_out_count"] = row["week"] % 3
                counts["defense_out_count"] = int(side == "away")
            rows.append(
                {
                    "game_id": row["game_id"],
                    "horizon": row["horizon"],
                    "side": side,
                    "team": row[side + "_team"],
                    "season": row["season"],
                    "week": row["week"],
                    "cutoff_utc": row["prediction_time_utc"],
                    "status": "designations" if formal else "unavailable",
                    "reason": None if formal else "no_earlier_edition",
                    "archive_captured_at_utc": row["prediction_time_utc"] - timedelta(hours=2)
                    if formal
                    else None,
                    "out_count": sum(counts.values()) if formal else None,
                    "unknown_position_out_players": 0 if formal else None,
                    **counts,
                }
            )
    return pl.DataFrame(rows, infer_schema_length=None)


def test_unknown_and_explicit_empty_have_different_flags(weekly_data):
    base = weekly_data[2]
    news = reports(base).with_columns(
        *[
            pl.when(pl.col("status") == "designations")
            .then(0)
            .otherwise(None)
            .alias(g + "_out_count")
            for g in study.GROUPS
        ],
        pl.when(pl.col("status") == "designations").then(0).otherwise(None).alias("out_count"),
    )
    features = study.add_dated_context(base, news)
    early = features.filter(pl.col("horizon") == "early_week")
    refresh = features.filter(pl.col("horizon") == "pregame_refresh")
    assert early["both_positions_known"].sum() == 0
    assert refresh["both_positions_known"].sum() == refresh.height
    for name in study.CONTRASTS:
        assert early[name].sum() == refresh[name].sum() == 0
    assert_frame_equal(base, features.select(base.columns))


def test_counts_use_offense_and_defense_contrasts_without_starter_assumption(weekly_data):
    base = weekly_data[2]
    features = study.add_dated_context(base, reports(base))
    refresh = features.filter(pl.col("horizon") == "pregame_refresh")
    assert set(refresh[study.CONTRASTS[0]]) == {1.0}
    assert set(refresh[study.CONTRASTS[1]]) == {-1.0}
    assert len(study.MODELS[study.INJURY]) == 15
    assert len(study.MODELS[study.FLAGS]) == 13


@pytest.mark.parametrize("change", ["missing", "team", "cutoff", "future", "stale"])
def test_missing_identity_or_unavailable_formal_reports_fail(weekly_data, change):
    base = weekly_data[2]
    news = reports(base)
    if change == "missing":
        news = news.slice(1)
    elif change == "team":
        news = news.with_columns(pl.lit("FAKE").alias("team"))
    elif change == "cutoff":
        news = news.with_columns(
            (pl.col("cutoff_utc") + pl.duration(seconds=1)).alias("cutoff_utc")
        )
    else:
        news = news.with_columns(
            (pl.col("cutoff_utc") - pl.duration(hours=0 if change == "future" else 73)).alias(
                "archive_captured_at_utc"
            )
        )
    with pytest.raises(DataQualityError):
        study.add_dated_context(base, news)


def test_unknown_positions_disable_counts_but_preserve_formal_report(weekly_data):
    base = weekly_data[2]
    news = reports(base).with_columns(
        pl.when(pl.col("side") == "home")
        .then(1)
        .otherwise(pl.col("unknown_position_out_players"))
        .alias("unknown_position_out_players")
    )
    features = study.add_dated_context(base, news).filter(pl.col("horizon") == "pregame_refresh")
    assert features["home_formal_report"].sum() == features.height
    assert features["home_positions_known"].sum() == 0
    assert features["both_positions_known"].sum() == 0
    assert features[study.CONTRASTS[0]].sum() == features[study.CONTRASTS[1]].sum() == 0


def test_counts_must_reconcile_and_2025_is_excluded(weekly_data):
    base = weekly_data[2]
    news = reports(base).with_columns(pl.lit(-1).alias("QB_out_count"))
    with pytest.raises(DataQualityError, match="reconcile"):
        study.add_dated_context(base, news)
    changed = base.with_columns(pl.lit(2025).alias("season"))
    with pytest.raises(DataQualityError, match="development"):
        study.add_dated_context(changed, reports(changed))


def test_weekly_fits_reproduce_control_and_exclude_same_week_outcomes(weekly_data):
    _, _, base, weather, injuries = weekly_data
    features = study.add_dated_context(base, reports(base))
    panel, folds, _ = study.evaluate_dated(features, seasons=(2024,))
    original, _, _ = evaluate_weeks(
        add_pregame_features(base, injuries, weather), seasons=(2024,), models=("compact_epa",)
    )
    study._check_control(panel, original.filter(pl.col("model") == study.CONTROL))
    assert len(folds) == 36
    assert panel.height == 36 * 2 * 3
    for row in folds:
        assert datetime.fromisoformat(
            row["latest_training_result_available_utc"]
        ) < datetime.fromisoformat(row["lock_utc"])
    altered = features.with_columns(
        *[
            pl.when(pl.col("season") == 2024).then(999.0).otherwise(pl.col(k)).alias(k)
            for k in ("home_score", "away_score")
        ]
    )
    changed, _, _ = study.evaluate_dated(altered, seasons=(2024,))
    columns = ["game_id", "horizon", "model", "predicted_home_score", "predicted_away_score"]
    assert_frame_equal(
        panel.filter(pl.col("week") == 1).select(columns),
        changed.filter(pl.col("week") == 1).select(columns),
    )
    assert panel.filter(
        (
            pl.col("predicted_home_score")
            - pl.col("predicted_away_score")
            - pl.col("predicted_home_margin")
        ).abs()
        > 1e-8
    ).is_empty()


def decision_panel(improve_flags_only=False):
    rows = []
    for season in (2023, 2024):
        for week in (1, 2):
            for horizon in study.HORIZONS:
                for model in study.MODELS:
                    error = (
                        2.0
                        if model == study.CONTROL
                        else (0.0 if improve_flags_only or model == study.INJURY else 1.0)
                    )
                    if horizon == "early_week":
                        error = 2.0
                    rows.append(
                        {
                            "game_id": f"{season}-{week}",
                            "season": season,
                            "week": week,
                            "horizon": horizon,
                            "model": model,
                            "home_score": 20.0,
                            "away_score": 20.0,
                            "target_home_margin": 0.0,
                            "predicted_home_score": 20 + error,
                            "predicted_away_score": 20.0,
                            "predicted_home_margin": error,
                            "result_available_at_utc": datetime(season, 9, week, tzinfo=UTC),
                        }
                    )
    return pl.DataFrame(rows)


def test_selection_requires_gain_beyond_archive_availability_and_rejects_future_validation():
    lock = datetime(2025, 9, 2, 15, tzinfo=UTC)
    assert study.choose_dated(decision_panel(), (2023, 2024), lock)["model"] == study.INJURY
    selected = study.choose_dated(decision_panel(True), (2023, 2024), lock)
    assert selected["model"] == study.CONTROL
    assert not selected["checks"][1]["eligible"]
    with pytest.raises(DataQualityError, match="unavailable"):
        study.choose_dated(decision_panel(), (2023, 2024), datetime(2024, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize("tamper", ["context", "input", "choice", "code"])
def test_offline_reader_verifies_context_sources_frozen_choice_and_code(
    weekly_data, tmp_path, monkeypatch, tamper
):
    base = weekly_data[2]
    news = reports(base)
    context = study.add_dated_context(base, news).select(
        "game_id", "horizon", *study.REPORT_FLAGS, *study.CONTRASTS
    )
    panel = decision_panel()
    controls = panel.filter(pl.col("model") == study.CONTROL)
    refs = {"source": {"sha256": "original-source"}}
    monkeypatch.setattr(study, "_inputs", lambda _: (base, controls, news, refs))
    frozen = study.choose_dated(panel, (2023, 2024), datetime(2025, 9, 2, 15, tzinfo=UTC))
    report = {
        "format_version": 1,
        "source_sha256": study.dated_source_hash(),
        "policy": study.POLICY,
        "inputs": refs,
        "metrics": study.score_metrics(panel).to_dicts(),
        "frozen_research_choice": frozen,
        "enriched_replay_ready": False,
        "2025_fits_performed": 0,
        "artifacts": {
            "context": store_frame(tmp_path, "context", context),
            "predictions": store_frame(tmp_path, "predictions", panel),
        },
    }
    path = tmp_path / "manifest.json"
    write_json(path, report)
    write_json(tmp_path / "frozen_2025.json", frozen)
    assert study.load_dated_report(path, tmp_path)["enriched_replay_ready"] is False
    if tamper == "context":
        changed = context.with_columns(pl.lit(77.0).alias(study.CONTRASTS[0]))
        report["artifacts"]["context"] = store_frame(tmp_path, "changed", changed)
    elif tamper == "input":
        report["inputs"] = {"source": {"sha256": "changed-source"}}
    elif tamper == "choice":
        changed = read_json(tmp_path / "frozen_2025.json")
        changed["model"] = study.CONTROL
        write_json(tmp_path / "frozen_2025.json", changed)
    else:
        report["source_sha256"] = "different-code"
    write_json(path, report)
    with pytest.raises(DataQualityError):
        study.load_dated_report(path, tmp_path)
