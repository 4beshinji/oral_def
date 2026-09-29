"""Grounded, varied questions for a model stuck on the same output."""

import re
import unicodedata

QUESTIONS = {
    "academic": (
        "What is the main question your research is trying to answer?",
        "Which assumption in your current research plan is most important to test?",
        "What observation would make you revise your current approach?",
        "How would you decide whether a comparison is fair?",
        "What is one limitation you expect in your planned method?",
        "How would you check that your findings are reproducible?",
        "What alternative explanation would you want to rule out?",
        "What would count as a useful result even if your main hypothesis is not supported?",
        "Which part of the work would you prioritize if time were limited?",
        "How would you explain the purpose of this research to someone outside your field?",
        "What information do you still need before choosing your evaluation method?",
        "How would you communicate uncertainty in your eventual results?",
        "What would be the next step after your initial study?",
        "Which aspect of your research plan would benefit most from feedback?",
        "What ethical or practical constraint should you consider in this work?",
        "What would make the research question worth studying even without a positive result?",
    ),
    "social": (
        "What first interested you in this area?",
        "What part of your research do you most enjoy discussing?",
        "What are you hoping to learn from people at this event?",
        "What has surprised you most while learning about your topic?",
        "How would you explain your work to someone in another field?",
        "What kind of collaboration would you find interesting?",
        "What is a question you would like to ask other researchers here?",
        "What do you enjoy doing when you take a break from research?",
        "What drew you to your current research group?",
        "What are you looking forward to working on next?",
    ),
}
_QUESTION_WORD = re.compile(r"\b(?:what|why|how|which|when|where|who)\b", re.IGNORECASE)
_COMMON = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "do",
    "for",
    "how",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
    "you",
    "your",
}


def normalized_question(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def question_keys(value: str) -> set[str]:
    """Match both the whole response and a question after a short preface."""
    parts = [part.strip() for part in re.split(r"[.!?]+", value) if part.strip()]
    if not parts:
        return set()
    keys = {normalized_question(value), normalized_question(parts[-1])}
    question_word = _QUESTION_WORD.search(parts[-1])
    if question_word:
        keys.add(normalized_question(parts[-1][question_word.start() :]))
    return keys


def _terms(value: str) -> set[str]:
    return set(normalized_question(value).split()) - _COMMON


def _same_topic(candidate: str, previous: str) -> bool:
    candidate_terms, previous_terms = _terms(candidate), _terms(previous)
    shared = len(candidate_terms & previous_terms)
    return shared >= 2 and shared / max(1, min(len(candidate_terms), len(previous_terms))) >= 0.5


def recovery_question(scenario: str, previous: list[str]) -> dict[str, object] | None:
    bank = QUESTIONS["social" if scenario in {"networking", "icebreaker"} else "academic"]
    used = set().union(*(question_keys(question) for question in previous))
    unused = [question for question in bank if not question_keys(question).intersection(used)]
    for question in unused:
        if not any(
            _same_topic(question, previous_question) for previous_question in previous[-12:]
        ):
            return {
                "question_en": question,
                "basis_note": "General discussion question after repeated model output; no learner facts asserted.",
                "follow_up": False,
            }
    if unused:
        return {
            "question_en": unused[0],
            "basis_note": "General discussion question after repeated model output; no learner facts asserted.",
            "follow_up": False,
        }
    return None
