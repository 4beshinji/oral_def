import json
from uuid import uuid4

from backend.app.db import encode
from backend.tests.test_conversation import ended, model_ready, session, start
from backend.tests.test_drill import PACK, fixed, new_turn, recorded, request_id
from backend.tests.test_persistence import make_client


def test_share_excludes_private_context_and_unsubmitted_references(tmp_path, monkeypatch):
    from backend.app import pronunciation

    original = pronunciation.assess

    def private_diagnostics(*args, **kwargs):
        return {**original(*args, **kwargs), "diagnostics": "PRIVATE_ASSESSMENT_DIAGNOSTICS"}

    monkeypatch.setattr(pronunciation, "assess", private_diagnostics)
    app, client = make_client(tmp_path)
    with client:
        sid, tid = new_turn(client)
        coach = client.post(
            f"/v1/turns/{tid}/coach",
            json={
                **request_id(),
                "level": "full_answer",
                "user_note": "PRIVATE_LEARNER_NOTE",
                "draft": "PRIVATE_DRAFT",
            },
        )
        assert coach.status_code == 200
        rejected = fixed(client, tid, "PRIVATE_UNADOPTED_REFERENCE")
        recorded(client, rejected["id"])
        accepted = fixed(client, tid, "The adopted public answer.")
        assert (
            client.post(
                f"/v1/turns/{tid}/confirm",
                json={**request_id(), "answer_en": accepted["reference_text"]},
            ).status_code
            == 200
        )
        attempt = recorded(client, accepted["id"])
        assert (
            client.post(
                f"/v1/attempts/{attempt['attempt_id']}/assess", json=request_id()
            ).status_code
            == 200
        )
        next_turn = client.post(f"/v1/sessions/{sid}/question", json=request_id()).json()
        fixed(client, next_turn["id"], "PRIVATE_NEXT_REFERENCE")

        with app.state.database.connect() as db:
            settings = json.loads(
                db.execute("SELECT settings_json FROM sessions WHERE id=?", (sid,)).fetchone()[0]
            )
            settings["future_private_field"] = "PRIVATE_SETTINGS"
            db.execute(
                "UPDATE sessions SET research_brief=?,settings_json=? WHERE id=?",
                ("PRIVATE_RESEARCH_BRIEF", encode(settings), sid),
            )
            db.execute(
                "UPDATE coach_messages SET response_json=? WHERE turn_id=?",
                (encode({"answer_en": "PRIVATE_COACH_RESPONSE"}), tid),
            )
            db.execute(
                "UPDATE attempts SET capture_requested_json=?,audio_meta_json=? WHERE id=?",
                (
                    encode({"deviceId": "PRIVATE_DEVICE", "echoCancellation": True}),
                    encode({"path": str(tmp_path / "PRIVATE_AUDIO_PATH"), "duration_s": 0.5}),
                    attempt["attempt_id"],
                ),
            )
            db.execute(
                "UPDATE requests SET payload_json=? WHERE session_id=?",
                (encode({"provider_configuration": {"token": "PRIVATE_REQUEST"}}), sid),
            )

        app.state.providers.settings.text_api_key = "PRIVATE_API_KEY"
        local = client.get(f"/v1/sessions/{sid}").json()
        assert local["turns"][0]["coach_messages"][0]["user_note"] == "PRIVATE_LEARNER_NOTE"
        assert len(local["turns"][0]["exercises"]) == 2
        response = client.get(f"/v1/sessions/{sid}/export")
        assert response.status_code == 200
        assert "PRIVATE_" not in response.text
        assert str(tmp_path) not in response.text
        exported = response.json()
        assert exported["schema_version"] == "2.0"
        assert exported["mode"] == "share"
        assert "providers" not in exported
        shared = exported["session"]
        assert not {"research_brief", "requests", "playbacks", "conversation"} & shared.keys()
        turn = shared["turns"][0]
        assert "coach_messages" not in turn
        assert "assistance" not in turn
        assert turn["submitted_via"] == "confirmed_reference"
        assert turn["confirmed_answer_en"] == accepted["reference_text"]
        assert turn["has_recording"] is True
        assert turn["transcript"] is None
        assert [value["id"] for value in turn["exercises"]] == [accepted["id"]]
        saved_attempt = turn["exercises"][0]["attempts"][0]
        assert saved_attempt["id"] == attempt["attempt_id"]
        assert saved_attempt["audio_meta"] == {"duration_s": 0.5}
        assert saved_attempt["capture_requested"] == {"echoCancellation": True}
        assert saved_attempt["assessments"][0]["evidence_status"] == "unavailable"
        assert "config" not in saved_attempt["assessments"][0]
        assert shared["turns"][1]["exercises"] == []


