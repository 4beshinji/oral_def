"""Explicit OpenCode model routes; catalog refresh never sends user content or keys."""

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from .db import encode, now
from .errors import APIError

USER_AGENT = "oral-defense-drill/0.4.0"
BASE_URLS = {
    "opencode-go": "https://opencode.ai/zen/go/v1",
    "opencode": "https://opencode.ai/zen/v1",
}
METADATA_URL = "https://models.dev/api.json"
# Protocol implementations, not model names. A new model using one of these
# published SDK formats becomes selectable without a source-code update.
SDK_PROTOCOLS = {
    "@ai-sdk/openai-compatible": "chat_completions",
    "@ai-sdk/openai": "responses",
    "@ai-sdk/anthropic": "messages",
}


def conversation_headers(session_id: str, role: str):
    # Require identity explicitly: no per-request random fallback and no shared role ID.
    if role not in {
        "examiner",
        "coach",
        "catalog",
        "meaning",
        "hint",
        "outline",
        "revision",
        "full_answer",
        "factuality_review",
    }:
        raise ValueError("Unknown conversation role")
    identity = uuid5(UUID(session_id), f"oral-defense-drill:{role}")
    return {"x-opencode-session": str(identity), "User-Agent": USER_AGENT}


@dataclass(frozen=True)
class TextTarget:
    provider: str
    model: str
    protocol: str
    base_url: str
    api_key: str = field(default="", repr=False)

    def public(self):
        return {
            "id": f"{self.provider}/{self.model}",
            "provider": self.provider,
            "model": self.model,
            "protocol": self.protocol,
            "endpoint": self.base_url,
            "mock": self.provider == "mock",
        }


