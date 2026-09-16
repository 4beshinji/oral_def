import difflib
import unicodedata


def normalize(text):
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(c for c in text if not unicodedata.category(c).startswith("P"))
    return " ".join(text.split())


def compare(reference, typed):
    expected, actual = normalize(reference).split(), normalize(typed).split()
    changes = [
        {"kind": kind, "expected": expected[a:b], "actual": actual[c:d]}
        for kind, a, b, c, d in difflib.SequenceMatcher(
            None, expected, actual, autojunk=False
        ).get_opcodes()
    ]
    return {
        "kind": "dictation",
        "normalized_reference": " ".join(expected),
        "normalized_typed": " ".join(actual),
        "matches": expected == actual,
        "changes": changes,
        "normalization": "Unicode NFKC・小文字化・句読点除去・空白整形。短縮形や数字の同値化はしません。",
    }
