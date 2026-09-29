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
        (9, "turn_reference_ownership"),
        (10, "generation_provenance"),
        (11, "free_speech_input"),
    ]
    Database(tmp_path)
    assert schema_snapshot(tmp_path) == first
    with sqlite3.connect(tmp_path / "drill.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(
            MIGRATIONS
        )


def test_v10_nonempty_session_survives_free_speech_migration(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "MIGRATIONS", MIGRATIONS[:10])
        old = Database(tmp_path)
    sid, tid = str(uuid.uuid4()), str(uuid.uuid4())
    audio_id = str(uuid.uuid4())
    (tmp_path / "audio" / "old.wav").write_bytes(b"prior audio")
    with old.connect() as connection:
        connection.execute(
            """INSERT INTO sessions
               (id,created_at,pack_snapshot_json,pack_hash,research_brief,scenario,
                settings_json,status) VALUES (?,?,?,?,?,?,?,'active')""",
            (sid, "now", "{}", digest("{}"), "old research", "seminar", "{}"),
        )
        connection.execute(
            """INSERT INTO turns
               (id,session_id,ordinal,question_en,basis_note,follow_up_count,generation_json)
               VALUES (?,?,?,?,?,0,?)""",
            (tid, sid, 1, "What changed?", "old source", '{"model":"old"}'),
        )
        connection.execute(
            """INSERT INTO audio_files
               (id,session_id,filename,media_type,cache_key,kind,storage_key,content_hash,
                media_json,save_status) VALUES (?,?,?,?,NULL,'recording',?,?,?,'ready')""",
            (audio_id, sid, "old.wav", "audio/wav", "old.wav", digest("old audio"), "{}"),
        )
        connection.execute(
            """INSERT INTO turn_submissions
               (turn_id,session_id,answer_text,submitted_via,provenance_status)
               VALUES (?,?,?,'confirmed_reference','legacy_missing')""",
            (tid, sid, "A saved answer."),
        )
        connection.execute(
            """INSERT INTO conversations
               (id,mode,status,stage,revision,turn_id)
               VALUES (?,'shadowing','paused','coach_generation',8,?)""",
            (sid, tid),
        )
    migrated = Database(tmp_path)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            connection.execute("SELECT research_brief FROM sessions WHERE id=?", (sid,)).fetchone()[
                0
            ]
            == "old research"
        )
        assert connection.execute(
            "SELECT question_en,generation_json FROM turns WHERE id=?", (tid,)
        ).fetchone()[:] == ("What changed?", '{"model":"old"}')
        assert connection.execute(
            "SELECT answer_text,submitted_via FROM turn_submissions WHERE turn_id=?", (tid,)
        ).fetchone()[:] == ("A saved answer.", "confirmed_reference")
        assert connection.execute(
            "SELECT stage,revision FROM conversations WHERE id=?", (sid,)
        ).fetchone()[:] == ("coach_generation", 8)
        assert (
            connection.execute(
                "SELECT filename FROM audio_files WHERE id=?", (audio_id,)
            ).fetchone()[0]
            == "old.wav"
        )


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
    assert versions == [migration.version for migration in MIGRATIONS]
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
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(
            MIGRATIONS
        )
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


