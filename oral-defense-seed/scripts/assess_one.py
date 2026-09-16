"""P0 CLI entry point. Reports unavailable until the real acoustic gate passes."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.audio import inspect_upload
from backend.app.pronunciation import assess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("audio", type=Path)
parser.add_argument("reference")
args = parser.parse_args()
if not args.audio.is_file():
    parser.error("Audio file not found")
print(
    json.dumps(
        assess(args.audio, args.reference, metadata=inspect_upload(args.audio)),
        ensure_ascii=False,
        indent=2,
    )
)
