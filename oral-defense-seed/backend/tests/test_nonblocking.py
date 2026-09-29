from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import audio, pronunciation
from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.test_conversation import (
    control,
    ended,
    model_ready,
    session,
    snapshot,
    start,
    step,
)
from backend.tests.test_drill import PACK, fixed, new_turn, recorded, wav_bytes


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, text_provider="mock", tts_provider="mock"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as value:
        yield value


def request_id():
    return {"request_id": str(uuid4())}


def test_slow_provider_does_not_serialize_other_sessions(client, monkeypatch):
    first_sid, first_tid = new_turn(client)
    second_sid = client.post("/v1/sessions", json={"mode": "independent", "pack": PACK}).json()[
        "session_id"
    ]
    entered, release = Event(), Event()
    original = client.app.state.providers.text

    def slow(role, messages, **kwargs):
        if kwargs.get("session_id") != first_sid:
            return original(role, messages, **kwargs)
        entered.set()
        assert release.wait(5)
        return original(role, messages, **kwargs)

    monkeypatch.setattr(client.app.state.providers, "text", slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(
            client.post,
            f"/v1/turns/{first_tid}/coach",
            json={**request_id(), "level": "hint"},
        )
        assert entered.wait(5)
        try:
            response = client.post(f"/v1/sessions/{second_sid}/question", json=request_id())
            assert response.status_code == 200, response.text
        finally:
            release.set()
        assert future.result().status_code == 200


def test_interrupted_independent_question_cannot_commit_late_result(client, monkeypatch):
    sid = client.post("/v1/sessions", json={"mode": "independent", "pack": PACK}).json()[
        "session_id"
    ]
    entered, release = Event(), Event()
    original = client.app.state.providers.text

    def slow(role, messages, **kwargs):
        if role == "examiner":
            entered.set()
            assert release.wait(5)
        return original(role, messages, **kwargs)

    monkeypatch.setattr(client.app.state.providers, "text", slow)
    request = request_id()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(client.post, f"/v1/sessions/{sid}/question", json=request)
        assert entered.wait(5)
        try:
            client.app.state.database.recover()
        finally:
            release.set()
        response = future.result()
    assert response.status_code == 409
    assert response.json()["code"] == "stale_operation"
    with client.app.state.database.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM turns WHERE session_id=?", (sid,)).fetchone()[0] == 0
        )
        assert (
            db.execute(
                "SELECT status FROM requests WHERE request_id=?", (request["request_id"],)
            ).fetchone()[0]
            == "interrupted"
        )


