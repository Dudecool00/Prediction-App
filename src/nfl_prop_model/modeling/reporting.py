"""Small report formatting helpers independent of model fitting."""

import polars as pl


def markdown_table(frame: pl.DataFrame) -> list[str]:
    lines = [
        "| " + " | ".join(frame.columns) + " |",
        "| " + " | ".join(["---"] * frame.width) + " |",
    ]
    for row in frame.iter_rows():
        lines.append(
            "| " + " | ".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in row) + " |"
        )
    return lines