def test_share_promotes_only_the_reference_from_successful_playback(tmp_path):
    _, client = make_client(tmp_path)
    with client:
        sid = session(client)
        state = model_ready(client, sid)
        before = client.get(f"/v1/sessions/{sid}/export").json()["session"]["turns"][0]
        assert before["confirmed_answer_en"] is None
        assert before["exercises"] == []
        playback = start(client, sid, state)
        ended(client, sid, playback)
        turn = client.get(f"/v1/sessions/{sid}/export").json()["session"]["turns"][0]
        assert turn["submitted_via"] == "shadowing_playback"
        assert turn["source_playback_id"] == playback["playback_id"]
        assert turn["confirmed_answer_en"] == playback["text"]
        assert turn["exercises"][0]["reference_text"] == playback["text"]
        assert turn["has_recording"] is False
        assert turn["transcript"] is None


def test_share_includes_only_adopted_source_segments_matching_session_pack(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        sid, _ = new_turn(client)
        document = client.post(
            f"/v1/sessions/{sid}/documents",
            json={"source_type": "brief", "text": "Adopted fact.\n\nPRIVATE_UNUSED_SEGMENT"},
        ).json()
        client.post(
            f"/v1/sessions/{sid}/documents",
            json={"source_type": "brief", "text": "PRIVATE_UNUSED_DOCUMENT"},
        )
        adopted = [{"document_id": document["id"], "segment_ids": [document["segments"][0]["id"]]}]
        manifest = client.post(
            f"/v1/sessions/{sid}/pack-manifest",
            json={"schema_version": "1.0", "pack": PACK, "adopted": adopted},
        ).json()
        # A later, unrelated candidate is not the pack the conversation uses.
        assert (
            client.post(
                f"/v1/sessions/{sid}/pack-manifest",
                json={"schema_version": "1.0", "pack": {"notes": "PRIVATE_CANDIDATE"}},
            ).status_code
            == 201
        )
        with app.state.database.connect() as db:
            db.execute(
                "UPDATE session_documents SET original_name=?,original_url=?,extractor_config_json=? WHERE id=?",
                (
                    str(tmp_path / "PRIVATE_FILENAME"),
                    "https://example.test/paper?token=PRIVATE_TOKEN#PRIVATE_FRAGMENT",
                    encode({"auth": "PRIVATE_EXTRACTOR_CONFIG"}),
                    document["id"],
                ),
            )
        response = client.get(f"/v1/sessions/{sid}/export")
        assert "PRIVATE_" not in response.text
        shared = response.json()["session"]
        assert shared["pack_snapshot"] == PACK
        assert shared["pack_manifest"]["id"] == manifest["id"]
        assert shared["pack_manifest"]["pack_hash"] == shared["pack_hash"]
        assert shared["pack_manifest"]["adopted"] == adopted
        assert len(shared["documents"]) == 1
        source = shared["documents"][0]
        assert source["original_url"] == "https://example.test/paper"
        assert source["segments"][0]["text"] == "Adopted fact."
        assert source["segments"][0]["location"] == {"part": 1}
        assert len(source["segments"]) == 1


def test_share_not_found(tmp_path):
    _, client = make_client(tmp_path)
    with client:
        assert client.get(f"/v1/sessions/{uuid4()}/export").status_code == 404
