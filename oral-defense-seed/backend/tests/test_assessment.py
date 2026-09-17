from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import pronunciation
from backend.app.config import Settings
from backend.app.errors import APIError
from backend.app.main import create_app
from backend.tests.test_drill import fixed, new_turn, recorded, wav_bytes


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, text_provider="mock", tts_provider="mock"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as value:
        yield value


def first_attempt(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    attempt = recorded(client, exercise["id"])
    return exercise, attempt


def runs(client):
    with client.app.state.database.connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM assessment_runs ORDER BY rowid")]


def assess(client, attempt_id):
    return client.post(f"/v1/attempts/{attempt_id}/assess", json={"request_id": str(uuid4())})


def upload_kind(client, exercise_id, kind):
    response = client.post(
        f"/v1/exercises/{exercise_id}/audio",
        files={"file": ("recording.wav", wav_bytes(), "audio/wav")},
        data={"input_kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_reassessment_appends_and_preserves_history(client):
    exercise, attempt = first_attempt(client)
    first = assess(client, attempt["attempt_id"])
    assert first.status_code == 200
    second = assess(client, attempt["attempt_id"])
    assert second.status_code == 200
    saved = runs(client)
    assert len(saved) == 2
    assert all(run["execution_status"] == "succeeded" for run in saved)
    assert all(run["evidence_status"] == "unavailable" for run in saved)
    assert saved[0]["result_json"]
    assert saved[0]["reference_hash"] == exercise["reference_hash"]
    assert saved[0]["provider"] == "unavailable"
    assert saved[0]["config_json"]
    assert saved[0]["started_at"] and saved[0]["finished_at"]


def test_same_request_resend_does_not_add_run(client):
    _, attempt = first_attempt(client)
    request_id = str(uuid4())
    payload = {"request_id": request_id}
    first = client.post(f"/v1/attempts/{attempt['attempt_id']}/assess", json=payload)
    again = client.post(f"/v1/attempts/{attempt['attempt_id']}/assess", json=payload)
    assert first.status_code == 200
    assert again.json() == first.json()
    assert len(runs(client)) == 1


def test_failed_reassessment_keeps_earlier_success(client, monkeypatch):
    _, attempt = first_attempt(client)
    assert assess(client, attempt["attempt_id"]).status_code == 200

    def fail(*args, **kwargs):
        raise APIError(504, "assessment_timeout", "Injected timeout", True)

    monkeypatch.setattr(pronunciation, "assess", fail)
    failed = assess(client, attempt["attempt_id"])
    assert failed.status_code == 504
    saved = runs(client)
    assert [run["execution_status"] for run in saved] == ["succeeded", "failed"]
    assert saved[0]["result_json"]


def test_shadowing_overlap_is_insufficient_evidence(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    attempt = upload_kind(client, exercise["id"], "shadowing_overlap")
    assert assess(client, attempt["attempt_id"]).status_code == 200
    run = runs(client)[0]
    assert run["input_kind"] == "shadowing_overlap"
    assert run["evidence_status"] == "insufficient_evidence"


def test_restart_marks_running_run_interrupted(client, tmp_path):
    _, attempt = first_attempt(client)
    with client.app.state.database.connect() as db:
        session_id = db.execute(
            "SELECT session_id FROM attempts WHERE id=?", (attempt["attempt_id"],)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO assessment_runs (id,attempt_id,session_id,input_kind,execution_status,evidence_status,provider,model_version,config_json,reference_hash,result_json,error_code,error_message,request_id,started_at,finished_at,created_at) VALUES (?,?,?,?,'running','pending','x',NULL,NULL,'h',NULL,NULL,NULL,NULL,?,NULL,?)",
            ("run-x", attempt["attempt_id"], session_id, "unknown", "now", "now"),
        )
    from backend.app.db import Database

    Database(tmp_path)
    with client.app.state.database.connect() as db:
        status = db.execute(
            "SELECT execution_status FROM assessment_runs WHERE id='run-x'"
        ).fetchone()[0]
    assert status == "interrupted"


def test_result_is_immutable_once_succeeded(client):
    import sqlite3

    _, attempt = first_attempt(client)
    assess(client, attempt["attempt_id"])
    with client.app.state.database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE assessment_runs SET result_json='{}'")


def test_dictation_is_not_an_assessment_run(client):
    sid, tid = new_turn(client)
    exercise = fixed(client, tid, "Hello world.", "dictation")
    response = client.post(
        f"/v1/exercises/{exercise['id']}/dictation", json={"typed_text": "hello world"}
    )
    assert response.status_code == 201
    assert runs(client) == []
    assert client.get(f"/v1/sessions/{sid}").status_code == 200
