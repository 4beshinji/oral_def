import os

import pytest
from fastapi.testclient import TestClient

from backend.app import db as db_module
from backend.app.config import Settings
from backend.app.db import Database, reconcile_audio_files
from backend.app.main import create_app
from backend.tests.test_drill import fixed, new_turn, wav_bytes


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, text_provider="mock", tts_provider="mock"))
    with TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False) as value:
        yield value


def add_recording(client, tid):
    exercise = fixed(client, tid)
    response = client.post(
        f"/v1/exercises/{exercise['id']}/audio",
        files={"file": ("recording.wav", wav_bytes(), "audio/wav")},
        data={"input_kind": "isolated_repeat"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_delete_leaves_other_session_files(client):
    sid_one, tid_one = new_turn(client)
    sid_two, tid_two = new_turn(client)
    first = add_recording(client, tid_one)
    second = add_recording(client, tid_two)
    assert client.delete(f"/v1/sessions/{sid_one}").status_code == 200
    assert client.get(f"/v1/audio/{first['audio_id']}").status_code == 404
    assert client.get(f"/v1/audio/{second['audio_id']}").status_code == 200


def test_unlink_failure_is_resumable(client, monkeypatch):
    sid, tid = new_turn(client)
    audio = add_recording(client, tid)
    calls = {"count": 0}
    real = db_module._remove_file

    def flaky(path):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("injected unlink failure")
        real(path)

    monkeypatch.setattr(db_module, "_remove_file", flaky)
    assert client.delete(f"/v1/sessions/{sid}").status_code == 500
    with client.app.state.database.connect() as db:
        session = db.execute(
            "SELECT deletion_status,deletion_error FROM sessions WHERE id=?", (sid,)
        ).fetchone()
        files = db.execute(
            "SELECT count(*) FROM audio_files WHERE session_id=?", (sid,)
        ).fetchone()[0]
    assert session["deletion_status"] == "deleting"
    assert session["deletion_error"] == "OSError"
    assert files == 1
    monkeypatch.setattr(db_module, "_remove_file", real)
    assert client.delete(f"/v1/sessions/{sid}").status_code == 200
    with client.app.state.database.connect() as db:
        assert db.execute("SELECT count(*) FROM sessions WHERE id=?", (sid,)).fetchone()[0] == 0
    assert client.get(f"/v1/audio/{audio['audio_id']}").status_code == 404


def test_restart_resumes_deletion_without_logging_private_content(
    client, tmp_path, monkeypatch, caplog
):
    sid, tid = new_turn(client)
    secret = "PRIVATE_RESEARCH_SENTINEL_9123"
    with client.app.state.database.connect() as db:
        db.execute("UPDATE sessions SET research_brief=? WHERE id=?", (secret, sid))
    add_recording(client, tid)
    real = db_module._remove_file
    monkeypatch.setattr(
        db_module, "_remove_file", lambda path: (_ for _ in ()).throw(OSError(secret))
    )
    assert client.delete(f"/v1/sessions/{sid}").status_code == 500
    with client.app.state.database.connect() as db:
        assert (
            db.execute("SELECT deletion_error FROM sessions WHERE id=?", (sid,)).fetchone()[0]
            == "OSError"
        )
    monkeypatch.setattr(db_module, "_remove_file", real)
    Database(tmp_path)
    assert secret not in caplog.text
    with client.app.state.database.connect() as db:
        assert db.execute("SELECT count(*) FROM sessions WHERE id=?", (sid,)).fetchone()[0] == 0


def test_upload_and_generation_are_rejected_while_deleting(client, monkeypatch):
    sid, tid = new_turn(client)
    exercise = fixed(client, tid)
    add_recording(client, tid)
    monkeypatch.setattr(
        db_module, "_remove_file", lambda path: (_ for _ in ()).throw(OSError("boom"))
    )
    assert client.delete(f"/v1/sessions/{sid}").status_code == 500
    response = client.post(
        f"/v1/exercises/{exercise['id']}/audio",
        files={"file": ("recording.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "session_deleting"
    assert client.get(f"/v1/sessions/{sid}").status_code == 200


def test_reconcile_reports_and_cleans_stray_files(client, tmp_path):
    sid, tid = new_turn(client)
    audio = add_recording(client, tid)
    audio_root = tmp_path / "audio"
    orphan = audio_root / "orphan.webm"
    orphan.write_bytes(b"stray")
    os.utime(orphan, (0, 0))
    with client.app.state.database.connect() as db:
        db.execute("UPDATE audio_files SET storage_key='gone.wav' WHERE id=?", (audio["audio_id"],))
    dry = reconcile_audio_files(client.app.state.database, dry_run=True)
    assert "orphan.webm" in dry["orphan"]
    assert any(item["audio_id"] == audio["audio_id"] for item in dry["missing"])
    assert orphan.exists()
    live = reconcile_audio_files(client.app.state.database, dry_run=False)
    assert "orphan.webm" in live["removed"]
    assert not orphan.exists()
    with client.app.state.database.connect() as db:
        status = db.execute(
            "SELECT save_status FROM audio_files WHERE id=?", (audio["audio_id"],)
        ).fetchone()[0]
    assert status == "missing"


def test_reconcile_keeps_processing_temp_files(client, tmp_path):
    staging = tmp_path / "audio" / "fresh.part.webm"
    staging.write_bytes(b"in progress")
    report = reconcile_audio_files(client.app.state.database, dry_run=False)
    assert "fresh.part.webm" not in report["temp"]
    assert staging.exists()
