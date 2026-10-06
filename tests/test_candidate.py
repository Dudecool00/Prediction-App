import shutil
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, store_frame, write_json
from nfl_prop_model.features.quarterback import add_lagged_features
from nfl_prop_model.modeling import candidate
from nfl_prop_model.modeling.uncertainty import chronological_calibration_split


def development_table():
    rows = []
    for week in range(1, 10):
        for player in range(20):
            kickoff = datetime(2024, 9, 1, 17, tzinfo=UTC) + timedelta(
                weeks=week - 1, hours=player // 2
            )
            rows.append(
                {
                    "player_id": f"QB-{player:02}",
                    "game_id": f"2024_{week:02}_{player // 2:02}",
                    "season": 2024,
                    "week": week,
                    "kickoff_utc": kickoff,
                    "prediction_time_utc": kickoff - timedelta(hours=1),
                    "target_passing_yards": float(100 + player * 10 + week),
                    "observed_attempts": 25.0,
                    "schedule_reported_starter": player % 2 == 0,
                    "is_designated_home": player % 2 == 0,
                    "is_neutral_site": False,
                    "team_rest_days": 7.0 if week > 1 else None,
                    "opponent_rest_days": 7.0 if week > 1 else None,
                }
            )
    return add_lagged_features(pl.DataFrame(rows))


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    return candidate.prepare_candidate(
        development_table(), tmp_path_factory.mktemp("candidate"), {"fixture": "synthetic"}
    )


def rewrite_manifest(bundle, transform):
    path = next(bundle.glob("manifest-*.json"))
    manifest = read_json(path)
    transform(manifest)
    write_json(path, manifest)
    path.rename(bundle / f"manifest-{sha256_file(path)}.json")


def test_model_round_trip_and_disjoint_saved_cohorts(prepared):
    report = candidate.verify_candidate(prepared)
    assert report["counts"]["training_rows"] == 60
    assert report["counts"]["calibration_rows"] == 120
    assert report["counts"]["embargoed_rows"] == 0
    assert report["holdout_access"] == "closed"
    assert report["production_enabled"] is False
    assert report["holdout_protocol"]["estimator_updates"].startswith("none")
    assert report["holdout_protocol"]["calibration_updates"].startswith("none")


def test_calibration_outcomes_and_diagnostic_columns_cannot_fit_model(prepared, tmp_path):
    # Mutate labels only after feature construction: the test isolates fitting, not lags.
    table = development_table().with_columns(
        pl.when(pl.col("week") >= 4)
        .then(pl.col("target_passing_yards") + 9999)
        .otherwise(pl.col("target_passing_yards"))
        .alias("target_passing_yards"),
        pl.lit(9999.0).alias("observed_attempts"),
        (~pl.col("schedule_reported_starter")).alias("schedule_reported_starter"),
    )
    bundle = candidate.prepare_candidate(table.reverse(), tmp_path, {"fixture": "mutated"})
    original, changed = (candidate.verify_candidate(p) for p in (prepared, bundle))
    assert original["artifacts"]["model"]["sha256"] == changed["artifacts"]["model"]["sha256"]
    assert (
        original["artifacts"]["calibration"]["sha256"]
        != changed["artifacts"]["calibration"]["sha256"]
    )


@pytest.mark.parametrize("artifact", ["model", "features", "calibration", "source"])
def test_corrupt_artifacts_fail_before_loading(prepared, tmp_path, artifact):
    bundle = tmp_path / "bundle"
    shutil.copytree(prepared, bundle)
    manifest = read_json(next(bundle.glob("manifest-*.json")))
    path = bundle / manifest["artifacts"][artifact]["file"]
    path.write_bytes(path.read_bytes() + b"damaged")
    with pytest.raises(DataQualityError, match="checksum"):
        candidate.verify_candidate(bundle)


def test_changed_manifest_fails(prepared, tmp_path):
    bundle = tmp_path / "bundle"
    shutil.copytree(prepared, bundle)
    path = next(bundle.glob("manifest-*.json"))
    manifest = read_json(path)
    manifest["holdout_access"] = "open"
    write_json(path, manifest)
    with pytest.raises(DataQualityError, match="manifest checksum"):
        candidate.verify_candidate(bundle)


