import ast
import hashlib
import inspect
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

log = logging.getLogger("drill.db")


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


class MigrationError(RuntimeError):
    """Raised when the database schema cannot be brought to the code's version."""


# The initial-commit schema is the baseline migration. Every statement uses
# IF NOT EXISTS so a database created by the pre-migration app is recognized
# as already being at this version instead of being reinitialized.
INITIAL_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, pack_snapshot_json TEXT NOT NULL,
 pack_hash TEXT NOT NULL, research_brief TEXT NOT NULL, scenario TEXT NOT NULL,
 settings_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active')""",
    """CREATE TABLE IF NOT EXISTS turns (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, question_en TEXT NOT NULL, basis_note TEXT NOT NULL,
 confirmed_answer_en TEXT, submitted_via TEXT, follow_up_count INTEGER NOT NULL,
 unable_to_answer INTEGER NOT NULL DEFAULT 0, UNIQUE(session_id, ordinal))""",
    """CREATE TABLE IF NOT EXISTS coach_messages (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE, level TEXT NOT NULL,
 user_note TEXT NOT NULL, draft TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS exercises (
 id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
 mode TEXT NOT NULL, reference_text TEXT NOT NULL, reference_hash TEXT NOT NULL,
 reference_origin TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS audio_files (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 filename TEXT NOT NULL, media_type TEXT NOT NULL, cache_key TEXT, UNIQUE(session_id, cache_key))""",
    """CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, exercise_id TEXT NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
 audio_id TEXT REFERENCES audio_files(id), dictation_text TEXT, audio_meta_json TEXT NOT NULL,
 result_json TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS assistance (
 id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
 exercise_id TEXT REFERENCES exercises(id) ON DELETE CASCADE, kind TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS conversations (
 id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
 mode TEXT NOT NULL DEFAULT 'shadowing', status TEXT NOT NULL DEFAULT 'paused',
 stage TEXT NOT NULL DEFAULT 'question_generation', revision INTEGER NOT NULL DEFAULT 0,
 turn_id TEXT REFERENCES turns(id) ON DELETE CASCADE,
 reference_id TEXT REFERENCES exercises(id) ON DELETE SET NULL,
 audio_id TEXT REFERENCES audio_files(id) ON DELETE SET NULL,
 playback_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS conversation_playbacks (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
 reference_id TEXT REFERENCES exercises(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, stage TEXT NOT NULL, reference_hash TEXT NOT NULL,
 audio_id TEXT REFERENCES audio_files(id) ON DELETE SET NULL,
 tts_settings_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'playing')""",
    """CREATE TABLE IF NOT EXISTS requests (
 request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 action TEXT NOT NULL, payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 status TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL)""",
)

MIGRATIONS_TABLE = """CREATE TABLE IF NOT EXISTS schema_migrations (
 version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
 applied_at TEXT NOT NULL)"""


def _backfill_session_settings(db, context):
    """Move the startup settings JSON backfill into a versioned migration."""
    defaults = context.get("speech_defaults") or {
        "question": "configured",
        "coach_answer": "configured",
        "exercise": "configured",
    }
    for old in db.execute("SELECT id,settings_json FROM sessions").fetchall():
        saved = json.loads(old["settings_json"])
        changed = False
        if "text_model" not in saved:
            previous = saved.get("providers_at_start", {}).get("text", {})
            provider = previous.get("provider", "mock")
            model = previous.get("model") or "demo"
            saved["text_model"] = {
                "id": f"{provider}/{model}",
                "provider": provider,
                "model": model,
                "protocol": "mock" if provider == "mock" else "chat_completions",
                "endpoint": previous.get("endpoint") or "",
                "mock": provider == "mock",
            }
            changed = True
        if "role_models" not in saved:
            saved["role_models"] = {
                role: saved["text_model"]
                for role in (
                    "examiner",
                    "meaning",
                    "hint",
                    "outline",
                    "revision",
                    "full_answer",
                )
            }
            changed = True
        if "speech_models" not in saved:
            saved["speech_models"] = dict(defaults)
            changed = True
        if changed:
            db.execute("UPDATE sessions SET settings_json=? WHERE id=?", (encode(saved), old["id"]))


