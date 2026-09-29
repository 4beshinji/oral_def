"""Allowlisted public export, separate from the private session read model.

Only a committed submission makes a reference public. Arbitrary provider JSON,
device identifiers, notes, and unused source material are not share metadata.
Pack contents and adopted source text are intentionally shared as user content.
"""

import json
from urllib.parse import urlsplit, urlunsplit

from .db import now, row, turn_row


def _fields(value, names):
    return {name: value[name] for name in names if name in value.keys()}


def _source_url(value):
    if not value:
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username or parts.password:
            return None
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        return None


def _attempt(db, value):
    attempt = _fields(value, ("id", "audio_id", "input_kind", "created_at"))
    attempt["transcript"] = None  # ASR is not implemented; never substitute the reference.
    attempt["audio_meta"] = _fields(
        json.loads(value["audio_meta_json"]),
        ("duration_s", "sample_rate", "channels", "format_name", "codec_name"),
    )
    capture = json.loads(value["capture_requested_json"] or "{}")
    attempt["capture_requested"] = {
        key: capture[key]
        for key in ("echoCancellation", "noiseSuppression", "autoGainControl")
        if isinstance(capture.get(key), bool)
    }
    audio = db.execute(
        "SELECT save_status FROM audio_files WHERE id=? AND session_id=?",
        (value["audio_id"], value["session_id"]),
    ).fetchone()
    attempt["audio_save_status"] = audio["save_status"] if audio else None
    # Share every run's evidence/execution state, not raw diagnostics or config.
    attempt["assessments"] = [
        dict(run)
        for run in db.execute(
            """SELECT id,input_kind,execution_status,evidence_status,provider,model_version,
                      reference_hash,error_code,started_at,finished_at,created_at
               FROM assessment_runs WHERE attempt_id=? AND session_id=?
               ORDER BY created_at,rowid""",
            (value["id"], value["session_id"]),
        )
    ]
    return attempt


def _turn(db, turn_id):
    saved = turn_row(db, turn_id)
    turn = _fields(
        saved,
        (
            "id",
            "ordinal",
            "question_en",
            "confirmed_answer_en",
            "submitted_via",
            "source_exercise_id",
            "source_playback_id",
            "source_recording_id",
            "source_transcription_id",
            "committed_at",
            "provenance_status",
        ),
    )
    turn["exercises"] = []
    # Legacy missing/ambiguous sources stay unknown. Do not guess from matching text.
    if saved["confirmed_answer_en"] is not None and saved["source_exercise_id"]:
        reference = db.execute(
            """SELECT id,mode,reference_text,reference_hash,reference_origin,created_at
               FROM exercises WHERE id=? AND turn_id=? AND session_id=?""",
            (saved["source_exercise_id"], turn_id, saved["session_id"]),
        ).fetchone()
        if reference:
            exercise = dict(reference)
            exercise["attempts"] = [
                _attempt(db, value)
                for value in db.execute(
                    """SELECT id,session_id,audio_id,input_kind,audio_meta_json,
                              capture_requested_json,created_at
                       FROM attempts WHERE exercise_id=? AND session_id=?
                       ORDER BY created_at,rowid""",
                    (reference["id"], saved["session_id"]),
                )
            ]
            turn["exercises"].append(exercise)
    turn["has_recording"] = bool(
        db.execute(
            "SELECT 1 FROM free_speech_recordings WHERE turn_id=? LIMIT 1", (turn_id,)
        ).fetchone()
    ) or any(attempt["audio_id"] for ex in turn["exercises"] for attempt in ex["attempts"])
    turn["transcript"] = (
        saved["confirmed_answer_en"] if saved["submitted_via"] == "free_speech_transcript" else None
    )
    return turn


def _sources(db, session):
    # Manifests are not yet pinned to a conversation (#5/RV03). A hash match is
    # required for inclusion; a newer, different candidate must not replace it.
    saved = db.execute(
        """SELECT id,schema_version,pack_hash,adopted_json,created_at
           FROM session_pack_manifests WHERE session_id=? AND pack_hash=?
           ORDER BY created_at DESC,rowid DESC LIMIT 1""",
        (session["id"], session["pack_hash"]),
    ).fetchone()
    if saved is None:
        return None, []
    manifest = _fields(saved, ("id", "schema_version", "pack_hash", "created_at"))
    manifest["pack"] = session["pack_snapshot"]
    manifest["adopted"] = []
    documents = {}
    for adopted in json.loads(saved["adopted_json"]):
        document = db.execute(
            """SELECT id,source_type,original_url,source_hash,content_hash,
                      extraction_status,extractor,extractor_version,provenance_role,
                      created_at,completed_at
               FROM session_documents WHERE id=? AND session_id=?
                 AND extraction_status='succeeded'""",
            (adopted["document_id"], session["id"]),
        ).fetchone()
        if document is None:
            continue
        selected = set(adopted["segment_ids"])
        segments = []
        for record in db.execute(
            """SELECT id,ordinal,text,text_hash,location_json FROM document_segments
               WHERE document_id=? ORDER BY ordinal""",
            (document["id"],),
        ):
            if record["id"] not in selected:
                continue
            segment = _fields(record, ("id", "ordinal", "text", "text_hash"))
            segment["location"] = _fields(
                json.loads(record["location_json"] or "{}"), ("page", "section", "part")
            )
            segments.append(segment)
        manifest["adopted"].append(
            {"document_id": document["id"], "segment_ids": [s["id"] for s in segments]}
        )
        if document["id"] not in documents:
            info = dict(document)
            info["original_url"] = _source_url(info["original_url"])
            info["segments"] = []
            documents[info["id"]] = info
        known = {s["id"] for s in documents[document["id"]]["segments"]}
        documents[document["id"]]["segments"].extend(s for s in segments if s["id"] not in known)
    return manifest, list(documents.values())


def export_shared_session(database, session_id):
    with database.read_snapshot() as db:
        saved = row(db, "sessions", session_id)
        session = _fields(saved, ("id", "created_at", "scenario", "status", "pack_hash"))
        session["pack_snapshot"] = json.loads(saved["pack_snapshot_json"])
        session["settings"] = _fields(
            json.loads(saved["settings_json"]),
            ("language_level", "technical_depth", "strictness"),
        )
        session["turns"] = [
            _turn(db, record["id"])
            for record in db.execute(
                "SELECT id FROM turns WHERE session_id=? ORDER BY ordinal", (session_id,)
            )
        ]
        session["turns_total"] = len(session["turns"])
        session["pack_manifest"], session["documents"] = _sources(db, session)
    return {"schema_version": "2.0", "mode": "share", "exported_at": now(), "session": session}
