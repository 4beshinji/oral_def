"""Free speech commits only a final transcript or explicit manual fallback."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.errors import APIError
from backend.app.main import create_app
from backend.tests.test_drill import PACK, wav_bytes


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, text_provider="mock", tts_provider="mock"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as value:
        yield value


def start_turn(client):
    created = client.post(
        "/v1/sessions", json={"mode": "free_speech", "pack": PACK, "scenario": "networking"}
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    base = f"/v1/sessions/{sid}"
    state = client.post(base + "/conversation/control", json={"action": "resume"}).json()
    state = client.post(
        base + "/conversation/step", json={"revision": state["revision"], "route": "browser"}
    ).json()
    playback = client.post(
        base + "/conversation/playbacks",
        json={"revision": state["revision"], "route": "browser"},
    ).json()
    state = client.post(
        base + "/conversation/ended",
        json={"playback_id": playback["playback_id"], "request_id": str(uuid4())},
    ).json()
    assert state["stage"] == "free_speech_input"
    return sid, state


def upload(client, sid, state, key=None):
    response = client.post(
        f"/v1/sessions/{sid}/free-speech/recordings",
        data={
            "turn_id": state["turn_id"],
            "revision": state["revision"],
            "client_key": key or str(uuid4()),
            "capture": "{}",
        },
        files={"file": ("speech.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def transcribe(client, sid, state, rid, request_id=None):
    return client.post(
        f"/v1/sessions/{sid}/free-speech/transcribe",
        json={
            "request_id": request_id or str(uuid4()),
            "turn_id": state["turn_id"],
            "recording_id": rid,
            "revision": state["revision"],
        },
    )


def test_final_transcript_commits_once_and_has_recording(client, monkeypatch):
    sid, state = start_turn(client)
    saved = upload(client, sid, state, "same-key")
    assert upload(client, sid, state, "same-key") == saved
    listed = client.get(f"/v1/sessions/{sid}/free-speech/recordings?turn_id={state['turn_id']}")
    assert listed.status_code == 200
    assert listed.json()["recordings"][0]["audio_id"] == saved["audio_id"]
    monkeypatch.setattr(
        client.app.state.asr,
        "transcribe",
        lambda path: {"status": "ok", "text": "I use a synthetic baseline."},
    )
    key = str(uuid4())
    first = transcribe(client, sid, state, saved["recording_id"], key)
    assert first.status_code == 200, first.text
    assert first.json()["text"] == "I use a synthetic baseline."
    assert upload(client, sid, state, "same-key") == saved
    assert transcribe(client, sid, state, saved["recording_id"], key).json() == first.json()
    assert transcribe(client, sid, state, saved["recording_id"]).status_code == 409
    turn = client.get(f"/v1/sessions/{sid}/turns/{state['turn_id']}").json()
    assert turn["confirmed_answer_en"] == "I use a synthetic baseline."
    assert turn["transcript"] == turn["confirmed_answer_en"]
    assert turn["submitted_via"] == "free_speech_transcript"
    assert turn["has_recording"] is True
    exported = client.get(f"/v1/sessions/{sid}/export").json()["session"]["turns"][0]
    assert exported["transcript"] == turn["transcript"]
    assert exported["source_recording_id"] == saved["recording_id"]
    backup = client.post("/v1/maintenance/backup", json={})
    assert backup.status_code == 201, backup.text
    assert backup.json()["verify"]["ok"] is True
    assert client.delete(f"/v1/sessions/{sid}").status_code == 200
    assert not list(client.app.state.database.audio_dir.glob(saved["audio_id"] + ".*"))


def test_no_speech_failure_and_manual_fallback(client, monkeypatch):
    sid, state = start_turn(client)
    rid = upload(client, sid, state)["recording_id"]
    monkeypatch.setattr(
        client.app.state.asr, "transcribe", lambda path: {"status": "no_speech", "text": ""}
    )
    assert transcribe(client, sid, state, rid).json()["status"] == "no_speech"
    assert (
        client.get(f"/v1/sessions/{sid}/turns/{state['turn_id']}").json()["confirmed_answer_en"]
        is None
    )

    def failed(path):
        raise APIError(503, "asr_unavailable", "No model", True)

    monkeypatch.setattr(client.app.state.asr, "transcribe", failed)
    assert transcribe(client, sid, state, rid).status_code == 503
    assert (
        client.get(f"/v1/sessions/{sid}/turns/{state['turn_id']}").json()["confirmed_answer_en"]
        is None
    )
    body = {
        "request_id": str(uuid4()),
        "turn_id": state["turn_id"],
        "revision": state["revision"],
        "text": "I have not run that experiment.",
    }
    path = f"/v1/sessions/{sid}/free-speech/manual"
    manual = client.post(path, json=body)
    assert manual.status_code == 200, manual.text
    assert client.post(path, json=body).json() == manual.json()
    turn = client.get(f"/v1/sessions/{sid}/turns/{state['turn_id']}").json()
    assert turn["submitted_via"] == "free_speech_manual"
    assert turn["transcript"] is None
    exported = client.get(f"/v1/sessions/{sid}/export").json()["session"]["turns"][0]
    assert exported["has_recording"] is True
    assert exported["transcript"] is None


def test_pause_mode_change_invalidates_late_asr(client, monkeypatch):
    sid, state = start_turn(client)
    rid = upload(client, sid, state)["recording_id"]

    def late(path):
        paused = client.post(
            f"/v1/sessions/{sid}/conversation/control", json={"action": "pause"}
        ).json()
        changed = client.post(
            f"/v1/sessions/{sid}/conversation/mode",
            json={"mode": "shadowing", "revision": paused["revision"]},
        )
        assert changed.status_code == 200
        restarted = client.post(
            f"/v1/sessions/{sid}/conversation/control", json={"action": "resume"}
        )
        assert restarted.status_code == 200
        return {"status": "ok", "text": "Late transcript."}

    monkeypatch.setattr(client.app.state.asr, "transcribe", late)
    response = transcribe(client, sid, state, rid)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "interrupted"
    old_pause = client.post(
        f"/v1/sessions/{sid}/conversation/control",
        json={"action": "pause", "revision": state["revision"]},
    )
    assert old_pause.status_code == 409
    assert client.get(f"/v1/sessions/{sid}/conversation").json()["status"] == "running"
    assert (
        client.get(f"/v1/sessions/{sid}/turns/{state['turn_id']}").json()["confirmed_answer_en"]
        is None
    )
