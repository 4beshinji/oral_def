import json
import shutil
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import MigrationError, backup_database, verify_backup
from backend.app.main import create_app
from backend.tests.test_drill import PACK, fixed, new_turn, recorded


def make_client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, text_provider="mock", tts_provider="mock"))
    return app, TestClient(app, base_url="http://127.0.0.1:8000")


def test_normal_snapshot_is_bounded_and_pageable(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        sid = client.post("/v1/sessions", json={"mode": "independent", "pack": PACK}).json()[
            "session_id"
        ]
        with app.state.database.connect() as db:
            for ordinal in range(1, 121):
                db.execute(
                    "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,follow_up_count) VALUES (?,?,?,?,?,?)",
                    (f"turn-{ordinal}", sid, ordinal, "q", "b", 0),
                )
        default = client.get(f"/v1/sessions/{sid}").json()
        assert default["turns_total"] == 120
        assert len(default["turns"]) == 50
        assert default["turns_has_more"] is True
        assert default["turns_before"] == 71
        assert [turn["ordinal"] for turn in default["turns"]] == list(range(71, 121))

        seen, before = [], 121
        while before is not None:
            page = client.get(f"/v1/sessions/{sid}/turns?before={before}&limit=25").json()
            seen.extend(turn["ordinal"] for turn in page["turns"])
            before = page["next_before"]
        assert sorted(seen) == list(range(1, 121))
        assert len(set(seen)) == 120


def test_export_is_share_mode_without_local_paths(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        sid, tid = new_turn(client)
        client.app.state.providers.settings.text_api_key = "EXPORT_SECRET"
        exported = client.get(f"/v1/sessions/{sid}/export").json()
        assert exported["mode"] == "share"
        assert exported["schema_version"] == "2.0"
        assert "EXPORT_SECRET" not in client.get(f"/v1/sessions/{sid}/export").text
        assert str(tmp_path) not in client.get(f"/v1/sessions/{sid}/export").text


def test_backup_and_verify_covers_db_and_audio(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        _, tid = new_turn(client)
        exercise = fixed(client, tid)
        recorded(client, exercise["id"])
        destination = tmp_path / "backup"
        manifest = backup_database(app.state.database, destination)
        assert manifest["files"]
        assert manifest["migration_versions"]
        report = verify_backup(destination)
        assert report["ok"] is True
        assert report["missing"] == []

        audio_file = destination / "audio" / manifest["files"][0]["storage_key"].rsplit("/", 1)[-1]
        audio_file.write_bytes(audio_file.read_bytes() + b"x")
        assert verify_backup(destination)["ok"] is False

        audio_file.unlink()
        broken = verify_backup(destination)
        assert broken["ok"] is False
        assert broken["missing"]


def test_backup_endpoint_creates_and_verifies(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        created = client.post("/v1/maintenance/backup", json={})
        assert created.status_code == 201, created.text
        name = created.json()["name"]
        assert created.json()["verify"]["ok"] is True
        checked = client.get(f"/v1/maintenance/backup/{name}")
        assert checked.status_code == 200
        assert checked.json()["ok"] is True
        assert client.get("/v1/maintenance/backup/..%2F..%2Fetc").status_code in {404, 422}


def test_backup_incomplete_is_not_ok(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        client.post("/v1/sessions", json={"mode": "independent", "pack": PACK})
        destination = tmp_path / "partial"
        backup_database(app.state.database, destination)
        (destination / "drill.sqlite3").unlink()
        report = verify_backup(destination)
        assert report["ok"] is False
        assert "backup_incomplete" in report["errors"]


@pytest.mark.parametrize(
    "damage",
    [
        "unlisted",
        "extra",
        "manifest",
        "database",
        "version",
        "path",
        "duplicate",
        "hash",
        "symlink",
    ],
)
def test_backup_rejects_incomplete_or_invalid_manifest(tmp_path, damage):
    app, client = make_client(tmp_path / "source")
    with client:
        _, tid = new_turn(client)
        recorded(client, fixed(client, tid)["id"])
        destination = tmp_path / "backup"
        manifest = backup_database(app.state.database, destination)
        if damage == "unlisted":
            # The old verifier trusted the manifest even when both a DB-referenced
            # file and its manifest entry had disappeared.
            entry = manifest["files"].pop()
            (destination / "audio" / entry["storage_key"]).unlink()
        elif damage == "extra":
            (destination / "audio" / "extra.wav").write_bytes(b"extra")
        elif damage == "database":
            (destination / "drill.sqlite3").write_bytes(b"not a database")
        elif damage == "version":
            manifest["schema_version"] = "999.0"
        elif damage == "path":
            manifest["files"][0]["storage_key"] = "../outside.wav"
        elif damage == "duplicate":
            manifest["files"].append(manifest["files"][0])
        elif damage == "hash":
            manifest["database"]["sha256"] = "0" * 64
        elif damage == "symlink":
            path = destination / "audio" / manifest["files"][0]["storage_key"]
            path.unlink()
            path.symlink_to(app.state.database.audio_dir / path.name)
        (destination / "manifest.json").write_text(
            "{incomplete" if damage == "manifest" else json.dumps(manifest)
        )
        assert verify_backup(destination)["ok"] is False


def test_backup_file_manifest_uses_the_copied_database(tmp_path, monkeypatch):
    app, client = make_client(tmp_path / "source")
    with client:
        _, tid = new_turn(client)
        first_exercise = fixed(client, tid)
        first = recorded(client, first_exercise["id"])
        database = app.state.database
        original = database.connect
        inserted = False

        @contextmanager
        def write_after_snapshot():
            nonlocal inserted
            with original() as connection:
                yield connection
            if not inserted:
                inserted = True
                recorded(client, first_exercise["id"])

        monkeypatch.setattr(database, "connect", write_after_snapshot)
        destination = tmp_path / "backup"
        manifest = backup_database(database, destination)
        assert [entry["audio_id"] for entry in manifest["files"]] == [first["audio_id"]]
        assert verify_backup(destination)["ok"] is True


def test_backup_does_not_reuse_an_existing_destination(tmp_path):
    app, client = make_client(tmp_path / "source")
    with client:
        destination = tmp_path / "backup"
        backup_database(app.state.database, destination)
        before = (destination / "manifest.json").read_bytes()
        with pytest.raises(FileExistsError):
            backup_database(app.state.database, destination)
        assert (destination / "manifest.json").read_bytes() == before


def test_backup_does_not_publish_manifest_after_file_loss(tmp_path, monkeypatch):
    app, client = make_client(tmp_path / "source")
    with client:
        _, tid = new_turn(client)
        recorded(client, fixed(client, tid)["id"])
        database = app.state.database
        original = database.connect

        @contextmanager
        def delete_after_snapshot():
            with original() as connection:
                yield connection
            for path in database.audio_dir.iterdir():
                path.unlink()

        monkeypatch.setattr(database, "connect", delete_after_snapshot)
        destination = tmp_path / "backup"
        with pytest.raises(MigrationError):
            backup_database(database, destination)
        assert not (destination / "manifest.json").exists()
        assert verify_backup(destination)["ok"] is False


def test_restore_into_another_data_dir_reads_audio_documents_and_private_history(tmp_path):
    from backend.tests.test_drill import request_id

    app, client = make_client(tmp_path / "source")
    with client:
        sid, tid = new_turn(client)
        assert (
            client.post(
                f"/v1/turns/{tid}/coach",
                json={**request_id(), "level": "hint", "user_note": "Private restored note"},
            ).status_code
            == 200
        )
        exercise = fixed(client, tid)
        attempt = recorded(client, exercise["id"])
        assert (
            client.post(
                f"/v1/attempts/{attempt['attempt_id']}/assess", json=request_id()
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/v1/turns/{tid}/confirm",
                json={**request_id(), "answer_en": exercise["reference_text"]},
            ).status_code
            == 200
        )
        document = client.post(
            f"/v1/sessions/{sid}/documents",
            json={"source_type": "brief", "text": "Restored document fact."},
        ).json()
        assert (
            client.post(
                f"/v1/sessions/{sid}/pack-manifest",
                json={
                    "schema_version": "1.0",
                    "pack": PACK,
                    "adopted": [{"document_id": document["id"], "segment_ids": []}],
                },
            ).status_code
            == 201
        )
        before = client.get(f"/v1/sessions/{sid}").json()
        audio = client.get(f"/v1/audio/{attempt['audio_id']}").content
        share_before = client.get(f"/v1/sessions/{sid}/export").json()["session"]
        # Business history must not depend on retaining idempotency requests.
        with app.state.database.connect() as db:
            db.execute("DELETE FROM requests")
        assert client.get(f"/v1/sessions/{sid}/export").json()["session"] == share_before
        destination = tmp_path / "backup"
        backup_database(app.state.database, destination)
        assert verify_backup(destination)["ok"] is True

    restored_dir = tmp_path / "restored"
    shutil.copytree(destination, restored_dir)
    assert verify_backup(restored_dir)["ok"] is True
    # Prove the restored app does not fall back to source files.
    shutil.rmtree(tmp_path / "source")
    restored_app, restored = make_client(restored_dir)
    with restored:
        after = restored.get(f"/v1/sessions/{sid}").json()
        for field in ("turns", "documents", "pack_manifest", "pack_snapshot", "pack_hash"):
            assert after[field] == before[field]
        response = restored.get(f"/v1/audio/{attempt['audio_id']}")
        assert response.status_code == 200
        assert response.content == audio
        assert restored.get(f"/v1/sessions/{sid}/export").json()["session"] == share_before
        with restored_app.state.database.read_snapshot() as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert list(db.execute("PRAGMA foreign_key_check")) == []


def test_read_snapshot_keeps_one_view_during_a_concurrent_commit(tmp_path, monkeypatch):
    from backend.app import main
    from backend.tests.test_conversation import ended, model_ready, session, start

    _, client = make_client(tmp_path)
    with client:
        sid = session(client)
        state = model_ready(client, sid)
        playback = start(client, sid, state)
        original = main.turn_row
        committed = False

        def commit_before_reading_turn(db, tid):
            nonlocal committed
            if not committed:
                committed = True
                ended(client, sid, playback)
            return original(db, tid)

        monkeypatch.setattr(main, "turn_row", commit_before_reading_turn)
        before = client.get(f"/v1/sessions/{sid}").json()
        assert before["conversation"]["stage"] == "model_playback"
        assert before["turns"][0]["confirmed_answer_en"] is None
        after = client.get(f"/v1/sessions/{sid}").json()
        assert after["conversation"]["stage"] == "question_generation"
        assert after["turns"][0]["confirmed_answer_en"] == playback["text"]


def test_legacy_backup_manifest_is_verified_without_writing_sidecars(tmp_path):
    app, client = make_client(tmp_path / "source")
    with client:
        _, tid = new_turn(client)
        recorded(client, fixed(client, tid)["id"])
        destination = tmp_path / "backup"
        manifest = backup_database(app.state.database, destination)
        manifest["schema_version"] = "1.0"
        manifest.pop("database")
        (destination / "manifest.json").write_text(json.dumps(manifest))
        files_before = {
            p.relative_to(destination): p.read_bytes()
            for p in destination.rglob("*")
            if p.is_file()
        }
        assert verify_backup(destination)["ok"] is True
        assert {
            p.relative_to(destination): p.read_bytes()
            for p in destination.rglob("*")
            if p.is_file()
        } == files_before


def test_backup_endpoint_failure_is_retryable_and_does_not_expose_paths(tmp_path, monkeypatch):
    from backend.app import main

    _, client = make_client(tmp_path)

    def fail(*args):
        raise MigrationError(str(tmp_path / "private.wav"))

    monkeypatch.setattr(main, "backup_database", fail)
    with client:
        response = client.post("/v1/maintenance/backup", json={})
        assert response.status_code == 503
        assert response.json()["code"] == "backup_incomplete"
        assert response.json()["retryable"] is True
        assert str(tmp_path) not in response.text


@pytest.mark.parametrize("state", ["processing", "missing", "deleting"])
def test_backup_requires_complete_audio_and_no_deletion_in_progress(tmp_path, state):
    app, client = make_client(tmp_path / "source")
    with client:
        sid, tid = new_turn(client)
        recorded(client, fixed(client, tid)["id"])
        with app.state.database.connect() as db:
            if state == "deleting":
                db.execute("UPDATE sessions SET deletion_status='deleting' WHERE id=?", (sid,))
            else:
                db.execute("UPDATE audio_files SET save_status=?", (state,))
        destination = tmp_path / "backup"
        with pytest.raises(MigrationError):
            backup_database(app.state.database, destination)
        assert verify_backup(destination)["ok"] is False
