import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from backend.app.db import Database
from backend.app.text_models import TextTarget
from backend.tests.test_conversation import client as client
from backend.tests.test_conversation import control, model_ready, session, snapshot
from backend.tests.test_drill import request_id, wav_bytes


def test_generation_provenance_survives_model_change_requests_cleanup_and_restart(
    client, monkeypatch
):
    sid = session(client)
    state = model_ready(client, sid)
    entered, release = Event(), Event()
    providers = client.app.state.providers
    original = providers.text
    resolve = providers.catalog.resolve
    replacement = TextTarget("mock", "replacement", "chat_completions", "")
    monkeypatch.setattr(
        providers.catalog,
        "resolve",
        lambda value=None: replacement if value == "mock/replacement" else resolve(value),
    )

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(providers, "text", slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(
            client.post,
            f"/v1/turns/{state['turn_id']}/coach",
            json={**request_id(), "level": "hint"},
        )
        assert entered.wait(5)
        try:
            assert (
                client.put(
                    f"/v1/sessions/{sid}/text-model",
                    json={"role": "hint", "text_model": "mock/replacement"},
                ).status_code
                == 200
            )
        finally:
            release.set()
        assert future.result().status_code == 200
    before = snapshot(client, sid)["turns"][0]
    assert before["generation"]["target"]["model"] == "demo"
    assert before["coach_messages"][-1]["generation"]["target"]["model"] == "demo"
    assert before["exercises"][0]["generation"]["role"] == "full_answer"
    assert (
        client.post(
            f"/v1/turns/{state['turn_id']}/coach", json={**request_id(), "level": "hint"}
        ).status_code
        == 200
    )
    after = snapshot(client, sid)["turns"][0]
    assert after["coach_messages"][-1]["generation"]["target"]["model"] == "replacement"
    assert after["coach_messages"][-2] == before["coach_messages"][-1]
    with client.app.state.database.connect() as db:
        db.execute("DELETE FROM requests")
        for table in ("turns", "coach_messages", "exercises"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(f"UPDATE {table} SET generation_json='{{}}'")
    Database(client.app.state.database.path.parent)
    assert snapshot(client, sid)["turns"][0] == after
    assert '"generation"' not in client.get(f"/v1/sessions/{sid}/export").text


def test_tts_provenance_uses_actual_generation_configuration(client, monkeypatch):
    sid = session(client)
    state = model_ready(client, sid)
    catalog = client.app.state.providers.speech_catalog
    monkeypatch.setattr(catalog, "validate", lambda selection: selection)
    monkeypatch.setattr(catalog, "cache_identity", lambda selection, voice: [selection, voice])
    monkeypatch.setattr(catalog, "synthesize", lambda *args: wav_bytes())
    control(client, sid, "pause")
    assert (
        client.put(
            f"/v1/sessions/{sid}/speech-model",
            json={"role": "coach_answer", "model": "fixture-first"},
        ).status_code
        == 200
    )
    first = client.post(
        "/v1/tts",
        json={"source_type": "conversation_reference", "source_id": state["reference_id"]},
    ).json()
    assert (
        client.put(
            f"/v1/sessions/{sid}/speech-model",
            json={"role": "coach_answer", "model": "fixture-second"},
        ).status_code
        == 200
    )
    second = client.post(
        "/v1/tts",
        json={"source_type": "conversation_reference", "source_id": state["reference_id"]},
    ).json()
    with client.app.state.database.connect() as db:
        values = {
            r["id"]: json.loads(r["generation_json"])
            for r in db.execute("SELECT * FROM audio_files")
        }
    assert values[first["audio_id"]]["configuration"][0] == "fixture-first"
    assert values[second["audio_id"]]["configuration"][0] == "fixture-second"
    assert values[first["audio_id"]]["output_hash"] == values[second["audio_id"]]["output_hash"]


def test_work_capacity_is_bounded_without_blocking_pause_and_recovers(client, monkeypatch):
    sessions = [session(client) for _ in range(5)]
    states = [model_ready(client, sid) for sid in sessions]
    entered, release = Event(), Event()
    lock, count = Lock(), 0
    original = client.app.state.providers.text

    def slow(*args, **kwargs):
        nonlocal count
        with lock:
            count += 1
            if count == 4:
                entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(client.app.state.providers, "text", slow)

    def hint(state):
        return client.post(
            f"/v1/turns/{state['turn_id']}/coach", json={**request_id(), "level": "hint"}
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [pool.submit(hint, state) for state in states[:4]]
        assert entered.wait(5)
        try:
            full = hint(states[4])
            assert full.status_code == 503
            assert full.json()["code"] == "capacity"
            assert control(client, sessions[0], "pause")["status"] == "paused"
            assert client.get(f"/v1/sessions/{sessions[0]}").status_code == 200
        finally:
            release.set()
        assert [future.result().status_code for future in pending] == [200] * 4
    assert hint(states[4]).status_code == 200
