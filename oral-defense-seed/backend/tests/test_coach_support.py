import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from backend.tests.test_conversation import client as client
from backend.tests.test_conversation import (
    control,
    ended,
    model_ready,
    session,
    snapshot,
    start,
    step,
)
from backend.tests.test_drill import request_id


def ask(client, tid, level="hint", **kwargs):
    return client.post(f"/v1/turns/{tid}/coach", json={**request_id(), "level": level, **kwargs})


def adoption(state, message):
    return {
        "revision": state["revision"],
        "turn_id": state["turn_id"],
        "reference_id": state["reference_id"],
        "message_id": message["id"],
    }


def test_delayed_support_stays_on_original_turn_without_stopping_playback(client, monkeypatch):
    sid = session(client)
    state = model_ready(client, sid)
    playback = start(client, sid, state)
    entered, release = Event(), Event()
    original = client.app.state.providers.text
    inputs = []

    def provider(role, messages, **kwargs):
        inputs.append((role, messages))
        if role == "hint":
            entered.set()
            assert release.wait(5)
        return original(role, messages, **kwargs)

    monkeypatch.setattr(client.app.state.providers, "text", provider)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(ask, client, state["turn_id"], user_note="PRIVATE_LATE_SENTINEL")
        assert entered.wait(5)
        try:
            next_state = step(client, sid, ended(client, sid, playback))
            assert next_state["turn_id"] != state["turn_id"]
            assert control(client, sid, "pause")["status"] == "paused"
        finally:
            release.set()
        response = future.result()
    assert response.status_code == 200, response.text
    assert response.json()["turn_id"] == state["turn_id"]
    turns = snapshot(client, sid)["turns"]
    assert turns[0]["coach_messages"][-1]["user_note"] == "PRIVATE_LATE_SENTINEL"
    assert turns[1]["coach_messages"] == []
    assert turns[0]["confirmed_answer_en"] == playback["text"]
    assert all(
        "PRIVATE_LATE_SENTINEL" not in json.dumps(messages)
        for role, messages in inputs
        if role == "examiner"
    )


def test_support_has_no_twelve_call_stop_and_history_is_pageable(client, monkeypatch):
    sid = session(client)
    state = model_ready(client, sid)
    original = client.app.state.providers.text
    payloads = []

    def capture(role, messages, **kwargs):
        payloads.append(json.loads(messages[-1]["content"]))
        return original(role, messages, **kwargs)

    monkeypatch.setattr(client.app.state.providers, "text", capture)
    for index in range(22):
        assert ask(client, state["turn_id"], user_note=f"Private {index}").status_code == 200
    assert all(len(p["coach_history"]) <= 12 for p in payloads)
    assert all(len(json.dumps(p["coach_history"], ensure_ascii=False)) < 8500 for p in payloads)
    turn = snapshot(client, sid)["turns"][0]
    assert turn["confirmed_answer_en"] is None
    assert turn["coach_messages_total"] == 23
    older = client.get(
        f"/v1/turns/{state['turn_id']}/coach-messages?before={turn['coach_messages_before']}"
    ).json()
    assert len({m["id"] for m in older["items"] + turn["coach_messages"]}) == 23


def test_adoption_requires_paused_current_turn_and_saved_message(client):
    sid = session(client)
    state = model_ready(client, sid)
    message = ask(
        client, state["turn_id"], "revision", draft="I have no measured results yet."
    ).json()
    url = f"/v1/sessions/{sid}/conversation/adopt"
    assert client.post(url, json=adoption(state, message)).status_code == 409
    old_playback = start(client, sid, state)
    paused = control(client, sid, "pause")
    body = adoption(paused, message)
    with ThreadPoolExecutor(max_workers=2) as pool:
        adopted = pool.submit(client.post, url, json=body)
        late = pool.submit(
            client.post,
            f"/v1/sessions/{sid}/conversation/ended",
            json={**request_id(), "playback_id": old_playback["playback_id"]},
        )
        assert late.result().status_code == 409
        response = adopted.result()
    assert response.status_code == 200, response.text
    assert client.post(url, json=body).status_code == 409
    changed = response.json()
    assert changed["reference_id"] != state["reference_id"]
    turn = snapshot(client, sid)["turns"][0]
    assert turn["confirmed_answer_en"] is None
    assert turn["exercises"][-1]["reference_text"] == message["response"]["answer_en"]
    assert turn["exercises"][-1]["generation"] == turn["coach_messages"][-1]["generation"]
    assert (
        client.post(
            f"/v1/sessions/{sid}/conversation/reference",
            json={
                "text": "stale edit",
                "revision": paused["revision"],
                "turn_id": paused["turn_id"],
            },
        ).status_code
        == 409
    )
    resumed = control(client, sid)
    ended(client, sid, start(client, sid, resumed))
    assert (
        snapshot(client, sid)["turns"][0]["confirmed_answer_en"] == message["response"]["answer_en"]
    )
    control(client, sid, "pause")
    assert (
        client.post(url, json=adoption(snapshot(client, sid)["conversation"], message)).status_code
        == 409
    )


def test_adoption_rejects_a_message_from_another_turn(client):
    sid = session(client)
    first = model_ready(client, sid)
    message = ask(client, first["turn_id"], "full_answer").json()
    state = step(client, sid, ended(client, sid, start(client, sid, first)))
    state = step(client, sid, ended(client, sid, start(client, sid, state)))
    state = control(client, sid, "pause")
    response = client.post(f"/v1/sessions/{sid}/conversation/adopt", json=adoption(state, message))
    assert response.status_code == 409
    assert response.json()["code"] == "message_mismatch"


@pytest.mark.parametrize("action", ["end", "delete", "recover"])
def test_end_delete_or_recovery_rejects_delayed_support(client, monkeypatch, action):
    sid = session(client)
    state = model_ready(client, sid)
    entered, release = Event(), Event()
    original = client.app.state.providers.text

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(client.app.state.providers, "text", slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(ask, client, state["turn_id"])
        assert entered.wait(5)
        try:
            if action == "delete":
                assert client.delete(f"/v1/sessions/{sid}").status_code == 200
            elif action == "recover":
                client.app.state.database.recover()
            else:
                control(client, sid, "end")
        finally:
            release.set()
        assert future.result().status_code in {404, 409}
    with client.app.state.database.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM coach_messages WHERE session_id=? AND level='hint'", (sid,)
            ).fetchone()[0]
            == 0
        )
