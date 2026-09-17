import io
import json
import wave
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import ROOT, Settings
from backend.app.contexts import coach_messages, examiner_messages
from backend.app.db import Database
from backend.app.dictation import compare
from backend.app.errors import APIError
from backend.app.main import create_app
from backend.app.providers import Providers

PACK = json.loads((ROOT / "examples/bo_pack.json").read_text())


def request_id():
    return {"request_id": str(uuid4())}


def wav_bytes(seconds=0.5):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * int(seconds * 16000))
    return stream.getvalue()


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, tts_provider="mock"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def new_turn(client):
    created = client.post(
        "/v1/sessions",
        json={
            "mode": "independent",
            "pack": PACK,
            "research_brief": "A synthetic test, not the user's research.",
        },
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    generated = client.post(f"/v1/sessions/{sid}/question", json=request_id())
    assert generated.status_code == 200, generated.text
    return sid, generated.json()["id"]


def fixed(client, turn_id, text="I compare against random search.", mode="read_aloud"):
    response = client.post(
        f"/v1/turns/{turn_id}/exercises", json={"mode": mode, "text": text, "origin": "manual"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def recorded(client, exercise_id):
    response = client.post(
        f"/v1/exercises/{exercise_id}/audio",
        files={"file": ("../../outside.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_context_private_sentinel_only_enters_examiner_after_confirmation():
    secret = "PRIVATE_COACH_SENTINEL_915"
    turns = [
        {
            "question_en": "Why?",
            "confirmed_answer_en": None,
            "draft": secret,
            "coach_messages": [secret],
        }
    ]
    kwargs = dict(
        pack=PACK,
        research_brief="brief",
        turns=turns,
        scenario="seminar",
        settings={
            "language_level": "simple",
            "technical_depth": "research",
            "strictness": "strict",
        },
        follow_up_count=0,
    )
    assert secret not in json.dumps(examiner_messages(**kwargs))
    private = coach_messages(
        pack=PACK,
        research_brief="brief",
        turns=turns,
        current_question="Why?",
        history=[],
        level="hint",
        user_note=secret,
        draft=secret,
    )
    assert secret in json.dumps(private)
    turns[0]["confirmed_answer_en"] = secret
    assert secret in json.dumps(examiner_messages(**kwargs))


def test_context_boundary_at_provider_call(client, monkeypatch):
    sid, tid = new_turn(client)
    secret = "PRIVATE_COACH_SENTINEL_915"
    client.post(
        f"/v1/turns/{tid}/coach",
        json={**request_id(), "level": "full_answer", "user_note": secret, "draft": secret},
    )
    fixed(client, tid)
    client.post(
        f"/v1/turns/{tid}/confirm",
        json={**request_id(), "answer_en": "I compare against random search."},
    )
    original = client.app.state.providers.text

    def observe(role, messages, **kwargs):
        assert role == "examiner"
        assert secret not in json.dumps(messages)
        assert "I compare against random search." in json.dumps(messages)
        return original(role, messages, **kwargs)

    monkeypatch.setattr(client.app.state.providers, "text", observe)
    assert client.post(f"/v1/sessions/{sid}/question", json=request_id()).status_code == 200


def test_dictation_normalization_and_real_word_differences():
    assert compare("Hello, WORLD!", "  hello world ")["matches"]
    assert compare("ＦＵＬＬ width.", "full width")["matches"]
    assert not compare("Don't stop", "do not stop")["matches"]
    assert not compare("2 trials", "two trials")["matches"]
    assert not compare("a good baseline", "a baseline")["matches"]


def test_reference_immutable_and_confirm_only_once(client):
    sid, tid = new_turn(client)
    ex = fixed(client, tid)
    recorded(client, ex["id"])
    assert client.patch(f"/v1/exercises/{ex['id']}", json={"text": "changed"}).status_code in {
        404,
        405,
    }
    new = fixed(client, tid, "A new reference.")
    assert new["id"] != ex["id"]
    body = {**request_id(), "answer_en": "A new reference."}
    assert client.post(f"/v1/turns/{tid}/confirm", json=body).status_code == 200
    assert client.post(f"/v1/turns/{tid}/confirm", json=body).status_code == 200
    assert (
        client.post(
            f"/v1/turns/{tid}/confirm", json={**request_id(), "answer_en": "changed"}
        ).status_code
        == 409
    )
    data = client.get(f"/v1/sessions/{sid}").json()
    assert data["turns"][0]["exercises"][0]["reference_text"] == ex["reference_text"]
    assert data["turns"][0]["submitted_via"] == "confirmed_reference"


def test_order_idempotency_failure_and_request_conflict(client, monkeypatch):
    sid, tid = new_turn(client)
    assert client.post(f"/v1/sessions/{sid}/question", json=request_id()).status_code == 409
    assert (
        client.post(
            f"/v1/turns/{tid}/confirm", json={**request_id(), "answer_en": "unfixed"}
        ).status_code
        == 409
    )
    fixed(client, tid)
    confirmed = {**request_id(), "answer_en": "I compare against random search."}
    assert client.post(f"/v1/turns/{tid}/confirm", json=confirmed).status_code == 200
    assert (
        client.post(
            f"/v1/turns/{tid}/confirm", json={**confirmed, "answer_en": "other"}
        ).status_code
        == 409
    )
    original = client.app.state.providers.text

    def fail(*args, **kwargs):
        raise APIError(504, "provider_timeout", "injected failure", True)

    monkeypatch.setattr(client.app.state.providers, "text", fail)
    body = request_id()
    assert client.post(f"/v1/sessions/{sid}/question", json=body).status_code == 504
    assert client.post(f"/v1/sessions/{sid}/question", json=body).status_code == 409
    data = client.get(f"/v1/sessions/{sid}").json()
    assert data["turns"][0]["confirmed_answer_en"] == confirmed["answer_en"]
    monkeypatch.setattr(client.app.state.providers, "text", original)
    body = request_id()
    first = client.post(f"/v1/sessions/{sid}/question", json=body)
    assert first.status_code == 200
    assert client.post(f"/v1/sessions/{sid}/question", json=body).json() == first.json()
    assert len(client.get(f"/v1/sessions/{sid}").json()["turns"]) == 2


def test_record_retake_unavailable_export_and_delete(client, tmp_path):
    sid, tid = new_turn(client)
    ex = fixed(client, tid)
    a, b = recorded(client, ex["id"]), recorded(client, ex["id"])
    assert a["attempt_id"] != b["attempt_id"] and a["audio_id"] != b["audio_id"]
    assert client.get("/v1/audio/" + a["audio_id"]).content == wav_bytes()
    result = client.post("/v1/attempts/" + a["attempt_id"] + "/assess", json=request_id()).json()
    assert result["status"] == "unavailable" and result["calibrated_score"] is None
    assert result["phones"] == [] and result["reference_hash"] == ex["reference_hash"]
    client.app.state.providers.settings.text_api_key = "TEST_API_SECRET"
    exported = client.get(f"/v1/sessions/{sid}/export")
    assert "TEST_API_SECRET" not in exported.text
    assert str(tmp_path) not in exported.text
    assert exported.json()["schema_version"] == "1.0"
    assert len(exported.json()["session"]["turns"][0]["exercises"][0]["attempts"]) == 2
    assert client.delete(f"/v1/sessions/{sid}").status_code == 200
    assert not list((tmp_path / "audio").iterdir())
    assert client.get("/v1/audio/" + a["audio_id"]).status_code == 404
    with client.app.state.database.connect() as db:
        for table in ("turns", "exercises", "attempts", "audio_files", "assistance", "requests"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_acoustic_timeout_keeps_original_and_reference(client, monkeypatch):
    from backend.app import pronunciation

    sid, tid = new_turn(client)
    ex = fixed(client, tid)
    attempt = recorded(client, ex["id"])

    def fail(*args):
        raise APIError(504, "assessment_timeout", "Injected timeout", True)

    monkeypatch.setattr(pronunciation, "assess", fail)
    assert (
        client.post(
            "/v1/attempts/" + attempt["attempt_id"] + "/assess", json=request_id()
        ).status_code
        == 504
    )
    assert client.get("/v1/audio/" + attempt["audio_id"]).content == wav_bytes()
    assert (
        client.get(f"/v1/sessions/{sid}").json()["turns"][0]["exercises"][0]["reference_hash"]
        == ex["reference_hash"]
    )


def test_invalid_origin_host_paths_and_cross_session(client):
    sid, tid = new_turn(client)
    sid2, tid2 = new_turn(client)
    ex = fixed(client, tid)
    response = client.post(
        "/v1/assistance", json={"turn_id": tid2, "exercise_id": ex["id"], "kind": "subtitle_shown"}
    )
    assert response.status_code == 409
    assert (
        client.get("/v1/capabilities", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert client.get("/v1/capabilities", headers={"Host": "evil.example"}).status_code == 403
    assert (
        client.post(
            "/v1/sessions", content="{}", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert client.get("/v1/audio/%2E%2E%2Fsecret").status_code in {404, 422}
    assert (
        client.post(
            "/v1/tts", json={"source_type": "exercise", "source_id": "../../etc/passwd"}
        ).status_code
        == 422
    )
    assert client.get(f"/v1/sessions/{sid2}").status_code == 200


def test_audio_validation_and_dictation_mode(client):
    sid, tid = new_turn(client)
    ex = fixed(client, tid)
    response = client.post(
        "/v1/exercises/" + ex["id"] + "/audio",
        files={"file": ("fake.wav", b"not audio", "audio/wav")},
    )
    assert response.status_code == 422
    response = client.post(
        "/v1/exercises/" + ex["id"] + "/audio",
        files={"file": ("long.wav", wav_bytes(31), "audio/wav")},
    )
    assert response.status_code == 422
    d = fixed(client, tid, "Hello world.", "dictation")
    response = client.post(
        "/v1/exercises/" + d["id"] + "/dictation", json={"typed_text": "HELLO, world!"}
    )
    assert response.json()["result"]["matches"]
    assert (
        client.post(
            "/v1/attempts/" + response.json()["attempt_id"] + "/assess", json=request_id()
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/v1/exercises/" + ex["id"] + "/dictation", json={"typed_text": "hello"}
        ).status_code
        == 409
    )


def test_session_and_coach_limits(client):
    sid, tid = new_turn(client)
    for _ in range(12):
        assert (
            client.post(
                f"/v1/turns/{tid}/coach", json={**request_id(), "level": "hint"}
            ).status_code
            == 200
        )
    assert (
        client.post(f"/v1/turns/{tid}/coach", json={**request_id(), "level": "hint"}).status_code
        == 409
    )
    for number in range(6):
        fixed(client, tid)
        assert (
            client.post(
                f"/v1/turns/{tid}/confirm",
                json={**request_id(), "answer_en": "I compare against random search."},
            ).status_code
            == 200
        )
        if number < 5:
            result = client.post(f"/v1/sessions/{sid}/question", json=request_id())
            assert result.status_code == 200, result.text
            tid = result.json()["id"]
            assert result.json()["follow_up_count"] <= 2
    assert client.post(f"/v1/sessions/{sid}/question", json=request_id()).status_code == 409
    assert client.get(f"/v1/sessions/{sid}").json()["status"] == "completed"


def test_operations_are_independent_of_a_process_wide_lock(client, monkeypatch):
    sid, tid = new_turn(client)
    entered, release = Event(), Event()
    original = client.app.state.providers.text

    def slow(role, messages, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(role, messages, **kwargs)

    monkeypatch.setattr(client.app.state.providers, "text", slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(
            client.post, f"/v1/turns/{tid}/coach", json={**request_id(), "level": "hint"}
        )
        assert entered.wait(5)
        try:
            # A slow provider call must not serialize unrelated writes.
            assert client.delete(f"/v1/sessions/{sid}").status_code == 200
        finally:
            release.set()
        # The late result must not revive the deleted session.
        assert future.result().status_code in {200, 404, 409}
    assert client.get(f"/v1/sessions/{sid}").status_code == 404


def test_processing_requests_mark_interrupted_on_restart(client, tmp_path):
    sid, tid = new_turn(client)
    with client.app.state.database.connect() as db:
        db.execute("UPDATE requests SET status='processing'")
    Database(tmp_path)
    with client.app.state.database.connect() as db:
        assert db.execute("SELECT status FROM requests").fetchone()[0] == "interrupted"


def test_tts_cache_is_owned_by_session_and_deleted(client, monkeypatch):
    sid, tid = new_turn(client)
    other_sid, other_tid = new_turn(client)
    client.app.state.providers.settings.tts_provider = "http"
    calls = []

    def speech(text, voice):
        calls.append((text, voice))
        return wav_bytes()

    monkeypatch.setattr(client.app.state.providers, "speech", speech)
    body = {"source_type": "question", "source_id": tid}
    first = client.post("/v1/tts", json=body)
    assert first.status_code == 200
    assert client.post("/v1/tts", json=body).json() == first.json()
    assert len(calls) == 1
    second = client.post("/v1/tts", json={"source_type": "question", "source_id": other_tid})
    assert second.json()["audio_id"] != first.json()["audio_id"]
    assert len(calls) == 2
    assert client.delete(f"/v1/sessions/{sid}").status_code == 200
    assert client.get("/v1/audio/" + first.json()["audio_id"]).status_code == 404
    assert client.get("/v1/audio/" + second.json()["audio_id"]).status_code == 200


def test_upload_size_limit_before_multipart_parsing(client):
    response = client.post(
        "/v1/sessions",
        content=b" " * (10 * 1024 * 1024 + 65537),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_cannot_answer_moves_to_new_angle(client):
    sid, tid = new_turn(client)
    fixed(client, tid, "I cannot answer yet.")
    assert (
        client.post(
            f"/v1/turns/{tid}/confirm",
            json={**request_id(), "answer_en": "I cannot answer yet.", "unable_to_answer": True},
        ).status_code
        == 200
    )
    question = client.post(f"/v1/sessions/{sid}/question", json=request_id())
    assert question.status_code == 200
    assert question.json()["follow_up_count"] == 0


def test_real_adapter_contract_with_http_fixture(monkeypatch, tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        text_provider="compatible",
        text_base_url="http://127.0.0.1:9999/v1",
        text_model="fixture-model",
        tts_provider="http",
        tts_base_url="http://127.0.0.1:9999/speech",
    )
    real_client = httpx.Client
    observed = []

    def handle(request):
        observed.append(json.loads(request.content))
        if request.url.path == "/speech":
            return httpx.Response(200, content=wav_bytes())
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "question_en": "Why this method?",
                                    "basis_note": "General question",
                                    "follow_up": False,
                                }
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    provider = Providers(settings)
    assert (
        provider.text("examiner", [{"role": "user", "content": "{}"}], session_id=str(uuid4()))[
            "question_en"
        ]
        == "Why this method?"
    )
    assert provider.speech("Hello", "alloy") == wav_bytes()
    assert observed[0]["model"] == "fixture-model" and "conversation_id" not in observed[0]
    assert observed[1]["response_format"] == "wav"


@pytest.mark.parametrize(
    "content",
    ["{}", '{"question_en":"Why?","basis_note":"General","follow_up":"false"}', "not json"],
)
def test_invalid_provider_response_is_not_saved(monkeypatch, tmp_path, content):
    settings = Settings(
        data_dir=tmp_path,
        text_provider="compatible",
        text_base_url="http://127.0.0.1:9999/v1",
        text_model="fixture",
    )
    real_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    with pytest.raises(APIError) as error:
        Providers(settings).text("examiner", [], session_id=str(uuid4()))
    assert error.value.status == 502


def test_opt_in_json_schema_and_received_length_validation(tmp_path, monkeypatch):
    from backend.app.providers import Coach, Question, generation_schema

    settings = Settings(
        data_dir=tmp_path,
        text_provider="compatible",
        text_base_url="http://127.0.0.1:10000/v1",
        text_model="local-fixture",
        text_response_format="json_schema",
    )
    providers = Providers(settings)
    requests = []
    responses = [
        {"question_en": "Why?", "basis_note": "Unknown", "follow_up": False},
        {"question_en": "x" * 2001, "basis_note": "Unknown", "follow_up": False},
    ]

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(responses.pop(0))}}]}
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    messages = [{"role": "user", "content": json.dumps({"must_change_angle": True})}]
    assert providers.text("examiner", messages, session_id=str(uuid4()))["question_en"] == "Why?"
    with pytest.raises(APIError) as caught:
        providers.text("examiner", messages, session_id=str(uuid4()))
    assert caught.value.body["code"] == "invalid_provider_response"
    schema = requests[0]["response_format"]["json_schema"]["schema"]
    assert schema["required"] == Question.model_json_schema()["required"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["follow_up"]["enum"] == [False]
    assert "maxLength" not in json.dumps(schema)
    coach_schema = generation_schema(Coach.model_json_schema())
    assert "maxItems" not in json.dumps(coach_schema)
    assert coach_schema["properties"]["answer_en"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
