import json
import sqlite3
import uuid

import pytest

from backend.app import db as db_module
from backend.app.db import (
    INITIAL_SCHEMA,
    MIGRATIONS,
    Database,
    Migration,
    MigrationError,
    digest,
    inspect_database,
)


def table_columns(connection, table):
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def schema_snapshot(data_dir):
    connection = sqlite3.connect(data_dir / "drill.sqlite3")
    connection.row_factory = sqlite3.Row
    snapshot = {
        row["name"]: {
            "sql": row["sql"],
            "columns": [dict(c) for c in connection.execute(f"PRAGMA table_info({row['name']})")],
        }
        for row in connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    }
    connection.close()
    return snapshot


def legacy_database(data_dir, sessions=()):
    """Create an initial-commit-style DB with no migration bookkeeping."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "audio").mkdir(exist_ok=True)
    connection = sqlite3.connect(data_dir / "drill.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in INITIAL_SCHEMA:
        connection.execute(statement)
    for session_id, settings in sessions:
        connection.execute(
            "INSERT INTO sessions (id,created_at,pack_snapshot_json,pack_hash,research_brief,scenario,settings_json,status) VALUES (?,?,?,?,?,?,?,?)",
            (
                session_id,
                "2026-01-01T00:00:00+00:00",
                "{}",
                digest("{}"),
                "",
                "seminar",
                json.dumps(settings),
                "active",
            ),
        )
    connection.commit()
    connection.close()


def test_fresh_database_records_migrations_and_is_idempotent(tmp_path):
    Database(tmp_path)
    first = schema_snapshot(tmp_path)
    with sqlite3.connect(tmp_path / "drill.sqlite3") as connection:
        applied = [
            tuple(row)
            for row in connection.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            )
        ]
    assert applied == [
        (1, "initial_schema"),
        (2, "session_settings_backfill"),
        (3, "composite_ownership_and_immutability"),
        (4, "turn_submissions"),
        (5, "recording_lifecycle"),
        (6, "resumable_deletion"),
        (7, "assessment_runs"),
        (8, "session_documents"),
    ]
    Database(tmp_path)
    assert schema_snapshot(tmp_path) == first
    with sqlite3.connect(tmp_path / "drill.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 8


def test_legacy_database_is_baselined_and_settings_backfilled(tmp_path):
    session_id = str(uuid.uuid4())
    legacy_database(
        tmp_path,
        sessions=[
            (session_id, {"providers_at_start": {"text": {"provider": "mock", "model": "demo"}}})
        ],
    )
    database = Database(tmp_path)
    with database.connect() as connection:
        saved = json.loads(
            connection.execute(
                "SELECT settings_json FROM sessions WHERE id=?", (session_id,)
            ).fetchone()[0]
        )
        versions = [
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert versions == [1, 2, 3, 4, 5, 6, 7, 8]
    assert saved["text_model"]["id"] == "mock/demo"
    assert set(saved["role_models"]) == {
        "examiner",
        "meaning",
        "hint",
        "outline",
        "revision",
        "full_answer",
    }
    assert saved["speech_models"]


def test_duplicate_migration_versions_are_rejected(tmp_path, monkeypatch):
    duplicate = (
        Migration(1, "one", statements=INITIAL_SCHEMA),
        Migration(1, "duplicate", statements=INITIAL_SCHEMA),
    )
    monkeypatch.setattr(db_module, "MIGRATIONS", duplicate)
    with pytest.raises(MigrationError):
        Database(tmp_path)


def test_modified_applied_migration_is_rejected(tmp_path, monkeypatch):
    Database(tmp_path)
    altered = (
        Migration(1, "initial_schema", statements=("CREATE TABLE IF NOT EXISTS other (id TEXT)",)),
        MIGRATIONS[1],
    )
    monkeypatch.setattr(db_module, "MIGRATIONS", altered)
    with pytest.raises(MigrationError):
        Database(tmp_path)


def test_unknown_future_version_stops_safely(tmp_path):
    Database(tmp_path)
    with sqlite3.connect(tmp_path / "drill.sqlite3") as connection:
        connection.execute(
            "INSERT INTO schema_migrations (version,name,checksum,applied_at) VALUES (999,'future','x','now')"
        )
    with pytest.raises(MigrationError):
        Database(tmp_path)


def test_failed_migration_does_not_record_or_partially_apply(tmp_path, monkeypatch):
    Database(tmp_path)

    def broken(db, context):
        raise RuntimeError("injected failure")

    failing = MIGRATIONS + (
        Migration(
            99,
            "broken",
            statements=("CREATE TABLE IF NOT EXISTS partial (id TEXT PRIMARY KEY)",),
            func=broken,
        ),
    )
    monkeypatch.setattr(db_module, "MIGRATIONS", failing)
    with pytest.raises(RuntimeError):
        Database(tmp_path)
    with sqlite3.connect(tmp_path / "drill.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 8
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "partial" not in tables


def test_inspect_database_reports_without_repairing(tmp_path):
    database = Database(tmp_path)
    with sqlite3.connect(tmp_path / "drill.sqlite3") as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO sessions (id,created_at,pack_snapshot_json,pack_hash,research_brief,scenario,settings_json,status) VALUES (?,?,?,?,?,?,?,?)",
            ("s1", "now", "not json", "hash", "", "seminar", "{}", "active"),
        )
        connection.execute(
            "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,follow_up_count) VALUES (?,?,?,?,?,?)",
            ("t1", "missing", 1, "q", "b", 0),
        )
        connection.execute(
            "INSERT INTO audio_files (id,session_id,filename,media_type,cache_key) VALUES (?,?,?,?,NULL)",
            ("a1", "s1", "absent.wav", "audio/wav"),
        )
    with database.connect() as db:
        codes = {issue["code"] for issue in inspect_database(db, (tmp_path / "audio").resolve())}
    assert {"invalid_json", "foreign_key_violation", "missing_audio_file"} <= codes


def test_legacy_confirmed_turn_backfill_provenance(tmp_path):
    session_id = str(uuid.uuid4())
    legacy_database(
        tmp_path,
        sessions=[
            (session_id, {"providers_at_start": {"text": {"provider": "mock", "model": "demo"}}})
        ],
    )
    with sqlite3.connect(tmp_path / "drill.sqlite3") as connection:
        connection.execute(
            "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,submitted_via,follow_up_count,confirmed_answer_en) VALUES (?,?,?,?,?,?,?,?)",
            ("t-exact", session_id, 1, "q", "b", "shadowing_playback", 0, "An answer."),
        )
        connection.execute(
            "INSERT INTO exercises (id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                "e-exact",
                "t-exact",
                "listen_repeat",
                "An answer.",
                digest("An answer."),
                "coach",
                "now",
            ),
        )
        connection.execute(
            "INSERT INTO conversation_playbacks (id,session_id,turn_id,reference_id,revision,stage,reference_hash,audio_id,tts_settings_json,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "p-exact",
                session_id,
                "t-exact",
                "e-exact",
                1,
                "model_playback",
                digest("An answer."),
                None,
                "{}",
                "completed",
            ),
        )
        connection.execute(
            "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,submitted_via,follow_up_count,confirmed_answer_en) VALUES (?,?,?,?,?,?,?,?)",
            ("t-amb", session_id, 2, "q", "b", None, 0, "Ambiguous."),
        )
        for index in (1, 2):
            connection.execute(
                "INSERT INTO exercises (id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    f"e-amb-{index}",
                    "t-amb",
                    "read_aloud",
                    "Ambiguous.",
                    digest("Ambiguous."),
                    "manual",
                    "now",
                ),
            )
    database = Database(tmp_path)
    with database.connect() as db:
        rows = {row["turn_id"]: dict(row) for row in db.execute("SELECT * FROM turn_submissions")}
    assert rows["t-exact"]["provenance_status"] == "exact"
    assert rows["t-exact"]["source_exercise_id"] == "e-exact"
    assert rows["t-exact"]["source_playback_id"] == "p-exact"
    assert rows["t-exact"]["committed_at"] is None
    assert rows["t-amb"]["provenance_status"] == "legacy_ambiguous"
    assert rows["t-amb"]["submitted_via"] == "legacy_unknown"
    assert rows["t-amb"]["source_exercise_id"] is None


def test_connect_sets_runtime_pragmas(tmp_path):
    database = Database(tmp_path)
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
    assert database.sqlite_version == sqlite3.sqlite_version