# Migration 3 rebuilds the ownership graph. Composite foreign keys pin every
# child row to the session it belongs to; nullable references use ON DELETE
# CASCADE because SQLite cannot SET NULL both columns of a composite key.
_V3_TABLES = (
    """CREATE TABLE audio_files_v3 (
 id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 filename TEXT NOT NULL, media_type TEXT NOT NULL, cache_key TEXT,
 UNIQUE(session_id, cache_key), UNIQUE(id, session_id))""",
    """CREATE TABLE turns_v3 (
 id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
 question_en TEXT NOT NULL, basis_note TEXT NOT NULL, confirmed_answer_en TEXT,
 submitted_via TEXT CHECK(submitted_via IS NULL OR submitted_via IN
  ('shadowing_playback','confirmed_reference','free_speech_transcript')),
 follow_up_count INTEGER NOT NULL CHECK(follow_up_count >= 0),
 unable_to_answer INTEGER NOT NULL DEFAULT 0 CHECK(unable_to_answer IN (0,1)),
 UNIQUE(session_id, ordinal), UNIQUE(id, session_id))""",
    """CREATE TABLE exercises_v3 (
 id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('read_aloud','listen_repeat','dictation')),
 reference_text TEXT NOT NULL, reference_hash TEXT NOT NULL,
 reference_origin TEXT NOT NULL CHECK(reference_origin IN ('coach','manual','examiner')),
 created_at TEXT NOT NULL,
 UNIQUE(id, session_id),
 FOREIGN KEY(turn_id, session_id) REFERENCES turns(id, session_id) ON DELETE CASCADE)""",
    """CREATE TABLE coach_messages_v3 (
 id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
 level TEXT NOT NULL CHECK(level IN ('meaning','hint','outline','revision','full_answer')),
 user_note TEXT NOT NULL, draft TEXT NOT NULL,
 response_json TEXT NOT NULL CHECK(json_valid(response_json)), created_at TEXT NOT NULL,
 FOREIGN KEY(turn_id, session_id) REFERENCES turns(id, session_id) ON DELETE CASCADE)""",
    """CREATE TABLE assistance_v3 (
 id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL, turn_id TEXT NOT NULL, exercise_id TEXT,
 kind TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(turn_id, session_id) REFERENCES turns(id, session_id) ON DELETE CASCADE,
 FOREIGN KEY(exercise_id, session_id) REFERENCES exercises(id, session_id) ON DELETE CASCADE)""",
    """CREATE TABLE attempts_v3 (
 id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL, exercise_id TEXT NOT NULL, audio_id TEXT,
 dictation_text TEXT, audio_meta_json TEXT NOT NULL CHECK(json_valid(audio_meta_json)),
 result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
 created_at TEXT NOT NULL,
 FOREIGN KEY(exercise_id, session_id) REFERENCES exercises(id, session_id) ON DELETE CASCADE,
 FOREIGN KEY(audio_id, session_id) REFERENCES audio_files(id, session_id) ON DELETE CASCADE)""",
    """CREATE TABLE conversation_playbacks_v3 (
 id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL, turn_id TEXT NOT NULL, reference_id TEXT,
 revision INTEGER NOT NULL CHECK(revision >= 0),
 stage TEXT NOT NULL CHECK(stage IN ('question_playback','model_playback')),
 reference_hash TEXT NOT NULL, audio_id TEXT,
 tts_settings_json TEXT NOT NULL CHECK(json_valid(tts_settings_json)),
 status TEXT NOT NULL DEFAULT 'playing' CHECK(status IN ('playing','completed','cancelled')),
 CHECK((stage = 'question_playback' AND reference_id IS NULL)
    OR (stage = 'model_playback' AND reference_id IS NOT NULL)),
 UNIQUE(id, session_id),
 FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
 FOREIGN KEY(turn_id, session_id) REFERENCES turns(id, session_id) ON DELETE CASCADE,
 FOREIGN KEY(reference_id, session_id) REFERENCES exercises(id, session_id) ON DELETE CASCADE,
 FOREIGN KEY(audio_id, session_id) REFERENCES audio_files(id, session_id) ON DELETE CASCADE)""",
    """CREATE TABLE conversations_v3 (
 id TEXT NOT NULL PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
 mode TEXT NOT NULL DEFAULT 'shadowing' CHECK(mode IN ('shadowing','free_speech')),
 status TEXT NOT NULL DEFAULT 'paused' CHECK(status IN ('paused','running','ended')),
 stage TEXT NOT NULL DEFAULT 'question_generation' CHECK(stage IN
  ('question_generation','question_playback','coach_generation','model_playback')),
 revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
 turn_id TEXT, reference_id TEXT, audio_id TEXT, playback_id TEXT,
 FOREIGN KEY(turn_id, id) REFERENCES turns(id, session_id) ON DELETE CASCADE,
 FOREIGN KEY(reference_id, id) REFERENCES exercises(id, session_id) ON DELETE CASCADE,
 FOREIGN KEY(audio_id, id) REFERENCES audio_files(id, session_id) ON DELETE CASCADE,
 FOREIGN KEY(playback_id, id) REFERENCES conversation_playbacks(id, session_id)
  ON DELETE CASCADE)""",
)

_V3_COPY = (
    """INSERT INTO audio_files_v3 (id,session_id,filename,media_type,cache_key)
 SELECT id,session_id,filename,media_type,cache_key FROM audio_files""",
    """INSERT INTO turns_v3 (id,session_id,ordinal,question_en,basis_note,confirmed_answer_en,submitted_via,follow_up_count,unable_to_answer)
 SELECT id,session_id,ordinal,question_en,basis_note,confirmed_answer_en,submitted_via,follow_up_count,unable_to_answer FROM turns""",
    """INSERT INTO exercises_v3 (id,session_id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at)
 SELECT e.id,t.session_id,e.turn_id,e.mode,e.reference_text,e.reference_hash,e.reference_origin,e.created_at
 FROM exercises e JOIN turns t ON t.id=e.turn_id""",
    """INSERT INTO coach_messages_v3 (id,session_id,turn_id,level,user_note,draft,response_json,created_at)
 SELECT id,session_id,turn_id,level,user_note,draft,response_json,created_at FROM coach_messages""",
    """INSERT INTO assistance_v3 (id,session_id,turn_id,exercise_id,kind,created_at)
 SELECT a.id,t.session_id,a.turn_id,a.exercise_id,a.kind,a.created_at
 FROM assistance a JOIN turns t ON t.id=a.turn_id""",
    """INSERT INTO attempts_v3 (id,session_id,exercise_id,audio_id,dictation_text,audio_meta_json,result_json,created_at)
 SELECT a.id,t.session_id,a.exercise_id,a.audio_id,a.dictation_text,a.audio_meta_json,a.result_json,a.created_at
 FROM attempts a JOIN exercises e ON e.id=a.exercise_id JOIN turns t ON t.id=e.turn_id""",
    """INSERT INTO conversation_playbacks_v3 (id,session_id,turn_id,reference_id,revision,stage,reference_hash,audio_id,tts_settings_json,status)
 SELECT id,session_id,turn_id,reference_id,revision,stage,reference_hash,audio_id,tts_settings_json,status FROM conversation_playbacks""",
    """INSERT INTO conversations_v3 (id,mode,status,stage,revision,turn_id,reference_id,audio_id,playback_id)
 SELECT id,mode,status,stage,revision,turn_id,reference_id,audio_id,playback_id FROM conversations""",
)

