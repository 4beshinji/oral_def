import json
import time
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import ROOT, Settings
from backend.app.errors import APIError
from backend.app.main import create_app
from backend.app.providers import Providers
from backend.app.text_models import BASE_URLS, METADATA_URL, ModelCatalog, conversation_headers

PACK = json.loads((ROOT / "examples/bo_pack.json").read_text())
QUESTION = {
    "question_en": "What evidence would you collect?",
    "basis_note": "General question",
    "follow_up": False,
}
COACH = {
    "explanation_ja": "確認済みの根拠を整理しましょう。",
    "answer_en": None,
    "needs_user_input": [],
}
# Synthetic API fixtures, never a production model list.
FAKE_MODELS = {
    "fixture-chat": "@ai-sdk/openai-compatible",
    "fixture-chat-two": "@ai-sdk/openai-compatible",
    "fixture-messages": "@ai-sdk/anthropic",
    "fixture-responses": "@ai-sdk/openai",
}


def settings_for(tmp_path):
    return Settings(
        data_dir=tmp_path,
        opencode_go_api_key="GO_TEST_SECRET",
        opencode_zen_api_key="ZEN_TEST_SECRET",
    )


def public_response(request, models=None):
    models = models or FAKE_MODELS
    if str(request.url) == METADATA_URL:
        return httpx.Response(
            200,
            json={
                provider: {
                    "api": endpoint,
                    "npm": "@ai-sdk/openai-compatible",
                    "models": {
                        model: {
                            "name": model,
                            "provider": {"npm": npm},
                            "modalities": {"output": ["text"]},
                        }
                        for model, npm in models.items()
                    },
                }
                for provider, endpoint in BASE_URLS.items()
            },
        )
    return httpx.Response(200, json={"data": [{"id": model} for model in models]})


def intercept(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize("provider", ["opencode-go", "opencode"])
@pytest.mark.parametrize(
    "model,suffix",
    [
        ("fixture-chat", "/chat/completions"),
        ("fixture-messages", "/messages"),
        ("fixture-responses", "/responses"),
    ],
)
def test_every_wire_format_has_stable_session_and_own_user_agent(
    tmp_path, monkeypatch, provider, model, suffix
):
    requests = []

    def handler(request):
        if request.method == "GET":
            return public_response(request)
        requests.append(request)
        assert str(request.url) == BASE_URLS[provider] + suffix
        payload = json.loads(request.content)
        assert payload["model"] == model
        expected_key = "GO_TEST_SECRET" if provider == "opencode-go" else "ZEN_TEST_SECRET"
        if suffix == "/messages":
            assert request.headers["x-api-key"] == expected_key
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert "system" in payload and all(m["role"] != "system" for m in payload["messages"])
            response = {"content": [{"type": "text", "text": json.dumps(QUESTION)}]}
        elif suffix == "/responses":
            assert payload["store"] is False
            assert "previous_response_id" not in payload and "conversation" not in payload
            response = {
                "output": [
                    {"type": "reasoning", "summary": []},
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(QUESTION)}],
                    },
                ]
            }
        else:
            response = {"choices": [{"message": {"content": json.dumps(QUESTION)}}]}
        if suffix != "/messages":
            assert request.headers["authorization"] == f"Bearer {expected_key}"
        return httpx.Response(200, json=response)

    intercept(monkeypatch, handler)
    adapter = Providers(settings_for(tmp_path))
    adapter.catalog.refresh()
    target = adapter.catalog.resolve(f"{provider}/{model}")
    sid = str(uuid4())
    messages = [{"role": "system", "content": "Return JSON"}, {"role": "user", "content": "{}"}]
    for _ in range(2):
        assert adapter.text("examiner", messages, session_id=sid, target=target) == QUESTION
    ids = [r.headers["x-opencode-session"] for r in requests]
    assert ids[0] == ids[1] and UUID(ids[0])
    assert all(r.headers["user-agent"] == "oral-defense-drill/0.4.0" for r in requests)
    assert ids[0] != conversation_headers(sid, "coach")["x-opencode-session"]
    assert ids[0] != conversation_headers(str(uuid4()), "examiner")["x-opencode-session"]
    assert "TEST_SECRET" not in repr(target)