def test_slow_assessment_does_not_block_new_work(client, monkeypatch):
    sid, tid = new_turn(client)
    exercise = fixed(client, tid)
    attempt = recorded(client, exercise["id"])
    entered, release = Event(), Event()
    original = pronunciation.assess

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(pronunciation, "assess", slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(
            client.post,
            f"/v1/attempts/{attempt['attempt_id']}/assess",
            json=request_id(),
        )
        assert entered.wait(5)
        try:
            created = client.post("/v1/sessions", json={"mode": "independent", "pack": PACK})
            assert created.status_code == 201
            assert client.get(f"/v1/sessions/{sid}").status_code == 200
        finally:
            release.set()
        assert future.result().status_code == 200


def test_assessment_finishes_on_original_attempt_after_same_session_advances(client, monkeypatch):
    sid = session(client)
    state = model_ready(client, sid)
    original_turn = snapshot(client, sid)["turns"][0]
    exercise_id = state["reference_id"]
    uploaded = client.post(
        f"/v1/exercises/{exercise_id}/audio",
        files={"file": ("recording.wav", wav_bytes(), "audio/wav")},
        data={"input_kind": "isolated_repeat", "client_attempt_key": "late-assessment"},
    )
    assert uploaded.status_code == 201, uploaded.text
    attempt_id = uploaded.json()["attempt_id"]
    entered, release = Event(), Event()
    original = pronunciation.assess

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(pronunciation, "assess", slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(
            client.post,
            f"/v1/attempts/{attempt_id}/assess",
            json=request_id(),
        )
        assert entered.wait(5)
        try:
            state = control(client, sid, "pause")
            assert state["status"] == "paused"
            state = control(client, sid, "resume")
            playback = start(client, sid, state)
            state = ended(client, sid, playback)
            step(client, sid, state)
        finally:
            release.set()
        assert future.result().status_code == 200
    saved = snapshot(client, sid)
    assert len(saved["turns"]) == 2
    assert saved["turns"][0]["id"] == original_turn["id"]
    assert saved["turns"][0]["exercises"][0]["attempts"][0]["id"] == attempt_id
    with client.app.state.database.connect() as db:
        run = db.execute(
            "SELECT attempt_id,execution_status FROM assessment_runs WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        assert (run["attempt_id"], run["execution_status"]) == (attempt_id, "succeeded")


def test_late_upload_keeps_original_reference_after_next_turn(client):
    sid = session(client)
    state = model_ready(client, sid)
    exercise_id = state["reference_id"]
    original_turn_id = state["turn_id"]
    playback = start(client, sid, state)
    state = ended(client, sid, playback)
    state = step(client, sid, state)
    assert state["turn_id"] != original_turn_id
    uploaded = client.post(
        f"/v1/exercises/{exercise_id}/audio",
        files={"file": ("recording.wav", wav_bytes(), "audio/wav")},
        data={"input_kind": "isolated_repeat", "client_attempt_key": "after-next-turn"},
    )
    assert uploaded.status_code == 201, uploaded.text
    saved = snapshot(client, sid)
    assert saved["turns"][0]["exercises"][0]["attempts"][0]["id"] == uploaded.json()["attempt_id"]
    assert saved["turns"][1]["exercises"] == []


def test_delete_during_upload_inspection_rejects_late_file(client, monkeypatch, tmp_path):
    sid, tid = new_turn(client)
    exercise = fixed(client, tid)
    entered, release = Event(), Event()
    original = audio.inspect_upload

    def slow(path):
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(audio, "inspect_upload", slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(
            client.post,
            f"/v1/exercises/{exercise['id']}/audio",
            files={"file": ("recording.wav", wav_bytes(), "audio/wav")},
            data={"client_attempt_key": "delete-race"},
        )
        assert entered.wait(5)
        try:
            assert client.delete(f"/v1/sessions/{sid}").status_code == 200
        finally:
            release.set()
        assert future.result().status_code == 409
    assert list((tmp_path / "audio").iterdir()) == []


def test_delete_during_assessment_rejects_late_result(client, monkeypatch):
    sid, tid = new_turn(client)
    exercise = fixed(client, tid)
    attempt = recorded(client, exercise["id"])
    entered, release = Event(), Event()
    original = pronunciation.assess

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(pronunciation, "assess", slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(
            client.post,
            f"/v1/attempts/{attempt['attempt_id']}/assess",
            json=request_id(),
        )
        assert entered.wait(5)
        try:
            assert client.delete(f"/v1/sessions/{sid}").status_code == 200
        finally:
            release.set()
        late = future.result()
        assert late.status_code == 409
        assert late.json()["code"] == "session_deleting"
    with client.app.state.database.connect() as db:
        assert db.execute("SELECT count(*) FROM assessment_runs").fetchone()[0] == 0


def test_concurrent_steps_do_not_create_two_turns(client):
    sid = session(client)
    state = control(client, sid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                client.post,
                f"/v1/sessions/{sid}/conversation/step",
                json={"revision": state["revision"], "route": "browser"},
            )
            for _ in range(2)
        ]
        statuses = sorted(future.result().status_code for future in futures)
    assert statuses == [200, 409]
    saved = snapshot(client, sid)
    assert len(saved["turns"]) == 1


def test_concurrent_same_key_upload_writes_one_attempt(client):
    _, tid = new_turn(client)
    exercise = fixed(client, tid)
    body = {"client_attempt_key": "race-key", "input_kind": "isolated_repeat"}

    def upload():
        return client.post(
            f"/v1/exercises/{exercise['id']}/audio",
            files={"file": ("recording.wav", wav_bytes(), "audio/wav")},
            data=body,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(upload) for _ in range(2)]]
    assert all(response.status_code == 201 for response in results), [r.text for r in results]
    assert results[0].json()["attempt_id"] == results[1].json()["attempt_id"]
    with client.app.state.database.connect() as db:
        assert db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1
