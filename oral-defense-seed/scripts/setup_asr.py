"""Acquire the pinned local English ASR model and verify exact files."""

import hashlib
import json
import os
from pathlib import Path

from huggingface_hub import snapshot_download

DEST = (
    Path(
        os.getenv("ASR_MODEL_DIR")
        or Path(__file__).resolve().parents[1] / "models/speech/asr-base.en"
    )
    .expanduser()
    .resolve()
)
REPOSITORY = "Systran/faster-whisper-base.en"
REVISION = "fad79c08a643e3aa623adfae1c1861e50438482d"
CHECKSUMS = {
    "model.bin": "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef",
    "config.json": "e14f7e44aedb4fcd6e5c44b36a2aee4e6d5d4a009f775ef894a9218d5e4dacf6",
    "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
}


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPOSITORY,
        revision=REVISION,
        local_dir=DEST,
        allow_patterns=list(CHECKSUMS),
    )
    for name, expected in CHECKSUMS.items():
        with (DEST / name).open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"ASR model checksum mismatch: {name}")
        print(f"Verified: {name}")
    (DEST / "downloads.json").write_text(
        json.dumps({"repository": REPOSITORY, "revision": REVISION, "sha256": CHECKSUMS}, indent=2)
        + "\n"
    )


if __name__ == "__main__":
    main()