def test_identity_required_before_http_request(tmp_path, monkeypatch):
    intercept(monkeypatch, lambda request: pytest.fail("Must not send without identity"))
    adapter = Providers(settings_for(tmp_path))
    with pytest.raises(TypeError):
        adapter.text("examiner", [])
    with pytest.raises(ValueError):
        adapter.text("examiner", [], session_id="")


def test_model_selection_header_retry_restart_and_private_context(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    app = create_app(settings)
    requests, fail_next = [], [False]

    def handler(request):
        if request.method == "GET":
            return public_response(request)
        requests.append(request)
        if fail_next[0]:
            fail_next[0] = False
            raise httpx.ReadTimeout("injected")
        payload = json.loads(request.content)
        body = json.loads(payload["messages"][-1]["content"])
        result = COACH if "requested_level" in body else QUESTION
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    intercept(monkeypatch, handler)
    app.state.providers.catalog.refresh()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        sid = client.post(
            "/v1/sessions",
            json={"mode": "independent", "pack": PACK, "text_model": "opencode-go/fixture-chat"},
        ).json()["session_id"]
        body = {"request_id": str(uuid4())}
        q = client.post(f"/v1/sessions/{sid}/question", json=body)
        assert q.status_code == 200
        tid = q.json()["id"]
        assert client.post(f"/v1/sessions/{sid}/question", json=body).json() == q.json()
        assert len(requests) == 1
        note = "COACH_PRIVATE_SENTINEL"
        client.post(
            f"/v1/turns/{tid}/coach",
            json={"request_id": str(uuid4()), "level": "hint", "user_note": note},
        )
        client.post(
            f"/v1/turns/{tid}/exercises", json={"mode": "read_aloud", "text": "I need evidence."}
        )
        client.post(
            f"/v1/turns/{tid}/confirm",
            json={"request_id": str(uuid4()), "answer_en": "I need evidence."},
        )
        assert (
            client.put(
                f"/v1/sessions/{sid}/text-model",
                json={"text_model": "opencode-go/fixture-chat-two"},
            ).status_code
            == 200
        )
        fail_next[0] = True
        assert (
            client.post(
                f"/v1/sessions/{sid}/question", json={"request_id": str(uuid4())}
            ).status_code
            == 504
        )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as client:
        assert (
            client.get(f"/v1/sessions/{sid}").json()["settings"]["text_model"]["id"]
            == "opencode-go/fixture-chat-two"
        )
        assert (
            client.post(
                f"/v1/sessions/{sid}/question", json={"request_id": str(uuid4())}
            ).status_code
            == 200
        )
        exported = client.get(f"/v1/sessions/{sid}/export").text
        assert "GO_TEST_SECRET" not in exported and "ZEN_TEST_SECRET" not in exported
    assert len(requests) == 4
    ids = [r.headers["x-opencode-session"] for r in requests]
    assert ids[0] == ids[2] == ids[3] and ids[1] != ids[0]
    assert note not in requests[3].content.decode()
    assert json.loads(requests[3].content)["model"] == "fixture-chat-two"


def test_catalog_dynamic_names_interval_failure_cache_and_unknown_protocol(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    calls, fail = [], [False]
    # A never-seen random model ID proves there is no model-name allowlist.
    new_model = "future-" + uuid4().hex
    remote_models = {new_model: "@ai-sdk/openai-compatible", "other-format": "@vendor/unknown-sdk"}

    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        assert (
            request.headers["x-opencode-session"]
            and request.headers["user-agent"] == "oral-defense-drill/0.4.0"
        )
        return httpx.Response(503) if fail[0] else public_response(request, remote_models)

    intercept(monkeypatch, handler)
    catalog = ModelCatalog(settings)
    assert catalog.candidates()["source"] == "empty"
    assert catalog.refresh(force=False)["source"] == "cached"
    assert len(calls) == 3
    assert catalog.resolve(f"opencode-go/{new_model}").protocol == "chat_completions"
    with pytest.raises(APIError):
        catalog.resolve("opencode-go/other-format")
    listed = catalog.candidates()["models"]
    before = len(calls)
    restarted = ModelCatalog(settings)
    restarted.refresh(force=False)
    assert len(calls) == before
    future = time.time() + 25 * 3600
    monkeypatch.setattr("backend.app.text_models.time.time", lambda: future)
    fail[0] = True
    result = restarted.refresh(force=False)
    assert result["source"] == "cached" and result["refresh_error"]
    assert restarted.candidates()["models"] == listed
    calls_after_failure = len(calls)
    ModelCatalog(settings).refresh(force=False)
    assert len(calls) == calls_after_failure
    assert result["next_refresh_at"] == future + 300


def test_missing_keys_and_unlisted_choices_fail_locally(tmp_path, monkeypatch):
    intercept(monkeypatch, public_response)
    catalog = ModelCatalog(
        Settings(
            data_dir=tmp_path,
            opencode_go_api_key="",
            opencode_zen_api_key="",
            opencode_auth_file=tmp_path / "absent-auth.json",
        )
    )
    catalog.refresh()
    with pytest.raises(APIError):
        catalog.resolve("opencode-go/fixture-chat")
    with pytest.raises(APIError):
        catalog.resolve("https://evil.example/model")
    assert any(m["id"].startswith("opencode-go/") for m in catalog.candidates()["models"])


def test_metadata_cannot_redirect_credentials(tmp_path, monkeypatch):
    def handler(request):
        result = public_response(request)
        if str(request.url) == METADATA_URL:
            payload = json.loads(result.content)
            payload["opencode-go"]["models"]["fixture-chat"]["provider"]["api"] = (
                "https://evil.example"
            )
            return httpx.Response(200, json=payload)
        return result

    intercept(monkeypatch, handler)
    catalog = ModelCatalog(settings_for(tmp_path))
    catalog.refresh()
    with pytest.raises(APIError):
        catalog.resolve("opencode-go/fixture-chat")


@pytest.mark.parametrize("legacy_without_text_model", [True, False])
def test_old_session_migration_preserves_mock(tmp_path, legacy_without_text_model):
    settings = settings_for(tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        sid = client.post("/v1/sessions", json={"mode": "independent", "pack": PACK}).json()[
            "session_id"
        ]
    with app.state.database.connect() as db:
        saved = json.loads(
            db.execute("SELECT settings_json FROM sessions WHERE id=?", (sid,)).fetchone()[0]
        )
        del saved["role_models"]
        if legacy_without_text_model:
            del saved["text_model"]
        db.execute("UPDATE sessions SET settings_json=? WHERE id=?", (json.dumps(saved), sid))
        # Simulate a session whose settings JSON predates the backfill migration.
        db.execute("DELETE FROM schema_migrations WHERE version=2")
    settings.text_provider = "opencode-go"
    settings.text_model = "fixture-chat"
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as client:
        assert (
            client.get(f"/v1/sessions/{sid}").json()["settings"]["text_model"]["id"] == "mock/demo"
        )
        migrated = client.get(f"/v1/sessions/{sid}").json()["settings"]["role_models"]
        assert len(migrated) == 6
        assert all(model["id"] == "mock/demo" for model in migrated.values())


def test_shared_auth_read_on_demand_and_never_persisted(tmp_path, monkeypatch):
    auth = tmp_path / "shared" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(auth.parents[1]))
    monkeypatch.delenv("OPENCODE_AUTH_FILE", raising=False)
    settings = Settings(data_dir=tmp_path, opencode_go_api_key="", opencode_zen_api_key="")
    catalog = ModelCatalog(settings)
    assert settings.opencode_auth_file == auth
    auth.write_text(json.dumps({"opencode-go": {"type": "api", "key": "LOCAL_SECRET"}}))
    original = auth.read_bytes()
    intercept(monkeypatch, public_response)
    catalog.refresh()
    target = catalog.resolve("opencode-go/fixture-chat")
    assert target.api_key == "LOCAL_SECRET"
    assert catalog.key("opencode") == ""
    assert "LOCAL_SECRET" not in json.dumps(catalog.candidates())
    assert "LOCAL_SECRET" not in json.dumps(target.public())
    assert "LOCAL_SECRET" not in catalog.path.read_text()
    assert "LOCAL_SECRET" not in repr(target)
    assert auth.read_bytes() == original
    auth.write_text(json.dumps({"opencode-go": {"type": "api", "key": "ROTATED_SECRET"}}))
    assert catalog.from_saved(target.public()).api_key == "ROTATED_SECRET"
    settings.opencode_go_api_key = "OVERRIDE_SECRET"
    assert catalog.key("opencode-go") == "OVERRIDE_SECRET"
    settings.opencode_go_api_key = ""
    for invalid in (
        "{broken",
        "[]",
        '{"opencode-go": {"type": "oauth", "key": "NO"}}',
        '{"opencode-go": {"type": "api", "key": 123}}',
    ):
        auth.write_text(invalid)
        assert catalog.key("opencode-go") == ""
    auth.unlink()
    assert catalog.key("opencode-go") == ""


def test_all_roles_route_independently_and_survive_restart(tmp_path, monkeypatch):
    from backend.app.schemas import TEXT_ROLES

    selections = {role: f"opencode-go/fixture-{role}" for role in TEXT_ROLES}
    remote = {f"fixture-{role}": "@ai-sdk/openai-compatible" for role in TEXT_ROLES}
    requests = []

    def handler(request):
        if request.method == "GET":
            return public_response(request, remote)
        payload = json.loads(request.content)
        body = json.loads(payload["messages"][-1]["content"])
        role = body.get("requested_level", "examiner")
        requests.append((role, payload["model"], request.headers["x-opencode-session"]))
        result = (
            QUESTION
            if role == "examiner"
            else {
                **COACH,
                "answer_en": "My revised answer." if role in {"revision", "full_answer"} else None,
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    intercept(monkeypatch, handler)
    settings = settings_for(tmp_path)
    app = create_app(settings)
    app.state.providers.catalog.refresh()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/v1/sessions", json={"mode": "independent", "pack": PACK, "role_models": selections}
        )
        assert response.status_code == 201
        sid = response.json()["session_id"]
        q = client.post(f"/v1/sessions/{sid}/question", json={"request_id": str(uuid4())})
        assert q.status_code == 200
        tid = q.json()["id"]
        for role in TEXT_ROLES[1:]:
            result = client.post(
                f"/v1/turns/{tid}/coach",
                json={"request_id": str(uuid4()), "level": role, "draft": "My draft."},
            )
            assert result.status_code == 200, result.text
        assert [(role, model) for role, model, _ in requests] == [
            (role, f"fixture-{role}") for role in TEXT_ROLES
        ]
        assert len({identity for _, _, identity in requests}) == len(TEXT_ROLES)
        assert (
            client.put(
                f"/v1/sessions/{sid}/text-model",
                json={"role": "revision", "text_model": selections["hint"]},
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"/v1/sessions/{sid}/text-model", json={"role": "typo", "text_model": "mock/demo"}
            ).status_code
            == 422
        )
        assert (
            client.put(
                f"/v1/sessions/{sid}/text-model",
                json={"role": "hint", "text_model": "opencode-go/unlisted"},
            ).status_code
            == 422
        )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as client:
        saved = client.get(f"/v1/sessions/{sid}").json()["settings"]["role_models"]
        assert {role: model["id"] for role, model in saved.items()} == {
            **selections,
            "revision": selections["hint"],
        }
        body = {"request_id": str(uuid4()), "level": "revision", "draft": "My draft."}
        result = client.post(f"/v1/turns/{tid}/coach", json=body)
        assert result.status_code == 200
        assert requests[-1][1] == "fixture-hint"
        assert requests[-1][2] == next(
            identity for role, _, identity in requests if role == "revision"
        )
        count = len(requests)
        assert client.post(f"/v1/turns/{tid}/coach", json=body).json() == result.json()
        assert len(requests) == count
        assert (
            client.post(
                f"/v1/turns/{tid}/coach",
                json={"request_id": str(uuid4()), "level": "revision", "draft": " "},
            ).status_code
            == 422
        )
        exported = client.get(f"/v1/sessions/{sid}/export").text
        assert "TEST_SECRET" not in exported
