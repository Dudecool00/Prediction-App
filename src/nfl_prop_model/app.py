"""Local research interface; no live predictions or external market connections."""

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import plotly.graph_objects as go
import polars as pl
import streamlit as st

from nfl_prop_model.data.prospective import load_feature_report, save_feature_snapshot
from nfl_prop_model.data.starter_audit import load_starter_audit
from nfl_prop_model.data.status_reviews import (
    checksum,
    evidence,
    review_context,
    save_status_review,
)
from nfl_prop_model.data.storage import load_frame, read_json
from nfl_prop_model.data.upcoming import load_upcoming_report
from nfl_prop_model.markets.journal import read_quote_records, save_quote_record
from nfl_prop_model.markets.odds import ManualMarket, parse_american
from nfl_prop_model.markets.quote import (
    HistoricalForecast,
    create_quote,
    load_historical_forecast,
    quote_markdown,
)
from nfl_prop_model.modeling.calibration_audit import load_calibration_audit
from nfl_prop_model.modeling.policy import history_policy_status

MODEL_LABELS = {
    "xgb_schedule": "XGBoost · QB + schedule",
    "xgb_context": "XGBoost · QB + schedule + opponent",
    "xgb_qb": "XGBoost · QB history",
    "ridge": "Ridge regression",
    "prior_five_mean": "Prior-five-game mean",
    "season_to_date_mean": "Season-to-date mean",
}


def research_catalog(data_dir: Path) -> tuple[dict[str, Any], pl.DataFrame]:
    directory = data_dir / "processed" / "research_2022_2024"
    manifest = read_json(directory / "manifest.json")
    if manifest.get("format_version") != 2:
        raise ValueError("Run nfl-prop research again to save reusable calibration records")
    predictions = load_frame(directory, manifest["artifacts"]["predictions"])
    identities = load_frame(directory, manifest["artifacts"]["features"]).select(
        "player_id", "game_id", "player_display_name", "team", "opponent_team"
    )
    catalog = predictions.drop("actual").join(
        identities, on=["player_id", "game_id"], validate="m:1", how="left"
    )
    if catalog["player_display_name"].null_count():
        raise ValueError("Some saved forecasts lack player identity")
    return manifest, catalog


