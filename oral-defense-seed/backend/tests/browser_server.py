"""Isolated browser-test server. Synthetic choices and no external inference."""

from backend.app.config import Settings
from backend.app.db import now
from backend.app.main import create_app

app = create_app(
    Settings(
        text_provider="mock",
        opencode_go_api_key="BROWSER_FIXTURE_KEY",
        opencode_zen_api_key="",
        model_catalog_refresh_hours=0,
    )
)
catalog = app.state.providers.catalog
catalog.availability = {
    "schema_version": 2,
    "checked_at": now(),
    "models": [
        {
            "id": f"opencode-go/{model}",
            "provider": "opencode-go",
            "model": model,
            "name": model,
            "protocol": "chat_completions",
            "reason": None,
        }
        for model in ("browser-fixture-one", "browser-fixture-two")
    ],
}
catalog.persist()
original_text = app.state.providers.text


def local_text(role, messages, **kwargs):
    if kwargs["target"].provider != "mock":
        raise AssertionError("Browser tests must not send external inference requests")
    return original_text(role, messages, **kwargs)


app.state.providers.text = local_text

original_speech_candidates = app.state.providers.speech_catalog.candidates


def speech_candidates():
    catalog = original_speech_candidates()
    for model in catalog["models"]:
        model["available"] = True
        model["reason"] = None
    return catalog


app.state.providers.speech_catalog.candidates = speech_candidates


# Fixture endpoints exist only in this isolated test server, never in the app.
@app.post("/v1/test/history/{session_id}")
def history_fixture(session_id: str):
    from backend.tests.history_fixture import seed_history

    return seed_history(app.state.database, session_id)


@app.post("/v1/test/history/{session_id}/append")
def append_history_fixture(session_id: str):
    from backend.tests.history_fixture import append_turns

    return append_turns(app.state.database, session_id, 1)


# Keep the static catch-all after the test routes.
app.router.routes.sort(key=lambda route: getattr(route, "name", None) == "frontend")
