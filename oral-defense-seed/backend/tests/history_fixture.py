"""Synthetic history boundaries shared by API and browser tests."""

from backend.app.db import digest, encode, now, uid
from backend.tests.test_drill import wav_bytes


def append_turns(database, sid, count=51):
    first = None
    with database.connect() as db:
        start = db.execute(
            "SELECT COALESCE(MAX(ordinal),0) FROM turns WHERE session_id=?", (sid,)
        ).fetchone()[0]
        for ordinal in range(start + 1, start + count + 1):
            tid, eid = uid(), uid()
            db.execute(
                "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,follow_up_count) VALUES (?,?,?,?,?,0)",
                (tid, sid, ordinal, f"History question {ordinal}?", "Synthetic fixture"),
            )
            text = f"History answer {ordinal}."
            db.execute(
                "INSERT INTO exercises (id,session_id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES (?,?,?,'listen_repeat',?,?,'manual',?)",
                (eid, sid, tid, text, digest(text), now()),
            )
            db.execute(
                "INSERT INTO turn_submissions (turn_id,session_id,answer_text,submitted_via,source_exercise_id,provenance_status) VALUES (?,?,?,'confirmed_reference',?,'exact')",
                (tid, sid, text, eid),
            )
            if first is None:
                first = {"turn_id": tid, "exercise_id": eid}
    return first


def seed_history(database, sid):
    first = append_turns(database, sid)
    tid, eid = first["turn_id"], first["exercise_id"]
    audio_id, attempt_id = uid(), uid()
    (database.audio_dir / f"{audio_id}.wav").write_bytes(wav_bytes())
    with database.connect() as db:
        db.execute(
            "INSERT INTO audio_files (id,session_id,filename,media_type,save_status) VALUES (?,?,?,'audio/wav','ready')",
            (audio_id, sid, f"{audio_id}.wav"),
        )
        for index in range(23):
            db.execute(
                "INSERT INTO attempts (id,session_id,exercise_id,audio_id,audio_meta_json,created_at,input_kind) VALUES (?,?,?,?,'{}',?,'isolated_repeat')",
                (
                    attempt_id if index == 0 else uid(),
                    sid,
                    eid,
                    audio_id if index == 0 else None,
                    now(),
                ),
            )
        for index in range(23):
            db.execute(
                "INSERT INTO assessment_runs (id,attempt_id,session_id,input_kind,execution_status,evidence_status,provider,reference_hash,result_json,created_at) VALUES (?,?,?,'isolated_repeat','succeeded','unavailable','unavailable',?,?,?)",
                (
                    uid(),
                    attempt_id,
                    sid,
                    digest("History answer 1."),
                    encode(
                        {
                            "status": "unavailable",
                            "reason_codes": [f"fixture_{index}"],
                            "limitations": ["Synthetic history fixture"],
                            "phones": [],
                        }
                    ),
                    now(),
                ),
            )
        for index in range(22):
            text = f"Alternate reference {index}."
            db.execute(
                "INSERT INTO exercises (id,session_id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES (?,?,?,'listen_repeat',?,?,'manual',?)",
                (uid(), sid, tid, text, digest(text), now()),
            )
        for index in range(60):
            db.execute(
                "INSERT INTO conversation_playbacks (id,session_id,turn_id,reference_id,revision,stage,reference_hash,tts_settings_json,status) VALUES (?,?,?,?,1,'model_playback',?,'{}','cancelled')",
                (uid(), sid, tid, eid, digest("History answer 1.")),
            )
            db.execute(
                "INSERT INTO requests (request_id,session_id,action,payload_hash,payload_json,status,created_at) VALUES (?,?,'fixture','hash','{}','done',?)",
                (uid(), sid, now()),
            )
    return {**first, "audio_id": audio_id, "attempt_id": attempt_id}
