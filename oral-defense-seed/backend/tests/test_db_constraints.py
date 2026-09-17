import sqlite3

import pytest

from backend.app.config import Settings
from backend.app.db import Database, digest
from backend.app.main import create_app


@pytest.fixture
def database(tmp_path):
    return Database(tmp_path)


def seed_session(db, sid, ordinal=1):
    db.execute(
        "INSERT INTO sessions (id,created_at,pack_snapshot_json,pack_hash,research_brief,scenario,settings_json,status) VALUES (?,?,?,?,?,?,?,?)",
        (sid, "now", "{}", digest("{}"), "", "seminar", "{}", "active"),
    )
    turn_id = f"{sid}-turn-{ordinal}"
    db.execute(
        "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,follow_up_count) VALUES (?,?,?,?,?,?)",
        (turn_id, sid, ordinal, "q", "b", 0),
    )
    return turn_id


def seed_exercise(db, sid, turn_id, exercise_id):
    db.execute(
        "INSERT INTO exercises (id,session_id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (exercise_id, sid, turn_id, "read_aloud", "text", digest("text"), "manual", "now"),
    )


def seed_playback(db, sid, turn_id, playback_id, stage="question_playback", reference_id=None):
    db.execute(
        "INSERT INTO conversation_playbacks (id,session_id,turn_id,reference_id,revision,stage,reference_hash,audio_id,tts_settings_json,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (playback_id, sid, turn_id, reference_id, 1, stage, digest("h"), None, "{}", "playing"),
    )


def test_cross_session_children_are_rejected(database):
    with database.connect() as db:
        first = seed_session(db, "s1")
        seed_session(db, "s2")
        seed_exercise(db, "s1", first, "e1")
        db.execute(
            "INSERT INTO audio_files (id,session_id,filename,media_type,cache_key) VALUES (?,?,?,?,NULL)",
            ("a2", "s2", "other.wav", "audio/wav"),
        )
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO coach_messages (id,session_id,turn_id,level,user_note,draft,response_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("m1", "s2", first, "hint", "", "", "{}", "now"),
            )
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO exercises (id,session_id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("e9", "s2", first, "read_aloud", "text", digest("text"), "manual", "now"),
            )
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO attempts (id,session_id,exercise_id,audio_id,dictation_text,audio_meta_json,result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("at1", "s1", "e1", "a2", None, "{}", None, "now"),
            )


def test_conversation_current_playback_must_exist_and_belong(database):
    with database.connect() as db:
        seed_session(db, "s1")
        seed_session(db, "s2")
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s1",))
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s2",))
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE conversations SET playback_id='ghost' WHERE id='s1'")
    with database.connect() as db:
        seed_playback(db, "s2", "s2-turn-1", "p-other")
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE conversations SET playback_id='p-other' WHERE id='s1'")


def test_playback_stage_reference_rules(database):
    with database.connect() as db:
        turn = seed_session(db, "s1")
        seed_exercise(db, "s1", turn, "e1")
    with database.connect() as db:
        seed_playback(db, "s1", turn, "p-question")
        db.execute("UPDATE conversation_playbacks SET status='cancelled' WHERE id='p-question'")
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            seed_playback(db, "s1", turn, "p-bad-question", reference_id="e1")
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            seed_playback(db, "s1", turn, "p-bad-model", stage="model_playback")
    with database.connect() as db:
        seed_playback(db, "s1", turn, "p-model", stage="model_playback", reference_id="e1")


def test_only_one_playing_per_session_and_recancel(database):
    with database.connect() as db:
        turn = seed_session(db, "s1")
    with database.connect() as db:
        seed_playback(db, "s1", turn, "p1")
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            seed_playback(db, "s1", turn, "p2")
    with database.connect() as db:
        db.execute("UPDATE conversation_playbacks SET status='cancelled' WHERE id='p1'")
    with database.connect() as db:
        seed_playback(db, "s1", turn, "p3")


def test_reference_is_immutable_and_new_version_is_allowed(database):
    with database.connect() as db:
        turn = seed_session(db, "s1")
        seed_exercise(db, "s1", turn, "e1")
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE exercises SET reference_text='changed' WHERE id='e1'")
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE exercises SET reference_hash='changed' WHERE id='e1'")
    with database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE exercises SET turn_id='elsewhere' WHERE id='e1'")
    with database.connect() as db:
        seed_exercise(db, "s1", turn, "e2")


def test_existing_shadowing_flow_still_passes_constraints(tmp_path):
    from fastapi.testclient import TestClient

    from backend.tests.test_drill import PACK

    app = create_app(Settings(data_dir=tmp_path, text_provider="mock", tts_provider="mock"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post("/v1/sessions", json={"pack": PACK, "scenario": "networking"})
        sid = response.json()["session_id"]
        state = client.post(
            f"/v1/sessions/{sid}/conversation/control", json={"action": "resume"}
        ).json()
        state = client.post(
            f"/v1/sessions/{sid}/conversation/step",
            json={"revision": state["revision"], "route": "browser"},
        ).json()
        playback = client.post(
            f"/v1/sessions/{sid}/conversation/playbacks",
            json={"revision": state["revision"], "route": "browser"},
        ).json()
        assert client.delete(f"/v1/sessions/{sid}").status_code == 200
        assert playback["playback_id"]
