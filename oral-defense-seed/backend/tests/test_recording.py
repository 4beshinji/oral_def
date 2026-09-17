import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.test_drill import fixed, new_turn, wav_bytes


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, text_provider="mock", tts_provider="mock"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as value:
        yield value


def upload(client, exercise_id, data=None, key=None, kind="unknown", capture="{}"):
    form = {"capture": capture, "input_kind": kind}
    if key is not None:
        form["client_attempt_key"] = key
    return client.post(
        f"/v1/exercises/{exercise_id}/audio",
        files={"file": ("recording.wav", data or wav_bytes(), "audio/wav")},
        data=form,
    )


def attempts(client):
    with client.app.state.database.connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM attempts")]


def test_same_key_resend_returns_same_attempt(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    first = upload(client, exercise["id"], key="take-1")
    assert first.status_code == 201, first.text
    second = upload(client, exercise["id"], key="take-1")
    assert second.status_code == 201
    assert second.json() == first.json()
    assert len(attempts(client)) == 1


def test_same_key_conflicting_content_or_owner_is_rejected(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    other = fixed(client, tid, "Another reference.")
    upload(client, exercise["id"], key="take-1")
    conflict = upload(client, exercise["id"], wav_bytes(1.0), key="take-1")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "attempt_key_conflict"
    cross = upload(client, other["id"], key="take-1")
    assert cross.status_code == 409


def test_retake_uses_new_key_and_new_attempt(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    first = upload(client, exercise["id"], key="take-1").json()
    second = upload(client, exercise["id"], key="take-2").json()
    assert first["attempt_id"] != second["attempt_id"]
    assert first["audio_id"] != second["audio_id"]
    assert len(attempts(client)) == 2


def test_capture_request_and_actual_media_are_separate(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    response = upload(
        client,
        exercise["id"],
        key="take-1",
        kind="isolated_repeat",
        capture='{"echoCancellation": true, "sampleRate": 16000}',
    )
    assert response.status_code == 201
    with client.app.state.database.connect() as db:
        attempt = dict(db.execute("SELECT * FROM attempts").fetchone())
        audio = dict(
            db.execute(
                "SELECT * FROM audio_files WHERE id=?", (response.json()["audio_id"],)
            ).fetchone()
        )
    assert attempt["input_kind"] == "isolated_repeat"
    assert attempt["file_status"] == "ready"
    assert json.loads(attempt["capture_requested_json"]) == {
        "echoCancellation": True,
        "sampleRate": 16000,
    }
    assert json.loads(attempt["capture_actual_json"])["duration_s"] > 0
    assert audio["kind"] == "recording"
    assert audio["storage_key"] == audio["filename"]
    assert audio["content_hash"]
    assert audio["save_status"] == "ready"


def test_invalid_input_kind_is_rejected(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    response = upload(client, exercise["id"], kind="guessed_isolated")
    assert response.status_code == 422


def test_late_upload_stays_on_original_exercise(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    # Advance the independent session to a second turn before uploading.
    client.post(
        f"/v1/turns/{tid}/confirm",
        json={"request_id": str(uuid4()), "answer_en": exercise["reference_text"]},
    )
    response = upload(client, exercise["id"], key="late")
    assert response.status_code == 201
    with client.app.state.database.connect() as db:
        attempt = dict(db.execute("SELECT * FROM attempts").fetchone())
    assert attempt["exercise_id"] == exercise["id"]


def test_not_ready_audio_is_not_served(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    audio_id = upload(client, exercise["id"], key="take-1").json()["audio_id"]
    assert client.get(f"/v1/audio/{audio_id}").status_code == 200
    with client.app.state.database.connect() as db:
        db.execute("UPDATE audio_files SET save_status='missing' WHERE id=?", (audio_id,))
    assert client.get(f"/v1/audio/{audio_id}").status_code == 404


def test_tts_audio_is_marked_as_tts(client, monkeypatch):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    client.app.state.providers.settings.tts_provider = "http"
    monkeypatch.setattr(client.app.state.providers, "speech", lambda text, voice: wav_bytes())
    response = client.post("/v1/tts", json={"source_type": "exercise", "source_id": exercise["id"]})
    assert response.status_code == 200, response.text
    with client.app.state.database.connect() as db:
        audio = dict(
            db.execute(
                "SELECT * FROM audio_files WHERE id=?", (response.json()["audio_id"],)
            ).fetchone()
        )
    assert audio["kind"] == "tts"
    assert audio["storage_key"] == audio["filename"]
