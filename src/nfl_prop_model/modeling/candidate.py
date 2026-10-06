"""Prepare inspectable development artifacts without opening or approving the holdout."""

import hashlib
import platform
import zipfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import polars as pl
from xgboost import XGBRegressor

from nfl_prop_model.data.ingest_nfl import load_snapshot
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.features.context import SCHEDULE_FEATURES, add_context_features
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS
from nfl_prop_model.modeling.config import COVERAGES, DIAGNOSTIC_THRESHOLDS, TREE_FEATURES
from nfl_prop_model.modeling.contracts import validate_model_table
from nfl_prop_model.modeling.policy import candidate_policy, policy_hash
from nfl_prop_model.modeling.uncertainty import ResidualCalibration, chronological_calibration_split

DEVELOPMENT_CUTOFF = datetime(2025, 2, 1, tzinfo=UTC)
FEATURES = TREE_FEATURES["xgb_schedule"]


def holdout_protocol() -> dict[str, Any]:
    """A reviewable static-estimator protocol, not authorization to access outcomes."""
    return {
        "version": "qb-passing-holdout-draft-v1",
        "status": "draft_pending_explicit_freeze",
        "season": 2025,
        "access": "closed",
        "development_seasons": [2022, 2023, 2024],
        "development_result_cutoff_utc": DEVELOPMENT_CUTOFF.isoformat(),
        "cohort": "Every recorded regular-season QB appearance with a completed schedule game "
        "and nonmissing target; retain backups, early exits, zero attempts and sparse history",
        "claim_scope": "Participation-conditioned retrospective diagnostic; not a verified "
        "pregame starter cohort or validation of prospective probabilities/EV",
        "estimator_updates": "none; reuse the prepared estimator for the entire season",
        "calibration_updates": "none; reuse the prepared development residual pool",
        "prediction_time": "Recorded kickoff minus one hour",
        "result_availability": "Kickoff plus 24 hours, strictly before the relevant cutoff",
        "feature_history": "Development and earlier available regular-season holdout QB "
        "appearances may supply lagged features; no current/future results, refitting or tuning",
        "primary_population": "All included appearances; MAE is the primary point metric",
        "secondary_population": "Same diagnostics for at least five prior available "
        "model-sample appearances; below five remains visible with prospective abstention",
        "point_metrics": ["MAE", "RMSE", "bias: prediction minus actual"],
        "interval_coverages": list(COVERAGES),
        "interval_metrics": ["empirical coverage", "mean width", "sample count"],
        "probability_thresholds": list(DIAGNOSTIC_THRESHOLDS),
        "probability_metrics": ["Brier", "log loss", "reliability bins with counts"],
        "report_splits": ["overall", "season/week", "pregame history: 0, 1-4, 5+"],
        "dependence_counts": ["distinct players", "distinct games", "distinct weeks"],
        "missing_data": "Reject invalid identities, duplicate keys, nonfinite targets, "
        "unmatched games and invalid timestamps; retain missing lagged/rest features with "
        "XGBoost native missing handling; enumerate target/position/unfinished exclusions",
        "comparison": "Describe against development diagnostics; this static estimator "
        "protocol differs from development weekly refitting, so do not pool their metrics",
        "acceptance": "Descriptive evaluation; no tuned pass threshold or profitability claim",
        "reruns": "One outcome evaluation after explicit freeze; errors require an audit trail. "
        "Any revised candidate uses a new holdout, never rebrands 2025 as untouched",
        "production_enabled": False,
    }


