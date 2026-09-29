import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.test_conversation import ended, model_ready, session, snapshot, start
from backend.tests.test_drill import PACK


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, text_provider="mock", tts_provider="mock"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as value:
        yield value


def submissions(client):
    with client.app.state.database.connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM turn_submissions")]


def test_shadowing_confirmation_writes_one_submission_with_sources(client):
    sid = session(client)
    state = model_ready(client, sid)
    playback = start(client, sid, state)
    state = ended(client, sid, playback)
    # The same completion is replayed with the same and with fresh request IDs.
    assert ended(client, sid, playback) == state
    assert ended(client, sid, playback, str(uuid4())) == state
    saved = submissions(client)
    assert len(saved) == 1
    submission = saved[0]
    assert submission["submitted_via"] == "shadowing_playback"
    assert submission["answer_text"] == playback["text"]
    assert submission["source_playback_id"] == playback["playback_id"]
    assert submission["source_exercise_id"] == playback["state"]["reference_id"]
    assert submission["provenance_status"] == "exact"
    assert submission["committed_at"]
    assert snapshot(client, sid)["turns"][0]["source_playback_id"] == playback["playback_id"]


def test_cancelled_playback_does_not_write_submission(client):
    from backend.tests.test_conversation import control

    sid = session(client)
    state = model_ready(client, sid)
    playback = start(client, sid, state)
    control(client, sid, "pause")
    response = client.post(
        f"/v1/sessions/{sid}/conversation/ended",
        json={"playback_id": playback["playback_id"], "request_id": str(uuid4())},
    )
    assert response.status_code == 409
    assert submissions(client) == []


def test_confirmation_rolls_back_if_playback_update_fails(client):
    sid = session(client)
    playback = start(client, sid, model_ready(client, sid))
    identity = str(uuid4())
    with client.app.state.database.connect() as db:
        db.execute(
            """CREATE TRIGGER fail_completion BEFORE UPDATE OF status ON conversation_playbacks
               WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        ended(client, sid, playback, identity)
    assert submissions(client) == []
    saved = snapshot(client, sid)
    assert saved["conversation"]["playback_id"] == playback["playback_id"]
    assert saved["turns"][0]["confirmed_answer_en"] is None
    with client.app.state.database.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM requests WHERE request_id=?", (identity,)).fetchone()[
                0
            ]
            == 0
        )
        db.execute("DROP TRIGGER fail_completion")
    ended(client, sid, playback, identity)
    assert len(submissions(client)) == 1


def test_submission_is_immutable_direct_update(client):
    sid = session(client)
    playback = start(client, sid, model_ready(client, sid))
    ended(client, sid, playback)
    with client.app.state.database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE turn_submissions SET answer_text='forged'")


def test_independent_confirm_uses_same_register(client):
    created = client.post("/v1/sessions", json={"mode": "independent", "pack": PACK})
    sid = created.json()["session_id"]
    turn = client.post(f"/v1/sessions/{sid}/question", json={"request_id": str(uuid4())}).json()
    client.post(
        f"/v1/turns/{turn['id']}/exercises",
        json={"mode": "read_aloud", "text": "A fixed answer.", "origin": "manual"},
    )
    confirmed = client.post(
        f"/v1/turns/{turn['id']}/confirm",
        json={"request_id": str(uuid4()), "answer_en": "A fixed answer."},
    )
    assert confirmed.status_code == 200, confirmed.text
    saved = submissions(client)
    assert len(saved) == 1
    assert saved[0]["submitted_via"] == "confirmed_reference"
    assert saved[0]["provenance_status"] == "exact"
    assert saved[0]["source_playback_id"] is None
