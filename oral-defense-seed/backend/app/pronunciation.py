from .db import digest


def assess(audio_path, reference_text, provider="unavailable", metadata=None):
    # P0 has not passed. No synthetic scores or unvalidated subprocess are exposed.
    return {
        "schema_version": "1.0",
        "status": "unavailable",
        "reason_codes": ["kaldi_setup_and_control_gate_required"]
        if provider == "kaldi"
        else ["pronunciation_not_configured"],
        "reference_hash": digest(reference_text),
        "provider": "kaldi_gop" if provider == "kaldi" else "unavailable",
        "model_version": None,
        "calibration_status": "uncalibrated",
        "phones": [],
        "calibrated_score": None,
        "metrics": {"duration_s": (metadata or {}).get("duration_s")},
        "limitations": [
            "発音評価は未実装です。録音の再生と手動比較ができます。",
            "Alignment success does not prove correct pronunciation.",
        ],
    }
