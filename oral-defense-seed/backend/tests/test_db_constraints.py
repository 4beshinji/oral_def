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


@pytest.mark.parametrize(
    "table", ["conversation_playbacks", "assistance", "conversations", "turn_submissions"]
)
@pytest.mark.parametrize("operation", ["insert", "update"])
def test_same_session_reference_must_belong_to_turn(database, table, operation):
    with database.connect() as db:
        first = seed_session(db, "s1")
        second = "s1-turn-2"
        db.execute(
            "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,follow_up_count) VALUES (?,?,?,?,?,?)",
            (second, "s1", 2, "q2", "b2", 0),
        )
        seed_exercise(db, "s1", first, "e1")
        seed_exercise(db, "s1", second, "e2")
        statements = {
            "conversation_playbacks": (
                "INSERT INTO conversation_playbacks (id,session_id,turn_id,reference_id,revision,stage,reference_hash,tts_settings_json) VALUES ('p1','s1',?,'e1',1,'model_playback','hash','{}')",
                "UPDATE conversation_playbacks SET turn_id=? WHERE id='p1'",
            ),
            "assistance": (
                "INSERT INTO assistance (id,session_id,turn_id,exercise_id,kind,created_at) VALUES ('a1','s1',?,'e1','reference_shown','now')",
                "UPDATE assistance SET turn_id=? WHERE id='a1'",
            ),
            "conversations": (
                "INSERT INTO conversations (id,turn_id,reference_id) VALUES ('s1',?,'e1')",
                "UPDATE conversations SET turn_id=? WHERE id='s1'",
            ),
            "turn_submissions": (
                "INSERT INTO turn_submissions (session_id,turn_id,answer_text,submitted_via,source_exercise_id,provenance_status) VALUES ('s1',?,'text','confirmed_reference','e1','exact')",
                "UPDATE turn_submissions SET turn_id=? WHERE session_id='s1'",
            ),
        }
        insert, update = statements[table]
        if operation == "update":
            db.execute(insert, (first,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(insert if operation == "insert" else update, (second,))


@pytest.mark.parametrize("table", ["conversations", "turn_submissions"])
@pytest.mark.parametrize("other_turn", [False, True])
def test_current_and_submitted_playback_match_turn_and_reference(database, table, other_turn):
    with database.connect() as db:
        turn = seed_session(db, "s1")
        second = "s1-turn-2"
        db.execute(
            "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,follow_up_count) VALUES (?,?,?,?,?,?)",
            (second, "s1", 2, "q2", "b2", 0),
        )
        seed_exercise(db, "s1", turn, "e1")
        seed_exercise(db, "s1", second if other_turn else turn, "e2")
        seed_playback(db, "s1", second if other_turn else turn, "p2", "model_playback", "e2")
        with pytest.raises(sqlite3.IntegrityError):
            if table == "conversations":
                db.execute(
                    "INSERT INTO conversations (id,turn_id,reference_id,playback_id) VALUES ('s1',?,'e1','p2')",
                    (turn,),
                )
            else:
                db.execute(
                    "INSERT INTO turn_submissions (session_id,turn_id,answer_text,submitted_via,source_exercise_id,source_playback_id,provenance_status) VALUES ('s1',?,'text','shadowing_playback','e1','p2','exact')",
                    (turn,),
                )


def test_existing_playback_and_parent_identity_cannot_be_reassigned(database):
    with database.connect() as db:
        turn = seed_session(db, "s1")
        seed_session(db, "s2")
        seed_exercise(db, "s1", turn, "e1")
        seed_playback(db, "s1", turn, "p1", "model_playback", "e1")
        for statement in (
            "UPDATE conversation_playbacks SET reference_hash='different' WHERE id='p1'",
            "UPDATE turns SET session_id='s2' WHERE id='s1-turn-1'",
            "UPDATE exercises SET id='e3' WHERE id='e1'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)
