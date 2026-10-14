"""Shared identity contracts for team research tables."""

import polars as pl


class DataQualityError(ValueError):
    """A source violates a contract that would make the modeling table unsafe."""


def require_keys(frame: pl.DataFrame, keys: list[str], name: str) -> None:
    if frame.select(pl.any_horizontal(pl.col(keys).is_null()).any()).item():
        raise DataQualityError(f"{name}: null keys in {keys}")
    if frame.select(keys).is_duplicated().any():
        raise DataQualityError(f"{name}: duplicate keys in {keys}")
