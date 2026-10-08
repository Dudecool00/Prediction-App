from datetime import timedelta

import polars as pl
import pytest
from test_prospective import AS_OF, buf, save_inputs

from nfl_prop_model.data.participation import (
    audit_participation,
    export_enrollment,
    read_registry,
    register_candidates,
)
from nfl_prop_model.data.prospective import load_feature_report, save_feature_snapshot
from nfl_prop_model.data.status_reviews import checksum, timestamp
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json


def enroll(tmp_path):
    save_inputs(tmp_path)
    report = load_feature_report(tmp_path, as_of=AS_OF)
    snapshot = save_feature_snapshot(tmp_path, report)
    registry = register_candidates(tmp_path, snapshot, now=AS_OF)
    return registry, report, snapshot


def outcomes(data, report, *, missing=False, absent=False, yards=0):
    candidate = buf(report)
    as_of = timestamp(candidate["kickoff_utc"]) + timedelta(hours=25)
    directory = data / "raw" / "upcoming_2026"
    manifest = read_json(directory / "manifest.json")
    schedules = load_frame(directory, manifest["datasets"]["schedules"]).with_columns(
        pl.when(pl.col("game_id") == candidate["game_id"])
        .then(24.0)
        .otherwise(pl.col("home_score"))
        .alias("home_score"),
        pl.when(pl.col("game_id") == candidate["game_id"])
        .then(17.0)
        .otherwise(pl.col("away_score"))
        .alias("away_score"),
    )
    manifest["datasets"]["schedules"] = store_frame(directory, "schedules", schedules)
    manifest["retrieved_at_utc"] = as_of.isoformat()
    write_json(directory / "manifest.json", manifest)
    directory = data / "raw" / "current_stats_2026"
    manifest = read_json(directory / "manifest.json")
    stats = load_frame(directory, manifest["player_stats"]).with_columns(
        pl.lit(candidate["game_id"]).alias("game_id"),
        pl.lit(candidate["week"]).alias("week"),
        pl.lit(yards).alias("passing_yards"),
        pl.lit(0).alias("attempts"),
    )
    if absent:
        stats = stats.with_columns(
            pl.when(pl.col("player_id") == "gsis-BUF")
            .then(pl.lit("different-QB"))
            .otherwise(pl.col("player_id"))
            .alias("player_id")
        )
    if missing:
        stats = stats.filter(pl.col("team") != "BUF")
    manifest["player_stats"] = store_frame(directory, "player_stats", stats)
    manifest["retrieved_at_utc"] = as_of.isoformat()
    write_json(directory / "manifest.json", manifest)
    return as_of


def test_enrollment_keeps_unknown_and_abstained_candidates(tmp_path):
    directory, report, snapshot = enroll(tmp_path)
    registry = read_registry(directory)
    assert registry["candidate_count"] == len(report["candidates"]) == 3
    assert registry["snapshot_sha256"] == sha256_file(snapshot / "features.json")
    assert (directory / "features.json").read_bytes() == (snapshot / "features.json").read_bytes()
    audit = audit_participation(tmp_path, directory, as_of=AS_OF)
    assert audit["counts"] == {"pending_game": 3}
    assert not audit["production_enabled"] and not audit["participation_validation_complete"]
    assert all(row["observed_passing_yards"] is None for row in audit["candidates"])
    assert not (tmp_path / "participation" / "enrollment.lock").exists()


def test_duplicate_games_cannot_be_reenrolled(tmp_path):
    _, _, snapshot = enroll(tmp_path)
    with pytest.raises(ValueError, match="already enrolled"):
        register_candidates(tmp_path, snapshot, now=AS_OF)
    assert not (tmp_path / "participation" / "enrollment.lock").exists()


def test_late_enrollment_rejected_without_registry(tmp_path):
    save_inputs(tmp_path)
    report = load_feature_report(tmp_path, as_of=AS_OF)
    snapshot = save_feature_snapshot(tmp_path, report)
    with pytest.raises(ValueError, match="cutoff"):
        register_candidates(tmp_path, snapshot, now=timestamp(buf(report)["forecast_cutoff_utc"]))
    assert not list((tmp_path / "participation").glob("cohort-*"))


