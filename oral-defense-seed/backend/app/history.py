"""Bounded local history reads; cursors survive new rows and application reloads."""

import json

from .db import row, turn_row
from .errors import APIError


def child_page(db, table, owner_column, owner_id, before=None, limit=20):
    # Table/column names are internal constants, never request values.
    size = max(1, min(limit, 100))
    records = db.execute(
        f"SELECT rowid AS cursor,* FROM {table} WHERE {owner_column}=?"
        + (" AND rowid<?" if before is not None else "")
        + " ORDER BY rowid DESC LIMIT ?",
        (owner_id, before, size + 1) if before is not None else (owner_id, size + 1),
    ).fetchall()
    total = db.execute(
        f"SELECT count(*) FROM {table} WHERE {owner_column}=?", (owner_id,)
    ).fetchone()[0]
    items = [dict(record) for record in records[:size]][::-1]
    cursor = items[0]["cursor"] if len(records) > size else None
    for item in items:
        item.pop("cursor")
    return {"items": items, "total": total, "next_before": cursor}


def assessment_detail(record):
    value = dict(record)
    value["result"] = json.loads(value.pop("result_json") or "null")
    value["config"] = json.loads(value.pop("config_json") or "null")
    return value


def coach_detail(record):
    value = dict(record)
    value["response"] = json.loads(value.pop("response_json"))
    value["generation"] = json.loads(value.pop("generation_json") or "null")
    return value


def attempt_detail(db, record):
    attempt = dict(record)
    attempt["audio_meta"] = json.loads(attempt.pop("audio_meta_json"))
    legacy_result = attempt.pop("result_json")
    run = db.execute(
        "SELECT * FROM assessment_runs WHERE attempt_id=? ORDER BY rowid DESC LIMIT 1",
        (attempt["id"],),
    ).fetchone()
    attempt["assessment"] = assessment_detail(run) if run is not None else None
    attempt["result"] = json.loads((run["result_json"] if run else legacy_result) or "null")
    attempt["audio_available"] = False
    if attempt["audio_id"]:
        saved = db.execute(
            "SELECT save_status FROM audio_files WHERE id=?", (attempt["audio_id"],)
        ).fetchone()
        attempt["audio_available"] = bool(saved and saved["save_status"] == "ready")
    return attempt


def exercise_detail(db, record):
    exercise = dict(record)
    exercise["generation"] = json.loads(exercise.pop("generation_json") or "null")
    page = child_page(db, "attempts", "exercise_id", exercise["id"])
    exercise["attempts"] = [attempt_detail(db, attempt) for attempt in page["items"]]
    exercise["attempts_total"] = page["total"]
    exercise["attempts_before"] = page["next_before"]
    return exercise


def hydrate_turn(db, turn):
    turn = dict(turn)
    turn["generation"] = json.loads(turn.pop("generation_json") or "null")
    messages = child_page(db, "coach_messages", "turn_id", turn["id"])
    turn["coach_messages"] = [coach_detail(message) for message in messages["items"]]
    turn["coach_messages_total"] = messages["total"]
    turn["coach_messages_before"] = messages["next_before"]
    exercises = child_page(db, "exercises", "turn_id", turn["id"])
    turn["exercises"] = [exercise_detail(db, exercise) for exercise in exercises["items"]]
    turn["exercises_total"] = exercises["total"]
    turn["exercises_before"] = exercises["next_before"]
    # Count all attempts, including those outside the visible detail page.
    turn["has_recording"] = bool(
        db.execute(
            """SELECT 1 FROM attempts a JOIN exercises e ON e.id=a.exercise_id
               WHERE e.turn_id=? AND a.audio_id IS NOT NULL LIMIT 1""",
            (turn["id"],),
        ).fetchone()
    ) or bool(
        db.execute(
            "SELECT 1 FROM free_speech_recordings WHERE turn_id=? LIMIT 1", (turn["id"],)
        ).fetchone()
    )
    turn["transcript"] = (
        turn.get("confirmed_answer_en")
        if turn.get("submitted_via") == "free_speech_transcript"
        else None
    )
    assistance = child_page(db, "assistance", "turn_id", turn["id"])
    turn["assistance"] = assistance["items"]
    turn["assistance_total"] = assistance["total"]
    return turn


def install_history(app, database):
    from uuid import UUID

    @app.get("/v1/sessions/{session_id}/turns/{turn_id}")
    def get_turn(session_id: UUID, turn_id: UUID):
        with database.read_snapshot() as db:
            row(db, "sessions", str(session_id))
            turn = turn_row(db, str(turn_id))
            if turn["session_id"] != str(session_id):
                raise APIError(404, "not_found", "このセッションの会話ではありません。")
            return hydrate_turn(db, turn)

    @app.get("/v1/turns/{turn_id}/exercises")
    def exercises(turn_id: UUID, before: int | None = None, limit: int = 20):
        with database.read_snapshot() as db:
            row(db, "turns", str(turn_id))
            page = child_page(db, "exercises", "turn_id", str(turn_id), before, limit)
            page["items"] = [exercise_detail(db, item) for item in page["items"]]
            return page

    @app.get("/v1/turns/{turn_id}/coach-messages")
    def coach_messages(turn_id: UUID, before: int | None = None, limit: int = 20):
        with database.read_snapshot() as db:
            row(db, "turns", str(turn_id))
            page = child_page(db, "coach_messages", "turn_id", str(turn_id), before, limit)
            page["items"] = [coach_detail(item) for item in page["items"]]
            return page

    @app.get("/v1/exercises/{exercise_id}/attempts")
    def attempts(exercise_id: UUID, before: int | None = None, limit: int = 20):
        with database.read_snapshot() as db:
            row(db, "exercises", str(exercise_id))
            page = child_page(db, "attempts", "exercise_id", str(exercise_id), before, limit)
            page["items"] = [attempt_detail(db, item) for item in page["items"]]
            return page

    @app.get("/v1/attempts/{attempt_id}/assessments")
    def assessments(attempt_id: UUID, before: int | None = None, limit: int = 20):
        with database.read_snapshot() as db:
            row(db, "attempts", str(attempt_id))
            page = child_page(db, "assessment_runs", "attempt_id", str(attempt_id), before, limit)
            page["items"] = [assessment_detail(item) for item in page["items"]]
            return page

    @app.get("/v1/attempts/{attempt_id}")
    def get_attempt(attempt_id: UUID):
        with database.read_snapshot() as db:
            return attempt_detail(db, row(db, "attempts", str(attempt_id)))
