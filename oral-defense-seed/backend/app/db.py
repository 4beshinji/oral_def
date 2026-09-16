import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, pack_snapshot_json TEXT NOT NULL,
 pack_hash TEXT NOT NULL, research_brief TEXT NOT NULL, scenario TEXT NOT NULL,
 settings_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE IF NOT EXISTS turns (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, question_en TEXT NOT NULL, basis_note TEXT NOT NULL,
 confirmed_answer_en TEXT, submitted_via TEXT, follow_up_count INTEGER NOT NULL,
 unable_to_answer INTEGER NOT NULL DEFAULT 0, UNIQUE(session_id, ordinal));
CREATE TABLE IF NOT EXISTS coach_messages (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE, level TEXT NOT NULL,
 user_note TEXT NOT NULL, draft TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exercises (
 id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
 mode TEXT NOT NULL, reference_text TEXT NOT NULL, reference_hash TEXT NOT NULL,
 reference_origin TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audio_files (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 filename TEXT NOT NULL, media_type TEXT NOT NULL, cache_key TEXT, UNIQUE(session_id, cache_key));
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, exercise_id TEXT NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
 audio_id TEXT REFERENCES audio_files(id), dictation_text TEXT, audio_meta_json TEXT NOT NULL,
 result_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assistance (
 id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
 exercise_id TEXT REFERENCES exercises(id) ON DELETE CASCADE, kind TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
 id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
 mode TEXT NOT NULL DEFAULT 'shadowing', status TEXT NOT NULL DEFAULT 'paused',
 stage TEXT NOT NULL DEFAULT 'question_generation', revision INTEGER NOT NULL DEFAULT 0,
 turn_id TEXT REFERENCES turns(id) ON DELETE CASCADE,
 reference_id TEXT REFERENCES exercises(id) ON DELETE SET NULL,
 audio_id TEXT REFERENCES audio_files(id) ON DELETE SET NULL,
 playback_id TEXT);
CREATE TABLE IF NOT EXISTS conversation_playbacks (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
 reference_id TEXT REFERENCES exercises(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, stage TEXT NOT NULL, reference_hash TEXT NOT NULL,
 audio_id TEXT REFERENCES audio_files(id) ON DELETE SET NULL,
 tts_settings_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'playing');
CREATE TABLE IF NOT EXISTS requests (
 request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 action TEXT NOT NULL, payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 status TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL);
"""


class Database:
    def __init__(self, data_dir):
        self.path = data_dir / "drill.sqlite3"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "audio").mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute("UPDATE requests SET status='interrupted' WHERE status='processing'")
            db.execute(
                "UPDATE conversation_playbacks SET status='cancelled' WHERE status='playing'"
            )
            db.execute(
                "UPDATE conversations SET status='paused',revision=revision+1,playback_id=NULL WHERE status='running'"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()


def row(db, table, identity):
    # table is always a server-side constant, never supplied by a client.
    value = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    if value is None:
        from .errors import APIError

        raise APIError(404, "not_found", "データが見つかりません。")
    return dict(value)
