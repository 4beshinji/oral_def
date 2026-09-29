"""Offline English ASR worker; run as a bounded child process."""

import json
import sys
from pathlib import Path

from faster_whisper import WhisperModel


def transcribe_file(audio_path: Path, model_dir: Path, cpu_threads: int) -> dict:
    required = ("model.bin", "config.json", "tokenizer.json")
    if not all((model_dir / name).is_file() for name in required):
        raise FileNotFoundError("ASR model files are missing")
    model = WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=cpu_threads)
    segments, info = model.transcribe(
        str(audio_path),
        language="en",
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    spoken = [segment for segment in segments if segment.text.strip()]
    transcript = " ".join(segment.text.strip() for segment in spoken).strip()
    if len(transcript) > 4000:
        raise ValueError("ASR transcript exceeds 4000 characters")
    return {
        "status": "ok" if transcript else "no_speech",
        "text": transcript,
        "language": info.language,
        "duration_s": info.duration,
        "segments": [
            {"start_s": segment.start, "end_s": segment.end, "text": segment.text.strip()}
            for segment in spoken
        ],
    }


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: python -m backend.app.local_asr AUDIO MODEL_DIR CPU_THREADS")
    result = transcribe_file(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
