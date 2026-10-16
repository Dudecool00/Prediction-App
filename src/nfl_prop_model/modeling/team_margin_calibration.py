"""Forecast-defined margin diagnostics; descriptive only, with no prediction changes."""

import argparse
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, sha256_file, write_json
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.modeling import team_compact_replay as replay
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import score_metrics
from nfl_prop_model.modeling.team_walkforward import load_walkforward_development

BANDS = ("0_to_under_3", "3_to_under_7", "7_to_under_14", "14_or_more")
PERIODS = {"2023": (2023,), "2024": (2024,), "development": (2023, 2024), "2025": (2025,)}
POLICY: dict[str, Any] = {
    "bands": list(BANDS),
    "reference": "compact_epa",
    "periods": PERIODS,
    "binning": "absolute predicted EPA margin; identical game IDs for every method",
    "fits": 0,
    "corrections": "none; OLS coefficients are descriptive only",
    "bootstrap": {"draws": 5000, "seed": 20261015, "blocks": "season/week"},
    "minimum_predictor_sd_for_slope": 1.0,
    "limits": "adaptive already-examined data; refresh duplicates Tuesday; sparse bands",
    "enriched_replay_ready": False,
}


def _mean(values: pl.Series) -> float:
    return float(np.mean(values.to_numpy()))


def forecast_bands(panel: pl.DataFrame, reference: str = "compact_epa") -> pl.DataFrame:
    require_keys(panel, ["game_id", "horizon", "model"], "calibration panel")
    if reference not in set(panel["model"]) or set(panel["horizon"]) != set(HORIZONS):
        raise DataQualityError("Calibration requires its reference and both horizons")
    numeric = ("home_score", "away_score", "target_home_margin", *replay.SCORES)
    if panel.filter(
        pl.any_horizontal(pl.col(numeric).is_null() | ~pl.col(numeric).is_finite())
    ).height:
        raise DataQualityError("Calibration scores must be finite and known")
    if panel.filter(
        ((pl.col("home_score") - pl.col("away_score") - pl.col("target_home_margin")).abs() > 1e-8)
        | (
            (
                pl.col("predicted_home_score")
                - pl.col("predicted_away_score")
                - pl.col("predicted_home_margin")
            ).abs()
            > 1e-8
        )
        | ((pl.col("predicted_home_margin") + pl.col("predicted_away_margin")).abs() > 1e-8)
        | (
            (
                pl.col("predicted_home_score")
                + pl.col("predicted_away_score")
                - pl.col("predicted_total")
            ).abs()
            > 1e-8
        )
    ).height:
        raise DataQualityError("Calibration score/margin/total contract mismatch")
    facts = ("season", "week", "home_score", "away_score", "target_home_margin")
    if (
        panel.group_by("game_id")
        .agg(pl.col(facts).n_unique())
        .select(pl.any_horizontal(pl.col(facts) != 1).any())
        .item()
    ):
        raise DataQualityError("Calibration methods disagree on game facts")
    expected = set(panel["game_id"])
    for group in panel.partition_by("model", "horizon"):
        if set(group["game_id"]) != expected:
            raise DataQualityError("Calibration methods must share the same game cohort")
    ref = panel.filter(pl.col("model") == reference).select(
        "game_id", "horizon", pl.col("predicted_home_margin").alias("reference_margin")
    )
    magnitude = pl.col("reference_margin").abs()
    ref = ref.with_columns(
        pl.when(magnitude < 3)
        .then(pl.lit(BANDS[0]))
        .when(magnitude < 7)
        .then(pl.lit(BANDS[1]))
        .when(magnitude < 14)
        .then(pl.lit(BANDS[2]))
        .otherwise(pl.lit(BANDS[3]))
        .alias("band"),
        pl.when(pl.col("reference_margin") > 0)
        .then(1.0)
        .when(pl.col("reference_margin") < 0)
        .then(-1.0)
        .otherwise(None)
        .alias("favorite_sign"),
    )
    return panel.join(ref, on=["game_id", "horizon"], validate="m:1")