def outcome_chart(forecast: HistoricalForecast, line: float | None = None) -> go.Figure:
    values = np.floor(forecast.point["prediction"] + forecast.calibration.residuals + 0.5)
    figure = go.Figure(
        go.Histogram(
            x=values, nbinsx=24, marker_color="#147D73", name="Calibration-derived samples"
        )
    )
    figure.add_vline(
        x=forecast.point["prediction"],
        line_dash="dash",
        line_color="#183431",
        annotation_text="Point projection",
    )
    if line is not None:
        figure.add_vline(
            x=line,
            line_color="#D47A36",
            annotation_text=f"Manual line {line:g}",
            annotation_position="top left",
        )
    figure.update_layout(
        height=280,
        margin={"l": 15, "r": 20, "t": 35, "b": 20},
        xaxis_title="Passing yards",
        yaxis_title="Sample count",
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return figure


def show_quote(quote: dict[str, Any], journal_dir: Path) -> None:
    st.subheader("Your manual-price comparison")
    left, right = st.columns(2)
    for side, column in (("over", left), ("under", right)):
        value = quote["sides"][side]
        with column.container(border=True):
            st.markdown(f"### {side.title()} {quote['market']['line']:g}")
            st.metric("Estimated EV", f"{value['estimated_ev_percent']:+.2f}%")
            st.caption(f"Manual price {value['american_odds']:+d} · expected profit per dollar")
            st.write(f"Model win, excluding pushes: **{value['conditional_win_probability']:.2%}**")
            st.write(
                "Break-even, excluding pushes: "
                f"**{value['break_even_conditional_probability']:.2%}**"
            )
            st.write(f"Probability edge: **{value['probability_edge_percentage_points']:+.2f} pp**")
            st.write(f"Fair American odds: **{value['fair_american_odds']:+.1f}**")
    probabilities = quote["probabilities"]
    st.caption(
        f"Unconditional outcomes: over {probabilities['over']:.2%} · "
        f"under {probabilities['under']:.2%} · push/refund {probabilities['push']:.2%}. "
        "Positive estimated EV is not a profitability guarantee."
    )
    if float(quote["market"]["line"]).is_integer():
        st.warning(
            "Integer-line push estimates are sparse and unvalidated; "
            "a zero estimate does not rule out a push."
        )
    evidence = quote["uncertainty"]["observed_coverage_history_group"]
    for row in evidence:
        st.info(
            f"For this history group, nominal 90% intervals covered {row['observed_coverage']:.1%} "
            f"of {row['n']:,} historical outcomes. This is not an individual-player guarantee."
        )
    save, download = st.columns(2)
    with save:
        if st.button("Save research snapshot", key="save_snapshot", width="stretch"):
            path = save_quote_record(journal_dir, quote)
            st.success(f"Saved a new snapshot: {path.name}")
    with download:
        st.download_button(
            "Download comparison JSON",
            json.dumps(quote, indent=2, allow_nan=False),
            file_name="research-comparison.json",
            mime="application/json",
            width="stretch",
        )
    with st.expander("Timestamps, model version, and full assumptions"):
        st.markdown(quote_markdown(quote).split("## Timestamps and model", 1)[1])


def comparison_page(
    data_dir: Path, journal_dir: Path, manifest: dict[str, Any], catalog: pl.DataFrame
) -> None:
    st.title("Quarterback passing yards")
    st.write("Compare manually entered prices with a saved historical forecast.")
    season_col, player_col, game_col = st.columns([1, 2, 2])
    with season_col:
        season = st.selectbox(
            "Season", sorted(catalog["season"].unique().to_list(), reverse=True), key="season"
        )
    available = catalog.filter(pl.col("season") == season)
    players = (
        available.select("player_id", "player_display_name").unique().sort("player_display_name")
    )
    names = dict(players.iter_rows())
    with player_col:
        player = st.selectbox(
            "Quarterback",
            players["player_id"].to_list(),
            format_func=lambda value: names[value],
            key="player",
        )
    games = (
        available.filter(pl.col("player_id") == player)
        .unique("game_id")
        .sort("kickoff_utc", "game_id")
    )
    game_labels = {
        row["game_id"]: f"Week {row['week']} · {row['team']} vs {row['opponent_team']}"
        for row in games.iter_rows(named=True)
    }
    with game_col:
        game = st.selectbox(
            "Game",
            games["game_id"].to_list(),
            format_func=lambda value: game_labels[value],
            key="game",
        )
    models = set(available["model"].to_list())
    model = st.selectbox(
        "Research model",
        [name for name in MODEL_LABELS if name in models],
        format_func=lambda value: MODEL_LABELS[value],
        key="model",
    )
    context = (
        manifest["artifacts"]["predictions"]["sha256"],
        manifest["artifacts"]["calibration_residuals"]["sha256"],
        manifest["research_source_sha256"],
        player,
        game,
        model,
    )
    if st.session_state.get("quote_context") != context:
        st.session_state.pop("quote", None)
        st.session_state["quote_context"] = context
    forecast = load_historical_forecast(data_dir, player, game, model)
    point = float(forecast.point["prediction"])
    radius = forecast.calibration.radius(0.9)
    a, b, c = st.columns(3)
    a.metric("Point projection", f"{point:.1f} yd")
    b.metric("Nominal 90% interval", f"{point - radius:.1f}–{point + radius:.1f}")
    c.metric("Calibration sample", f"{len(forecast.calibration.residuals):,} QB-games")
    st.caption(
        f"History: {forecast.point['history_bucket']} · "
        f"Conceptual prediction time: {forecast.point['prediction_time_utc'].isoformat()}"
    )
    if (
        history_policy_status(int(forecast.point["prior_games_in_sample"]))
        == "research_only_sparse_history"
    ):
        st.warning(
            "This QB has fewer than five prior model-sample games. Historical comparisons "
            "remain available for research; the candidate policy would abstain from prospective "
            "probability and EV for this history group."
        )
    with st.form("manual_market"):
        st.subheader("Enter a manual market")
        line_col, over_col, under_col = st.columns(3)
        line = line_col.number_input(
            "Passing-yards line", min_value=0.0, value=225.5, step=0.5, key="line"
        )
        over = over_col.text_input("Over American odds", value="-110", key="over_odds")
        under = under_col.text_input("Under American odds", value="-110", key="under_odds")
        sportsbook = st.text_input("Sportsbook or note (optional)", key="sportsbook")
        submitted = st.form_submit_button("Compare prices", type="primary", width="stretch")
    if submitted:
        st.session_state.pop("quote", None)
        try:
            market = ManualMarket(
                line, parse_american(over), parse_american(under), sportsbook or None
            )
            st.session_state["quote"] = create_quote(forecast, market)
        except ValueError as error:
            st.error(str(error))
    quote = st.session_state.get("quote")
    if quote is not None:
        show_quote(quote, journal_dir)
    st.plotly_chart(
        outcome_chart(forecast, quote["market"]["line"] if quote else None), width="stretch"
    )
    st.caption(
        "The histogram uses rounded point-plus-calibration-error samples. "
        "It is an approximation, not a guaranteed outcome range."
    )


def results_page(manifest: dict[str, Any]) -> None:
    st.title("Model results")
    st.write(
        "All six forecasts use the same chronological training, calibration, "
        "and evaluation cohorts."
    )
    counts = manifest["counts"]
    a, b, c = st.columns(3)
    a.metric("Evaluated QB-games", f"{counts['evaluated_qb_games']:,}")
    b.metric("Weekly folds", counts["weekly_folds"])
    c.metric("Evaluation rows dropped", counts["dropped_evaluation_rows"])
    points = pl.DataFrame(manifest["point_metrics"])
    overall = (
        points.filter(pl.col("scope") == "overall")
        .select("model", "mae", "rmse", "bias", "n")
        .sort("mae")
    )
    st.subheader("Point errors in yards")
    st.dataframe(overall, hide_index=True, width="stretch")
    model = st.selectbox(
        "Inspect model",
        list(MODEL_LABELS),
        format_func=lambda value: MODEL_LABELS[value],
        key="results_model",
    )
    st.subheader("90% interval coverage by history")
    history = pl.DataFrame(manifest["interval_by_history"]).filter(
        (pl.col("model") == model) & (pl.col("coverage") == 0.9)
    )
    st.dataframe(
        history.select("history_bucket", "n", "observed_coverage", "mean_width"),
        hide_index=True,
        width="stretch",
    )
    st.subheader("Probability calibration")
    line = st.selectbox("Diagnostic threshold", [150.5, 200.5, 250.5, 300.5], key="diagnostic_line")
    reliability = (
        pl.DataFrame(manifest["reliability"])
        .filter((pl.col("model") == model) & (pl.col("line") == line))
        .sort("bin")
    )
    figure = go.Figure(
        [
            go.Scatter(
                x=[0, 1],
                y=[0, 1],
                mode="lines",
                name="Perfect calibration",
                line={"dash": "dash", "color": "#8A9895"},
            ),
            go.Scatter(
                x=reliability["mean_predicted_probability"].to_list(),
                y=reliability["observed_over_rate"].to_list(),
                mode="lines+markers",
                name=MODEL_LABELS[model],
                customdata=reliability["n"].to_list(),
                hovertemplate=(
                    "Predicted %{x:.1%}<br>Observed %{y:.1%}<br>n=%{customdata}<extra></extra>"
                ),
                line={"color": "#147D73"},
            ),
        ]
    )
    figure.update_layout(
        height=350,
        xaxis={"range": [0, 1], "tickformat": ".0%", "title": "Predicted over probability"},
        yaxis={"range": [0, 1], "tickformat": ".0%", "title": "Observed over rate"},
        legend={"orientation": "h", "y": -0.25},
    )
    st.plotly_chart(figure, width="stretch")
    st.caption(
        "Fixed diagnostic thresholds, not historical sportsbook lines. "
        "Small bins and tail probabilities are uncertain."
    )
    with st.expander("Results by season and feature contribution"):
        st.dataframe(points.filter(pl.col("scope") == "season").drop("scope"), hide_index=True)
        st.dataframe(pl.DataFrame(manifest["feature_contributions"]), hide_index=True)


def calibration_audit_page(data_dir: Path) -> None:
    st.title("Calibration audit")
    st.write("Check uncertainty by each QB's available history before the evaluated game.")
    report = load_calibration_audit(data_dir)
    policy = report["policy"]
    st.info(
        "Development candidate: XGBoost with QB and schedule features. "
        "Below five prior model-sample games, the policy would abstain from prospective "
        "probability and EV. The policy is not frozen; 2025 is closed "
        "and forecasts remain unavailable."
    )
    a, b, c = st.columns(3)
    a.metric("Evaluated QB-games", report["counts"]["evaluated_qb_games"])
    b.metric("Weekly folds", report["counts"]["weekly_folds"])
    c.metric("Group residual minimum", policy["calibration"]["group_matched_minimum_rows"])
    model = st.selectbox(
        "Audit model",
        list(MODEL_LABELS),
        format_func=lambda value: MODEL_LABELS[value],
        key="audit_model",
    )
    coverage = st.selectbox(
        "Nominal interval coverage",
        [0.5, 0.8, 0.9],
        index=2,
        format_func=lambda value: f"{value:.0%}",
        key="audit_coverage",
    )
    metrics = pl.DataFrame(report["interval_metrics"]).filter(
        (pl.col("scope") == "overall")
        & (pl.col("model") == model)
        & (pl.col("coverage") == coverage)
    )
    st.subheader("Interval support and observed coverage")
    st.dataframe(
        metrics.select(
            "history_bucket",
            "n",
            "distinct_players",
            "distinct_games",
            "distinct_weeks",
            "pooled_coverage",
            "pooled_mean_width",
            "matched_available_n",
            "matched_coverage",
            "paired_pooled_coverage",
            "matched_mean_width",
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "History-matched intervals are diagnostics only. Matched and paired pooled coverage "
        "use the same available rows. Missing values mean insufficient group residuals. "
        "Rows, players, games, and overlapping calibration windows are dependent."
    )
    with st.expander("History-matched cohort support"):
        st.dataframe(
            metrics.select(
                "history_bucket",
                "matched_available_n",
                "matched_distinct_players",
                "matched_distinct_games",
                "matched_distinct_weeks",
            ),
            hide_index=True,
            width="stretch",
        )
    st.subheader("Probability diagnostics by history")
    line = st.selectbox("Audit threshold", [150.5, 200.5, 250.5, 300.5], key="audit_line")
    scores = pl.DataFrame(report["probability_scores"]).filter(
        (pl.col("model") == model) & (pl.col("line") == line)
    )
    st.dataframe(scores.drop("model", "line"), hide_index=True, width="stretch")
    history = st.selectbox(
        "Inspect history group", sorted(metrics["history_bucket"].to_list()), key="audit_history"
    )
    bins = (
        pl.DataFrame(report["reliability"])
        .filter(
            (pl.col("model") == model)
            & (pl.col("line") == line)
            & (pl.col("history_bucket") == history)
        )
        .sort("bin")
    )
    figure = go.Figure(
        [
            go.Scatter(
                x=[0, 1],
                y=[0, 1],
                mode="lines",
                name="Perfect calibration",
                line={"dash": "dash", "color": "#8A9895"},
            ),
            go.Scatter(
                x=bins["mean_probability"].to_list(),
                y=bins["observed_over_rate"].to_list(),
                mode="markers",
                name="Observed bins",
                customdata=bins["n"].to_list(),
                hovertemplate=(
                    "Predicted %{x:.1%}<br>Observed %{y:.1%}<br>n=%{customdata}<extra></extra>"
                ),
            ),
        ]
    )
    figure.update_layout(
        height=350,
        xaxis={"range": [0, 1], "tickformat": ".0%", "title": "Mean predicted over probability"},
        yaxis={"range": [0, 1], "tickformat": ".0%", "title": "Observed over frequency"},
    )
    st.plotly_chart(figure, width="stretch")
    st.dataframe(bins.drop("model", "line", "history_bucket"), hide_index=True, width="stretch")
    st.caption(
        "Fixed diagnostic thresholds, not sportsbook lines. Small bins and tails remain uncertain."
    )
    st.download_button(
        "Download calibration audit",
        json.dumps(report, indent=2, allow_nan=False),
        file_name="calibration-audit.json",
        mime="application/json",
    )
    with st.expander("Candidate policy, sources, and limits"):
        st.json(policy)
        st.caption(f"Policy SHA-256: {report['policy_sha256']}")
        for limitation in report["limitations"]:
            st.write(limitation)


def saved_page(journal_dir: Path) -> None:
    st.title("Saved research snapshots")
    st.write("Each save creates a new file. The app never edits or replaces earlier snapshots.")
    records = read_quote_records(journal_dir)
    if not records:
        st.info(
            "Compare a manual market, then select Save research snapshot "
            "to keep its inputs and results."
        )
        return
    selected = st.selectbox(
        "Snapshot",
        range(len(records)),
        format_func=lambda index: (
            f"{records[index]['logged_at_utc']} · "
            f"{records[index]['quote']['player']['player_display_name']} "
            f"· {records[index]['quote']['market']['line']:g} yd"
        ),
        key="saved_record",
    )
    record = records[selected]
    st.markdown(quote_markdown(record["quote"]))
    st.download_button(
        "Download saved snapshot",
        json.dumps(record, indent=2, allow_nan=False),
        file_name=f"snapshot-{record['record_id']}.json",
        mime="application/json",
    )


def status_review_form(data_dir: Path, candidate: dict[str, Any]) -> None:
    st.subheader("Record a status review")
    st.caption(
        "Record what a dated source says about this QB for this game. "
        "Links are saved for your review; the app does not verify their contents. "
        "Starter evidence expires after 24 hours and active evidence after 6 hours. "
        "Active does not establish health or playing time."
    )
    context = review_context(candidate)
    form_key = checksum(context)[:16]
    with st.form(f"status_review_{form_key}"):
        starter = st.selectbox(
            "Reported starter status",
            ["unknown", "confirmed", "not_starter"],
            format_func=lambda value: value.replace("_", " ").capitalize(),
            key=f"starter_{form_key}",
        )
        starter_url = st.text_input("Starter source URL", key=f"starter_url_{form_key}")
        starter_time = st.text_input(
            "Starter source publication time (ISO 8601 with time zone)",
            placeholder="2026-10-01T14:30:00-05:00",
            key=f"starter_time_{form_key}",
        )
        availability = st.selectbox(
            "Reported active status",
            ["unknown", "active", "inactive"],
            format_func=lambda value: value.capitalize(),
            key=f"availability_{form_key}",
        )
        active_url = st.text_input("Active status source URL", key=f"active_url_{form_key}")
        active_time = st.text_input(
            "Active source publication time (ISO 8601 with time zone)",
            placeholder="2026-10-01T14:30:00-05:00",
            key=f"active_time_{form_key}",
        )
        notes = st.text_area("Review notes", max_chars=2000, key=f"notes_{form_key}")
        st.caption(
            "Leave evidence fields empty for unknown status. A new review replaces both "
            "current statuses for this QB and keeps every saved record. To change starters, "
            "review the former starter too; two confirmed QBs on one team require resolution."
        )
        submitted = st.form_submit_button(
            "Save status review",
            disabled=not candidate["sources_fresh"],
        )
    if submitted:
        try:
            save_status_review(
                data_dir,
                candidate["game_id"],
                candidate["espn_id"],
                starter=evidence(starter, starter_url, starter_time),
                availability=evidence(availability, active_url, active_time),
                notes=notes,
                expected_context=context,
            )
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            st.session_state["status_review_saved"] = True
            st.rerun()
    review = candidate["status_review"]
    if review["record_ids"]:
        with st.expander("Latest saved review evidence"):
            st.json(review)


def upcoming_page(data_dir: Path) -> None:
    st.title("Upcoming quarterbacks")
    st.write("Match scheduled 2026 games to the latest cached ESPN-derived QB depth charts.")
    st.info(
        "Candidate review only. Depth rank does not confirm a starter or active status. "
        "Upcoming projections and EV are not available yet."
    )
    days = st.select_slider("Look ahead", options=[7, 14, 21, 28], value=14, key="upcoming_days")
    report = load_upcoming_report(data_dir, days=days)
    if st.session_state.pop("status_review_saved", False):
        st.success("Status review saved. The readiness report now includes it.")
    counts = report["counts"]
    a, b, c = st.columns(3)
    a.metric("Scheduled games", counts["upcoming_games"])
    b.metric("QB / game candidates", counts["candidate_rows"])
    c.metric("Cache age", f"{report['cache_age_hours']:.1f} h")
    st.caption(
        f"Checked {report['as_of_utc']} · retrieved {report['sources']['retrieved_at_utc']}. "
        "Fresh means retrieval within 24 hours and team chart within 48 hours."
    )
    if report["cache_age_hours"] > 24 or counts["fresh_candidate_rows"] < counts["candidate_rows"]:
        st.warning("Some sources need refreshing. Review chart timestamps before using this list.")
    if counts["unknown_kickoff_games"]:
        st.caption(
            f"{counts['unknown_kickoff_games']} season games have an unknown kickoff; "
            "they are excluded from the dated list and retained in the download."
        )
    with st.expander("Refresh the local sources"):
        st.code("nfl-prop upcoming --refresh", language="text")
        st.write("Run this in the repository terminal, then reload this page.")
    candidates = report["candidates"]
    if not candidates:
        st.info("No dated, unscored games appear in this window. Check source freshness.")
    else:
        games = list(dict.fromkeys(row["game_id"] for row in candidates))
        game = st.selectbox("Scheduled game", games, key="upcoming_game")
        rows = [row for row in candidates if row["game_id"] == game]
        st.caption(f"Scheduled kickoff: {rows[0]['kickoff_utc']}")
        st.dataframe(
            [
                {
                    "Team": row["team"],
                    "Quarterback": row["player_name"],
                    "Depth rank": row["depth_rank"],
                    "Source status": "Fresh" if row["sources_fresh"] else "Refresh needed",
                    "Chart age (hours)": round(row["chart_age_hours"], 1),
                    "2022–2024 games": row["development_games"],
                    "History": row["history_status"].replace("_", " "),
                    "Starter review": row["status_review"]["starter"].replace("_", " "),
                    "Active review": row["status_review"]["availability"].replace("_", " "),
                }
                for row in rows
            ],
            hide_index=True,
            width="stretch",
        )
        labels = {row["espn_id"]: f"{row['team']} · {row['player_name']}" for row in rows}
        selected = st.selectbox(
            "Review quarterback",
            list(labels),
            format_func=lambda key: labels[key],
            key="upcoming_qb",
        )
        candidate = next(row for row in rows if row["espn_id"] == selected)
        st.write("Needs review:")
        for reason in candidate["review_reasons"]:
            st.write(f"• {reason.replace('_', ' ').capitalize()}")
        st.caption(
            "Development-history counts describe 2022–2024, not current form. "
            "Newcomers and QBs without an ID mapping remain visible."
        )
        status_review_form(data_dir, candidate)
    st.download_button(
        "Download readiness report",
        json.dumps(report, indent=2, allow_nan=False),
        file_name="upcoming-qb-readiness.json",
        mime="application/json",
    )
    with st.expander("Sources and limits"):
        st.markdown(
            "[ESPN-derived depth charts](https://github.com/nflverse/nflverse-data/releases/tag/depth_charts)"
            " · [nflverse schedules](https://github.com/nflverse/nfldata/blob/master/data/games.csv)"
        )
        for item in report["limitations"]:
            st.write(item)


def starter_coverage_page(data_dir: Path) -> None:
    from nfl_prop_model.data.starter_coverage import load_starter_coverage

    st.title("Full historical starter coverage")
    st.info(
        "Retrospective development audit. Original training rows and upcoming forecasts "
        "are unchanged."
    )
    report = load_starter_coverage(data_dir)
    counts = report["counts"]
    a, b, c = st.columns(3)
    a.metric("Completed team-games", counts["completed_team_game_slots"])
    b.metric("Resolved starter slots", counts["resolved_slots"])
    c.metric("Unresolved slots", counts["needs_review"])
    st.caption(
        f"{counts['verified_schedule_labels']} schedule labels agree; "
        f"{counts['corrected_schedule_labels']} differ. "
        f"All {counts['historical_qb_rows']} recorded QB rows remain in the descriptive overlay."
    )
    st.write(f"Recorded appearances: {report['appearance_counts']}")
    unresolved = st.checkbox("Show unresolved slots only", key="coverage_unresolved")
    cases = [
        row for row in report["cases"] if not unresolved or row["status"].startswith("unresolved_")
    ]
    st.dataframe(
        [
            {
                "Game": row["game_id"],
                "Team": row["team"],
                "Schedule QB": row["scheduled_name"],
                "Roster starter": row["reconciled_name"],
                "Status": row["status"],
                "Detail": row["error"],
            }
            for row in cases
        ],
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "Download full starter coverage",
        json.dumps(report, indent=2, allow_nan=False),
        file_name="development-starter-coverage.json",
        mime="application/json",
    )
    with st.expander("Evidence and limits"):
        st.caption(
            f"Evidence manifest published {report['evidence_manifest']['retrieved_at_utc']}."
        )
        st.json(report["rules"])
        for item in report["limitations"]:
            st.write(item)
        st.code("nfl-prop starter-coverage --collect", language="text")


def starter_audit_page(data_dir: Path) -> None:
    st.title("Historical starter audit")
    st.write("Reconcile schedule-listed QBs missing from the 2022–2024 statistics table.")
    st.info("Historical roster evidence. These corrections do not confirm upcoming starters.")
    report = load_starter_audit(data_dir)
    counts = report["counts"]
    a, b, c = st.columns(3)
    a.metric("Flagged starter labels", counts["flagged_starter_labels"])
    b.metric("Reconciled labels", counts["corrected_schedule_labels"])
    c.metric("Needs review", counts["needs_review"])
    st.caption(
        f"Evidence retrieved {report['evidence_manifest']['retrieved_at_utc']}. "
        f"{counts['unflagged_slots_not_independently_verified']} other team-game labels "
        "remain unverified. Historical training data is unchanged."
    )
    st.dataframe(
        [
            {
                "Game": row["game_id"],
                "Team": row["team"],
                "Schedule-listed QB": row["scheduled_name"],
                "ESPN-listed starter": row["reconciled_name"],
                "Resolution": row["status"].replace("_", " "),
            }
            for row in report["cases"]
        ],
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "Download starter audit",
        json.dumps(report, indent=2, allow_nan=False),
        file_name="historical-starter-audit.json",
        mime="application/json",
    )
    with st.expander("Sources and limits"):
        for item in report["limitations"]:
            st.write(item)
        for row in report["cases"]:
            st.markdown(
                f"{row['game_id']} · {row['team']}: "
                f"[ESPN event roster]({row['sources']['roster']['source']})"
            )


def claim_review_page(data_dir: Path) -> None:
    from nfl_prop_model.data.claim_review import claim_review_queue, save_claim_review
    from nfl_prop_model.data.status_evidence import article_text, validate_evidence

    st.title("Pregame evidence review")
    st.write("Read the archived article and judge whether it supports the recorded QB claim.")
    st.info("Reviews retain the original expiry. Upcoming forecasts remain disabled.")
    if message := st.session_state.pop("claim_review_saved", None):
        st.success(message)
    report = claim_review_queue(data_dir)
    rows = report["evidence"]
    st.caption(f"Checked {report['as_of_utc']} · reviewer names are self-reported.")
    if not rows:
        st.info("No primary articles have been archived yet.")
        st.code("nfl-prop capture-status --help", language="text")
        return
    st.dataframe(
        [
            {
                "Game": row["evidence"]["context"]["game_id"],
                "QB": row["evidence"]["player_name"],
                "Claim": f"{row['evidence']['kind']}: {row['evidence']['claim']}",
                "Review": row["review_state"],
                "Readiness": row["readiness"],
                "Fresh until (UTC)": row["fresh_until_utc"],
            }
            for row in rows
        ],
        hide_index=True,
        width="stretch",
    )
    selected = st.selectbox(
        "Archived claim",
        [row["evidence"]["record_id"] for row in rows],
        format_func=lambda value: next(
            f"{row['evidence']['player_name']} · {row['evidence']['kind']} · "
            f"{row['evidence']['context']['game_id']}"
            for row in rows
            if row["evidence"]["record_id"] == value
        ),
        key="claim_archive",
    )
    row = next(row for row in rows if row["evidence"]["record_id"] == selected)
    archived = row["evidence"]
    raw = (data_dir / "status_evidence" / f"evidence-{selected}" / "article.html").read_bytes()
    validate_evidence(archived, raw)
    text, _ = article_text(raw)
    st.link_button("Open official source", archived["final_url"])
    st.write(f"Recorded claim: {archived['kind']} / {archived['claim']}")
    st.write(f"Excerpt: {archived['quote']}")
    st.caption(
        f"Published {archived['published_at_utc']} · archived {archived['retrieved_at_utc']}. "
        "The live page may have changed; the text below is the saved version."
    )
    st.text_area(
        "Archived article text", text, height=240, disabled=True, key=f"article_{selected}"
    )
    with st.expander("Review history"):
        st.json(row["review_history"])
    if not row["review_allowed"]:
        st.warning(
            f"This claim cannot receive a pregame review: {row['readiness'].replace('_', ' ')}."
        )
    form_key = f"claim_{selected}_{checksum(archived)}"
    with st.form(form_key):
        reviewer = st.text_input("Reviewer name", max_chars=100, key=f"{form_key}_reviewer")
        verdict = st.selectbox(
            "Does the archived article support this exact claim?",
            ["unclear", "supported", "contradicted"],
            key=f"{form_key}_verdict",
        )
        notes = st.text_area("Reason for verdict", max_chars=2000, key=f"{form_key}_notes")
        checked = st.checkbox(
            "I read the archived article and checked the player and game context.",
            key=f"{form_key}_read",
        )
        submitted = st.form_submit_button("Save claim review", disabled=not row["review_allowed"])
    if submitted:
        if not checked:
            st.error("Read the archived article and confirm the context before saving.")
        else:
            try:
                saved = save_claim_review(
                    data_dir,
                    selected,
                    reviewer=reviewer,
                    verdict=verdict,
                    notes=notes,
                    expected_evidence_sha256=checksum(archived),
                )
            except (OSError, ValueError) as error:
                st.error(str(error))
            else:
                st.session_state["claim_review_saved"] = f"Saved a new review: {saved.name}"
                st.rerun()
    st.caption(
        "A later review by the same named reviewer replaces their displayed verdict; "
        "the full history is retained. Different reviewers' disagreements remain conflicts. "
        "A supported claim does not certify independent validation or gameday participation."
    )
    st.download_button(
        "Download claim review queue",
        json.dumps(report, indent=2, allow_nan=False),
        file_name="pregame-claim-reviews.json",
        mime="application/json",
    )


def current_features_page(data_dir: Path) -> None:
    st.title("Current feature audit")
    st.write("Inspect available QB history and schedule inputs for upcoming 2026 games.")
    st.info(
        "Research features only. Starter/active evidence and the participation cohort "
        "still need validation. Upcoming predictions and EV remain disabled."
    )
    days = st.select_slider("Look ahead", options=[7, 14, 21, 28], value=14, key="feature_days")
    report = load_feature_report(data_dir, days=days)
    counts = report["counts"]
    a, b, c = st.columns(3)
    a.metric("QB / game candidates", counts["candidate_rows"])
    b.metric("Feature checks passed", counts["features_ready_rows"])
    c.metric("Five available sample games", counts["history_minimum_rows"])
    st.caption(
        f"Checked {report['as_of_utc']} · current stats age "
        f"{report['current_stats_age_hours']:.1f} h. "
        "Available means both retrieved and at least 24 hours after kickoff."
    )
    rows = report["candidates"]
    if rows:
        labels = {
            f"{row['game_id']}:{row['espn_id']}": (
                f"{row['game_id']} · {row['team']} · {row['player_name']}"
            )
            for row in rows
        }
        selected = st.selectbox(
            "Candidate", list(labels), format_func=lambda key: labels[key], key="feature_candidate"
        )
        row = next(row for row in rows if f"{row['game_id']}:{row['espn_id']}" == selected)
        st.dataframe(
            [
                {
                    "Feature": name,
                    "Value": str(row["features"][name]),
                    "Available at (UTC)": row["feature_available_at_utc"][name],
                }
                for name in report["feature_columns"]
            ],
            hide_index=True,
            width="stretch",
        )
        st.write("Forecast checks still open:")
        for reason in row["forecast_blockers"]:
            st.write(f"• {reason.replace('_', ' ').capitalize()}")
        with st.expander("History, rest and status evidence"):
            st.json(
                {
                    "history": row["history_audit"],
                    "rest": row["rest_audit"],
                    "status": row["status_review"],
                    "archived_primary_articles": row.get("primary_status", {}),
                }
            )
    else:
        st.info("No dated, unscored games appear in this window.")
    if st.button("Save feature snapshot", key="save_features"):
        directory = save_feature_snapshot(data_dir, report)
        st.success(f"Saved a new research snapshot: {directory.name}")
    st.download_button(
        "Download feature audit",
        json.dumps(report, indent=2, allow_nan=False),
        file_name="current-qb-features.json",
        mime="application/json",
    )
    with st.expander("Sources and limits"):
        st.code("nfl-prop current-features --refresh", language="text")
        for item in report["limitations"]:
            st.write(item)
    with st.expander("Enrolled participation candidates"):
        from nfl_prop_model.data.roster_reconciliation import reconcile_rosters

        registries = sorted((data_dir / "participation").glob("cohort-*"))
        st.caption(
            "Pregame enrollment fixes every candidate, including abstentions. "
            "Missing stats never become zero targets."
        )
        if registries:
            selected_registry = st.selectbox(
                "Registry", registries, format_func=lambda path: path.name
            )
            audit = reconcile_rosters(data_dir, selected_registry)
            st.write(f"{audit['candidate_count']} enrolled candidates; outcomes: {audit['counts']}")
            st.write(f"Roster checks: {audit['roster_counts']}")
            st.dataframe(
                [
                    {
                        "Game": row["context"]["game_id"],
                        "Team": row["context"]["team"],
                        "QB": row["player_name"],
                        "QB stats": row["state"],
                        "Roster check": row["roster_reconciliation"]["state"],
                        "Starter annotation": row["roster_reconciliation"]["starter_claim_result"],
                    }
                    for row in audit["candidates"]
                ],
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "Roster agreement does not verify article meaning or gameday inactive status. "
                "Forecasts stay disabled."
            )
        else:
            st.write("No candidate registry has been enrolled yet.")
        st.code(
            "nfl-prop register-participation --snapshot data/prospective/snapshot-…\n"
            "nfl-prop participation-reconcile --registry data/participation/cohort-… --refresh",
            language="text",
        )


def main() -> None:
    st.set_page_config(page_title="QB Research | Prediction App", page_icon="🏈", layout="wide")
    data_dir = Path(os.environ.get("NFL_PROP_DATA_DIR", "data"))
    journal_dir = data_dir / "journal"
    st.sidebar.title("QB Research")
    st.sidebar.caption("NFL passing yards · local workspace")
    page = st.sidebar.radio(
        "Workspace",
        [
            "Compare a line",
            "Upcoming QBs",
            "Current features",
            "Pregame evidence review",
            "Starter audit",
            "Starter coverage",
            "Model results",
            "Calibration audit",
            "Saved snapshots",
        ],
        key="page",
    )
    if st.session_state.get("last_page") != page:
        st.session_state.pop("quote", None)
        st.session_state["last_page"] = page
    st.sidebar.info("The frozen 2025 diagnostic is complete. Upcoming forecasts remain disabled.")
    st.sidebar.caption("Manual prices only. No sportsbook connection or automatic betting.")
    st.caption("RESEARCH PREVIEW")
    try:
        if page == "Upcoming QBs":
            upcoming_page(data_dir)
        elif page == "Current features":
            current_features_page(data_dir)
        elif page == "Pregame evidence review":
            claim_review_page(data_dir)
        elif page == "Starter audit":
            starter_audit_page(data_dir)
        elif page == "Starter coverage":
            starter_coverage_page(data_dir)
        elif page == "Calibration audit":
            calibration_audit_page(data_dir)
        else:
            manifest, catalog = research_catalog(data_dir)
            st.sidebar.caption("Historical replay · 2023–2024 recorded QB appearances")
            st.sidebar.caption(f"Data retrieved: {manifest['context_sources']['retrieved_at_utc']}")
            st.sidebar.caption(f"Model source: {manifest['research_source_sha256'][:12]}")
            if page == "Compare a line":
                comparison_page(data_dir, journal_dir, manifest, catalog)
            elif page == "Model results":
                results_page(manifest)
            else:
                saved_page(journal_dir)
    except (OSError, ValueError, pl.exceptions.PolarsError) as error:
        st.error(str(error))
        st.info("Prepare this page's local cache from the repository terminal:")
        st.code(
            "nfl-prop upcoming --refresh"
            if page in {"Upcoming QBs", "Pregame evidence review"}
            else "nfl-prop current-features --refresh"
            if page == "Current features"
            else "nfl-prop starter-audit --refresh"
            if page == "Starter audit"
            else "nfl-prop starter-coverage --collect"
            if page == "Starter coverage"
            else "nfl-prop fetch\nnfl-prop build\nnfl-prop research",
            language="text",
        )
    st.divider()
    st.caption(
        "Estimates may be wrong and may lose money. Historical starter labels, weather, and "
        "limited-history calibration remain open research work. No production model is enabled."
    )
