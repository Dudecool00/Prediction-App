"""Chronological feature/parameter, loss and constrained nonlinear comparisons."""

import hashlib
import warnings
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_games import load_team_cache
from nfl_prop_model.features.team_epa import HORIZONS, TEAM_FEATURES, matchup_features
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import (
    forecast_rows,
    model_source_hash,
    predict_models,
    runtime_versions,
    score_metrics,
    validate_features,
)

CONTEXT = ("home_field", "rest_difference", "home_prior_games", "away_prior_games")
ALL_EPA = tuple(
    f"{side}_{kind}_epa_{window}"
    for side in ("home", "away")
    for kind in ("off", "def")
    for window in ("recent", "season")
)
SEASON_EPA = tuple(
    f"{side}_{kind}_epa_season" for side in ("home", "away") for kind in ("off", "def")
)


@dataclass(frozen=True)
class RefinementProfile:
    name: str
    features: tuple[str, ...] = TEAM_FEATURES
    family: str = "ridge"
    alpha: float = 20.0
    loss: str = "squared_error"

    def __post_init__(self) -> None:
        if self.family not in {"ridge", "elastic", "median", "boosting"}:
            raise ValueError("Unknown refinement estimator")
        if (
            not self.features
            or len(set(self.features)) != len(self.features)
            or set(self.features) - set(TEAM_FEATURES)
        ):
            raise ValueError("Use unique canonical features")
        if not np.isfinite(self.alpha) or self.alpha <= 0:
            raise ValueError("Regularization must be positive")
        if self.loss not in {"squared_error", "absolute_error"}:
            raise ValueError("Unsupported refinement loss")

    def policy(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "features": list(self.features),
            "alpha": None if self.family == "boosting" else self.alpha,
            "targets": "scores" if self.family in {"ridge", "elastic"} else "margin,total",
        }


PROFILES = (
    RefinementProfile("ridge_epa_scores"),
    RefinementProfile("ridge_compact_12", ALL_EPA + CONTEXT),
    RefinementProfile("ridge_compact_8", SEASON_EPA + CONTEXT),
    RefinementProfile(
        "ridge_without_history_counts", tuple(c for c in TEAM_FEATURES if c not in CONTEXT[2:])
    ),
    RefinementProfile("ridge_alpha_200", alpha=200),
    RefinementProfile("ridge_alpha_2000", alpha=2000),
    RefinementProfile("elastic_alpha_01", family="elastic", alpha=0.1),
    RefinementProfile("elastic_alpha_05", family="elastic", alpha=0.5),
    RefinementProfile("median_compact_8", SEASON_EPA + CONTEXT, "median", 0.1, "absolute_error"),
    RefinementProfile("boost_squared_compact_8", SEASON_EPA + CONTEXT, "boosting"),
    RefinementProfile(
        "boost_absolute_compact_8", SEASON_EPA + CONTEXT, "boosting", loss="absolute_error"
    ),
    RefinementProfile("median_alpha_001", SEASON_EPA + CONTEXT, "median", 0.01, "absolute_error"),
    RefinementProfile("median_alpha_0001", SEASON_EPA + CONTEXT, "median", 0.001, "absolute_error"),
)
SELECTED_MODEL = "chronologically_refined_scores"
REFINEMENT_POLICY = {
    "profiles": [p.policy() for p in PROFILES],
    "elastic": "joint score-target MultiTaskElasticNet, l1_ratio 0.5, max_iter 10000, tol 1e-6",
    "median": "separate 0.5-quantile margin/total regressions, alpha 0.1/0.01/0.001, HiGHS solver",
    "median_followup": "two weaker penalties added after the initial pre-2025 training audit "
    "found only one active encoded input at alpha 0.1; initial eleven-profile results retained",
    "boosting": "margin/total models, 80 iterations, learning_rate 0.05, seven leaves, depth 3, "
    "min_samples_leaf 40, l2 10, no random early-stopping split",
    "selection": "preceding two completed NFL seasons; early-week margin MAE; "
    "lock before first early forecast",
    "guards": "score MAE <= control +0.05, total MAE <= control +0.10, margin gain >=0.05",
    "parsimonious": "one bootstrap standard error above best eligible MAE; prefer linear, "
    "fewer fitted active encoded features, stronger Ridge, then fixed family/profile order",
    "standard_error": "2000 season-week block resamples, seed 20261014; "
    "descriptive selection heuristic",
    "refresh": "same season-locked choice; refit on strictly available outcomes at each forecast",
    "complexity": "encoded features include missing indicators; rank and Ridge effective df "
    "audited; tree leaves are not comparable linear parameter counts",
    "evaluation": "2022-2024 development; 2025 locked-choice diagnostic, already accessed",
    "promotion": "no automatic forecast change or untouched-holdout claim",
}


