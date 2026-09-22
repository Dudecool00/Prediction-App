"""American prices, fair prices, and push-aware profit per dollar staked."""

import math
import re
from dataclasses import dataclass


def finite_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def validate_american(odds: int) -> int:
    if isinstance(odds, bool) or not isinstance(odds, int) or abs(odds) < 100:
        raise ValueError("American odds must be an integer at or below -100 or at or above +100")
    finite_number(odds, "American odds")
    return odds


def parse_american(text: str) -> int:
    if not re.fullmatch(r"[+-]?[0-9]+", text.strip()):
        raise ValueError("American odds must be a signed or unsigned integer, such as -110 or +125")
    return validate_american(int(text))


def american_to_decimal(odds: int) -> float:
    price = validate_american(odds)
    decimal = 1 + price / 100 if price > 0 else 1 + 100 / abs(price)
    if not math.isfinite(decimal) or decimal <= 1:
        raise ValueError("Price cannot be represented with a finite decimal payout above one")
    return decimal


def break_even_probability(odds: int) -> float:
    """Required win probability among resolved bets (excluding refunds)."""
    return 1 / american_to_decimal(odds)


def fair_american(probability: float) -> float:
    probability = finite_number(probability, "Probability")
    if not 0 < probability < 1:
        raise ValueError("Finite fair American odds require a probability strictly between 0 and 1")
    if probability >= 0.5:
        price = -100 * probability / (1 - probability)
    else:
        price = 100 * (1 - probability) / probability
    if not math.isfinite(price):
        raise ValueError("Fair American odds exceed the finite numeric range")
    return price


@dataclass(frozen=True)
class OutcomeProbabilities:
    over: float
    under: float
    push: float = 0.0

    def __post_init__(self) -> None:
        values = [finite_number(getattr(self, name), name) for name in ("over", "under", "push")]
        if any(not 0 <= value <= 1 for value in values):
            raise ValueError("Outcome probabilities must lie between zero and one")
        if not math.isclose(math.fsum(values), 1, rel_tol=0, abs_tol=1e-12):
            raise ValueError("Over, under, and push probabilities must sum to one")


@dataclass(frozen=True)
class ManualMarket:
    line: float
    over_american: int
    under_american: int
    sportsbook: str | None = None
    game_spread: float | None = None
    game_total: float | None = None

    def __post_init__(self) -> None:
        line = finite_number(self.line, "Passing-yards line")
        if line < 0 or not (line * 2).is_integer():
            raise ValueError("Passing-yards lines must be nonnegative whole or half yards")
        validate_american(self.over_american)
        validate_american(self.under_american)
        if self.sportsbook is not None:
            if not isinstance(self.sportsbook, str):
                raise ValueError("Sportsbook must be text")
            name = self.sportsbook.strip()
            if not name or len(name) > 120 or not name.isprintable():
                raise ValueError("Sportsbook must contain 1–120 printable characters")
            object.__setattr__(self, "sportsbook", name)
        if self.game_spread is not None:
            finite_number(self.game_spread, "Game spread")
        if self.game_total is not None and finite_number(self.game_total, "Game total") <= 0:
            raise ValueError("Game total must be positive")


@dataclass(frozen=True)
class SideValue:
    american_odds: int
    decimal_odds: float
    model_win_probability: float
    model_loss_probability: float
    push_probability: float
    conditional_win_probability: float | None
    break_even_conditional_probability: float
    break_even_unconditional_probability: float
    probability_edge_percentage_points: float | None
    fair_decimal_odds: float | None
    fair_american_odds: float | None
    estimated_ev_per_dollar: float
    estimated_ev_percent: float


def side_value(win: float, loss: float, push: float, odds: int) -> SideValue:
    OutcomeProbabilities(win, loss, push)
    decimal = american_to_decimal(odds)
    resolved = win + loss
    conditional = win / resolved if resolved else None
    break_even = 1 / decimal
    ev = win * (decimal - 1) - loss
    fair_decimal = resolved / win if win > 0 else None
    fair_price = (
        fair_american(conditional) if conditional is not None and 0 < conditional < 1 else None
    )
    return SideValue(
        odds,
        decimal,
        win,
        loss,
        push,
        conditional,
        break_even,
        resolved * break_even,
        (conditional - break_even) * 100 if conditional is not None else None,
        fair_decimal,
        fair_price,
        ev,
        ev * 100,
    )


def evaluate_market(
    market: ManualMarket, probabilities: OutcomeProbabilities
) -> dict[str, SideValue]:
    if not float(market.line).is_integer() and probabilities.push != 0:
        raise ValueError("An integer-yard outcome cannot push on a half-yard line")
    return {
        "over": side_value(
            probabilities.over, probabilities.under, probabilities.push, market.over_american
        ),
        "under": side_value(
            probabilities.under, probabilities.over, probabilities.push, market.under_american
        ),
    }
