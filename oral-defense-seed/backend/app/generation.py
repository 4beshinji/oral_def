"""Runtime limits and provenance for saved provider outputs."""

import json
from contextlib import contextmanager
from threading import BoundedSemaphore

from . import contexts
from .db import digest, encode, now
from .errors import APIError
from .factuality import has_unsupported_personal_claim


class WorkLimiter:
    """At most four expensive operations per app; control requests never wait."""

    def __init__(self, maximum=4):
        self.maximum = maximum
        self._slots = BoundedSemaphore(maximum)

    @contextmanager
    def slot(self):
        if not self._slots.acquire(blocking=False):
            raise APIError(
                503, "capacity", "処理が混み合っています。少し待って再試行してください。", True
            )
        try:
            yield
        finally:
            self._slots.release()

    def run(self, function, *args, **kwargs):
        with self.slot():
            return function(*args, **kwargs)


def generate_text(providers, role, messages, *, session_id, target):
    metadata = {
        "schema_version": "1.0",
        "kind": "text",
        "role": role,
        "target": target.public(),
        "response_format": (
            providers.settings.text_response_format
            if target.protocol == "chat_completions"
            else "json_object"
            if target.protocol == "responses"
            else "prompt_json"
        ),
        "temperature": providers.settings.text_temperature,
        "max_output_tokens": 4096,
        "prompt_hash": digest(encode([m for m in messages if m["role"] == "system"])),
        "input_hash": digest(encode(messages)),
        "started_at": now(),
    }
    result = providers.work.run(
        providers.text, role, messages, session_id=session_id, target=target
    )
    if role == "full_answer" and result.get("answer_en"):
        review_messages = contexts.factuality_review_messages(messages, result["answer_en"])
        original = json.loads(messages[-1]["content"])
        policy_rejected = has_unsupported_personal_claim(
            result["answer_en"],
            [original["research_brief"], original["learner_note"], original["learner_draft"]],
        )
        review = providers.work.run(
            providers.text,
            "factuality_review",
            review_messages,
            session_id=session_id,
            target=target,
        )
        metadata["factuality_review"] = {
            "input_hash": digest(encode(review_messages)),
            "output_hash": digest(encode(review)),
            "model_supported": review["supported"],
            "policy_rejected": policy_rejected,
            "supported": review["supported"] and not policy_rejected,
        }
        if not metadata["factuality_review"]["supported"]:
            metadata["candidate_output_hash"] = digest(encode(result))
            result = {
                **result,
                "answer_en": "I haven't decided on that detail yet.",
                "explanation_ja": "元の案に本人が提示していない計画や事実が含まれたため、未決定であることを伝える文に置き換えました。本人の情報を追加して編集できます。",
                "needs_user_input": ["この質問について本人が決めた内容"],
            }
    metadata["finished_at"] = now()
    metadata["output_hash"] = digest(encode(result))
    return result, encode(metadata)


def save_generation(db, table, identity, metadata):
    # Only application call sites choose the business table.
    if table not in {"turns", "coach_messages", "exercises", "audio_files"}:
        raise ValueError("Unsupported generation owner")
    db.execute(f"UPDATE {table} SET generation_json=? WHERE id=?", (metadata, identity))
