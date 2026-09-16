"""Role-specific local speech presets; secrets and model paths remain on the server."""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import ROOT
from .errors import APIError

SPEECH_ROLES = ("question", "coach_answer", "exercise")
PRESETS = {
    "piper/ljspeech": {
        "name": "Piper · LJSpeech（米語・お手本）",
        "engine": "piper",
        "voice": "ljspeech",
        "pitch": 1.0,
    },
    "kokoro/sky-anime": {
        "name": "Kokoro · Sky（高め・アニメ調）",
        "engine": "kokoro",
        "voice": "af_sky",
        "pitch": 1.18,
    },
    "kokoro/sky": {
        "name": "Kokoro · Sky（自然な声）",
        "engine": "kokoro",
        "voice": "af_sky",
        "pitch": 1.0,
    },
    "kokoro/heart": {
        "name": "Kokoro · Heart（米語）",
        "engine": "kokoro",
        "voice": "af_heart",
        "pitch": 1.0,
    },
}


class SpeechCatalog:
    def __init__(self, settings):
        self.settings = settings

    def files(self, engine):
        names = (
            ["en_US-ljspeech-high.onnx", "en_US-ljspeech-high.onnx.json"]
            if engine == "piper"
            else ["kokoro-v1.0.onnx", "voices-v1.0.bin"]
        )
        return [self.settings.speech_models_dir / name for name in names]

    def defaults(self):
        if self.settings.tts_provider == "local":
            return {
                "question": "kokoro/sky-anime",
                "coach_answer": "piper/ljspeech",
                "exercise": "piper/ljspeech",
            }
        return {role: "configured" for role in SPEECH_ROLES}

    def candidates(self):
        models = []
        for identity, preset in PRESETS.items():
            installed = all(p.is_file() for p in self.files(preset["engine"]))
            module = "piper" if preset["engine"] == "piper" else "kokoro_onnx"
            installed = installed and importlib.util.find_spec(module) is not None
            models.append(
                {
                    "id": identity,
                    "name": preset["name"],
                    "available": installed,
                    "reason": None if installed else "音声モデル未導入",
                    "local": True,
                }
            )
        models.append(
            {
                "id": "configured",
                "name": "設定済み音声API"
                if self.settings.tts_provider == "http"
                else "保存音声なし（ブラウザ読み上げを利用）",
                "available": True,
                "reason": None,
                "local": self.settings.tts_provider != "http",
            }
        )
        return {"models": models, "defaults": self.defaults()}

    def validate(self, selection):
        item = next((m for m in self.candidates()["models"] if m["id"] == selection), None)
        if item is None:
            raise APIError(422, "unknown_speech_model", "音声モデル候補から選択してください。")
        if not item["available"]:
            raise APIError(
                409,
                "speech_model_unavailable",
                "音声モデルが未導入です。setup_speech.pyを実行してください。",
            )
        return selection

    def cache_identity(self, selection, voice):
        if selection == "configured":
            return [
                selection,
                self.settings.tts_provider,
                self.settings.tts_base_url,
                self.settings.tts_model,
                voice,
            ]
        preset = PRESETS[selection]
        return [
            selection,
            preset,
            [
                (p.name, p.stat().st_size, p.stat().st_mtime_ns)
                for p in self.files(preset["engine"])
            ],
        ]

    def synthesize(self, text, selection):
        self.validate(selection)
        preset = PRESETS[selection]
        # A bounded child process keeps a slow native inference from holding the API forever.
        with tempfile.TemporaryDirectory(prefix="oral-speech-") as work:
            output = Path(work) / "speech.wav"
            try:
                subprocess.run(
                    [sys.executable, "-m", "backend.app.local_speech", str(output)],
                    input=json.dumps(
                        {
                            "text": text,
                            "preset": preset,
                            "models_dir": str(self.settings.speech_models_dir),
                        }
                    ),
                    text=True,
                    capture_output=True,
                    cwd=ROOT,
                    timeout=60,
                    check=True,
                )
                if output.stat().st_size > 10 * 1024 * 1024:
                    raise APIError(
                        502, "tts_too_large", "音声が上限を超えています。短い英文で試してください。"
                    )
                return output.read_bytes()
            except subprocess.TimeoutExpired:
                raise APIError(504, "tts_timeout", "ローカル音声合成が時間切れになりました。", True)
            except (OSError, subprocess.CalledProcessError):
                raise APIError(
                    502,
                    "tts_error",
                    "ローカル音声合成に失敗しました。音声モデルの導入状態を確認してください。",
                    True,
                )