def test_legacy_audio_attempts_and_assessments_survive_migration(tmp_path):
    sid = str(uuid.uuid4())
    legacy_database(tmp_path, sessions=[(sid, {})])
    audio_bytes = {"a1": b"first take", "a2": b"second take", "a3": b"shadow take"}
    with sqlite3.connect(tmp_path / "drill.sqlite3") as db:
        db.execute("PRAGMA foreign_keys=ON")
        for ordinal, turn_id, answer, via in (
            (1, "t1", "Independent answer.", "confirmed_reference"),
            (2, "t2", "Shadow answer.", "shadowing_playback"),
        ):
            db.execute(
                """INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,
                   confirmed_answer_en,submitted_via,follow_up_count)
                   VALUES (?,?,?,?,?,?,?,0)""",
                (turn_id, sid, ordinal, "Question?", "basis", answer, via),
            )
            db.execute(
                """INSERT INTO exercises (id,turn_id,mode,reference_text,reference_hash,
                   reference_origin,created_at) VALUES (?,?,?,?,?,?,?)""",
                (f"e{ordinal}", turn_id, "read_aloud", answer, digest(answer), "coach", "now"),
            )
        db.execute(
            """INSERT INTO conversation_playbacks
               (id,session_id,turn_id,reference_id,revision,stage,reference_hash,
                tts_settings_json,status) VALUES (?,?,?,?,1,'model_playback',?,'{}','completed')""",
            ("p2", sid, "t2", "e2", digest("Shadow answer.")),
        )
        for index, (audio_id, data) in enumerate(audio_bytes.items(), start=1):
            filename = f"{audio_id}.wav"
            (tmp_path / "audio" / filename).write_bytes(data)
            db.execute(
                "INSERT INTO audio_files (id,session_id,filename,media_type,cache_key) VALUES (?,?,?,'audio/wav',NULL)",
                (audio_id, sid, filename),
            )
            result = {"status": "unavailable" if index == 1 else "ok", "phones": []}
            db.execute(
                """INSERT INTO attempts
                   (id,exercise_id,audio_id,dictation_text,audio_meta_json,result_json,created_at)
                   VALUES (?,?,?,NULL,'{}',?,?)""",
                (
                    f"attempt-{index}",
                    "e1" if index < 3 else "e2",
                    audio_id,
                    json.dumps(result),
                    "now",
                ),
            )
    database = Database(tmp_path)
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM exercises").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM assessment_runs").fetchone()[0] == 3
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        submissions = {
            record["turn_id"]: dict(record)
            for record in db.execute("SELECT * FROM turn_submissions")
        }
        assert submissions["t1"]["source_exercise_id"] == "e1"
        assert submissions["t2"]["source_playback_id"] == "p2"
        for index, (audio_id, data) in enumerate(audio_bytes.items(), start=1):
            audio = db.execute("SELECT * FROM audio_files WHERE id=?", (audio_id,)).fetchone()
            attempt = db.execute(
                "SELECT * FROM attempts WHERE id=?", (f"attempt-{index}",)
            ).fetchone()
            run = db.execute(
                "SELECT * FROM assessment_runs WHERE attempt_id=?", (attempt["id"],)
            ).fetchone()
            assert (tmp_path / "audio" / audio["storage_key"]).read_bytes() == data
            assert audio["kind"] == "recording"
            assert attempt["session_id"] == sid
            assert attempt["input_kind"] == "unknown"
            assert run["provider"] == "legacy_unknown"
            assert run["reference_hash"] == digest(
                "Independent answer." if index < 3 else "Shadow answer."
            )


def test_connect_sets_runtime_pragmas(tmp_path):
    database = Database(tmp_path)
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
    assert database.sqlite_version == sqlite3.sqlite_version


@pytest.mark.parametrize("legacy", [True, False])
def test_migration_rejects_cross_turn_reference_without_changing_data(
    tmp_path, monkeypatch, legacy
):
    if legacy:
        legacy_database(tmp_path, sessions=[("s1", {})])
    else:
        with monkeypatch.context() as patch:
            patch.setattr(db_module, "MIGRATIONS", MIGRATIONS[:8])
            Database(tmp_path)
        with sqlite3.connect(tmp_path / "drill.sqlite3") as db:
            db.execute(
                "INSERT INTO sessions (id,created_at,pack_snapshot_json,pack_hash,research_brief,scenario,settings_json,status) VALUES ('s1','now','{}',?,'','seminar','{}','active')",
                (digest("{}"),),
            )
    with sqlite3.connect(tmp_path / "drill.sqlite3") as db:
        for ordinal in (1, 2):
            db.execute(
                "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,follow_up_count) VALUES (?,'s1',?,'q','b',0)",
                (f"t{ordinal}", ordinal),
            )
        cols = "" if legacy else "session_id,"
        values = "" if legacy else "'s1',"
        db.execute(
            f"INSERT INTO exercises (id,{cols}turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES ('e2',{values}'t2','listen_repeat','text',?,'coach','now')",
            (digest("text"),),
        )
        db.execute(
            "INSERT INTO conversation_playbacks (id,session_id,turn_id,reference_id,revision,stage,reference_hash,tts_settings_json,status) VALUES ('p1','s1','t1','e2',1,'model_playback',?,'{}','playing')",
            (digest("text"),),
        )
        before = list(db.iterdump())
    with pytest.raises(MigrationError, match="別turn"):
        Database(tmp_path)
    with sqlite3.connect(tmp_path / "drill.sqlite3") as db:
        # The only permitted difference is the empty bookkeeping table for a legacy DB.
        after = list(db.iterdump())
        assert [line for line in after if "schema_migrations" not in line] == [
            line for line in before if "schema_migrations" not in line
        ]


def test_fresh_and_legacy_schema_match(tmp_path):
    fresh, legacy = tmp_path / "fresh", tmp_path / "legacy"
    Database(fresh)
    legacy_database(legacy, sessions=[("s1", {})])
    database = Database(legacy)
    assert schema_snapshot(fresh) == schema_snapshot(legacy)
    with database.connect() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
