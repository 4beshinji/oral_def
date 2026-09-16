from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Difficulty(Input):
    language_level: Literal["simple", "standard", "advanced"] = "standard"
    technical_depth: Literal["introductory", "research"] = "research"
    strictness: Literal["supportive", "strict"] = "supportive"


TextRole = Literal["examiner", "meaning", "hint", "outline", "revision", "full_answer"]
TEXT_ROLES = ("examiner", "meaning", "hint", "outline", "revision", "full_answer")


SpeechRole = Literal["question", "coach_answer", "exercise"]


class SessionInput(Input):
    pack: dict
    text_model: str | None = Field(default=None, max_length=250)
    speech_models: dict[SpeechRole, str] = Field(default_factory=dict)
    role_models: dict[TextRole, str] = Field(default_factory=dict)
    research_brief: str = Field(default="", max_length=4000)
    mode: Literal["shadowing", "independent"] = "shadowing"
    scenario: Literal["seminar", "lab_defense", "icebreaker", "networking"] = "seminar"
    settings: Difficulty = Field(default_factory=Difficulty)

    @field_validator("pack")
    @classmethod
    def pack_size(cls, value):
        from .db import encode

        if len(encode(value)) > 8000:
            raise ValueError("PackはJSON全体で8000文字以下にしてください")
        for field in ("persona", "notes"):
            if not isinstance(value.get(field), str):
                raise ValueError(f"Pack requires {field}: string")
        for field in ("glossary", "question_angles"):
            if not isinstance(value.get(field), list):
                raise ValueError(f"Pack requires {field}: array")
        return value


class RequestInput(Input):
    request_id: UUID


class ModelInput(Input):
    role: TextRole | None = None
    text_model: str = Field(min_length=1, max_length=250)


class CatalogRefreshInput(Input):
    force: bool = True


class CoachInput(RequestInput):
    level: Literal["meaning", "hint", "outline", "revision", "full_answer"]
    user_note: str = Field(default="", max_length=4000)
    draft: str = Field(default="", max_length=4000)


class ExerciseInput(Input):
    mode: Literal["read_aloud", "listen_repeat", "dictation"]
    text: str = Field(min_length=1, max_length=4000)
    origin: Literal["coach", "manual", "examiner"] = "manual"


class ConfirmInput(RequestInput):
    answer_en: str = Field(min_length=1, max_length=4000)
    unable_to_answer: bool = False


class DictationInput(Input):
    typed_text: str = Field(max_length=4000)


class TTSInput(Input):
    source_type: Literal["question", "coach_answer", "exercise", "conversation_reference"]
    source_id: UUID
    voice: str | None = Field(default=None, max_length=100)


class AssistanceInput(Input):
    turn_id: UUID
    exercise_id: UUID | None = None
    kind: Literal["subtitle_shown", "reference_shown", "browser_tts_played", "recording_played"]


class SpeechModelInput(Input):
    role: SpeechRole
    model: str = Field(min_length=1, max_length=100)


class SpeechPreviewInput(Input):
    model: str = Field(min_length=1, max_length=100)