def refinement_source_hash() -> str:
    return hashlib.sha256(model_source_hash().encode() + Path(__file__).read_bytes()).hexdigest()


def validate_profiles(profiles: tuple[RefinementProfile, ...]) -> None:
    if (
        not profiles
        or profiles[0] != PROFILES[0]
        or len({p.name for p in profiles}) != len(profiles)
    ):
        raise ValueError("Profiles must be unique and begin with the unchanged EPA control")


def candidate_pipeline(profile: RefinementProfile) -> Any:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import MultiTaskElasticNet, QuantileRegressor, Ridge
    from sklearn.multioutput import MultiOutputRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    if profile.family == "ridge":
        estimator: Any = Ridge(alpha=profile.alpha)
    elif profile.family == "elastic":
        estimator = MultiTaskElasticNet(
            alpha=profile.alpha, l1_ratio=0.5, max_iter=10000, tol=1e-6, selection="cyclic"
        )
    elif profile.family == "median":
        estimator = MultiOutputRegressor(
            QuantileRegressor(quantile=0.5, alpha=profile.alpha, solver="highs")
        )
    else:
        estimator = MultiOutputRegressor(
            HistGradientBoostingRegressor(
                loss=profile.loss,
                max_iter=80,
                learning_rate=0.05,
                max_leaf_nodes=7,
                max_depth=3,
                min_samples_leaf=40,
                l2_regularization=10,
                early_stopping=False,
                random_state=20261014,
            )
        )
    return Pipeline(
        [
            (
                "imputer",
                SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
            ),
            ("scaler", StandardScaler()),
            ("regressor", estimator),
        ]
    )


def target_matrix(frame: pl.DataFrame, profile: RefinementProfile) -> NDArray[np.float64]:
    scores = np.asarray(frame.select("home_score", "away_score").to_numpy(), dtype=np.float64)
    if profile.family in {"median", "boosting"}:
        return np.column_stack((scores[:, 0] - scores[:, 1], scores.sum(axis=1)))
    return scores


def predicted_scores(
    model: Any, frame: pl.DataFrame, profile: RefinementProfile
) -> NDArray[np.float64]:
    result = np.asarray(
        model.predict(np.asarray(frame.select(profile.features).to_numpy(), dtype=np.float64)),
        dtype=np.float64,
    )
    if profile.family in {"median", "boosting"}:
        result = np.column_stack(
            ((result[:, 1] + result[:, 0]) / 2, (result[:, 1] - result[:, 0]) / 2)
        )
    if not np.isfinite(result).all():
        raise DataQualityError("Nonfinite refinement predictions")
    return result


def fit_candidate(train: pl.DataFrame, profile: RefinementProfile) -> Any:
    from sklearn.exceptions import ConvergenceWarning

    validate_features(train)
    if train.height < 64:
        raise DataQualityError("At least 64 earlier games required for refinement")
    targets = target_matrix(train, profile)
    if not np.isfinite(targets).all():
        raise DataQualityError("Refinement targets must be finite")
    model = candidate_pipeline(profile)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(
                np.asarray(train.select(profile.features).to_numpy(), dtype=np.float64), targets
            )
    except ConvergenceWarning as exc:
        raise DataQualityError(f"Refinement fit did not converge: {profile.name}") from exc
    return model


def predict_candidate(
    train: pl.DataFrame, test: pl.DataFrame, profile: RefinementProfile
) -> NDArray[np.float64]:
    validate_features(test)
    cutoff = test["prediction_time_utc"].min()
    if train.filter(pl.col("result_available_at_utc") >= cutoff).height or set(
        train["game_id"]
    ) & set(test["game_id"]):
        raise DataQualityError("Refinement training unavailable or overlaps test games")
    return predicted_scores(fit_candidate(train, profile), test, profile)


