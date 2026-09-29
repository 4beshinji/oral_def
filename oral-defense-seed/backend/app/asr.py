"""Bounded offline speech recognition with no implicit model downloads."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from .config import ROOT
from .errors import APIError


class ASR:
    def __init__(self, settings):
        self.settings = settings

    def available(self) -> bool:
        model = self.settings.asr_model_dir
        return self.settings.asr_provider == "local" and all(
            (model / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")
        )

    def model_hash(self) -> str:
        digest = hashlib.sha256()
        with (self.settings.asr_model_dir / "model.bin").open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def transcribe(self, audio_path: Path) -> dict:
        if not self.available():
            raise APIError(503, "asr_unavailable", "ローカル音声認識モデルが利用できません。", True)
        try:
            finished = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "backend.app.local_asr",
                    str(audio_path),
                    str(self.settings.asr_model_dir),
                    str(self.settings.asr_cpu_threads),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=55,
                check=True,
            )
            result = json.loads(finished.stdout)
            if result.get("status") not in {"ok", "no_speech"}:
                raise ValueError("Invalid ASR status")
            return {**result, "model_hash": self.model_hash(), "model_id": "faster-whisper-base.en"}
        except subprocess.TimeoutExpired as exc:
            raise APIError(
                504, "asr_timeout", "音声認識が時間切れになりました。再試行できます。", True
            ) from exc
        except (subprocess.CalledProcessError, ValueError, json.JSONDecodeError, OSError) as exc:
            raise APIError(
                502, "asr_failed", "音声認識に失敗しました。録音を保持して再試行できます。", True
            ) from exc