def validate_candidate_table(table: pl.DataFrame) -> None:
    validate_model_table(table)
    if missing := set(SCHEDULE_FEATURES) - set(table.columns):
        raise DataQualityError(f"Candidate schedule features missing: {sorted(missing)}")
    if table.filter(pl.col("kickoff_utc") + pl.duration(hours=24) >= DEVELOPMENT_CUTOFF).height:
        raise DataQualityError("Development results must be available before the fixed cutoff")
    counts = ["prior_games_in_sample", "prior_season_games_in_sample"]
    if table.filter(
        pl.any_horizontal((pl.col(c) < 0) | (pl.col(c) != pl.col(c).floor()) for c in counts)
    ).height:
        raise DataQualityError("Candidate history counts must be nonnegative whole numbers")
    flags = ["is_designated_home", "is_neutral_site"]
    if any(table.schema[c] != pl.Boolean and not table.schema[c].is_numeric() for c in flags):
        raise DataQualityError("Candidate schedule flags must be present and binary")
    if table.filter(
        pl.any_horizontal(
            pl.col(c).is_null() | ~pl.col(c).cast(pl.Float64).is_in([0.0, 1.0]) for c in flags
        )
    ).height:
        raise DataQualityError("Candidate schedule flags must be present and binary")
    rest = ["team_rest_days", "opponent_rest_days"]
    if table.filter(
        pl.any_horizontal((~pl.col(c).is_finite() | (pl.col(c) < 0)).fill_null(False) for c in rest)
    ).height:
        raise DataQualityError("Candidate rest days must be nonnegative finite values or null")


def _matrix(table: pl.DataFrame) -> np.ndarray[Any, np.dtype[np.float64]]:
    return np.asarray(table.select(FEATURES).to_numpy(), dtype=np.float64)


def _metadata(path: Path) -> dict[str, Any]:
    return {"file": path.name, "sha256": sha256_file(path)}


def _source_archive(directory: Path) -> dict[str, Any]:
    package = Path(__file__).parents[1]
    project = package.parent.parent
    files = sorted(package.rglob("*.py")) + [
        project / name for name in ("pyproject.toml", "requirements-dev.lock", "MODEL_POLICY.md")
    ]
    hashes = {}
    path = directory / "source.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            relative = file.relative_to(project).as_posix()
            contents = file.read_bytes()
            hashes[relative] = hashlib.sha256(contents).hexdigest()
            # Fixed ZIP metadata makes identical source snapshots byte reproducible.
            entry = zipfile.ZipInfo(relative, date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, contents)
    return {**_metadata(path), "members": hashes}


def prepare_candidate(table: pl.DataFrame, output_dir: Path, provenance: dict[str, Any]) -> Path:
    """Fit only development training rows and publish a new bundle with a hashed manifest."""
    validate_candidate_table(table)
    policy = candidate_policy()
    ordered = table.sort("kickoff_utc", "game_id", "player_id")
    split = chronological_calibration_split(
        ordered,
        weeks=policy["calibration"]["weeks"],
        minimum_rows=policy["calibration"]["minimum_rows"],
    )
    estimator = XGBRegressor(**policy["parameters"])
    estimator.fit(_matrix(split.train), split.train["target_passing_yards"].to_numpy())
    prediction = np.asarray(estimator.predict(_matrix(split.calibration)), dtype=np.float64)
    residuals = ResidualCalibration(
        split.calibration["target_passing_yards"].to_numpy() - prediction
    )
    calibration = split.calibration.with_columns(
        pl.Series("prediction", prediction), pl.Series("residual", residuals.residuals)
    )
    used = pl.concat([split.train, split.calibration]).select("game_id", "player_id")
    embargoed = ordered.join(used, on=["game_id", "player_id"], how="anti")
    created = datetime.now(UTC)
    directory = output_dir / f"candidate-{created.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    # The manifest is written last. A failed preparation cannot publish a valid bundle.
    estimator.get_booster().feature_names = list(FEATURES)
    model_path = directory / "model.json"
    estimator.save_model(model_path)
    artifacts = {
        "model": _metadata(model_path),
        "features": store_frame(directory, "features", ordered),
        "training": store_frame(directory, "training", split.train),
        "calibration": store_frame(directory, "calibration", calibration),
        "embargoed": store_frame(directory, "embargoed", embargoed),
        "source": _source_archive(directory),
    }
    manifest = {
        "format_version": 1,
        "status": "prepared_candidate_not_frozen",
        "created_at_utc": created.isoformat(),
        "production_enabled": False,
        "holdout_access": "closed",
        "policy": policy,
        "policy_sha256": policy_hash(policy),
        "holdout_protocol": holdout_protocol(),
        "holdout_protocol_sha256": policy_hash(holdout_protocol()),
        "provenance": provenance,
        "environment": {
            "python": platform.python_version(),
            **{name: version(name) for name in ("polars", "numpy", "xgboost-cpu")},
        },
        "features": list(FEATURES),
        "training_cutoff_utc": split.cutoff_utc.isoformat(),
        "development_result_cutoff_utc": DEVELOPMENT_CUTOFF.isoformat(),
        "counts": {
            "development_rows": ordered.height,
            "training_rows": split.train.height,
            "calibration_rows": calibration.height,
            "embargoed_rows": embargoed.height,
            "calibration_games": calibration["game_id"].n_unique(),
            "calibration_players": calibration["player_id"].n_unique(),
            "calibration_weeks": calibration.select("season", "week").unique().height,
        },
        "calibration_radii": {str(c): residuals.radius(c) for c in COVERAGES},
        "artifacts": artifacts,
        "remaining_gates": policy["remaining_gates"],
    }
    path = directory / "manifest.json"
    write_json(path, manifest)
    path.rename(directory / f"manifest-{sha256_file(path)}.json")
    verify_candidate(directory)
    return directory