def complexity_audit(train: pl.DataFrame, profile: RefinementProfile) -> dict[str, Any]:
    model = fit_candidate(train, profile)
    x = np.asarray(train.select(profile.features).to_numpy(), dtype=np.float64)
    z = np.asarray(model[:-1].transform(x), dtype=np.float64)
    names = list(model.named_steps["imputer"].get_feature_names_out(profile.features))
    centered = z - z.mean(axis=0)
    scales = centered.std(axis=0)
    standardized = np.divide(centered, scales, out=np.zeros_like(centered), where=scales > 0)
    correlation = standardized.T @ standardized / len(z)
    estimator = model.named_steps["regressor"]
    record: dict[str, Any] = {
        "profile": profile.name,
        "family": profile.family,
        "raw_features": len(profile.features),
        "encoded_features": z.shape[1],
        "training_games": train.height,
        "matrix_rank": int(np.linalg.matrix_rank(centered)),
        "high_correlations": [
            {"first": names[i], "second": names[j], "correlation": float(correlation[i, j])}
            for i in range(len(names))
            for j in range(i + 1, len(names))
            if abs(correlation[i, j]) >= 0.9
        ],
        "encoded_feature_names": names,
        "ridge_effective_df_per_output": None,
        "allocated_linear_coefficients_and_intercepts": None,
        "active_encoded_features": None,
        "tree_leaves": None,
    }
    if profile.family == "boosting":
        record["tree_leaves"] = sum(
            int(tree[0].get_n_leaf_nodes())
            for reg in estimator.estimators_
            for tree in reg._predictors
        )
        record["selection_active_features"] = z.shape[1]
    else:
        coefficients = np.asarray(
            estimator.coef_
            if profile.family in {"ridge", "elastic"}
            else [reg.coef_ for reg in estimator.estimators_],
            dtype=np.float64,
        )
        active = np.any(np.abs(coefficients) > 1e-8, axis=0)
        record["allocated_linear_coefficients_and_intercepts"] = coefficients.size + 2
        record["active_encoded_features"] = int(active.sum())
        record["active_feature_names"] = [
            name for name, keep in zip(names, active, strict=True) if keep
        ]
        record["selection_active_features"] = int(active.sum())
        if profile.family == "ridge":
            singular = np.linalg.svd(centered, compute_uv=False)
            record["ridge_effective_df_per_output"] = float(
                1 + np.sum(singular**2 / (singular**2 + profile.alpha))
            )
    scores = predicted_scores(model, train, profile)
    actual = np.asarray(train.select("home_score", "away_score").to_numpy(), dtype=np.float64)
    record["in_sample_margin_mae"] = float(
        np.mean(np.abs((scores[:, 0] - scores[:, 1]) - (actual[:, 0] - actual[:, 1])))
    )
    return record


