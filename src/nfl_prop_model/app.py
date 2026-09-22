"""Local research interface; no live predictions or external market connections."""

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import plotly.graph_objects as go
import polars as pl
import streamlit as st

from nfl_prop_model.data.storage import load_frame, read_json
from nfl_prop_model.markets.journal import read_quote_records, save_quote_record
from nfl_prop_model.markets.odds import ManualMarket, parse_american
from nfl_prop_model.markets.quote import (
    HistoricalForecast,
    create_quote,
    load_historical_forecast,
    quote_markdown,
)

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


def main() -> None:
    st.set_page_config(page_title="QB Research | Prediction App", page_icon="🏈", layout="wide")
    data_dir = Path(os.environ.get("NFL_PROP_DATA_DIR", "data"))
    journal_dir = data_dir / "journal"
    st.sidebar.title("QB Research")
    st.sidebar.caption("NFL passing yards · local workspace")
    page = st.sidebar.radio(
        "Workspace", ["Compare a line", "Model results", "Saved snapshots"], key="page"
    )
    if st.session_state.get("last_page") != page:
        st.session_state.pop("quote", None)
        st.session_state["last_page"] = page
    st.sidebar.info(
        "Historical replay · 2023–2024\n\n2025 is reserved. "
        "Upcoming-game forecasts are not available yet."
    )
    st.sidebar.caption("Manual prices only. No sportsbook connection or automatic betting.")
    st.caption("RESEARCH PREVIEW · RECORDED QB APPEARANCES")
    try:
        manifest, catalog = research_catalog(data_dir)
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
        st.info("Prepare the verified local research cache from the repository terminal:")
        st.code("nfl-prop fetch\nnfl-prop build\nnfl-prop research", language="text")
    st.divider()
    st.caption(
        "Estimates may be wrong and may lose money. Historical starter labels, weather, and "
        "limited-history calibration remain open research work. No production model is selected."
    )