@pytest.mark.parametrize("mutation", ["policy", "path", "counts", "residual"])
def test_rehashed_inconsistent_bundles_fail(prepared, tmp_path, mutation):
    bundle = tmp_path / "bundle"
    shutil.copytree(prepared, bundle)

    def change(manifest):
        if mutation == "policy":
            manifest["policy"]["parameters"]["n_estimators"] = 1
        elif mutation == "path":
            manifest["artifacts"]["model"]["file"] = "../model.json"
        elif mutation == "counts":
            manifest["counts"]["training_rows"] = 999
        else:
            metadata = manifest["artifacts"]["calibration"]
            frame = pl.read_parquet(bundle / metadata["file"]).with_columns(
                (pl.col("residual") + 1).alias("residual")
            )
            manifest["artifacts"]["calibration"] = store_frame(bundle, "calibration", frame)

    rewrite_manifest(bundle, change)
    with pytest.raises(DataQualityError):
        candidate.verify_candidate(bundle)


@pytest.mark.parametrize("issue", ["holdout", "late_result", "rest", "flag", "history"])
def test_invalid_inputs_rejected_before_fitting(tmp_path, monkeypatch, issue):
    table = development_table()
    if issue == "holdout":
        table = table.with_columns(pl.lit(2025).alias("season"))
    elif issue == "late_result":
        table = table.with_columns(
            (pl.col("kickoff_utc") + pl.duration(days=160)).alias("kickoff_utc"),
            (pl.col("prediction_time_utc") + pl.duration(days=160)).alias("prediction_time_utc"),
        )
    elif issue == "rest":
        table = table.with_columns(pl.lit(float("inf")).alias("team_rest_days"))
    elif issue == "flag":
        table = table.with_columns(pl.lit(None).alias("is_neutral_site"))
    else:
        table = table.with_columns(pl.lit(1.5).alias("prior_games_in_sample"))

    def forbidden(*args, **kwargs):
        pytest.fail("Estimator fitting must not happen for invalid data")

    monkeypatch.setattr(candidate.XGBRegressor, "fit", forbidden)
    with pytest.raises(DataQualityError):
        candidate.prepare_candidate(table, tmp_path / "outputs", {})
    assert not (tmp_path / "outputs").exists()


def test_unavailable_boundary_rows_are_retained_as_embargoed(tmp_path):
    table = development_table()
    cutoff = chronological_calibration_split(table).cutoff_utc
    late_game = (
        table.filter(pl.col("week") == 3)
        .head(2)
        .with_columns(
            pl.lit(cutoff - timedelta(hours=12)).alias("kickoff_utc"),
            pl.lit(cutoff - timedelta(hours=13)).alias("prediction_time_utc"),
        )
    )
    table = pl.concat([table.filter(pl.col("game_id") != late_game["game_id"][0]), late_game])
    bundle = candidate.prepare_candidate(table, tmp_path, {})
    counts = candidate.verify_candidate(bundle)["counts"]
    assert counts["training_rows"] == 58
    assert counts["embargoed_rows"] == 2
    assert counts["development_rows"] == 180


def test_preparations_preserve_previous_bundle(prepared, tmp_path):
    before = {p.name: sha256_file(p) for p in prepared.iterdir()}
    first = candidate.prepare_candidate(development_table(), tmp_path, {})
    second = candidate.prepare_candidate(development_table(), tmp_path, {})
    assert first != second
    assert (
        candidate.verify_candidate(first)["artifacts"]
        == candidate.verify_candidate(second)["artifacts"]
    )
    assert before == {p.name: sha256_file(p) for p in prepared.iterdir()}


def test_verify_cli_is_offline(prepared, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Verification must not load source caches or fit models")

    monkeypatch.setattr(candidate, "load_snapshot", forbidden)
    monkeypatch.setattr(candidate.XGBRegressor, "fit", forbidden)
    assert main(["verify-candidate", "--bundle", str(prepared)]) == 0
    assert "2025 access is closed" in capsys.readouterr().out


def test_source_snapshot_mismatch_rejected(tmp_path, monkeypatch):
    from types import SimpleNamespace

    directory = tmp_path / "data" / "processed" / "2022_2023_2024"
    table = development_table()
    write_json(
        directory / "manifest.json",
        {
            "feature_columns": list(candidate.FEATURE_COLUMNS),
            "table": store_frame(directory, "historical", table),
            "raw_manifest": {"datasets": {"snapshot": "original"}},
        },
    )
    monkeypatch.setattr(
        candidate,
        "load_snapshot",
        lambda *a: SimpleNamespace(manifest={"datasets": {"snapshot": "changed"}}),
    )
    with pytest.raises(DataQualityError, match="snapshots differ"):
        candidate.write_candidate(tmp_path / "data", tmp_path / "models", tmp_path / "reports")
    assert not (tmp_path / "models").exists()
