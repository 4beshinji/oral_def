"""Opt-in real-provider outputs for the frozen RV02 rubric; no automatic pass claim.

Run from the app root with `uv run --locked python -m scripts.validate_factuality`.
Configure the same text provider/model as the app before invoking this command.
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from backend.app.config import ROOT, Settings
from backend.app.contexts import coach_messages
from backend.app.db import digest, now
from backend.app.generation import generate_text
from backend.app.providers import Providers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 5:
        parser.error("repetitions must be between 1 and 5")
    providers = Providers(Settings())
    target = providers.catalog.resolve()
    if target.provider == "mock":
        parser.error("A real text provider must be configured; mock cannot validate factuality.")
    case_file = ROOT / "examples/factuality_cases.json"
    cases = json.loads(case_file.read_text())
    results = {
        "created_at": now(),
        "fixture_hash": digest(case_file.read_text()),
        "target": target.public(),
        "rubric": cases["judgement"],
        "review_status": "pending",
        "runs": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for repetition in range(args.repetitions):
        for case in cases["cases"]:
            messages = coach_messages(
                pack={
                    "persona": "Academic conversation partner",
                    "notes": case["reference_notes"],
                    "glossary": [],
                    "question_angles": [],
                },
                research_brief=case["research_brief"],
                turns=[],
                current_question=case["question"],
                history=[],
                level="full_answer",
                user_note="",
                draft="",
                scenario="seminar",
                settings={
                    "language_level": "simple",
                    "technical_depth": "research",
                    "strictness": "supportive",
                },
            )
            response, metadata = generate_text(
                providers, "full_answer", messages, session_id=str(uuid4()), target=target
            )
            results["runs"].append(
                {
                    "case_id": case["id"],
                    "repetition": repetition + 1,
                    "input": json.loads(messages[-1]["content"]),
                    "expected": case["expected"],
                    "forbidden": case["forbidden"],
                    "response": response,
                    "generation": json.loads(metadata),
                    "judgement": None,
                }
            )
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
            print(f"{case['id']} / repetition {repetition + 1}: saved for review", flush=True)
    print(f"Outputs saved to {args.output}; assess every response against the frozen rubric.")


if __name__ == "__main__":
    main()