def select_refinements(
    panel: pl.DataFrame,
    locks: dict[int, datetime],
    audits: list[dict[str, Any]],
    *,
    profiles: tuple[RefinementProfile, ...] = PROFILES,
) -> list[dict[str, Any]]:
    validate_profiles(profiles)
    require_keys(panel, ["game_id", "horizon", "model"], "refinement selection panel")
    result = []
    names = [p.name for p in profiles]
    for season, lock in sorted(locks.items()):
        validation = panel.filter(
            (pl.col("horizon") == "early_week")
            & pl.col("season").is_in([season - 2, season - 1])
            & pl.col("model").is_in(names)
        )
        if (
            set(validation["season"]) != {season - 2, season - 1}
            or validation.filter(pl.col("result_available_at_utc") >= lock).height
        ):
            raise DataQualityError("Both earlier validation seasons must precede the lock")
        cohort = set(validation.filter(pl.col("model") == names[0])["game_id"])
        if not cohort or any(
            set(validation.filter(pl.col("model") == name)["game_id"]) != cohort for name in names
        ):
            raise DataQualityError("Refinement validation cohorts differ")
        metrics = score_metrics(validation).filter(pl.col("scope") == "overall").to_dicts()
        by_name = {row["model"]: row for row in metrics}
        control = by_name[names[0]]
        eligible = [
            p
            for p in profiles
            if by_name[p.name]["score_mae"] <= control["score_mae"] + 0.05
            and by_name[p.name]["total_mae"] <= control["total_mae"] + 0.10
        ]
        best = min(eligible, key=lambda p: (by_name[p.name]["margin_mae"], names.index(p.name)))
        week_errors = (
            validation.filter(pl.col("model") == best.name)
            .with_columns(
                (pl.col("predicted_home_margin") - pl.col("target_home_margin"))
                .abs()
                .alias("error")
            )
            .group_by("season", "week")
            .agg(pl.col("error").sum(), pl.len().alias("games"))
            .sort("season", "week")
        )
        rng = np.random.default_rng(20261014)
        indexes = rng.integers(0, week_errors.height, size=(2000, week_errors.height))
        samples = week_errors["error"].to_numpy()[indexes].sum(axis=1) / week_errors[
            "games"
        ].to_numpy()[indexes].sum(axis=1)
        standard_error = float(samples.std(ddof=1))
        near_best = [
            p
            for p in eligible
            if by_name[p.name]["margin_mae"] <= by_name[best.name]["margin_mae"] + standard_error
            and control["margin_mae"] - by_name[p.name]["margin_mae"] >= 0.05
        ]
        lookup = {row["profile"]: row for row in audits if row["season"] == season}
        if set(lookup) != set(names) or any(
            datetime.fromisoformat(row["latest_training_result_available_utc"]) >= lock
            for row in lookup.values()
        ):
            raise DataQualityError("Refinement complexity audit unavailable at lock")

        def simplicity(
            p: RefinementProfile, fitted: dict[str, dict[str, Any]] = lookup
        ) -> tuple[Any, ...]:
            return (
                p.family == "boosting",
                fitted[p.name]["selection_active_features"],
                -p.alpha if p.family == "ridge" else 0,
                {"ridge": 0, "elastic": 1, "median": 2, "boosting": 3}[p.family],
                names.index(p.name),
            )

        selected = min(near_best, key=simplicity) if near_best else profiles[0]
        result.append(
            {
                "season": season,
                "selected_profile": selected.name,
                "best_validation_profile": best.name,
                "lock_utc": lock.isoformat(),
                "validation_seasons": [season - 2, season - 1],
                "validation_games": len(cohort),
                "validation_metrics": metrics,
                "best_margin_standard_error": standard_error,
                "selection_gain": control["margin_mae"] - by_name[selected.name]["margin_mae"],
                "selected_complexity": lookup[selected.name]["selection_active_features"],
            }
        )
    return result