def _checked_file(directory: Path, metadata: dict[str, Any]) -> Path:
    name = metadata["file"]
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name:
        raise DataQualityError("Candidate artifact must have a local filename")
    path = directory / name
    if not path.is_file() or path.is_symlink() or path.resolve().parent != directory.resolve():
        raise DataQualityError("Candidate artifact is missing or outside the bundle")
    if sha256_file(path) != metadata["sha256"]:
        raise DataQualityError(f"Candidate artifact checksum mismatch: {name}")
    return path


def verify_candidate(directory: Path) -> dict[str, Any]:
    """Check provenance, cohort/split, saved model and residuals entirely offline."""
    manifests = list(directory.glob("manifest-*.json"))
    if len(manifests) != 1:
        raise DataQualityError("Candidate requires exactly one content-addressed manifest")
    path = manifests[0]
    if path.name != f"manifest-{sha256_file(path)}.json":
        raise DataQualityError("Candidate manifest checksum mismatch")
    manifest = read_json(path)
    if (
        manifest.get("format_version") != 1
        or manifest.get("status") != "prepared_candidate_not_frozen"
        or manifest.get("production_enabled") is not False
        or manifest.get("holdout_access") != "closed"
        or manifest.get("policy") != candidate_policy()
        or manifest.get("policy_sha256") != policy_hash(candidate_policy())
        or manifest.get("holdout_protocol") != holdout_protocol()
        or manifest.get("holdout_protocol_sha256") != policy_hash(holdout_protocol())
        or manifest.get("features") != list(FEATURES)
    ):
        raise DataQualityError("Candidate policy, protocol or access gates differ")
    artifacts = manifest["artifacts"]
    if set(artifacts) != {"model", "features", "training", "calibration", "embargoed", "source"}:
        raise DataQualityError("Candidate artifact set differs")
    paths = {name: _checked_file(directory, item) for name, item in artifacts.items()}
    with zipfile.ZipFile(paths["source"]) as archive:
        members = artifacts["source"]["members"]
        if len(archive.namelist()) != len(members) or set(archive.namelist()) != set(members):
            raise DataQualityError("Candidate source archive members differ")
        for name, digest in members.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise DataQualityError("Candidate source member checksum mismatch")
    frames = {
        name: load_frame(directory, artifacts[name])
        for name in ("features", "training", "calibration", "embargoed")
    }
    features, training, calibration = (
        frames[name] for name in ("features", "training", "calibration")
    )
    validate_candidate_table(features)
    split = chronological_calibration_split(features)
    expected_embargoed = features.join(
        pl.concat([split.train, split.calibration]).select("game_id", "player_id"),
        on=["game_id", "player_id"],
        how="anti",
    )
    for actual, expected in (
        (training, split.train),
        (calibration.select(features.columns), split.calibration),
        (frames["embargoed"], expected_embargoed),
    ):
        if not actual.equals(expected, null_equal=True):
            raise DataQualityError("Candidate cohort or chronological split differs")
    expected_counts = {
        "development_rows": features.height,
        "training_rows": training.height,
        "calibration_rows": calibration.height,
        "embargoed_rows": frames["embargoed"].height,
        "calibration_games": calibration["game_id"].n_unique(),
        "calibration_players": calibration["player_id"].n_unique(),
        "calibration_weeks": calibration.select("season", "week").unique().height,
    }
    if (
        manifest["counts"] != expected_counts
        or manifest["training_cutoff_utc"] != split.cutoff_utc.isoformat()
        or manifest["development_result_cutoff_utc"] != DEVELOPMENT_CUTOFF.isoformat()
    ):
        raise DataQualityError("Candidate counts or cutoffs differ")
    estimator = XGBRegressor(**manifest["policy"]["parameters"])
    estimator.load_model(paths["model"])
    if estimator.get_booster().feature_names != list(FEATURES):
        raise DataQualityError("Saved estimator feature order differs")
    predictions = np.asarray(estimator.predict(_matrix(calibration)), dtype=np.float64)
    if not np.array_equal(predictions, calibration["prediction"].to_numpy()):
        raise DataQualityError("Saved estimator does not reproduce calibration predictions")
    residuals = ResidualCalibration(calibration["target_passing_yards"].to_numpy() - predictions)
    if not np.array_equal(residuals.residuals, calibration["residual"].to_numpy()) or manifest[
        "calibration_radii"
    ] != {str(c): residuals.radius(c) for c in COVERAGES}:
        raise DataQualityError("Saved residuals or interval radii differ")
    return manifest