_V3_REBUILT = (
    "audio_files",
    "turns",
    "exercises",
    "coach_messages",
    "assistance",
    "attempts",
    "conversation_playbacks",
    "conversations",
)

_V3_AFTER = (
    "CREATE INDEX exercises_turn_session_idx ON exercises(turn_id, session_id)",
    "CREATE INDEX coach_messages_turn_session_idx ON coach_messages(turn_id, session_id)",
    "CREATE INDEX attempts_exercise_session_idx ON attempts(exercise_id, session_id)",
    "CREATE INDEX attempts_audio_session_idx ON attempts(audio_id, session_id)",
    "CREATE INDEX assistance_turn_session_idx ON assistance(turn_id, session_id)",
    "CREATE INDEX assistance_exercise_session_idx ON assistance(exercise_id, session_id)",
    "CREATE INDEX conversation_playbacks_session_idx ON conversation_playbacks(session_id)",
    "CREATE UNIQUE INDEX conversation_playbacks_one_playing ON conversation_playbacks(session_id) WHERE status='playing'",
    """CREATE TRIGGER exercises_reference_immutable
 BEFORE UPDATE OF reference_text, reference_hash, turn_id, session_id ON exercises
 BEGIN SELECT RAISE(ABORT, 'exercise reference is immutable'); END""",
    """CREATE TRIGGER conversations_owner_insert BEFORE INSERT ON conversations
 WHEN (NEW.turn_id IS NOT NULL AND NOT EXISTS
   (SELECT 1 FROM turns WHERE id=NEW.turn_id AND session_id=NEW.id))
  OR (NEW.reference_id IS NOT NULL AND NOT EXISTS
   (SELECT 1 FROM exercises WHERE id=NEW.reference_id AND session_id=NEW.id))
  OR (NEW.audio_id IS NOT NULL AND NOT EXISTS
   (SELECT 1 FROM audio_files WHERE id=NEW.audio_id AND session_id=NEW.id))
  OR (NEW.playback_id IS NOT NULL AND NOT EXISTS
   (SELECT 1 FROM conversation_playbacks WHERE id=NEW.playback_id AND session_id=NEW.id))
 BEGIN SELECT RAISE(ABORT, 'conversation reference ownership mismatch'); END""",
    """CREATE TRIGGER conversations_owner_update
 BEFORE UPDATE OF turn_id, reference_id, audio_id, playback_id ON conversations
 WHEN (NEW.turn_id IS NOT NULL AND NOT EXISTS
   (SELECT 1 FROM turns WHERE id=NEW.turn_id AND session_id=NEW.id))
  OR (NEW.reference_id IS NOT NULL AND NOT EXISTS
   (SELECT 1 FROM exercises WHERE id=NEW.reference_id AND session_id=NEW.id))
  OR (NEW.audio_id IS NOT NULL AND NOT EXISTS
   (SELECT 1 FROM audio_files WHERE id=NEW.audio_id AND session_id=NEW.id))
  OR (NEW.playback_id IS NOT NULL AND NOT EXISTS
   (SELECT 1 FROM conversation_playbacks WHERE id=NEW.playback_id AND session_id=NEW.id))
 BEGIN SELECT RAISE(ABORT, 'conversation reference ownership mismatch'); END""",
)


def _composite_ownership(db, context):
    """Rebuild persisted tables so session ownership is a database guarantee."""
    # A crash can leave more than one 'playing' row, which the partial unique
    # index would reject. Same cancellation the restart recovery would perform.
    db.execute("UPDATE conversation_playbacks SET status='cancelled' WHERE status='playing'")
    for statement in _V3_TABLES:
        db.execute(statement)
    for statement in _V3_COPY:
        db.execute(statement)
    for table in _V3_REBUILT:
        db.execute(f"DROP TABLE {table}")
    for table in _V3_REBUILT:
        db.execute(f"ALTER TABLE {table}_v3 RENAME TO {table}")
    for statement in _V3_AFTER:
        db.execute(statement)


