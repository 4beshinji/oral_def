"""Conservative checks for unsupported first-person commitments."""

import re
import unicodedata

_COMMITMENT = re.compile(
    r"\b(?:I|we)\s+(?:plan to|intend to|will|expect(?: to)?|want to|aim to|chose to|"
    r"have decided to|am still considering|are still considering)\s+([^.!?;,]+)",
    re.IGNORECASE,
)
_RESULT = re.compile(
    r"\b(?:my|our)\s+(?:experiments?|data|results?|analysis|study)\s+"
    r"(?:showed|found|demonstrated|indicated|proved)\b[^.!?;,]*",
    re.IGNORECASE,
)
_ONGOING = re.compile(
    r"\b(?:I(?:'m| am)|we(?:'re| are))\s+(?:working with|conducting|running|preparing)\b"
    r"[^.!?;,]*",
    re.IGNORECASE,
)


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def has_unsupported_personal_claim(answer: str, learner_sources: list[str]) -> bool:
    """Require explicit learner wording for personal plans and completed results."""
    source = " ".join(_normalize(value) for value in learner_sources if value)
    for pattern in (_COMMITMENT, _RESULT, _ONGOING):
        for match in pattern.finditer(answer):
            if _normalize(match.group(0)) not in source:
                return True
    return False