def write_candidate(data_dir: Path, output_dir: Path, report_dir: Path) -> Path:
    directory = data_dir / "processed" / "2022_2023_2024"
    source = read_json(directory / "manifest.json")
    if source["feature_columns"] != list(FEATURE_COLUMNS):
        raise DataQualityError("Historical feature allowlist differs from the candidate")
    historical = load_frame(directory, source["table"])
    validate_model_table(historical)
    snapshot = load_snapshot(data_dir, [2022, 2023, 2024])
    if source["raw_manifest"]["datasets"] != snapshot.manifest["datasets"]:
        raise DataQualityError("Raw/processed snapshots differ; rebuild before preparation")
    table, context = add_context_features(historical, snapshot.player_stats, snapshot.schedules)
    bundle = prepare_candidate(
        table,
        output_dir,
        {
            "historical_manifest": source,
            "historical_manifest_sha256": sha256_file(directory / "manifest.json"),
            "raw_manifest": snapshot.manifest,
            "context": context,
        },
    )
    report = verify_candidate(bundle)
    write_json(report_dir / "candidate.json", report)
    counts = report["counts"]
    text = (
        "# Prepared QB passing-yards candidate\n\n"
        f"Prepared: {report['created_at_utc']}\n\n"
        "Status: **prepared, not frozen**. 2025 access stays closed; production stays disabled.\n\n"
        f"Bundle: `{bundle.resolve()}`\n\n"
        f"Policy SHA-256: `{report['policy_sha256']}`\n\n"
        f"Draft protocol SHA-256: `{report['holdout_protocol_sha256']}`\n\n"
        f"Development rows: {counts['development_rows']}; training: {counts['training_rows']}; "
        f"calibration: {counts['calibration_rows']}; embargoed: {counts['embargoed_rows']}.\n\n"
        f"Training results must be available before {report['training_cutoff_utc']}. "
        f"All development results must be available before {DEVELOPMENT_CUTOFF.isoformat()}.\n\n"
        "The saved model exactly reproduces its saved calibration predictions. "
        "Checksums cover the manifest, model, input/split tables, residuals and source archive. "
        "Each preparation creates a separate bundle.\n\n"
        "The draft 2025 protocol keeps this estimator and residual pool fixed for the season. "
        "Earlier available appearances may update lagged inputs, never model/calibration fits. "
        "Every recorded appearance remains in diagnostics; the five-game history subgroup is "
        "reported separately. This is participation-conditioned research, not starter validation. "
        "It differs from weekly-refit development evaluation.\n\n"
        "Preparation is retrospective and does not establish pregame source availability. "
        "Review and explicitly freeze artifacts/protocol before any holdout access. "
        "Prospective feature, identity, starter/active evidence and cohort validation "
        "remain open.\n"
    )
    (report_dir / "candidate.md").write_text(text, encoding="utf-8")
    return bundle