_TURN_SUBMISSIONS_DDL = (
    """CREATE TABLE IF NOT EXISTS turn_submissions (
 turn_id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL,
 answer_text TEXT NOT NULL,
 submitted_via TEXT NOT NULL CHECK(submitted_via IN
  ('shadowing_playback','confirmed_reference','free_speech_transcript','legacy_unknown')),
 source_exercise_id TEXT,
 source_playback_id TEXT,
 committed_at TEXT,
 provenance_status TEXT NOT NULL CHECK(provenance_status IN
  ('exact','legacy_missing','legacy_ambiguous')),
 FOREIGN KEY(turn_id, session_id) REFERENCES turns(id, session_id) ON DELETE CASCADE,
 FOREIGN KEY(source_exercise_id, session_id) REFERENCES exercises(id, session_id) ON DELETE CASCADE,
 FOREIGN KEY(source_playback_id, session_id) REFERENCES conversation_playbacks(id, session_id) ON DELETE CASCADE)""",
    "CREATE INDEX IF NOT EXISTS turn_submissions_session_idx ON turn_submissions(session_id)",
    """CREATE TRIGGER IF NOT EXISTS turn_submissions_immutable
 BEFORE UPDATE OF turn_id, session_id, answer_text, submitted_via, source_exercise_id,
  source_playback_id, committed_at, provenance_status ON turn_submissions
 BEGIN SELECT RAISE(ABORT, 'turn submission is immutable'); END""",
)

_SUBMISSION_VIA = {
    "shadowing_playback",
    "confirmed_reference",
    "free_speech_transcript",
}


def _backfill_turn_submissions(db, context):
    """Move confirmed turns to the submission register without guessing sources."""
    for turn in db.execute(
        "SELECT id,session_id,confirmed_answer_en,submitted_via FROM turns WHERE confirmed_answer_en IS NOT NULL"
    ):
        answer = turn["confirmed_answer_en"]
        via = turn["submitted_via"]
        if via not in _SUBMISSION_VIA:
            via = "legacy_unknown"
        exercise_id, playback_id, provenance = None, None, "legacy_missing"
        playbacks = db.execute(
            """SELECT p.id AS playback_id, e.id AS exercise_id
               FROM conversation_playbacks p
               JOIN exercises e ON e.turn_id=p.turn_id AND e.id=p.reference_id
               WHERE p.turn_id=? AND p.status='completed' AND p.stage='model_playback'
                 AND e.reference_text=? AND e.session_id=?""",
            (turn["id"], answer, turn["session_id"]),
        ).fetchall()
        if len(playbacks) == 1:
            exercise_id = playbacks[0]["exercise_id"]
            playback_id = playbacks[0]["playback_id"]
            provenance = "exact"
        elif len(playbacks) > 1:
            provenance = "legacy_ambiguous"
        else:
            exercises = db.execute(
                "SELECT id FROM exercises WHERE turn_id=? AND reference_text=?",
                (turn["id"], answer),
            ).fetchall()
            if len(exercises) == 1:
                exercise_id = exercises[0]["id"]
                provenance = "exact"
            elif len(exercises) > 1:
                provenance = "legacy_ambiguous"
        db.execute(
            """INSERT INTO turn_submissions (turn_id,session_id,answer_text,submitted_via,
               source_exercise_id,source_playback_id,committed_at,provenance_status)
               VALUES (?,?,?,?,?,?,NULL,?)""",
            (turn["id"], turn["session_id"], answer, via, exercise_id, playback_id, provenance),
        )


_RECORDING_DDL = (
    "ALTER TABLE audio_files ADD COLUMN kind TEXT NOT NULL DEFAULT 'legacy_unknown'",
    "ALTER TABLE audio_files ADD COLUMN storage_key TEXT",
    "ALTER TABLE audio_files ADD COLUMN content_hash TEXT",
    "ALTER TABLE audio_files ADD COLUMN media_json TEXT",
    "ALTER TABLE audio_files ADD COLUMN save_status TEXT NOT NULL DEFAULT 'ready'",
    "ALTER TABLE attempts ADD COLUMN client_attempt_key TEXT",
    "ALTER TABLE attempts ADD COLUMN input_kind TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE attempts ADD COLUMN capture_requested_json TEXT",
    "ALTER TABLE attempts ADD COLUMN capture_actual_json TEXT",
    "ALTER TABLE attempts ADD COLUMN file_status TEXT NOT NULL DEFAULT 'ready'",
    """CREATE UNIQUE INDEX IF NOT EXISTS attempts_client_key
 ON attempts(session_id, client_attempt_key) WHERE client_attempt_key IS NOT NULL""",
    "CREATE INDEX IF NOT EXISTS attempts_file_status_idx ON attempts(file_status)",
)


def _backfill_recording_lifecycle(db, context):
    # Only classify with positive evidence; unknown legacy audio stays unknown.
    db.execute("UPDATE audio_files SET storage_key=filename WHERE storage_key IS NULL")
    db.execute(
        "UPDATE audio_files SET kind='tts' WHERE kind='legacy_unknown' AND cache_key IS NOT NULL"
    )
    db.execute(
        """UPDATE audio_files SET kind='recording' WHERE kind='legacy_unknown' AND id IN
           (SELECT audio_id FROM attempts WHERE audio_id IS NOT NULL)"""
    )


_DELETION_DDL = (
    "ALTER TABLE sessions ADD COLUMN deletion_status TEXT NOT NULL DEFAULT 'active'",
    "ALTER TABLE sessions ADD COLUMN deletion_error TEXT",
    "ALTER TABLE sessions ADD COLUMN deletion_started_at TEXT",
    "CREATE INDEX IF NOT EXISTS sessions_deletion_status_idx ON sessions(deletion_status)",
)


def _remove_file(path):
    path.unlink(missing_ok=True)


