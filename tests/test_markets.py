from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from nfl_prop_model.markets.audit import settlement_scores
from nfl_prop_model.markets.distribution import passing_yard_probabilities
from nfl_prop_model.markets.odds import (
    ManualMarket,
    OutcomeProbabilities,
    american_to_decimal,
    break_even_probability,
    evaluate_market,
    fair_american,
    parse_american,
    side_value,
)
from nfl_prop_model.modeling.uncertainty import ResidualCalibration


@pytest.mark.parametrize(
    "odds,decimal,probability",
    [
        (100, 2.0, 0.5),
        (-100, 2.0, 0.5),
        (150, 2.5, 0.4),
        (-200, 1.5, 2 / 3),
        (-110, 21 / 11, 11 / 21),
    ],
)
def test_prices_and_break_even_match_known_values(odds, decimal, probability):
    assert american_to_decimal(odds) == pytest.approx(decimal)
    assert break_even_probability(odds) == pytest.approx(probability)


@pytest.mark.parametrize("text", ["0", "-99", "+99", "-110.5", "EVEN", "nan", "1e3", "", "--110"])
def test_bad_american_text_is_rejected(text):
    with pytest.raises(ValueError):
        parse_american(text)


def test_price_parser_and_numeric_validation():
    assert parse_american(" +150 ") == 150
    assert parse_american("-110") == -110
    assert parse_american("125") == 125
    for price in (True, 100.5, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            american_to_decimal(price)
    with pytest.raises(ValueError, match="finite decimal payout"):
        american_to_decimal(-(10**100))
    with pytest.raises(ValueError, match="finite numeric range"):
        fair_american(1e-308)


@pytest.mark.parametrize("probability,price", [(0.4, 150), (0.5, -100), (0.6, -150)])
def test_fair_odds(probability, price):
    assert fair_american(probability) == pytest.approx(price)
    assert fair_american(0.55) == pytest.approx(-122.22222222222223)


@pytest.mark.parametrize("probability", [0, 1, -0.1, 1.1, float("nan"), float("inf"), True])
def test_fair_odds_reject_undefined_or_invalid_inputs(probability):
    with pytest.raises(ValueError):
        fair_american(probability)


def test_expected_value_with_and_without_pushes():
    simple = side_value(0.55, 0.45, 0, -110)
    assert simple.estimated_ev_per_dollar == pytest.approx(0.05)
    assert simple.probability_edge_percentage_points == pytest.approx((0.55 - 11 / 21) * 100)
    push = side_value(0.5, 0.4, 0.1, 150)
    assert push.estimated_ev_per_dollar == pytest.approx(0.35)
    assert push.conditional_win_probability == pytest.approx(5 / 9)
    assert push.break_even_unconditional_probability == pytest.approx(0.36)
    assert push.fair_decimal_odds == pytest.approx(1.8)
    assert push.fair_american_odds == pytest.approx(-125)


def test_all_push_and_probability_boundaries_are_explicit():
    value = side_value(0, 0, 1, -110)
    assert value.estimated_ev_per_dollar == 0
    assert value.conditional_win_probability is None
    assert value.fair_american_odds is None
    assert value.probability_edge_percentage_points is None
    assert side_value(0, 1, 0, 100).estimated_ev_per_dollar == -1
    assert side_value(1, 0, 0, 100).estimated_ev_per_dollar == 1


def test_both_sides_and_ev_edge_identity_without_rounding():
    for push in (0, 0.01, 0.2):
        for conditional in (0.01, 0.4, 0.5, 0.53781, 0.99):
            probabilities = OutcomeProbabilities(
                (1 - push) * conditional, (1 - push) * (1 - conditional), push
            )
            market = ManualMarket(200, -110, 125)
            result = evaluate_market(market, probabilities)
            for value in result.values():
                expected = (
                    (1 - push) * value.decimal_odds * value.probability_edge_percentage_points / 100
                )
                assert value.estimated_ev_per_dollar == pytest.approx(expected, abs=1e-14)
            assert result["over"].model_win_probability == probabilities.over
            assert result["under"].model_win_probability == probabilities.under


def test_market_rejects_invalid_lines_notes_and_probability_vectors():
    for line in (float("nan"), float("inf"), -1, 225.25, True, "225.5"):
        with pytest.raises(ValueError):
            ManualMarket(line, -110, -110)
    for probabilities in ((0.6, 0.5, 0), (-0.1, 1.1, 0), (float("nan"), 0, 0)):
        with pytest.raises(ValueError):
            OutcomeProbabilities(*probabilities)
    market = ManualMarket(200, -110, 110, " Example ", -3.5, 45.5)
    assert market.sportsbook == "Example"
    for changes in ({"game_spread": float("inf")}, {"game_total": 0}, {"sportsbook": "\n"}):
        with pytest.raises(ValueError):
            replace(market, **changes)
    with pytest.raises(ValueError, match="half-yard"):
        evaluate_market(replace(market, line=200.5), OutcomeProbabilities(0.4, 0.5, 0.1))


def test_integer_and_half_yard_settlement_preserve_total_mass():
    calibration = ResidualCalibration(np.array([-1.0, -0.5, 0.0, 0.49, 0.5, 1.0]))
    # Rounded pseudo-outcomes: 99, 100, 100, 100, 101, 101.
    integer = passing_yard_probabilities(100, calibration, 100)
    assert integer.over == pytest.approx(2.5 / 7)
    assert integer.under == pytest.approx(1.5 / 7)
    assert integer.push == pytest.approx(3 / 7)
    half = passing_yard_probabilities(100, calibration, 100.5)
    assert half.over == pytest.approx(2.5 / 7)
    assert half.under == pytest.approx(4.5 / 7)
    assert half.push == 0
    assert half.under == pytest.approx(integer.under + integer.push)
    assert passing_yard_probabilities(100, calibration, 99.5).over == pytest.approx(
        integer.over + integer.push
    )


def test_residual_settlement_is_monotone_keeps_negative_yards_and_has_bounded_tails():
    calibration = ResidualCalibration(np.array([-150.0, -1.0, 0.0, 1.0, 50.0]))
    probabilities = [passing_yard_probabilities(100, calibration, line) for line in range(201)]
    assert probabilities[0].under > 0  # negative passing yards were not clipped away
    assert all(0 < value.over < 1 and 0 < value.under < 1 for value in probabilities)
    assert all(
        left.over >= right.over
        for left, right in zip(probabilities, probabilities[1:], strict=False)
    )
    for line in (-1, 1.25, float("nan")):
        with pytest.raises(ValueError):
            passing_yard_probabilities(100, calibration, line)
    with pytest.raises(ValueError):
        passing_yard_probabilities(float("inf"), calibration, 100)


def test_integer_audit_exposes_zero_probability_pushes():
    predictions = pl.DataFrame(
        {
            "player_id": ["QB"],
            "game_id": ["game"],
            "model": ["example"],
            "fold": ["week"],
            "season": [2023],
            "prediction": [200.0],
            "actual": [200.0],
        }
    )
    residuals = pl.DataFrame(
        {"fold": ["week"] * 3, "model": ["example"] * 3, "residual": [-20.0, -10.0, 10.0]}
    )
    probabilities, pushes = settlement_scores(predictions, residuals)
    assert probabilities.height == 8
    at_200 = pushes.filter(pl.col("line") == 200).row(0, named=True)
    assert at_200["pushes_given_zero_probability"] == 1
    assert at_200["mean_predicted_push_probability"] == 0
    assert at_200["observed_push_rate"] == 1
    assert at_200["push_brier"] == 1
    with pytest.raises(ValueError, match="whole-yard"):
        settlement_scores(predictions.with_columns(pl.lit(200.25).alias("actual")), residuals)
