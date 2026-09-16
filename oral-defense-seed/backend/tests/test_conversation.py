import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.contexts import coach_messages, examiner_messages
from backend.app.errors import APIError
from backend.app.main import create_app
from backend.tests.test_drill import PACK, wav_bytes


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, text_provider="mock", tts_provider="mock"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as value:
        yield value


def session(client):
    response = client.post("/v1/sessions", json={"pack": PACK, "scenario": "networking"})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def post(client, sid, endpoint, body):
    response = client.post(f"/v1/sessions/{sid}/conversation/{endpoint}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def control(client, sid, action="resume"):
    return post(client, sid, "control", {"action": action})


def step(client, sid, state):
    return post(client, sid, "step", {"revision": state["revision"], "route": "browser"})


def start(client, sid, state):
    return post(client, sid, "playbacks", {"revision": state["revision"], "route": "browser"})


def ended(client, sid, playback, request_id=None):
    return post(
        client,
        sid,
        "ended",
        {"playback_id": playback["playback_id"], "request_id": request_id or str(uuid4())},
    )


def snapshot(client, sid):
    return client.get(f"/v1/sessions/{sid}").json()


def model_ready(client, sid):
    state = step(client, sid, control(client, sid))
    playback = start(client, sid, state)
    return step(client, sid, ended(client, sid, playback))


def test_thirteen_turns_no_recording_and_private_context(client, monkeypatch):
    sid = session(client)
    original = client.app.state.providers.text
    calls = []

    def provider(role, messages, **kwargs):
        calls.append((role, json.loads(messages[-1]["content"])))
        result = original(role, messages, **kwargs)
        if role != "examiner":
            result["explanation_ja"] = "PRIVATE_SENTINEL"
        return result

    monkeypatch.setattr(client.app.state.providers, "text", provider)
    state = control(client, sid)
    for number in range(13):
        state = step(client, sid, state)
        assert state["stage"] == "question_playback"
        assert snapshot(client, sid)["turns"][-1]["confirmed_answer_en"] is None
        question = start(client, sid, state)
        state = ended(client, sid, question)
        assert snapshot(client, sid)["turns"][-1]["confirmed_answer_en"] is None
        state = step(client, sid, state)
        assert state["stage"] == "model_playback"
        playback = start(client, sid, state)
        assert snapshot(client, sid)["turns"][-1]["confirmed_answer_en"] is None
        identity = str(uuid4())
        state = ended(client, sid, playback, identity)
        assert ended(client, sid, playback, identity) == state
        assert ended(client, sid, playback) == state
        saved = snapshot(client, sid)
        assert len(saved["turns"]) == number + 1
        assert saved["turns"][-1]["confirmed_answer_en"] == playback["text"]
        assert saved["turns"][-1]["submitted_via"] == "shadowing_playback"
        assert saved["turns"][-1]["has_recording"] is False
        assert saved["turns"][-1]["transcript"] is None
    examiner_inputs = [payload for role, payload in calls if role == "examiner"]
    assert all("PRIVATE_SENTINEL" not in json.dumps(payload) for payload in examiner_inputs)
    assert examiner_inputs[0]["confirmed_public_turns"] == []
    assert examiner_inputs[1]["confirmed_public_turns"][0]["answer_en"] == playback["text"]
    assert len(examiner_inputs[-1]["confirmed_public_turns"]) <= 12
    exported = client.get(f"/v1/sessions/{sid}/export").json()["session"]
    assert len(exported["playbacks"]) == 26
    assert (
        exported["playbacks"][-1]["reference_hash"]
        == exported["turns"][-1]["exercises"][0]["reference_hash"]
    )


def test_cancel_reload_foreign_and_obsolete_playbacks(client):
    sid = session(client)
    state = model_ready(client, sid)
    playback = start(client, sid, state)
    paused = control(client, sid, "pause")
    body = {"request_id": str(uuid4()), "playback_id": playback["playback_id"]}
    assert client.post(f"/v1/sessions/{sid}/conversation/ended", json=body).status_code == 409
    other = session(client)
    assert client.post(f"/v1/sessions/{other}/conversation/ended", json=body).status_code == 409
    assert snapshot(client, sid)["conversation"] == paused
    assert snapshot(client, sid)["turns"][0]["confirmed_answer_en"] is None
    state = control(client, sid)
    first = start(client, sid, state)
    replacement = start(client, sid, first["state"])
    assert (
        client.post(
            f"/v1/sessions/{sid}/conversation/ended",
            json={**body, "playback_id": first["playback_id"]},
        ).status_code
        == 409
    )
    state = ended(client, sid, replacement)
    assert state["stage"] == "question_generation"
    control(client, sid, "end")
    assert (
        client.post(
            f"/v1/sessions/{sid}/conversation/step", json={"revision": state["revision"]}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/v1/sessions/{sid}/conversation/control", json={"action": "resume"}
        ).status_code
        == 409
    )


def test_generation_failure_preserves_commit_and_retry_no_duplicate(client, monkeypatch):
    sid = session(client)
    state = model_ready(client, sid)
    playback = start(client, sid, state)
    state = ended(client, sid, playback)
    original = client.app.state.providers.text

    def fail(*args, **kwargs):
        raise APIError(502, "fixture_failure", "fixture")

    monkeypatch.setattr(client.app.state.providers, "text", fail)
    assert (
        client.post(
            f"/v1/sessions/{sid}/conversation/step", json={"revision": state["revision"]}
        ).status_code
        == 502
    )
    assert snapshot(client, sid)["turns"][0]["confirmed_answer_en"] == playback["text"]
    monkeypatch.setattr(client.app.state.providers, "text", original)
    next_state = step(client, sid, state)
    assert len(snapshot(client, sid)["turns"]) == 2
    # Lost responses can be recovered from GET, not by creating another turn.
    assert (
        client.post(
            f"/v1/sessions/{sid}/conversation/step", json={"revision": state["revision"]}
        ).status_code
        == 409
    )
    assert ended(client, sid, playback) == next_state


def test_pause_during_provider_call_discards_late_generation(client, monkeypatch):
    sid = session(client)
    state = control(client, sid)
    entered, release = Event(), Event()
    original = client.app.state.providers.text

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(client.app.state.providers, "text", slow)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(
            client.post,
            f"/v1/sessions/{sid}/conversation/step",
            json={"revision": state["revision"]},
        )
        try:
            assert entered.wait(5)
            control(client, sid, "pause")
        finally:
            release.set()
        assert pending.result().status_code == 409
    assert snapshot(client, sid)["turns"] == []


def test_tts_and_independent_audio_cannot_commit_and_delete_cascades(client):
    sid = session(client)
    state = model_ready(client, sid)
    eid = state["reference_id"]
    response = client.post("/v1/tts", json={"source_type": "exercise", "source_id": eid})
    assert response.status_code == 200
    assert snapshot(client, sid)["turns"][0]["confirmed_answer_en"] is None
    tid = state["turn_id"]
    assert (
        client.post(
            f"/v1/turns/{tid}/confirm", json={"request_id": str(uuid4()), "answer_en": "forged"}
        ).status_code
        == 409
    )
    playback = start(client, sid, state)
    ended(client, sid, playback)
    state = step(client, sid, snapshot(client, sid)["conversation"])
    # Late upload remains attached to the original exercise after advancement.
    upload = client.post(
        f"/v1/exercises/{eid}/audio", files={"file": ("recording.wav", wav_bytes(), "audio/wav")}
    )
    assert upload.status_code == 201, upload.text
    saved = snapshot(client, sid)
    assert saved["turns"][0]["has_recording"] is True
    assert saved["turns"][1]["has_recording"] is False
    assert client.delete(f"/v1/sessions/{sid}").status_code == 200
    with client.app.state.database.connect() as db:
        for table in [
            "conversations",
            "conversation_playbacks",
            "turns",
            "exercises",
            "attempts",
            "audio_files",
        ]:
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_context_budgets():
    turns = [{"question_en": "q" * 2000, "confirmed_answer_en": "a" * 4000} for _ in range(100)]
    payload = examiner_messages(
        pack=PACK,
        research_brief="brief",
        turns=turns,
        scenario="seminar",
        settings={
            "language_level": "simple",
            "technical_depth": "research",
            "strictness": "supportive",
        },
        follow_up_count=0,
    )
    assert len(payload[-1]["content"]) < 25000
    history = [
        {
            "level": "full_answer",
            "user_note": "x" * 4000,
            "draft": "x" * 4000,
            "response_json": json.dumps({"explanation_ja": "x" * 8000}),
        }
        for _ in range(100)
    ]
    payload = coach_messages(
        pack=PACK,
        research_brief="brief",
        turns=turns,
        current_question="why?",
        history=history,
        level="full_answer",
        user_note="",
        draft="",
    )
    assert len(payload[-1]["content"]) < 33000


def test_reference_edit_creates_version_and_invalidates_old_completion(client):
    sid = session(client)
    state = model_ready(client, sid)
    playback = start(client, sid, state)
    control(client, sid, "pause")
    changed = post(client, sid, "reference", {"text": "I need to verify this claim first."})
    assert changed["reference_id"] != state["reference_id"]
    old = client.post(
        f"/v1/sessions/{sid}/conversation/ended",
        json={"request_id": str(uuid4()), "playback_id": playback["playback_id"]},
    )
    assert old.status_code == 409
    state = control(client, sid)
    edited = start(client, sid, state)
    ended(client, sid, edited)
    saved = snapshot(client, sid)["turns"][0]
    assert len(saved["exercises"]) == 2
    assert saved["exercises"][0]["reference_text"] == playback["text"]
    assert saved["confirmed_answer_en"] == "I need to verify this claim first."
    control(client, sid, "pause")
    assert (
        client.post(
            f"/v1/sessions/{sid}/conversation/reference",
            json={"text": "Changed after confirmation"},
        ).status_code
        == 409
    )


def test_saved_tts_uses_coach_voice_and_settings_changes_invalidate_cache(client, monkeypatch):
    catalog = client.app.state.providers.speech_catalog
    calls = []
    monkeypatch.setattr(catalog, "validate", lambda selection: selection)
    monkeypatch.setattr(catalog, "cache_identity", lambda selection, voice: [selection, voice])
    monkeypatch.setattr(
        catalog, "synthesize", lambda text, selection: calls.append(selection) or wav_bytes()
    )
    sid = session(client)
    assert (
        client.put(
            f"/v1/sessions/{sid}/speech-model",
            json={"role": "coach_answer", "model": "fixture-coach"},
        ).status_code
        == 200
    )
    state = model_ready(client, sid)
    prepared = post(client, sid, "step", {"revision": state["revision"], "route": "saved"})
    assert calls == ["fixture-coach"]
    assert prepared["audio_id"]
    assert snapshot(client, sid)["turns"][0]["confirmed_answer_en"] is None
    assert (
        client.put(
            f"/v1/sessions/{sid}/speech-model",
            json={"role": "coach_answer", "model": "fixture-other"},
        ).status_code
        == 409
    )
    control(client, sid, "pause")
    assert (
        client.put(
            f"/v1/sessions/{sid}/speech-model",
            json={"role": "coach_answer", "model": "fixture-other"},
        ).status_code
        == 200
    )
    assert snapshot(client, sid)["conversation"]["audio_id"] is None
    state = control(client, sid)
    prepared = post(client, sid, "step", {"revision": state["revision"], "route": "saved"})
    assert calls == ["fixture-coach", "fixture-other"]
    playback = post(
        client, sid, "playbacks", {"revision": prepared["revision"], "route": "saved", "rate": 0.7}
    )
    ended(client, sid, playback)
    assert snapshot(client, sid)["playbacks"][-1]["tts_settings"]["rate"] == 0.7


def test_restart_invalidates_playback_and_request_ids_cannot_be_reused(client):
    from backend.app.db import Database

    sid = session(client)
    state = model_ready(client, sid)
    playback = start(client, sid, state)
    Database(client.app.state.database.path.parent)
    body = {"request_id": str(uuid4()), "playback_id": playback["playback_id"]}
    assert client.post(f"/v1/sessions/{sid}/conversation/ended", json=body).status_code == 409
    state = control(client, sid)
    replacement = start(client, sid, state)
    ended(client, sid, replacement)
    identity = str(uuid4())
    state = ended(client, sid, replacement, identity)
    state = step(client, sid, state)
    question = start(client, sid, state)
    assert (
        client.post(
            f"/v1/sessions/{sid}/conversation/ended",
            json={"request_id": identity, "playback_id": question["playback_id"]},
        ).status_code
        == 409
    )