def purge_session(database, session_id):
    """Mark, remove files outside the transaction, then finally delete rows.

    Resumable: a crash between phases leaves a ``deleting`` session whose files
    can be recomputed from their surviving metadata.
    """
    with database.connect() as db:
        session = db.execute(
            "SELECT deletion_status FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if session is None:
            return False
        if session["deletion_status"] != "deleting":
            db.execute(
                "UPDATE sessions SET deletion_status='deleting',deletion_started_at=?,deletion_error=NULL WHERE id=?",
                (now(), session_id),
            )
            db.execute(
                "UPDATE conversations SET status='paused',revision=revision+1,playback_id=NULL WHERE id=?",
                (session_id,),
            )
            db.execute(
                "UPDATE conversation_playbacks SET status='cancelled' WHERE session_id=? AND status='playing'",
                (session_id,),
            )
    with database.connect() as db:
        files = [
            dict(record)
            for record in db.execute(
                "SELECT storage_key,filename FROM audio_files WHERE session_id=?", (session_id,)
            )
        ]
    try:
        for record in files:
            key = record["storage_key"] or record["filename"]
            path = (database.audio_dir / key).resolve()
            if path.parent != database.audio_dir:
                continue
            _remove_file(path)
    except OSError as exc:
        with database.connect() as db:
            db.execute(
                "UPDATE sessions SET deletion_error=? WHERE id=?", (type(exc).__name__, session_id)
            )
        raise
    with database.connect() as db:
        db.execute("DELETE FROM attempts WHERE session_id=?", (session_id,))
        db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    return True


def reconcile_audio_files(database, dry_run=True, grace_seconds=900):
    """Report missing/orphan/temp audio; remove stray files only when not dry-run."""
    report = {"missing": [], "orphan": [], "temp": [], "removed": []}
    current = time.time()
    with database.connect() as db:
        records = [
            dict(record)
            for record in db.execute("SELECT id,storage_key,filename,save_status FROM audio_files")
        ]
    referenced = set()
    for record in records:
        key = record["storage_key"] or record["filename"]
        referenced.add(key)
        if record["save_status"] != "ready":
            continue
        path = (database.audio_dir / key).resolve()
        if path.parent != database.audio_dir or not path.is_file():
            report["missing"].append({"audio_id": record["id"], "storage_key": key})
    for path in sorted(database.audio_dir.iterdir()):
        if not path.is_file() or path.name in referenced:
            continue
        if current - path.stat().st_mtime < grace_seconds:
            continue
        bucket = "temp" if ".part" in path.name else "orphan"
        report[bucket].append(path.name)
        if not dry_run:
            _remove_file(path)
            report["removed"].append(path.name)
    if not dry_run:
        with database.connect() as db:
            for item in report["missing"]:
                db.execute(
                    "UPDATE audio_files SET save_status='missing' WHERE id=?", (item["audio_id"],)
                )
    return report


_ASSESSMENT_DDL = (
    """CREATE TABLE IF NOT EXISTS assessment_runs (
 id TEXT NOT NULL PRIMARY KEY,
 attempt_id TEXT NOT NULL,
 session_id TEXT NOT NULL,
 input_kind TEXT NOT NULL DEFAULT 'unknown',
 execution_status TEXT NOT NULL CHECK(execution_status IN
  ('queued','running','succeeded','failed','interrupted')),
 evidence_status TEXT CHECK(evidence_status IS NULL OR evidence_status IN
  ('pending','unavailable','insufficient_evidence','uncalibrated','calibrated')),
 provider TEXT NOT NULL,
 model_version TEXT,
 config_json TEXT CHECK(config_json IS NULL OR json_valid(config_json)),
 reference_hash TEXT NOT NULL,
 result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
 error_code TEXT,
 error_message TEXT,
 request_id TEXT,
 started_at TEXT,
 finished_at TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(attempt_id, request_id),
 FOREIGN KEY(attempt_id, session_id) REFERENCES attempts(id, session_id) ON DELETE CASCADE)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS attempts_id_session ON attempts(id, session_id)",
    "CREATE INDEX IF NOT EXISTS assessment_runs_attempt_idx ON assessment_runs(attempt_id, created_at)",
    "CREATE INDEX IF NOT EXISTS assessment_runs_session_idx ON assessment_runs(session_id)",
    """CREATE TRIGGER IF NOT EXISTS assessment_runs_result_immutable
 BEFORE UPDATE OF result_json, provider, model_version, config_json, reference_hash,
  attempt_id, session_id, started_at ON assessment_runs
 WHEN OLD.result_json IS NOT NULL
 BEGIN SELECT RAISE(ABORT, 'assessment run result is immutable'); END""",
)


def _backfill_assessment_runs(db, context):
    # Only audio attempts become runs; dictation stays a text comparison result.
    for record in db.execute(
        """SELECT a.id,a.session_id,a.result_json,a.input_kind,e.reference_hash
           FROM attempts a JOIN exercises e ON e.id=a.exercise_id
           WHERE a.audio_id IS NOT NULL AND a.result_json IS NOT NULL"""
    ):
        result = json.loads(record["result_json"])
        evidence = "unavailable" if result.get("status") == "unavailable" else "uncalibrated"
        db.execute(
            """INSERT INTO assessment_runs (id,attempt_id,session_id,input_kind,execution_status,
               evidence_status,provider,model_version,config_json,reference_hash,result_json,
               error_code,error_message,request_id,started_at,finished_at,created_at)
               VALUES (?,?,?,?,'succeeded',?,'legacy_unknown',NULL,NULL,?,?,NULL,NULL,NULL,NULL,NULL,?)""",
            (
                uid(),
                record["id"],
                record["session_id"],
                record["input_kind"],
                evidence,
                record["reference_hash"],
                record["result_json"],
                now(),
            ),
        )


_DOCUMENTS_DDL = (
    """CREATE TABLE IF NOT EXISTS session_documents (
 id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL,
 source_type TEXT NOT NULL CHECK(source_type IN ('brief','pdf','url')),
 original_name TEXT,
 original_url TEXT,
 source_hash TEXT,
 content_hash TEXT,
 extraction_status TEXT NOT NULL CHECK(extraction_status IN
  ('pending','succeeded','failed','unsupported')),
 error_code TEXT,
 extractor TEXT,
 extractor_version TEXT,
 extractor_config_json TEXT CHECK(extractor_config_json IS NULL OR json_valid(extractor_config_json)),
 provenance_role TEXT NOT NULL CHECK(provenance_role IN ('learner_work','reference')),
 created_at TEXT NOT NULL,
 completed_at TEXT,
 FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS document_segments (
 id TEXT NOT NULL PRIMARY KEY,
 document_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
 text TEXT NOT NULL,
 text_hash TEXT NOT NULL,
 location_json TEXT CHECK(location_json IS NULL OR json_valid(location_json)),
 UNIQUE(document_id, ordinal),
 FOREIGN KEY(document_id) REFERENCES session_documents(id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS session_pack_manifests (
 id TEXT NOT NULL PRIMARY KEY,
 session_id TEXT NOT NULL,
 schema_version TEXT NOT NULL,
 pack_hash TEXT NOT NULL,
 pack_json TEXT NOT NULL CHECK(json_valid(pack_json)),
 adopted_json TEXT NOT NULL CHECK(json_valid(adopted_json)),
 created_at TEXT NOT NULL,
 FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE)""",
    "CREATE INDEX IF NOT EXISTS session_documents_session_idx ON session_documents(session_id)",
    "CREATE INDEX IF NOT EXISTS document_segments_document_idx ON document_segments(document_id)",
    "CREATE INDEX IF NOT EXISTS session_pack_manifests_session_idx ON session_pack_manifests(session_id)",
    """CREATE TRIGGER IF NOT EXISTS session_pack_manifests_immutable
 BEFORE UPDATE ON session_pack_manifests
 BEGIN SELECT RAISE(ABORT, 'pack manifest is immutable'); END""",
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple = ()
    func: object = None
    disable_foreign_keys: bool = False

    def checksum(self):
        # ast.dump normalizes formatting so a reformat of the module does not
        # look like a changed migration; logical edits still change the digest.
        parts = []
        if self.statements:
            parts.append("\n".join(self.statements))
        if self.func is not None:
            parts.append(ast.dump(ast.parse(inspect.getsource(self.func))))
        source = "\n".join(parts)
        return hashlib.sha256(f"{self.version}\x00{self.name}\x00{source}".encode()).hexdigest()


MIGRATIONS = (
    Migration(1, "initial_schema", statements=INITIAL_SCHEMA),
    Migration(2, "session_settings_backfill", func=_backfill_session_settings),
    Migration(
        3,
        "composite_ownership_and_immutability",
        func=_composite_ownership,
        disable_foreign_keys=True,
    ),
    Migration(
        4, "turn_submissions", statements=_TURN_SUBMISSIONS_DDL, func=_backfill_turn_submissions
    ),
    Migration(
        5,
        "recording_lifecycle",
        statements=_RECORDING_DDL,
        func=_backfill_recording_lifecycle,
    ),
    Migration(6, "resumable_deletion", statements=_DELETION_DDL),
    Migration(7, "assessment_runs", statements=_ASSESSMENT_DDL, func=_backfill_assessment_runs),
    Migration(8, "session_documents", statements=_DOCUMENTS_DDL),
)

BASELINE_VERSION = 1

_ALLOWED_VALUES = {
    ("sessions", "status"): {"active", "completed"},
    ("sessions", "deletion_status"): {"active", "deleting"},
    ("turns", "submitted_via"): {
        None,
        "shadowing_playback",
        "confirmed_reference",
        "free_speech_transcript",
    },
    ("conversations", "mode"): {"shadowing", "free_speech"},
    ("conversations", "status"): {"paused", "running", "ended"},
    (
        "conversations",
        "stage",
    ): {"question_generation", "question_playback", "coach_generation", "model_playback"},
    ("conversation_playbacks", "status"): {"playing", "completed", "cancelled"},
    ("requests", "status"): {"processing", "done", "failed", "interrupted"},
}

_JSON_COLUMNS = (
    ("sessions", "settings_json"),
    ("sessions", "pack_snapshot_json"),
    ("coach_messages", "response_json"),
    ("attempts", "audio_meta_json"),
    ("attempts", "result_json"),
    ("conversation_playbacks", "tts_settings_json"),
    ("requests", "payload_json"),
    ("requests", "result_json"),
)


def inspect_database(db, audio_dir):
    """Report ownership, uniqueness, JSON/enum, hash, and file problems.

    The result is a list of ``{"code", "detail"}`` dicts. Nothing is repaired:
    ambiguous legacy rows are surfaced for a human instead of guessed at.
    """
    issues = []
    tables = {
        value["name"] for value in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    def add(code, detail):
        issues.append({"code": code, "detail": detail})

    def columns(table):
        return {value["name"] for value in db.execute(f"PRAGMA table_info({table})")}

    if "sessions" not in tables:
        return issues

    try:
        for violation in db.execute("PRAGMA foreign_key_check"):
            add("foreign_key_violation", tuple(violation))
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        add("foreign_key_check_failed", str(exc))

    for table, column in _JSON_COLUMNS:
        if table not in tables or column not in columns(table):
            continue
        for record in db.execute(f"SELECT rowid,{column} FROM {table} WHERE {column} IS NOT NULL"):
            try:
                json.loads(record[1])
            except (ValueError, TypeError):
                add("invalid_json", {"table": table, "column": column, "rowid": record[0]})

    for (table, column), allowed in _ALLOWED_VALUES.items():
        if table not in tables or column not in columns(table):
            continue
        for record in db.execute(f"SELECT rowid,{column} FROM {table}"):
            if record[1] not in allowed:
                add("invalid_enum", {"table": table, "column": column, "rowid": record[0]})

    for record in db.execute("SELECT id,reference_text,reference_hash FROM exercises"):
        if digest(record[1]) != record[2]:
            add("reference_hash_mismatch", {"exercise_id": record[0]})

    for record in db.execute(
        """SELECT t.id,t.session_id,c.session_id FROM turns t
           JOIN coach_messages c ON c.turn_id=t.id WHERE c.session_id!=t.session_id"""
    ):
        add("coach_session_mismatch", {"turn_id": record[0]})

    for record in db.execute(
        """SELECT p.id,p.session_id,t.session_id FROM conversation_playbacks p
           JOIN turns t ON t.id=p.turn_id WHERE p.session_id!=t.session_id"""
    ):
        add("playback_session_mismatch", {"playback_id": record[0]})

    for record in db.execute(
        """SELECT a.id,af.session_id,t.session_id FROM attempts a
           JOIN audio_files af ON af.id=a.audio_id
           JOIN exercises e ON e.id=a.exercise_id
           JOIN turns t ON t.id=e.turn_id WHERE af.session_id!=t.session_id"""
    ):
        add("attempt_audio_session_mismatch", {"attempt_id": record[0]})

    for record in db.execute("SELECT id,filename FROM audio_files"):
        path = (audio_dir / record[1]).resolve()
        if path.parent != audio_dir or not path.is_file():
            add("missing_audio_file", {"audio_id": record[0], "filename": record[1]})

    return issues


class Database:
    def __init__(self, data_dir, speech_defaults=None):
        data_dir = data_dir
        self.path = data_dir / "drill.sqlite3"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "audio").mkdir(exist_ok=True)
        self.audio_dir = (data_dir / "audio").resolve()
        self.sqlite_version = sqlite3.sqlite_version
        with self.connect() as db:
            self.migrate(db, speech_defaults)
        self.recover()

    @staticmethod
    def known_migrations():
        versions = [migration.version for migration in MIGRATIONS]
        if len(set(versions)) != len(versions):
            raise MigrationError("migration versionが重複しています。")
        return {migration.version: migration for migration in MIGRATIONS}

    def migrate(self, db, speech_defaults=None):
        known = self.known_migrations()
        db.execute(MIGRATIONS_TABLE)
        applied = {
            record["version"]: record
            for record in db.execute(
                "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
            )
        }
        for version, record in applied.items():
            migration = known.get(version)
            if migration is None:
                raise MigrationError(
                    f"適用済みの未知のmigration version {version} を検出しました。"
                    "新しいアプリで作成されたDBの可能性があります。"
                )
            if record["name"] != migration.name or record["checksum"] != migration.checksum():
                raise MigrationError(
                    f"適用済みmigration {version} の内容が変更されています。"
                    "適用済みmigrationを書き換えず、新しい番号を追加してください。"
                )
        pending = [m for m in MIGRATIONS if m.version not in applied]
        if pending:
            for issue in inspect_database(db, self.audio_dir):
                log.warning("DB移行前検査: %s", issue)
        for migration in pending:
            self._apply(db, migration, {"speech_defaults": speech_defaults})

    def _apply(self, db, migration, context):
        if migration.disable_foreign_keys:
            db.execute("PRAGMA foreign_keys=OFF")
        try:
            db.execute("BEGIN IMMEDIATE")
            for statement in migration.statements:
                db.execute(statement)
            if migration.func is not None:
                migration.func(db, context)
            if migration.disable_foreign_keys:
                violations = list(db.execute("PRAGMA foreign_key_check"))
                if violations:
                    raise MigrationError(
                        f"migration {migration.version} 適用後の外部キー検査に失敗しました: "
                        f"{violations[:3]}"
                    )
            db.execute(
                "INSERT INTO schema_migrations (version,name,checksum,applied_at) VALUES (?,?,?,?)",
                (migration.version, migration.name, migration.checksum(), now()),
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            if migration.disable_foreign_keys:
                db.execute("PRAGMA foreign_keys=ON")

    def recover(self):
        """Resume non-schema work that a restart interrupted. Not a migration."""
        with self.connect() as db:
            db.execute("UPDATE requests SET status='interrupted' WHERE status='processing'")
            db.execute(
                "UPDATE conversation_playbacks SET status='cancelled' WHERE status='playing'"
            )
            db.execute(
                "UPDATE conversations SET status='paused',revision=revision+1,playback_id=NULL WHERE status='running'"
            )
            db.execute(
                "UPDATE assessment_runs SET execution_status='interrupted',finished_at=? WHERE execution_status IN ('queued','running')",
                (now(),),
            )
            deleting = [
                record["id"]
                for record in db.execute("SELECT id FROM sessions WHERE deletion_status='deleting'")
            ]
        for session_id in deleting:
            try:
                purge_session(self, session_id)
            except OSError:
                log.warning("セッション削除を再開できませんでした: %s", session_id)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=NORMAL")
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def read_snapshot(self):
        """Hold one read transaction so every SELECT sees the same point in time."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN")
            yield db
        finally:
            db.rollback()
            db.close()

    @contextmanager
    def write_transaction(self):
        """Short BEGIN IMMEDIATE section; commit on success, rollback on error."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def backup_database(database, destination):
    """Consistent SQLite snapshot plus an external-file manifest, restore-ready."""
    destination = Path(destination)
    (destination / "audio").mkdir(parents=True, exist_ok=True)
    target = destination / "drill.sqlite3"
    with database.connect() as source:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    files = []
    with database.connect() as db:
        records = [
            dict(record)
            for record in db.execute(
                "SELECT id,storage_key,filename,kind,save_status FROM audio_files"
            )
        ]
        schema_versions = [
            record["version"] for record in db.execute("SELECT version FROM schema_migrations")
        ]
    for record in records:
        key = record["storage_key"] or record["filename"]
        path = (database.audio_dir / key).resolve()
        if path.parent != database.audio_dir or not path.is_file():
            if record["save_status"] == "ready":
                raise MigrationError(f"ready音声ファイルが見つかりません: {key}")
            continue
        data = path.read_bytes()
        (destination / "audio" / Path(key).name).write_bytes(data)
        files.append(
            {
                "storage_key": key,
                "audio_id": record["id"],
                "kind": record["kind"],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest = {
        "schema_version": "1.0",
        "sqlite_version": database.sqlite_version,
        "migration_versions": sorted(schema_versions),
        "files": files,
    }
    (destination / "manifest.json").write_text(encode(manifest))
    return manifest


def verify_backup(destination):
    """Validate a backup before it is trusted for restore."""
    destination = Path(destination)
    report = {"ok": True, "errors": [], "missing": [], "extra": []}
    database_file = destination / "drill.sqlite3"
    manifest_file = destination / "manifest.json"
    if not database_file.is_file() or not manifest_file.is_file():
        report["ok"] = False
        report["errors"].append("backup_incomplete")
        return report
    manifest = json.loads(manifest_file.read_text())
    connection = sqlite3.connect(database_file)
    try:
        connection.row_factory = sqlite3.Row
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            report["ok"] = False
            report["errors"].append("integrity_check_failed")
        if list(connection.execute("PRAGMA foreign_key_check")):
            report["ok"] = False
            report["errors"].append("foreign_key_violation")
        versions = sorted(
            record["version"]
            for record in connection.execute("SELECT version FROM schema_migrations")
        )
        if versions != manifest.get("migration_versions"):
            report["ok"] = False
            report["errors"].append("schema_version_mismatch")
    finally:
        connection.close()
    expected = {entry["storage_key"] for entry in manifest["files"]}
    for entry in manifest["files"]:
        path = destination / "audio" / Path(entry["storage_key"]).name
        if not path.is_file():
            report["missing"].append(entry["storage_key"])
            continue
        data = path.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            report["ok"] = False
            report["errors"].append("file_hash_mismatch")
    for path in (destination / "audio").iterdir() if (destination / "audio").is_dir() else []:
        if path.name not in {Path(key).name for key in expected}:
            report["extra"].append(path.name)
    if report["missing"]:
        report["ok"] = False
    return report


def row(db, table, identity):
    # table is always a server-side constant, never supplied by a client.
    value = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    if value is None:
        from .errors import APIError

        raise APIError(404, "not_found", "データが見つかりません。")
    return dict(value)


_TURN_SELECT = """SELECT t.*,
 s.answer_text AS submission_answer, s.submitted_via AS submission_via,
 s.source_exercise_id AS submission_exercise, s.source_playback_id AS submission_playback,
 s.committed_at AS submission_committed_at, s.provenance_status AS submission_provenance
 FROM turns t LEFT JOIN turn_submissions s ON s.turn_id=t.id"""


def turn_row(db, identity):
    value = db.execute(f"{_TURN_SELECT} WHERE t.id=?", (identity,)).fetchone()
    if value is None:
        from .errors import APIError

        raise APIError(404, "not_found", "データが見つかりません。")
    turn = dict(value)
    answer = turn.pop("submission_answer")
    via = turn.pop("submission_via")
    if answer is not None or via is not None:
        turn["confirmed_answer_en"] = answer
        turn["submitted_via"] = via
    turn["source_exercise_id"] = turn.pop("submission_exercise")
    turn["source_playback_id"] = turn.pop("submission_playback")
    turn["committed_at"] = turn.pop("submission_committed_at")
    turn["provenance_status"] = turn.pop("submission_provenance")
    return turn


def turns_for(db, session_id):
    return [
        turn_row(db, value["id"])
        for value in db.execute(
            "SELECT id FROM turns WHERE session_id=? ORDER BY ordinal", (session_id,)
        )
    ]