@pytest.mark.parametrize(
    "missing,absent,yards,state",
    [
        (False, False, 0, "recorded_qb_appearance"),
        (False, False, -5, "recorded_qb_appearance"),
        (False, True, 0, "no_recorded_qb_appearance"),
        (True, False, 0, "qb_stats_coverage_missing"),
    ],
)
def test_postgame_join_preserves_denominator_and_never_imputes(
    tmp_path, missing, absent, yards, state
):
    directory, report, _ = enroll(tmp_path)
    before = sha256_file(directory / "registry.json")
    as_of = outcomes(tmp_path, report, missing=missing, absent=absent, yards=yards)
    audit = audit_participation(tmp_path, directory, as_of=as_of)
    row = next(row for row in audit["candidates"] if row["context"]["gsis_id"] == "gsis-BUF")
    assert row["state"] == state
    assert row["observed_passing_yards"] == (yards if state == "recorded_qb_appearance" else None)
    assert audit["candidate_count"] == sum(audit["counts"].values()) == 3
    assert audit["counts"]["identity_unmapped"] == 1
    assert sha256_file(directory / "registry.json") == before


def test_stale_sources_do_not_adjudicate_absence(tmp_path):
    directory, report, _ = enroll(tmp_path)
    as_of = outcomes(tmp_path, report, absent=True)
    audit = audit_participation(tmp_path, directory, as_of=as_of + timedelta(hours=25))
    assert audit["counts"] == {"pending_fresh_postgame_sources": 3}


def test_schedule_change_keeps_enrolled_rows(tmp_path):
    directory, report, _ = enroll(tmp_path)
    as_of = outcomes(tmp_path, report)
    root = tmp_path / "raw" / "upcoming_2026"
    manifest = read_json(root / "manifest.json")
    schedules = load_frame(root, manifest["datasets"]["schedules"]).with_columns(
        pl.lit("17:00").alias("gametime")
    )
    manifest["datasets"]["schedules"] = store_frame(root, "schedules", schedules)
    write_json(root / "manifest.json", manifest)
    assert audit_participation(tmp_path, directory, as_of=as_of)["counts"] == {
        "schedule_changed": 3
    }


def test_registry_and_copied_snapshot_tampering_fails(tmp_path):
    directory, _, _ = enroll(tmp_path)
    path = directory / "features.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum"):
        read_registry(directory)


def test_rehashed_late_registration_still_fails(tmp_path):
    directory, report, _ = enroll(tmp_path)
    path = directory / "registry.json"
    envelope = read_json(path)
    envelope["payload"]["registered_at_utc"] = buf(report)["kickoff_utc"]
    envelope["sha256"] = checksum(envelope["payload"])
    write_json(path, envelope)
    with pytest.raises(ValueError, match="cutoff"):
        read_registry(directory)


def test_audit_uses_local_2026_sources_only(tmp_path, monkeypatch):
    import nflreadpy

    directory, _, _ = enroll(tmp_path)

    def fail(*args, **kwargs):
        raise AssertionError("No source download or holdout evaluation allowed")

    monkeypatch.setattr(nflreadpy, "load_player_stats", fail)
    monkeypatch.setattr(nflreadpy, "load_schedules", fail)
    audit_participation(tmp_path, directory, as_of=AS_OF)


def test_public_export_keeps_denominator_without_raw_articles(tmp_path):
    directory, report, _ = enroll(tmp_path)
    path = tmp_path / "public" / "enrollment.json"
    export_enrollment(directory, path)
    public = read_json(path)
    assert len(public["candidates"]) == len(report["candidates"]) == 3
    assert public["registry_sha256"] == checksum(read_registry(directory))
    assert all("context" in row and "features_sha256" in row for row in public["candidates"])
    assert "article.html" not in path.read_text()


def test_null_target_never_becomes_zero_or_absence(tmp_path):
    directory, report, _ = enroll(tmp_path)
    as_of = outcomes(tmp_path, report)
    root = tmp_path / "raw" / "current_stats_2026"
    manifest = read_json(root / "manifest.json")
    stats = load_frame(root, manifest["player_stats"]).with_columns(
        pl.lit(None, dtype=pl.Int64).alias("passing_yards")
    )
    manifest["player_stats"] = store_frame(root, "player_stats", stats)
    write_json(root / "manifest.json", manifest)
    audit = audit_participation(tmp_path, directory, as_of=as_of)
    assert audit["counts"]["qb_stats_coverage_missing"] == 2
    assert all(row["observed_passing_yards"] is None for row in audit["candidates"])
