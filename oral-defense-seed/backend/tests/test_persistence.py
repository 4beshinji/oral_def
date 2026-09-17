from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import backup_database, verify_backup
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
        assert exported["schema_version"] == "1.0"
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