class ModelCatalog:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data_dir / "opencode-models.json"
        self.availability = None
        self.refresh_lock = threading.Lock()
        self.retry_after = 0.0
        self.last_error = None
        if self.path.is_file():
            try:
                saved = json.loads(self.path.read_text())
                self.retry_after = float(saved.get("retry_after", 0))
                self.last_error = saved.get("last_error")
                if saved.get("schema_version") == 2 and isinstance(saved.get("models"), list):
                    datetime.fromisoformat(saved["checked_at"])
                    self.availability = saved
            except (OSError, ValueError, KeyError, TypeError):
                pass

    def key(self, provider):
        explicit = (
            self.settings.opencode_go_api_key
            if provider == "opencode-go"
            else self.settings.opencode_zen_api_key
        )

        if explicit:
            return explicit
        # Read the existing OpenCode store on demand; never copy or persist secrets.
        try:
            saved = json.loads(self.settings.opencode_auth_file.read_text())
            entry = saved.get(provider) if isinstance(saved, dict) else None
            if isinstance(entry, dict) and entry.get("type") == "api":
                key = entry.get("key")
                if isinstance(key, str) and key.strip():
                    return key.strip()
        except (OSError, ValueError):
            pass
        return ""

    def default_id(self):
        s = self.settings
        if s.text_provider == "mock":
            return "mock/demo"
        if s.text_provider == "compatible":
            return f"compatible/{s.text_model}"
        if not s.text_model:
            return ""
        return (
            s.text_model
            if s.text_model.startswith(s.text_provider + "/")
            else f"{s.text_provider}/{s.text_model}"
        )

    def candidates(self):
        candidates = [
            {
                "id": "mock/demo",
                "provider": "mock",
                "model": "demo",
                "available": True,
                "reason": None,
            }
        ]
        s = self.settings
        if s.text_provider == "compatible":
            candidates.append(
                {
                    "id": f"compatible/{s.text_model}",
                    "provider": "compatible",
                    "model": s.text_model,
                    "available": True,
                    "reason": None,
                }
            )
        for entry in (self.availability or {}).get("models", []):
            reason = entry.get("reason")
            if reason is None and not self.key(entry["provider"]):
                reason = "APIキー未設定"
            candidates.append({**entry, "available": reason is None, "reason": reason})
        return {
            "models": candidates,
            "default_id": self.default_id(),
            "checked_at": self.availability["checked_at"] if self.availability else None,
            "source": "cached" if self.availability else "empty",
            "auto_refresh_hours": self.settings.model_catalog_refresh_hours,
            "next_refresh_at": self.next_refresh_at(),
            "refresh_error": self.last_error,
        }

    def next_refresh_at(self):
        if not self.settings.model_catalog_refresh_hours:
            return None
        if self.last_error:
            return self.retry_after
        last_success = (
            datetime.fromisoformat(self.availability["checked_at"]).timestamp()
            if self.availability
            else 0
        )
        return max(
            last_success + self.settings.model_catalog_refresh_hours * 3600, self.retry_after
        )

    def resolve(self, selection=None):
        selection = selection or self.default_id()
        candidate = next((m for m in self.candidates()["models"] if m["id"] == selection), None)
        if candidate is None:
            raise APIError(422, "unknown_model", "モデル候補から選択してください。")
        if not candidate["available"]:
            raise APIError(409, "model_unavailable", candidate["reason"])
        provider, model = candidate["provider"], candidate["model"]
        if provider == "mock":
            return TextTarget(provider, model, "mock", "")
        if provider == "compatible":
            return TextTarget(
                provider,
                model,
                "chat_completions",
                self.settings.text_base_url,
                self.settings.text_api_key,
            )
        protocol = candidate["protocol"]
        return TextTarget(provider, model, protocol, BASE_URLS[provider], self.key(provider))

    def from_saved(self, saved):
        # The endpoint/protocol come only from server configuration, never from a GUI URL.
        target = self.resolve(saved["id"])
        if target.base_url != saved["endpoint"] or target.protocol != saved["protocol"]:
            raise APIError(
                409,
                "model_configuration_changed",
                "接続設定が変更されています。GUIからモデルを選び直してください。",
            )
        return target

    def refresh(self, force=True):
        if not force and (self.next_refresh_at() is None or time.time() < self.next_refresh_at()):
            return self.candidates()
        if not self.refresh_lock.acquire(blocking=False):
            return self.candidates()
        try:
            return self._refresh()
        finally:
            self.refresh_lock.release()

    def persist(self):
        saved = {
            **(self.availability or {}),
            "retry_after": self.retry_after,
            "last_error": self.last_error,
        }
        temp = self.path.with_suffix(".tmp")
        temp.write_text(encode(saved))
        temp.replace(self.path)

    def _refresh(self):
        identity = str(uuid5(NAMESPACE_URL, str(self.settings.data_dir)))
        headers = conversation_headers(identity, "catalog")
        entries = []
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                metadata_response = client.get(METADATA_URL, headers=headers)
                metadata_response.raise_for_status()
                metadata = metadata_response.json()
                for provider, base_url in BASE_URLS.items():
                    provider_metadata = metadata[provider]
                    model_metadata = provider_metadata["models"]
                    if not isinstance(model_metadata, dict):
                        raise ValueError("Invalid provider metadata")
                    response = client.get(base_url + "/models", headers=headers)
                    response.raise_for_status()
                    values = response.json()["data"]
                    if not isinstance(values, list) or not values:
                        raise ValueError("Empty catalog")
                    models = [v["id"] for v in values]
                    if not all(
                        isinstance(m, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,150}", m)
                        for m in models
                    ):
                        raise ValueError("Invalid model IDs")
                    for model in sorted(set(models)):
                        info = model_metadata.get(model)
                        reason, protocol, name = "API形式が未確認または未対応", None, model
                        if isinstance(info, dict):
                            name = info.get("name") if isinstance(info.get("name"), str) else model
                            override = info.get("provider") or {}
                            if not isinstance(override, dict):
                                raise ValueError("Invalid model metadata")
                            protocol = SDK_PROTOCOLS.get(
                                override.get("npm", provider_metadata.get("npm"))
                            )
                            endpoint = override.get("api", provider_metadata.get("api"))
                            output = (info.get("modalities") or {}).get("output", ["text"])
                            if protocol and endpoint == base_url and "text" in output:
                                reason = None
                        entries.append(
                            {
                                "id": f"{provider}/{model}",
                                "provider": provider,
                                "model": model,
                                "name": name[:200],
                                "protocol": protocol,
                                "reason": reason,
                            }
                        )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            self.retry_after = time.time() + 300
            self.last_error = (
                "候補を更新できませんでした。前回の一覧を保持し、5分後以降に再試行します。"
            )
            self.persist()
            return self.candidates()
        updated = {"schema_version": 2, "checked_at": now(), "models": entries}
        self.availability = updated
        self.retry_after = 0
        self.last_error = None
        self.persist()
        return self.candidates()
