"""Download the two local English speech models. No user text or keys are sent."""

import hashlib
import json
import os
from pathlib import Path

import httpx

DEST = (
    Path(os.getenv("SPEECH_MODELS_DIR") or Path(__file__).resolve().parents[1] / "models/speech")
    .expanduser()
    .resolve()
)
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/high/"
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
FILES = {
    "en_US-ljspeech-high.onnx": PIPER + "en_US-ljspeech-high.onnx",
    "en_US-ljspeech-high.onnx.json": PIPER + "en_US-ljspeech-high.onnx.json",
    "ljspeech-MODEL_CARD": PIPER + "MODEL_CARD",
    "kokoro-v1.0.onnx": KOKORO + "kokoro-v1.0.onnx",
    "voices-v1.0.bin": KOKORO + "voices-v1.0.bin",
}


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = {}
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        for name, url in FILES.items():
            target = DEST / name
            if not target.is_file():
                print(f"Downloading {name}", flush=True)
                temp = target.with_suffix(target.suffix + ".part")
                try:
                    with client.stream("GET", url) as response:
                        response.raise_for_status()
                        with temp.open("wb") as output:
                            for chunk in response.iter_bytes():
                                output.write(chunk)
                    temp.replace(target)
                finally:
                    temp.unlink(missing_ok=True)
            with target.open("rb") as source:
                checksum = hashlib.file_digest(source, "sha256").hexdigest()
            manifest[name] = {"url": url, "sha256": checksum, "bytes": target.stat().st_size}
            print(f"Ready: {name}", flush=True)
    (DEST / "downloads.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