def linear_calibration(rows: pl.DataFrame) -> dict[str, Any]:
    x = rows["predicted_home_margin"].to_numpy()
    y = rows["target_home_margin"].to_numpy()
    sd = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    result: dict[str, Any] = {
        "games": len(x),
        "predicted_margin_sd": sd,
        "intercept": None,
        "slope": None,
        "slope_week_block_95_lower": None,
        "slope_week_block_95_upper": None,
    }
    if sd < POLICY["minimum_predictor_sd_for_slope"]:
        return result
    slope = float(np.sum((x - x.mean()) * (y - y.mean())) / np.sum((x - x.mean()) ** 2))
    result.update(intercept=float(y.mean() - slope * x.mean()), slope=slope)
    blocks = (
        rows.group_by("season", "week")
        .agg(
            pl.len().alias("n"),
            pl.col("predicted_home_margin").sum().alias("x"),
            pl.col("target_home_margin").sum().alias("y"),
            (pl.col("predicted_home_margin") ** 2).sum().alias("xx"),
            (pl.col("predicted_home_margin") * pl.col("target_home_margin")).sum().alias("xy"),
        )
        .sort("season", "week")
    )
    rng = np.random.default_rng(POLICY["bootstrap"]["seed"])
    indexes = rng.integers(0, blocks.height, (POLICY["bootstrap"]["draws"], blocks.height))
    n, sx, sy, sxx, sxy = (
        blocks[k].to_numpy()[indexes].sum(axis=1) for k in ("n", "x", "y", "xx", "xy")
    )
    denominator = sxx - sx * sx / n
    valid = denominator > 1e-8
    slopes = (sxy[valid] - sx[valid] * sy[valid] / n[valid]) / denominator[valid]
    if len(slopes):
        low, high = np.quantile(slopes, [0.025, 0.975])
        result.update(slope_week_block_95_lower=float(low), slope_week_block_95_upper=float(high))
    return result


def analyze(panel: pl.DataFrame, reference: str = "compact_epa") -> dict[str, Any]:
    annotated = forecast_bands(panel, reference)
    band_rows, calibration, coverage = [], [], []
    for period, seasons in PERIODS.items():
        current = annotated.filter(pl.col("season").is_in(seasons))
        if current.is_empty():
            continue
        for horizon in HORIZONS:
            predictions = current.filter(pl.col("horizon") == horizon)
            refs = predictions.filter(pl.col("model") == reference)
            for band in BANDS:
                coverage.append(
                    {
                        "period": period,
                        "horizon": horizon,
                        "band": band,
                        "games": refs.filter(pl.col("band") == band).height,
                    }
                )
            for model_rows in predictions.partition_by("model"):
                model = model_rows["model"][0]
                calibration.append(
                    {
                        "period": period,
                        "horizon": horizon,
                        "model": model,
                        **linear_calibration(model_rows),
                    }
                )
                for group in model_rows.partition_by("band"):
                    metric = (
                        score_metrics(group).filter(pl.col("scope") == "overall").row(0, named=True)
                    )
                    errors = (group["predicted_home_margin"] - group["target_home_margin"]).abs()
                    favorite = group.filter(pl.col("favorite_sign").is_not_null())
                    actual = favorite["favorite_sign"] * favorite["target_home_margin"]
                    band_rows.append(
                        {
                            "period": period,
                            "band": group["band"][0],
                            **metric,
                            "mean_predicted_home_margin": _mean(group["predicted_home_margin"]),
                            "mean_actual_home_margin": _mean(group["target_home_margin"]),
                            "within_3": _mean(errors <= 3),
                            "within_7": _mean(errors <= 7),
                            "within_14": _mean(errors <= 14),
                            "reference_predicted_ties": group.height - favorite.height,
                            "reference_favorite_games": favorite.height,
                            "reference_favorite_predicted_margin": _mean(
                                favorite["reference_margin"].abs()
                            )
                            if favorite.height
                            else None,
                            "reference_favorite_actual_margin": _mean(actual)
                            if favorite.height
                            else None,
                            "reference_favorite_win_fraction": _mean(actual > 0)
                            if favorite.height
                            else None,
                        }
                    )
    return {"bands": band_rows, "calibration": calibration, "coverage": coverage}


def load_panels(data_dir: Path) -> tuple[pl.DataFrame, dict[str, Any]]:
    path = data_dir / "processed" / "team_walkforward" / "manifest.json"
    development = load_walkforward_development(data_dir)
    early = load_frame(path.parent, development["artifacts"]["predictions"]).filter(
        pl.col("model").is_in(replay.MODELS)
    )
    paths = list(
        (data_dir / "processed" / "team_compact_replay").glob(
            f"{replay.source_hash()[:12]}-*/manifest.json"
        )
    )
    if len(paths) != 1:
        raise DataQualityError("Require one current frozen compact replay")
    replay_path = paths[0]
    saved = replay.load_compact_replay(replay_path, data_dir)
    late = load_frame(replay_path.parent, saved["artifacts"]["predictions"])
    # Older predictions retain timing in parent features/receipts, not these columns.
    columns = [c for c in replay.FACTS if c not in {"week_lock_utc", "result_available_at_utc"}]
    columns += list(replay.SCORES) + ["model"]
    panel = pl.concat([early.select(columns), late.select(columns)], how="vertical_relaxed")
    if panel.filter(pl.col("horizon") == "early_week")["game_id"].n_unique() != 816:
        raise DataQualityError("Expected all 816 games from 2023-2025")
    refs = {
        "development_manifest_sha256": sha256_file(path),
        "replay_manifest_sha256": sha256_file(replay_path),
        "replay_source_sha256": saved["source_sha256"],
        "freeze_sha256": saved["freeze_sha256"],
    }
    return panel, refs


