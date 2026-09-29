"""Free speech recording, final transcription, and separate typed fallback."""

import asyncio
import json
import sqlite3
from uuid import UUID

from fastapi import File, Form, UploadFile
from pydantic import Field

from . import audio
from .db import digest, digest_bytes, encode, now, row, uid
from .errors import APIError
from .schemas import RequestInput


class Transcribe(RequestInput):
    recording_id: UUID
    turn_id: UUID
    revision: int = Field(ge=0)


class Manual(RequestInput):
    turn_id: UUID
    revision: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=4000)
    recording_id: UUID | None = None


def install_free_speech(app, database, providers, recognizer):
    audio_root = database.audio_dir

    def current(db, sid, tid, revision=None, *, allow_paused=False):
        session = row(db, "sessions", sid)
        state = row(db, "conversations", sid)
        if session["deletion_status"] != "active":
            raise APIError(409, "session_deleting", "削除中のセッションです。")
        if state["mode"] != "free_speech" or state["stage"] != "free_speech_input":
            raise APIError(409, "wrong_stage", "自由発話の入力段階ではありません。")
        if state["turn_id"] != tid or (revision is not None and state["revision"] != revision):
            raise APIError(409, "stale_operation", "古いターンまたは操作版です。")
        if state["status"] != "running" and not (allow_paused and state["status"] == "paused"):
            raise APIError(409, "pause_required", "会話を再開してから返答してください。")
        return state

    def recording_row(db, sid, rid):
        record = row(db, "free_speech_recordings", rid)
        if record["session_id"] != sid:
            raise APIError(409, "session_mismatch", "録音のセッションが一致しません。")
        return record

    @app.get("/v1/sessions/{session_id}/free-speech/recordings")
    def list_recordings(session_id: UUID, turn_id: UUID | None = None):
        sid = str(session_id)
        tid = str(turn_id) if turn_id else None
        with database.connect() as db:
            row(db, "sessions", sid)
            if tid and row(db, "turns", tid)["session_id"] != sid:
                raise APIError(404, "not_found", "このセッションの録音ではありません。")
            records = []
            for record in db.execute(
                """SELECT r.id,r.turn_id,r.audio_id,r.created_at,r.audio_meta_json,
                          a.save_status
                   FROM free_speech_recordings r JOIN audio_files a ON a.id=r.audio_id
                   WHERE r.session_id=? AND (? IS NULL OR r.turn_id=?)
                   ORDER BY r.rowid DESC LIMIT 50""",
                (sid, tid, tid),
            ):
                item = dict(record)
                item["audio_meta"] = json.loads(item.pop("audio_meta_json"))
                item["transcriptions"] = [
                    dict(value)
                    for value in db.execute(
                        """SELECT id,status,text,error_code,created_at,finished_at
                           FROM free_speech_transcriptions WHERE recording_id=?
                           ORDER BY rowid DESC LIMIT 10""",
                        (item["id"],),
                    )
                ]
                records.append(item)
            return {"recordings": records}

    @app.post("/v1/sessions/{session_id}/free-speech/recordings", status_code=201)
    async def upload_recording(
        session_id: UUID,
        file: UploadFile = File(...),
        turn_id: UUID = Form(...),
        revision: int = Form(...),
        client_key: str = Form(...),
        capture: str = Form("{}"),
    ):
        sid, tid = str(session_id), str(turn_id)
        client_key = client_key.strip()
        if not client_key or len(client_key) > 100:
            raise APIError(422, "recording_key", "録音キーが不正です。")
        try:
            capture_data = json.loads(capture)
            if not isinstance(capture_data, dict) or len(capture) > 4000:
                raise ValueError("capture")
        except (ValueError, TypeError):
            raise APIError(422, "capture_settings", "録音設定の形式が不正です。") from None
        media_type = (file.content_type or "").split(";")[0]
        extension = {
            "audio/webm": ".webm",
            "video/webm": ".webm",
            "audio/ogg": ".ogg",
            "audio/mp4": ".mp4",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
        }.get(media_type)
        if extension is None:
            raise APIError(415, "audio_type", "WebM、Ogg、MP4、WAVの音声を使用してください。")
        data = await file.read(audio.MAX_UPLOAD + 1)
        await file.close()
        if not data or len(data) > audio.MAX_UPLOAD:
            raise APIError(413, "upload_limit", "空でない10 MiB以下の録音を使用してください。")
        content_hash = digest_bytes(data)
        with database.connect() as db:
            existing = db.execute(
                """SELECT r.id,r.turn_id,a.content_hash,a.id AS audio_id
                   FROM free_speech_recordings r JOIN audio_files a ON a.id=r.audio_id
                   WHERE r.session_id=? AND r.client_key=?""",
                (sid, client_key),
            ).fetchone()
            if existing:
                if existing["turn_id"] != tid or existing["content_hash"] != content_hash:
                    raise APIError(
                        409, "recording_key_conflict", "同じ録音キーに別の内容があります。"
                    )
                return {"recording_id": existing["id"], "audio_id": existing["audio_id"]}
            current(db, sid, tid, revision, allow_paused=True)
        rid, aid = uid(), uid()
        temporary = audio_root / (aid + ".part" + extension)
        stored = audio_root / (aid + extension)
        try:
            temporary.write_bytes(data)
            media = await asyncio.to_thread(providers.work.run, audio.inspect_upload, temporary)
            if media["duration_s"] > 30:
                raise APIError(413, "recording_too_long", "30秒以下の録音を使用してください。")
            temporary.replace(stored)
            with database.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current(db, sid, tid, revision, allow_paused=True)
                try:
                    db.execute(
                        """INSERT INTO audio_files
                           (id,session_id,filename,media_type,cache_key,kind,storage_key,
                            content_hash,media_json,save_status)
                           VALUES (?,?,?,?,NULL,'recording',?,?,?,'ready')""",
                        (
                            aid,
                            sid,
                            stored.name,
                            media_type,
                            stored.name,
                            content_hash,
                            encode(media),
                        ),
                    )
                    db.execute(
                        """INSERT INTO free_speech_recordings
                           (id,session_id,turn_id,audio_id,client_key,capture_json,
                            audio_meta_json,created_at) VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            rid,
                            sid,
                            tid,
                            aid,
                            client_key,
                            encode(capture_data),
                            encode(media),
                            now(),
                        ),
                    )
                except sqlite3.IntegrityError:
                    db.rollback()
                    existing = db.execute(
                        """SELECT r.id,r.turn_id,a.content_hash,a.id AS audio_id
                           FROM free_speech_recordings r JOIN audio_files a ON a.id=r.audio_id
                           WHERE r.session_id=? AND r.client_key=?""",
                        (sid, client_key),
                    ).fetchone()
                    if (
                        existing
                        and existing["turn_id"] == tid
                        and existing["content_hash"] == content_hash
                    ):
                        return {"recording_id": existing["id"], "audio_id": existing["audio_id"]}
                    raise
            return {"recording_id": rid, "audio_id": aid}
        finally:
            temporary.unlink(missing_ok=True)
            with database.connect() as db:
                saved = db.execute("SELECT 1 FROM audio_files WHERE id=?", (aid,)).fetchone()
            if saved is None:
                stored.unlink(missing_ok=True)

    @app.post("/v1/sessions/{session_id}/free-speech/transcribe")
    def transcribe(session_id: UUID, body: Transcribe):
        sid, rid, tid = str(session_id), str(body.recording_id), str(body.turn_id)
        request_id = str(body.request_id)
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = recording_row(db, sid, rid)
            if record["turn_id"] != tid:
                raise APIError(409, "turn_mismatch", "録音のターンが一致しません。")
            existing = db.execute(
                "SELECT * FROM free_speech_transcriptions WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing:
                if existing["recording_id"] != rid or existing["session_id"] != sid:
                    raise APIError(409, "request_conflict", "要求IDが別の録音に使われています。")
                if existing["status"] == "succeeded":
                    return {"status": "succeeded", "text": existing["text"], "recording_id": rid}
                if existing["status"] == "no_speech":
                    return {"status": "no_speech", "text": None, "recording_id": rid}
                raise APIError(409, existing["status"], "新しい要求IDで再認識してください。", True)
            current(db, sid, tid, body.revision)
            audio_record = row(db, "audio_files", record["audio_id"])
            path = (audio_root / audio_record["filename"]).resolve()
            if (
                path.parent != audio_root
                or audio_record["save_status"] != "ready"
                or not path.is_file()
            ):
                raise APIError(404, "audio_not_found", "録音が見つかりません。")
            txid = uid()
            db.execute(
                """INSERT INTO free_speech_transcriptions
                   (id,session_id,recording_id,request_id,status,created_at)
                   VALUES (?,?,?,?, 'running',?)""",
                (txid, sid, rid, request_id, now()),
            )
        try:
            result = providers.work.run(recognizer.transcribe, path)
        except APIError as exc:
            with database.connect() as db:
                db.execute(
                    """UPDATE free_speech_transcriptions SET status='failed',error_code=?,
                       finished_at=? WHERE id=? AND status='running'""",
                    (exc.body["code"], now(), txid),
                )
            raise
        except Exception as exc:
            with database.connect() as db:
                db.execute(
                    """UPDATE free_speech_transcriptions SET status='failed',error_code='asr_failed',
                       finished_at=? WHERE id=? AND status='running'""",
                    (now(), txid),
                )
            raise APIError(
                502, "asr_failed", "音声認識に失敗しました。録音を保持して再試行できます。", True
            ) from exc
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            live = db.execute(
                "SELECT status FROM free_speech_transcriptions WHERE id=?", (txid,)
            ).fetchone()
            if live is None:
                raise APIError(409, "stale_operation", "削除済みの録音です。")
            try:
                current(db, sid, tid, body.revision)
            except APIError:
                db.execute(
                    "UPDATE free_speech_transcriptions SET status='interrupted',finished_at=? WHERE id=?",
                    (now(), txid),
                )
                return {"status": "interrupted", "text": None, "recording_id": rid}
            status = (
                "succeeded" if result["status"] == "ok" and result["text"].strip() else "no_speech"
            )
            db.execute(
                """UPDATE free_speech_transcriptions SET status=?,text=?,result_json=?,finished_at=?
                   WHERE id=? AND status='running'""",
                (status, result["text"] or None, encode(result), now(), txid),
            )
            if status == "no_speech":
                return {"status": status, "text": None, "recording_id": rid}
            db.execute(
                """INSERT INTO turn_submissions
                   (turn_id,session_id,answer_text,submitted_via,source_recording_id,
                    source_transcription_id,committed_at,provenance_status)
                   VALUES (?,?,?,'free_speech_transcript',?,?,?,'exact')""",
                (tid, sid, result["text"], rid, txid, now()),
            )
            db.execute(
                """UPDATE conversations SET stage='question_generation',playback_id=NULL,
                   revision=revision+1 WHERE id=?""",
                (sid,),
            )
            return {"status": "succeeded", "text": result["text"], "recording_id": rid}

    @app.post("/v1/sessions/{session_id}/free-speech/manual")
    def manual(session_id: UUID, body: Manual):
        sid, tid = str(session_id), str(body.turn_id)
        text = body.text.strip()
        if not text:
            raise APIError(422, "empty_answer", "空でない英文を入力してください。")
        payload = body.model_dump(mode="json")
        request_id = str(body.request_id)
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM requests WHERE request_id=?", (request_id,)
            ).fetchone()
            action = f"free_speech_manual:{tid}"
            payload_hash = digest(encode(payload))
            if existing:
                if (
                    existing["session_id"] != sid
                    or existing["action"] != action
                    or existing["payload_hash"] != payload_hash
                ):
                    raise APIError(409, "request_conflict", "要求IDが別の入力に使われています。")
                return json.loads(existing["result_json"])
            current(db, sid, tid, body.revision)
            rid = str(body.recording_id) if body.recording_id else None
            if rid and recording_row(db, sid, rid)["turn_id"] != tid:
                raise APIError(409, "turn_mismatch", "録音のターンが一致しません。")
            db.execute(
                """INSERT INTO turn_submissions
                   (turn_id,session_id,answer_text,submitted_via,source_recording_id,
                    committed_at,provenance_status)
                   VALUES (?,?,?,'free_speech_manual',?,?,'exact')""",
                (tid, sid, text, rid, now()),
            )
            db.execute(
                """UPDATE conversations SET stage='question_generation',playback_id=NULL,
                   revision=revision+1 WHERE id=?""",
                (sid,),
            )
            output = {"status": "succeeded", "text": text, "recording_id": rid}
            db.execute(
                """INSERT INTO requests
                   (request_id,session_id,action,payload_hash,payload_json,status,result_json,created_at)
                   VALUES (?,?,?,?,?,'done',?,?)""",
                (request_id, sid, action, payload_hash, encode(payload), encode(output), now()),
            )
            return output
