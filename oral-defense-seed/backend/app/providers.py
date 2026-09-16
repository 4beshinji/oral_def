import json
import logging
import time

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import APIError
from .speech_models import SpeechCatalog
from .text_models import ModelCatalog, conversation_headers

log = logging.getLogger("drill.providers")


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question_en: str = Field(min_length=1, max_length=2000)
    basis_note: str = Field(min_length=1, max_length=2000)
    follow_up: bool


class Coach(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    explanation_ja: str = Field(min_length=1, max_length=8000)
    answer_en: str | None = Field(max_length=4000)
    needs_user_input: list[str] = Field(max_length=30)


def generation_schema(schema):
    # llama.cpp expands length bounds into grammar repetitions. Keep the
    # structural contract small; Pydantic still checks every bound on receipt.
    if isinstance(schema, dict):
        return {
            key: generation_schema(value)
            for key, value in schema.items()
            if key not in {"minLength", "maxLength", "minItems", "maxItems"}
        }
    if isinstance(schema, list):
        return [generation_schema(value) for value in schema]
    return schema


class Providers:
    def __init__(self, settings):
        self.settings = settings
        self.catalog = ModelCatalog(settings)
        self.speech_catalog = SpeechCatalog(settings)

    def text(self, role, messages, *, session_id, target=None):
        headers = conversation_headers(session_id, role)
        target = target or self.catalog.resolve()
        schema = Question if role == "examiner" else Coach
        if target.provider == "mock":
            p = json.loads(messages[-1]["content"])
            if role == "examiner":
                count = len(p["confirmed_public_turns"])
                follow_up = count > 0 and not p["must_change_angle"]
                questions = [
                    "What problem does your research address?",
                    "What evidence supports that choice?",
                    "How would you compare that approach with a baseline?",
                    "What is the main limitation?",
                    "How would you measure the impact of that limitation?",
                    "What would you investigate next?",
                ]
                data = {
                    "question_en": questions[count % len(questions)],
                    "basis_note": "開発用mock: 一般的な確認質問。研究成果は仮定していません。",
                    "follow_up": follow_up,
                }
            else:
                full = p["requested_level"] in {"full_answer", "revision"}
                explanation = {
                    "meaning": "この質問で何を説明する必要があるか、自分の研究の目的・根拠・制約に照らして確認しましょう。",
                    "hint": "本人のメモから、根拠となる事実を一つ選びましょう。未確認の結果は述べないようにします。",
                    "outline": "結論を一文で述べ、次に根拠か制約を一つ添えましょう。",
                    "revision": "開発用mockのため下書きをそのまま返します。実モデルでは意味を保って英文を修正します。",
                    "full_answer": "開発用mockの返答です。本人の成果が不明なので、未確認であることを伝えます。",
                }
                data = {
                    "explanation_ja": explanation[p["requested_level"]],
                    "answer_en": (
                        p["learner_draft"]
                        or "I am still clarifying the details of my research. I do not have verified results to share yet."
                    )
                    if full
                    else None,
                    "needs_user_input": ["研究の目的と、確認できた根拠"] if full else [],
                }
            return schema.model_validate(data).model_dump()

        started = time.monotonic()
        try:
            if target.api_key:
                headers["x-api-key" if target.protocol == "messages" else "Authorization"] = (
                    target.api_key if target.protocol == "messages" else f"Bearer {target.api_key}"
                )
            if target.protocol == "chat_completions":
                output_schema = generation_schema(schema.model_json_schema())
                if self.settings.text_response_format == "json_schema" and role == "examiner":
                    context = json.loads(messages[-1]["content"])
                    if (
                        context.get("must_change_angle")
                        or context.get("confirmed_public_turns") == []
                    ):
                        output_schema["properties"]["follow_up"]["enum"] = [False]
                endpoint = "/chat/completions"
                payload = {
                    "model": target.model,
                    "messages": messages,
                    "response_format": (
                        {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "examiner" if role == "examiner" else "coach",
                                "strict": True,
                                "schema": output_schema,
                            },
                        }
                        if self.settings.text_response_format == "json_schema"
                        else {"type": "json_object"}
                    ),
                    "max_tokens": 4096,
                }
            elif target.protocol == "responses":
                endpoint = "/responses"
                payload = {
                    "model": target.model,
                    "input": messages,
                    "store": False,
                    "max_output_tokens": 4096,
                    "text": {"format": {"type": "json_object"}},
                }
            elif target.protocol == "messages":
                endpoint = "/messages"
                headers["anthropic-version"] = "2023-06-01"
                payload = {
                    "model": target.model,
                    "system": "\n\n".join(m["content"] for m in messages if m["role"] == "system"),
                    "messages": [m for m in messages if m["role"] != "system"],
                    "max_tokens": 4096,
                }
            else:
                raise APIError(409, "unsupported_protocol", "このモデルのAPI形式は未対応です。")
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                response = client.post(
                    target.base_url.rstrip("/") + endpoint,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
                if target.protocol == "chat_completions":
                    content = result["choices"][0]["message"]["content"]
                elif target.protocol == "responses":
                    content = "".join(
                        part["text"]
                        for item in result["output"]
                        if item.get("type") == "message"
                        for part in item.get("content", [])
                        if part.get("type") == "output_text"
                    )
                else:
                    content = "".join(
                        part["text"] for part in result["content"] if part.get("type") == "text"
                    )
                validated = schema.model_validate_json(content).model_dump()
                if (
                    role != "examiner"
                    and json.loads(messages[-1]["content"])["requested_level"]
                    not in {"full_answer", "revision"}
                    and validated["answer_en"] is not None
                ):
                    raise ValueError("Answer at the wrong assistance level")
                usage = result.get("usage") or {}
                counts = {
                    key: usage[key]
                    for key in (
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                        "input_tokens",
                        "output_tokens",
                    )
                    if isinstance(usage.get(key), int)
                }
                log.info(
                    "text provider=%s model=%s latency_s=%.3f usage=%s",
                    target.provider,
                    target.model,
                    time.monotonic() - started,
                    counts,
                )
                return validated
        except httpx.TimeoutException:
            raise APIError(
                504,
                "provider_timeout",
                "応答が時間切れになりました。入力は保存済みです。新しい要求で再試行すると再課金される場合があります。",
                True,
            )
        except (ValidationError, ValueError, KeyError, IndexError, TypeError):
            raise APIError(
                502,
                "invalid_provider_response",
                "応答の形式が不正です。新しい要求で再試行してください。",
                True,
            )
        except httpx.HTTPError:
            raise APIError(
                502,
                "provider_error",
                "テキストAPIに接続できませんでした。設定を確認してください。",
                True,
            )

    def speech(self, text, voice):
        s = self.settings
        if s.tts_provider != "http":
            return None
        headers = {"Authorization": f"Bearer {s.tts_api_key}"} if s.tts_api_key else {}
        payload = {"input": text, "voice": voice, "response_format": "wav"}
        if s.tts_model:
            payload["model"] = s.tts_model
        try:
            with httpx.Client(timeout=60, follow_redirects=False) as client:
                with client.stream(
                    "POST", s.tts_base_url, headers=headers, json=payload
                ) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 10 * 1024 * 1024:
                            raise APIError(502, "tts_too_large", "TTS音声が上限を超えています。")
                        chunks.append(chunk)
                    return b"".join(chunks)
        except httpx.TimeoutException:
            raise APIError(
                504, "tts_timeout", "TTSが時間切れになりました。手動で再試行できます。", True
            )
        except httpx.HTTPError:
            raise APIError(502, "tts_error", "TTS APIに接続できませんでした。", True)
