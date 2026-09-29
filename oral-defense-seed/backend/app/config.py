import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()
    )
    text_provider: str = field(default_factory=lambda: os.getenv("TEXT_PROVIDER", "mock"))
    text_base_url: str = field(default_factory=lambda: os.getenv("TEXT_BASE_URL", ""))
    text_model: str = field(default_factory=lambda: os.getenv("TEXT_MODEL", ""))
    text_response_format: str = field(
        default_factory=lambda: os.getenv("TEXT_RESPONSE_FORMAT", "json_object")
    )
    text_temperature: float | None = field(
        default_factory=lambda: (
            float(os.environ["TEXT_TEMPERATURE"]) if os.getenv("TEXT_TEMPERATURE") else None
        )
    )
    text_api_key: str = field(default_factory=lambda: os.getenv("TEXT_API_KEY", ""), repr=False)
    opencode_go_api_key: str = field(
        default_factory=lambda: os.getenv("OPENCODE_GO_API_KEY", os.getenv("OPENCODE_API_KEY", "")),
        repr=False,
    )
    opencode_zen_api_key: str = field(
        default_factory=lambda: os.getenv(
            "OPENCODE_ZEN_API_KEY", os.getenv("OPENCODE_API_KEY", "")
        ),
        repr=False,
    )
    opencode_auth_file: Path = field(
        default_factory=lambda: Path(
            os.getenv("OPENCODE_AUTH_FILE")
            or Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share")
            / "opencode/auth.json"
        ).expanduser(),
        repr=False,
    )
    model_catalog_refresh_hours: float = field(
        default_factory=lambda: float(os.getenv("MODEL_CATALOG_REFRESH_HOURS", "24"))
    )
    tts_provider: str = field(default_factory=lambda: os.getenv("TTS_PROVIDER", "local"))
    speech_models_dir: Path = field(
        default_factory=lambda: (
            Path(os.getenv("SPEECH_MODELS_DIR", ROOT / "models/speech")).expanduser().resolve()
        )
    )
    tts_base_url: str = field(default_factory=lambda: os.getenv("TTS_BASE_URL", ""))
    tts_model: str = field(default_factory=lambda: os.getenv("TTS_MODEL", ""))
    tts_voice: str = field(default_factory=lambda: os.getenv("TTS_VOICE", "alloy"))
    tts_api_key: str = field(default_factory=lambda: os.getenv("TTS_API_KEY", ""), repr=False)
    pronunciation_provider: str = field(
        default_factory=lambda: os.getenv("PRONUNCIATION_PROVIDER", "unavailable")
    )
    asr_provider: str = field(default_factory=lambda: os.getenv("ASR_PROVIDER", "unavailable"))
    asr_model_dir: Path = field(
        default_factory=lambda: (
            Path(os.getenv("ASR_MODEL_DIR", ROOT / "models/speech/asr-base.en"))
            .expanduser()
            .resolve()
        )
    )
    asr_cpu_threads: int = field(default_factory=lambda: int(os.getenv("ASR_CPU_THREADS", "8")))

    def __post_init__(self):
        if self.text_temperature is not None and not 0 <= self.text_temperature <= 1:
            raise ValueError("TEXT_TEMPERATURE must be between 0 and 1")
        if not 0 <= self.model_catalog_refresh_hours <= 168:
            raise ValueError("MODEL_CATALOG_REFRESH_HOURS must be between 0 and 168")
        if self.text_response_format not in {"json_object", "json_schema"}:
            raise ValueError("TEXT_RESPONSE_FORMAT must be json_object or json_schema")
        if self.text_provider not in {"mock", "compatible", "opencode-go", "opencode"}:
            raise ValueError("Unknown TEXT_PROVIDER")
        if self.tts_provider not in {"mock", "http", "browser", "local"}:
            raise ValueError("Unknown TTS_PROVIDER")
        if self.pronunciation_provider not in {"unavailable", "kaldi"}:
            raise ValueError("Unknown PRONUNCIATION_PROVIDER")
        if self.asr_provider not in {"unavailable", "local"}:
            raise ValueError("Unknown ASR_PROVIDER")
        if not 1 <= self.asr_cpu_threads <= 32:
            raise ValueError("ASR_CPU_THREADS must be between 1 and 32")
        for enabled, url in [
            (self.text_provider == "compatible", self.text_base_url),
            (self.tts_provider == "http", self.tts_base_url),
        ]:
            if enabled:
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError(
                        "Provider URL must be an HTTP(S) URL without credentials or query"
                    )
        if self.text_provider == "compatible" and not self.text_model:
            raise ValueError("TEXT_MODEL is required")
