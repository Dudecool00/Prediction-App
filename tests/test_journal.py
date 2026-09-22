import json
from uuid import UUID

import pytest

from nfl_prop_model.markets.journal import read_quote_records, save_quote_record
from nfl_prop_model.markets.odds import ManualMarket
from nfl_prop_model.markets.quote import create_quote, load_historical_forecast


def test_journal_preserves_originals_and_detects_corruption(saved_research, tmp_path, monkeypatch):
    data, _, _ = saved_research
    quote = create_quote(
        load_historical_forecast(data, "QB", "2023_02_A_B", "xgb_schedule"),
        ManualMarket(200.5, -110, -110),
    )
    directory = tmp_path / "journal"
    monkeypatch.setattr("nfl_prop_model.markets.journal.uuid4", lambda: UUID(int=1))
    first = save_quote_record(directory, quote)
    original = first.read_bytes()
    with pytest.raises(FileExistsError):
        save_quote_record(directory, quote)
    assert first.read_bytes() == original
    assert read_quote_records(directory)[0]["quote"] == quote
    changed = json.loads(original)
    changed["payload"]["quote"]["market"]["line"] = 999
    first.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_quote_records(directory)