def write_charts(report: dict[str, Any], path: Path) -> None:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    rows = pl.DataFrame(report["bands"])
    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=(
            "EPA favorite: predicted vs actual margin",
            "2025 margin MAE on the same EPA-defined bands",
        ),
    )
    for period in ("development", "2025"):
        selected = rows.filter(
            (pl.col("period") == period)
            & (pl.col("horizon") == "early_week")
            & (pl.col("model") == "compact_epa")
        )
        fig.add_trace(
            go.Scatter(
                x=selected["reference_favorite_predicted_margin"].to_list(),
                y=selected["reference_favorite_actual_margin"].to_list(),
                text=[f"{r['band']}: n={r['games']}" for r in selected.to_dicts()],
                mode="markers+text",
                textposition="top center",
                name=period,
            ),
            row=1,
            col=1,
        )
    fig.add_trace(
        go.Scatter(
            x=[0, 22],
            y=[0, 22],
            mode="lines",
            line={"dash": "dash", "color": "gray"},
            showlegend=False,
            hoverinfo="skip",
        ),
        row=1,
        col=1,
    )
    models = list(replay.MODELS) + sorted(set(rows["model"]) - set(replay.MODELS))
    for model in models:
        selected = rows.filter(
            (pl.col("period") == "2025")
            & (pl.col("horizon") == "early_week")
            & (pl.col("model") == model)
        )
        values = [
            selected.filter(pl.col("band") == b)["margin_mae"][0]
            if selected.filter(pl.col("band") == b).height
            else None
            for b in BANDS
        ]
        fig.add_trace(go.Bar(x=list(BANDS), y=values, name=model), row=1, col=2)
    fig.update_xaxes(title_text="Mean EPA predicted favorite margin", row=1, col=1)
    fig.update_yaxes(title_text="Mean actual favorite margin", row=1, col=1)
    fig.update_yaxes(title_text="Margin MAE (points)", row=1, col=2)
    fig.update_layout(
        template="plotly_white",
        height=650,
        barmode="group",
        title="Forecast-defined calibration · every outcome retained · no corrections fitted",
    )
    fig.write_html(path, include_plotlyjs="cdn")


def save_analysis(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    panel, refs = load_panels(data_dir)
    report = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "policy": POLICY,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "inputs": refs,
        **analyze(panel),
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "results.json", report)
    for key in ("bands", "calibration", "coverage"):
        pl.DataFrame(report[key], infer_schema_length=None).write_csv(report_dir / f"{key}.csv")
    write_charts(report, report_dir / "comparison.html")
    lines = [
        "# Forecast-defined margin calibration",
        "",
        "Every game retained; bands use Tuesday EPA forecasts, not actual results. "
        "Same games for every method. No prediction correction or model fit. "
        "Development and 2025 were already examined; these are descriptive diagnostics.",
        "",
    ]
    bands = pl.DataFrame(report["bands"], infer_schema_length=None).filter(
        (pl.col("horizon") == "early_week") & (pl.col("period") == "2025")
    )
    lines += markdown_table(bands.select("band", "model", "games", "margin_mae", "within_7"))
    lines += ["", "## Descriptive calibration", ""]
    cal = pl.DataFrame(report["calibration"], infer_schema_length=None).filter(
        (pl.col("horizon") == "early_week") & pl.col("period").is_in(["development", "2025"])
    )
    lines += markdown_table(cal.drop("horizon"))
    lines += [
        "",
        "An ideal mean-calibration slope is 1 with intercept 0. Ranges use resampled "
        "season/week blocks and do not account for earlier adaptive exploration. Near-constant "
        "forecasts have no reported slope. Empty bands remain in coverage.csv. Sparse bands "
        "and forecast ties must not be interpreted as established confidence levels.",
        "",
        "[Interactive chart](comparison.html). Favorite statistics orient actual margins "
        "toward the EPA favorite; predicted ties are excluded only from favorite summaries. "
        "All score errors retain them. Early and refresh results are separate duplicate "
        "forecasts, not independent evidence. The enriched replay remains gated.",
    ]
    (report_dir / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--report-dir", type=Path, default=Path("reports/local/team_margin_calibration")
    )
    args = parser.parse_args()
    save_analysis(args.data_dir, args.report_dir)
    print(f"Saved forecast-defined calibration: {args.report_dir / 'findings.md'}")


if __name__ == "__main__":
    main()
