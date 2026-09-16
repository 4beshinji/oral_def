import json

from .config import ROOT


def recent_within_budget(records, budget):
    selected = []
    used = 0
    for record in reversed(records[-12:]):
        size = len(json.dumps(record, ensure_ascii=False))
        if used + size > budget:
            break
        selected.append(record)
        used += size
    return selected[::-1]


def public_turns(turns):
    return recent_within_budget(
        [
            {
                "question_en": t["question_en"],
                "answer_en": t["confirmed_answer_en"],
                "unable_to_answer": bool(t.get("unable_to_answer", False)),
            }
            for t in turns[-12:]
            if t.get("confirmed_answer_en") is not None
        ],
        16000,
    )


def examiner_messages(*, pack, research_brief, turns, scenario, settings, follow_up_count):
    payload = {
        "expert_pack_snapshot": pack,
        "research_brief": research_brief,
        "confirmed_public_turns": public_turns(turns),
        "scenario": scenario,
        "language_level": settings["language_level"],
        "technical_depth": settings["technical_depth"],
        "strictness": settings["strictness"],
        "follow_up_count": follow_up_count,
        "max_follow_ups": 2,
        "must_change_angle": follow_up_count >= 2
        or bool(turns and turns[-1].get("unable_to_answer")),
    }
    return _messages("examiner", payload)


def coach_messages(
    *,
    pack,
    research_brief,
    turns,
    current_question,
    history,
    level,
    user_note,
    draft,
    scenario="seminar",
    settings=None,
):
    payload = {
        "expert_pack_snapshot": pack,
        "research_brief": research_brief,
        "public_turns": public_turns(turns),
        "current_question": current_question,
        "scenario": scenario,
        "assistance_settings": settings or {},
        "coach_history": recent_within_budget(
            [
                {
                    "level": h["level"],
                    "user_note": h["user_note"],
                    "draft": h["draft"],
                    "response": json.loads(h["response_json"]),
                }
                for h in history[-12:]
            ],
            8000,
        ),
        "requested_level": level,
        "learner_note": user_note,
        "learner_draft": draft,
    }
    return _messages("coach", payload)


def _messages(role, payload):
    return [
        {"role": "system", "content": (ROOT / "prompts" / f"{role}.md").read_text()},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