def evaluate_refinements(
    teams: pl.DataFrame,
    games: pl.DataFrame,
    *,
    evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024, 2025),
    profiles: tuple[RefinementProfile, ...] = PROFILES,
) -> tuple[
    pl.DataFrame, pl.DataFrame, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    validate_profiles(profiles)
    if not evaluation_seasons or min(evaluation_seasons) < 2018 or max(evaluation_seasons) > 2025:
        raise ValueError("Use completed refinement seasons between 2018 and 2025")
    chosen_games = games.filter(pl.col("season") <= max(evaluation_seasons))
    chosen_teams = teams.filter(pl.col("season") <= max(evaluation_seasons))
    early = matchup_features(chosen_teams, chosen_games, schedule_history=games)
    candidate_years = set(year - offset for year in evaluation_seasons for offset in (1, 2))
    candidate_years.update(year for year in evaluation_seasons if year < 2025)
    candidate_games = early.filter(
        pl.col("season").is_in(candidate_years) & pl.col("home_score").is_not_null()
    )
    panel_records, folds, audits = [], [], []
    for test in candidate_games.partition_by("prediction_time_utc", maintain_order=True):
        cutoff = test["prediction_time_utc"][0]
        train = early.filter(
            (pl.col("result_available_at_utc") < cutoff) & pl.col("home_score").is_not_null()
        )
        values = predict_models(train, test)
        for profile in profiles[1:]:
            values[profile.name] = predict_candidate(train, test, profile)
        records = forecast_rows(test, values, train)
        availability = dict(test.select("game_id", "result_available_at_utc").iter_rows())
        for record in records:
            record["result_available_at_utc"] = availability[record["game_id"]]
        panel_records.extend(records)
        folds.append(
            {
                "stage": "candidate_validation",
                "cutoff_utc": cutoff.isoformat(),
                "training_games": train.height,
                "test_games": test.height,
            }
        )
        if test["week"][0] == 1:
            print(f"Refinement candidate season {test['season'][0]}", flush=True)
    panel = pl.DataFrame(panel_records, infer_schema_length=None)
    locks: dict[int, datetime] = {}
    for season in evaluation_seasons:
        lock = early.filter(pl.col("season") == season)["prediction_time_utc"].min()
        if not isinstance(lock, datetime):
            raise DataQualityError("Missing refinement season lock")
        locks[season] = lock
        train = early.filter(
            (pl.col("result_available_at_utc") < lock) & pl.col("home_score").is_not_null()
        )
        latest = train["result_available_at_utc"].max()
        if not isinstance(latest, datetime):
            raise DataQualityError("Missing refinement audit availability")
        for profile in profiles:
            audits.append(
                {
                    "season": season,
                    "lock_utc": lock.isoformat(),
                    "latest_training_result_available_utc": latest.isoformat(),
                    **complexity_audit(train, profile),
                }
            )
    selections = select_refinements(panel, locks, audits, profiles=profiles)
    lookup = {p.name: p for p in profiles}
    selected_by_year = {s["season"]: lookup[s["selected_profile"]] for s in selections}
    records = []
    for horizon in HORIZONS:
        features = (
            early
            if horizon == "early_week"
            else matchup_features(
                chosen_teams, chosen_games, horizon=horizon, schedule_history=games
            )
        )
        evaluation = features.filter(
            pl.col("season").is_in(evaluation_seasons) & pl.col("home_score").is_not_null()
        )
        for test in evaluation.partition_by("prediction_time_utc", maintain_order=True):
            cutoff, season = test["prediction_time_utc"][0], test["season"][0]
            train = features.filter(
                (pl.col("result_available_at_utc") < cutoff) & pl.col("home_score").is_not_null()
            )
            values = predict_models(train, test)
            profile = selected_by_year[season]
            values[SELECTED_MODEL] = (
                values["ridge_epa_scores"]
                if profile == profiles[0]
                else predict_candidate(train, test, profile)
            )
            for record in forecast_rows(test, values, train):
                record["selected_profile"] = (
                    profile.name if record["model"] == SELECTED_MODEL else record["model"]
                )
                records.append(record)
            latest = train["result_available_at_utc"].max()
            if not isinstance(latest, datetime):
                raise DataQualityError("Missing refinement fit availability")
            folds.append(
                {
                    "stage": horizon,
                    "season": season,
                    "cutoff_utc": cutoff.isoformat(),
                    "training_games": train.height,
                    "test_games": test.height,
                    "latest_training_result_available_utc": latest.isoformat(),
                }
            )
    predictions = pl.DataFrame(records, infer_schema_length=None)
    require_keys(predictions, ["game_id", "horizon", "model"], "refined predictions")
    expected = set(
        chosen_games.filter(
            pl.col("season").is_in(evaluation_seasons) & pl.col("home_score").is_not_null()
        )["game_id"]
    )
    if any(
        set(group["game_id"]) != expected for group in predictions.partition_by("horizon", "model")
    ):
        raise DataQualityError("Refinement output cohort differs from completed schedule")
    return predictions, panel, selections, audits, folds


def refinement_comparisons(predictions: pl.DataFrame) -> list[dict[str, Any]]:
    records = []
    for scope, years in (("development", [2022, 2023, 2024]), ("diagnostic_2025", [2025])):
        for horizon in HORIZONS:
            group = predictions.filter(
                pl.col("season").is_in(years) & (pl.col("horizon") == horizon)
            ).with_columns(
                (pl.col("predicted_home_margin") - pl.col("target_home_margin"))
                .abs()
                .alias("error")
            )
            if group.is_empty():
                continue
            control = group.filter(pl.col("model") == "ridge_epa_scores").select(
                "game_id", pl.col("error").alias("control_error")
            )
            candidate = group.filter(pl.col("model") == SELECTED_MODEL)
            paired = candidate.join(control, on="game_id", validate="1:1").with_columns(
                (pl.col("control_error") - pl.col("error")).alias("gain")
            )
            if paired.height != control.height or paired.height != candidate.height:
                raise DataQualityError("Paired refinement cohorts differ")
            blocks = (
                paired.group_by("season", "week")
                .agg(pl.col("gain").sum(), pl.len().alias("games"))
                .sort("season", "week")
            )
            rng = np.random.default_rng(20261014)
            indexes = rng.integers(0, blocks.height, size=(5000, blocks.height))
            samples = blocks["gain"].to_numpy()[indexes].sum(axis=1) / blocks["games"].to_numpy()[
                indexes
            ].sum(axis=1)
            lower, upper = np.quantile(samples, [0.025, 0.975])
            records.append(
                {
                    "scope": scope,
                    "horizon": horizon,
                    "games": paired.height,
                    "margin_mae_gain": float(paired["gain"].to_numpy().mean()),
                    "week_block_gain_lower": float(lower),
                    "week_block_gain_upper": float(upper),
                }
            )
    return records


def save_refinements(
    data_dir: Path,
    report_dir: Path,
    *,
    evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024, 2025),
) -> dict[str, Any]:
    teams, games, sources = load_team_cache(data_dir)
    directory = data_dir / "processed" / "team_refinement"
    write_json(
        directory / "protocol.json",
        {
            "locked_at_utc": datetime.now(UTC).isoformat(),
            "policy": REFINEMENT_POLICY,
            "refinement_source_sha256": refinement_source_hash(),
            "evaluation_seasons": list(evaluation_seasons),
        },
    )
    predictions, panel, selections, audits, folds = evaluate_refinements(
        teams, games, evaluation_seasons=evaluation_seasons
    )
    report: dict[str, Any] = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "refinement_source_sha256": refinement_source_hash(),
        "policy": REFINEMENT_POLICY,
        "runtime": runtime_versions(),
        "sources": sources,
        "evaluation_seasons": list(evaluation_seasons),
        "metrics": score_metrics(predictions).to_dicts(),
        "candidate_metrics": score_metrics(panel).to_dicts(),
        "paired_comparisons": refinement_comparisons(predictions),
        "selections": selections,
        "complexity_audits": audits,
        "folds": folds,
        "artifacts": {
            "predictions": store_frame(directory, "predictions", predictions),
            "candidate_panel": store_frame(directory, "candidate_panel", panel),
        },
    }
    write_json(directory / "manifest.json", report)
    write_json(report_dir / "team_refinement.json", report)
    lines = [
        "# EPA feature and model refinement",
        "",
        "Thirteen candidates; settings chosen on preceding two seasons and locked for both "
        "forecast horizons. The one-standard-error heuristic favors simpler fitted models among "
        "eligible candidates. No model promotion is automatic.",
        "Two weaker median penalties follow the initial training-only underfitting audit. "
        "The initial eleven-profile comparison is retained separately.",
        "",
        "2022–2024 remain development; 2025 is already accessed diagnostic data. "
        "Scores/margins remain consistent.",
        "",
        "## Season choices",
        "",
        *markdown_table(
            pl.DataFrame(
                [
                    {
                        k: s[k]
                        for k in (
                            "season",
                            "selected_profile",
                            "best_validation_profile",
                            "selection_gain",
                            "best_margin_standard_error",
                            "selected_complexity",
                        )
                    }
                    for s in selections
                ]
            )
        ),
        "",
        "## Following-season metrics",
        "",
        *markdown_table(pl.DataFrame(report["metrics"]).drop("scope")),
        "",
        "## Complexity before each season",
        "",
        *markdown_table(
            pl.DataFrame(
                [
                    {
                        k: a[k]
                        for k in (
                            "season",
                            "profile",
                            "raw_features",
                            "encoded_features",
                            "training_games",
                            "matrix_rank",
                            "allocated_linear_coefficients_and_intercepts",
                            "active_encoded_features",
                            "ridge_effective_df_per_output",
                            "tree_leaves",
                            "in_sample_margin_mae",
                        )
                    }
                    for a in audits
                ]
            )
        ),
        "",
        "## Early-week candidate metrics (development, not locked-choice performance)",
        "",
        *markdown_table(pl.DataFrame(report["candidate_metrics"]).drop("scope")),
        "",
        "Validation uncertainty and outcome comparisons are descriptive; serial dependence and "
        "repeated development access are not corrected. Historical source revisions/publication "
        "are not reconstructed.",
        "",
        "## Paired locked-choice gains",
        "",
        *markdown_table(pl.DataFrame(report["paired_comparisons"])),
    ]
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "team_refinement.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def load_refinements(data_dir: Path) -> tuple[dict[str, Any], pl.DataFrame]:
    directory = data_dir / "processed" / "team_refinement"
    report = read_json(directory / "manifest.json")
    if report["refinement_source_sha256"] != refinement_source_hash():
        raise DataQualityError("Refinement model changed; run team-refine again")
    return report, load_frame(directory, report["artifacts"]["predictions"])
