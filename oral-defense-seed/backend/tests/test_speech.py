import json
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.config import ROOT, Settings
from backend.app.main import create_app
from backend.tests.test_drill import wav_bytes

PACK = json.loads((ROOT / "examples/bo_pack.json").read_text())


def test_speech_roles_preview_cache_and_restart(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path, tts_provider="local", speech_models_dir=tmp_path / "models"
    )
    settings.speech_models_dir.mkdir()
    for name in [
        "en_US-ljspeech-high.onnx",
        "en_US-ljspeech-high.onnx.json",
        "kokoro-v1.0.onnx",
        "voices-v1.0.bin",
    ]:
        (settings.speech_models_dir / name).write_text("fixture")
    app = create_app(settings)
    calls = []

    def synthesize(text, selection):
        calls.append((text, selection))
        return wav_bytes()

    monkeypatch.setattr(app.state.providers.speech_catalog, "synthesize", synthesize)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        created = client.post("/v1/sessions", json={"mode": "independent", "pack": PACK})
        sid = created.json()["session_id"]
        saved = client.get(f"/v1/sessions/{sid}").json()["settings"]["speech_models"]
        assert saved == {
            "question": "kokoro/sky-anime",
            "coach_answer": "piper/ljspeech",
            "exercise": "piper/ljspeech",
        }
        tid = client.post(f"/v1/sessions/{sid}/question", json={"request_id": str(uuid4())}).json()[
            "id"
        ]
        text = client.get(f"/v1/sessions/{sid}").json()["turns"][0]["question_en"]
        eid = client.post(
            f"/v1/turns/{tid}/exercises", json={"text": text, "mode": "read_aloud"}
        ).json()["id"]
        body = {"source_type": "question", "source_id": tid}
        first = client.post("/v1/tts", json=body)
        assert first.status_code == 200
        assert client.post("/v1/tts", json=body).json() == first.json()
        assert len(calls) == 1 and calls[-1][1] == "kokoro/sky-anime"
        reference = client.post("/v1/tts", json={"source_type": "exercise", "source_id": eid})
        assert reference.json()["audio_id"] != first.json()["audio_id"]
        assert calls[-1][1] == "piper/ljspeech"
        message = client.post(
            f"/v1/turns/{tid}/coach", json={"request_id": str(uuid4()), "level": "full_answer"}
        ).json()
        assert (
            client.post(
                "/v1/tts", json={"source_type": "coach_answer", "source_id": message["id"]}
            ).status_code
            == 200
        )
        assert calls[-1][1] == "piper/ljspeech"
        assert (
            client.put(
                f"/v1/sessions/{sid}/speech-model",
                json={"role": "question", "model": "kokoro/heart"},
            ).status_code
            == 200
        )
        changed = client.post("/v1/tts", json=body)
        assert changed.json()["audio_id"] != first.json()["audio_id"]
        assert calls[-1][1] == "kokoro/heart"
        count = len(calls)
        assert (
            client.put(
                f"/v1/sessions/{sid}/speech-model",
                json={"role": "question", "model": "../../secret"},
            ).status_code
            == 422
        )
        assert len(calls) == count
        preview = client.post("/v1/speech-preview", json={"model": "kokoro/sky"})
        assert preview.status_code == 200 and preview.headers["content-type"] == "audio/wav"
        assert calls[-1] == (
            "Hello! Tell me about your research. What evidence supports your idea?",
            "kokoro/sky",
        )
        assert str(settings.speech_models_dir) not in client.get(f"/v1/sessions/{sid}/export").text
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as client:
        restored = client.get(f"/v1/sessions/{sid}").json()["settings"]["speech_models"]
        assert restored == {**saved, "question": "kokoro/heart"}
        settings.speech_models_dir.joinpath("kokoro-v1.0.onnx").unlink()
        assert client.post("/v1/tts", json=body).status_code == 409
        assert client.post("/v1/speech-preview", json={"model": "kokoro/sky"}).status_code == 409
