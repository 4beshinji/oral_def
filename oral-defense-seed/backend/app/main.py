import asyncio
import json
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from . import audio, contexts, dictation, pronunciation
from .config import ROOT, Settings
from .conversation import install_conversation
from .db import Database, digest, encode, now, row, uid
from .errors import APIError
from .providers import Providers
from .schemas import (
    TEXT_ROLES,
    AssistanceInput,
    CatalogRefreshInput,
    CoachInput,
    ConfirmInput,
    DictationInput,
    ExerciseInput,
    ModelInput,
    RequestInput,
    SessionInput,
    SpeechModelInput,
    SpeechPreviewInput,
    TTSInput,
)


def create_app(settings=None):
    settings = settings or Settings()
    database = Database(settings.data_dir)
    providers = Providers(settings)
    app = FastAPI(title="Oral Defense Drill", version="0.4.0")
    app.state.database = database
    app.state.providers = providers
    write_lock = asyncio.Lock()
    audio_root = (settings.data_dir / "audio").resolve()

    # Upgrade existing sessions once, retaining their recorded provider instead of
    # silently changing it when the server's default provider changes.
    with database.connect() as db:
        for old in db.execute("SELECT id,settings_json FROM sessions").fetchall():
            saved = json.loads(old["settings_json"])
            if "text_model" in saved:
                continue
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
            db.execute("UPDATE sessions SET settings_json=? WHERE id=?", (encode(saved), old["id"]))

    with database.connect() as db:
        for old in db.execute("SELECT id,settings_json FROM sessions").fetchall():
            saved = json.loads(old["settings_json"])
            if "role_models" not in saved:
                saved["role_models"] = {role: saved["text_model"] for role in TEXT_ROLES}
                db.execute(
                    "UPDATE sessions SET settings_json=? WHERE id=?", (encode(saved), old["id"])
                )

    with database.connect() as db:
        for old in db.execute("SELECT id,settings_json FROM sessions").fetchall():
            saved = json.loads(old["settings_json"])
            if "speech_models" not in saved:
                saved["speech_models"] = providers.speech_catalog.defaults()
                db.execute(
                    "UPDATE sessions SET settings_json=? WHERE id=?", (encode(saved), old["id"])
                )

    @app.exception_handler(APIError)
    async def handle_error(request, error):
        return JSONResponse(error.body, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse(
            {
                "code": "invalid_input",
                "message": "入力形式・文字数・必須項目を確認してください。",
                "retryable": False,
            },
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse(
            {"code": "http_error", "message": str(error.detail), "retryable": False},
            status_code=error.status_code,
        )

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        def reject(status, code, message):
            return JSONResponse(
                {"code": code, "message": message, "retryable": code == "busy"}, status_code=status
            )

        try:
            host = urlsplit("http://" + request.headers.get("host", ""))
            valid_host = (
                host.hostname in {"localhost", "127.0.0.1", "::1"}
                and host.username is None
                and host.password is None
                and not host.path
            )
        except ValueError:
            valid_host = False
        if not valid_host:
            return reject(403, "invalid_host", "localhostからアクセスしてください。")
        origin = request.headers.get("origin")
        allowed_origins = {
            f"http://{name}:{port}"
            for name in ("localhost", "127.0.0.1", "[::1]")
            for port in (8000, 5173)
        }
        allowed_origins.add("http://" + request.headers["host"])
        if origin and origin not in allowed_origins:
            return reject(403, "invalid_origin", "許可されていないOriginです。")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return reject(403, "cross_site", "別サイトからのアクセスは拒否しました。")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            content_type = request.headers.get("content-type", "")
            if request.method != "DELETE" and not (
                content_type.startswith("application/json")
                or content_type.startswith("multipart/form-data")
            ):
                return reject(415, "content_type", "JSONまたは録音uploadを使用してください。")
            # Enforce the bound even for chunked requests before multipart parsing.
            parts, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > audio.MAX_UPLOAD + 65536:
                    return reject(413, "upload_limit", "uploadは10 MiB以下にしてください。")
                parts.append(chunk)
            request._body = b"".join(parts)
            if request.url.path == "/v1/text-models/refresh" or request.url.path.endswith(
                "/conversation/control"
            ):
                return await call_next(request)
            if write_lock.locked():
                return reject(409, "busy", "処理中です。終了後にもう一度操作してください。")
            async with write_lock:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def turns_for(db, session_id):
        return [
            dict(t)
            for t in db.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY ordinal", (session_id,)
            )
        ]

    def owner_of_turn(db, turn_id):
        turn = row(db, "turns", turn_id)
        return turn, row(db, "sessions", turn["session_id"])

    def active_turn(db, turn_id):
        turn, session = owner_of_turn(db, turn_id)
        if turn["confirmed_answer_en"] is not None:
            raise APIError(409, "already_confirmed", "確定済みです。次の質問へ進んでください。")
        if db.execute("SELECT 1 FROM conversations WHERE id=?", (session["id"],)).fetchone():
            raise APIError(409, "conversation_managed", "自動会話の操作を使用してください。")
        return turn, session

    def saved_audio(db, audio_id):
        record = row(db, "audio_files", audio_id)
        path = (audio_root / record["filename"]).resolve()
        if path.parent != audio_root or not path.is_file():
            raise APIError(404, "audio_not_found", "音声が見つかりません。")
        return record, path

    def assistance(db, turn_id, exercise_id, kind):
        db.execute(
            "INSERT INTO assistance VALUES (?,?,?,?,?)", (uid(), turn_id, exercise_id, kind, now())
        )

    def idempotent(session_id, action, body, operation):
        payload = body.model_dump(mode="json")
        request_id = payload["request_id"]
        payload_hash = digest(encode(payload))
        with database.connect() as db:
            session = row(db, "sessions", session_id)
            stored_input = {
                **payload,
                "provider_configuration": {
                    **capabilities(),
                    "text": json.loads(session["settings_json"])["text_model"],
                    "role_models": json.loads(session["settings_json"])["role_models"],
                },
            }
            existing = db.execute(
                "SELECT * FROM requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing:
                if (
                    existing["action"] != action
                    or existing["payload_hash"] != payload_hash
                    or existing["session_id"] != session_id
                ):
                    raise APIError(
                        409, "request_conflict", "このrequest_idは別の入力に使用されています。"
                    )
                if existing["status"] == "done":
                    return json.loads(existing["result_json"])
                raise APIError(
                    409,
                    existing["status"],
                    "処理中または中断済みの要求です。結果不明の再試行には新しいIDが必要で、再課金される場合があります。",
                    True,
                )
            db.execute(
                "INSERT INTO requests (request_id,session_id,action,payload_hash,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    request_id,
                    session_id,
                    action,
                    payload_hash,
                    encode(stored_input),
                    "processing",
                    now(),
                ),
            )
        try:
            with database.connect() as db:
                result = operation(db)
                db.execute(
                    "UPDATE requests SET status='done',result_json=? WHERE request_id=?",
                    (encode(result), request_id),
                )
            return result
        except Exception:
            with database.connect() as db:
                db.execute("UPDATE requests SET status='failed' WHERE request_id=?", (request_id,))
            raise

    def capabilities():
        return {
            "text": {
                "provider": settings.text_provider,
                "model": settings.text_model or None,
                "endpoint": settings.text_base_url or None,
                "mock": settings.text_provider == "mock",
                "response_format": settings.text_response_format,
            },
            "tts": {
                "provider": settings.tts_provider,
                "model": settings.tts_model or None,
                "endpoint": settings.tts_base_url or None,
                "mock": settings.tts_provider == "mock",
            },
            "pronunciation": {
                "provider": settings.pronunciation_provider,
                "status": "unavailable",
                "control_gate_passed": False,
            },
            "limits": {
                "questions": 6,
                "coach_exchanges": 12,
                "recording_seconds": 30,
                "upload_bytes": audio.MAX_UPLOAD,
            },
            "retention": "録音と参照文はセッションを削除するまで、このPCに保存します。",
        }

    @app.get("/v1/capabilities")
    def get_capabilities():
        return capabilities()

    @app.get("/v1/text-models")
    def list_text_models():
        return providers.catalog.candidates()

    @app.post("/v1/text-models/refresh")
    def refresh_text_models(body: CatalogRefreshInput):
        return providers.catalog.refresh(force=body.force)

    @app.get("/v1/packs/sample")
    def sample_pack():
        return json.loads((ROOT / "examples/bo_pack.json").read_text())

    @app.post("/v1/sessions", status_code=201)
    def create_session(body: SessionInput):
        target = providers.catalog.resolve(body.text_model or body.role_models.get("examiner"))
        targets = {
            role: providers.catalog.resolve(body.role_models[role]).public()
            if role in body.role_models
            else target.public()
            for role in TEXT_ROLES
        }
        speech_models = {**providers.speech_catalog.defaults(), **body.speech_models}
        for selection in body.speech_models.values():
            providers.speech_catalog.validate(selection)
        session_id = uid()
        with database.connect() as db:
            snapshot = encode(body.pack)
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    now(),
                    snapshot,
                    digest(snapshot),
                    body.research_brief,
                    body.scenario,
                    encode(
                        {
                            **body.settings.model_dump(),
                            "providers_at_start": capabilities(),
                            "text_model": targets["examiner"],
                            "role_models": targets,
                            "speech_models": speech_models,
                        }
                    ),
                    "active",
                ),
            )
            if body.mode == "shadowing":
                db.execute("INSERT INTO conversations (id) VALUES (?)", (session_id,))
        return {"session_id": session_id}

    @app.get("/v1/sessions")
    def list_sessions():
        with database.connect() as db:
            return [
                dict(s)
                for s in db.execute(
                    "SELECT id, created_at, research_brief, scenario, status FROM sessions ORDER BY created_at DESC"
                )
            ]

    def snapshot(session_id):
        with database.connect() as db:
            session = row(db, "sessions", session_id)
            session["pack_snapshot"] = json.loads(session.pop("pack_snapshot_json"))
            session["settings"] = json.loads(session.pop("settings_json"))
            flow = db.execute("SELECT * FROM conversations WHERE id=?", (session_id,)).fetchone()
            session["conversation"] = dict(flow) if flow else None
            session["turns"] = turns_for(db, session_id)
            for turn in session["turns"]:
                turn["coach_messages"] = []
                for value in db.execute(
                    "SELECT * FROM coach_messages WHERE turn_id=? ORDER BY created_at",
                    (turn["id"],),
                ):
                    msg = dict(value)
                    msg["response"] = json.loads(msg.pop("response_json"))
                    turn["coach_messages"].append(msg)
                turn["exercises"] = []
                for value in db.execute(
                    "SELECT * FROM exercises WHERE turn_id=? ORDER BY created_at", (turn["id"],)
                ):
                    exercise = dict(value)
                    exercise["attempts"] = []
                    for value in db.execute(
                        "SELECT * FROM attempts WHERE exercise_id=? ORDER BY created_at",
                        (exercise["id"],),
                    ):
                        attempt = dict(value)
                        attempt["audio_meta"] = json.loads(attempt.pop("audio_meta_json"))
                        attempt["result"] = json.loads(attempt.pop("result_json") or "null")
                        exercise["attempts"].append(attempt)
                    turn["exercises"].append(exercise)
                turn["has_recording"] = any(
                    a["audio_id"] for e in turn["exercises"] for a in e["attempts"]
                )
                turn["transcript"] = None
                turn["assistance"] = [
                    dict(v)
                    for v in db.execute(
                        "SELECT * FROM assistance WHERE turn_id=? ORDER BY created_at",
                        (turn["id"],),
                    )
                ]
            session["playbacks"] = [
                {**dict(p), "tts_settings": json.loads(p["tts_settings_json"])}
                for p in db.execute(
                    "SELECT * FROM conversation_playbacks WHERE session_id=? ORDER BY rowid",
                    (session_id,),
                )
            ]
            for playback in session["playbacks"]:
                playback.pop("tts_settings_json")
            session["requests"] = []
            for saved_request in db.execute(
                "SELECT request_id,action,status,created_at,payload_json FROM requests WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ):
                request_info = dict(saved_request)
                stored_payload = json.loads(request_info.pop("payload_json"))
                request_info["provider_configuration"] = stored_payload.get(
                    "provider_configuration"
                )
                session["requests"].append(request_info)
            return session

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: UUID):
        return snapshot(str(session_id))

    @app.put("/v1/sessions/{session_id}/text-model")
    def select_text_model(session_id: UUID, body: ModelInput):
        target = providers.catalog.resolve(body.text_model)
        with database.connect() as db:
            session = row(db, "sessions", str(session_id))
            saved = json.loads(session["settings_json"])
            if body.role is None:
                saved["role_models"] = {role: target.public() for role in TEXT_ROLES}
            else:
                saved["role_models"][body.role] = target.public()
            saved["text_model"] = saved["role_models"]["examiner"]
            db.execute(
                "UPDATE sessions SET settings_json=? WHERE id=?", (encode(saved), str(session_id))
            )
        return {"text_model": saved["text_model"], "role_models": saved["role_models"]}

    @app.post("/v1/sessions/{session_id}/question")
    def question(session_id: UUID, body: RequestInput):
        sid = str(session_id)

        def operation(db):
            session = row(db, "sessions", sid)
            if db.execute("SELECT 1 FROM conversations WHERE id=?", (sid,)).fetchone():
                raise APIError(409, "conversation_managed", "自動会話の操作を使用してください。")
            turns = turns_for(db, sid)
            if turns and turns[-1]["confirmed_answer_en"] is None:
                raise APIError(409, "unanswered_turn", "今の質問への回答を先に確定してください。")
            if len(turns) >= 6:
                raise APIError(
                    409,
                    "session_complete",
                    "6問が終了しました。新しいセッションを作成してください。",
                )
            count = turns[-1]["follow_up_count"] if turns else 0
            messages = contexts.examiner_messages(
                pack=json.loads(session["pack_snapshot_json"]),
                research_brief=session["research_brief"],
                turns=turns,
                scenario=session["scenario"],
                settings=json.loads(session["settings_json"]),
                follow_up_count=count,
            )
            target = providers.catalog.from_saved(
                json.loads(session["settings_json"])["role_models"]["examiner"]
            )
            result = providers.text("examiner", messages, session_id=sid, target=target)
            if result["follow_up"] and (not turns or count >= 2 or turns[-1]["unable_to_answer"]):
                raise APIError(
                    502,
                    "follow_up_limit",
                    "質問APIが追質問の上限を守りませんでした。手動で再試行してください。",
                    True,
                )
            turn_id = uid()
            db.execute(
                "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,follow_up_count) VALUES (?,?,?,?,?,?)",
                (
                    turn_id,
                    sid,
                    len(turns) + 1,
                    result["question_en"],
                    result["basis_note"],
                    count + 1 if result["follow_up"] else 0,
                ),
            )
            return row(db, "turns", turn_id)

        return idempotent(sid, f"question:{sid}", body, operation)

    @app.post("/v1/turns/{turn_id}/coach")
    def coach(turn_id: UUID, body: CoachInput):
        tid = str(turn_id)
        if body.level == "revision" and not body.draft:
            raise APIError(422, "draft_required", "文章修正には回答文を入力してください。")
        with database.connect() as db:
            turn, session = owner_of_turn(db, tid)

        def operation(db):
            active_turn(db, tid)
            history = [
                dict(h)
                for h in db.execute(
                    "SELECT * FROM coach_messages WHERE session_id=? ORDER BY created_at",
                    (session["id"],),
                )
            ]
            if len(history) >= 12:
                raise APIError(409, "coach_limit", "Coachは1セッション12往復までです。")
            messages = contexts.coach_messages(
                pack=json.loads(session["pack_snapshot_json"]),
                research_brief=session["research_brief"],
                turns=turns_for(db, session["id"]),
                current_question=turn["question_en"],
                history=history,
                level=body.level,
                user_note=body.user_note,
                draft=body.draft,
            )
            target = providers.catalog.from_saved(
                json.loads(session["settings_json"])["role_models"][body.level]
            )
            result = providers.text(body.level, messages, session_id=session["id"], target=target)
            message_id = uid()
            db.execute(
                "INSERT INTO coach_messages VALUES (?,?,?,?,?,?,?,?)",
                (
                    message_id,
                    session["id"],
                    tid,
                    body.level,
                    body.user_note,
                    body.draft,
                    encode(result),
                    now(),
                ),
            )
            assistance(db, tid, None, "coach_" + body.level)
            return {"id": message_id, "response": result}

        return idempotent(session["id"], f"coach:{tid}", body, operation)

    @app.post("/v1/turns/{turn_id}/exercises", status_code=201)
    def create_exercise(turn_id: UUID, body: ExerciseInput):
        tid, identity = str(turn_id), uid()
        with database.connect() as db:
            active_turn(db, tid)
            db.execute(
                "INSERT INTO exercises VALUES (?,?,?,?,?,?,?)",
                (identity, tid, body.mode, body.text, digest(body.text), body.origin, now()),
            )
            if body.mode == "read_aloud":
                assistance(db, tid, identity, "reference_shown")
            return row(db, "exercises", identity)

    @app.post("/v1/turns/{turn_id}/confirm")
    def confirm(turn_id: UUID, body: ConfirmInput):
        tid = str(turn_id)
        with database.connect() as db:
            turn, session = owner_of_turn(db, tid)

        def operation(db):
            active_turn(db, tid)
            if not db.execute(
                "SELECT 1 FROM exercises WHERE turn_id=? AND reference_text=?",
                (tid, body.answer_en),
            ).fetchone():
                raise APIError(
                    409,
                    "reference_not_fixed",
                    "この回答文を先に練習用の参照文として固定してください。",
                )
            db.execute(
                "UPDATE turns SET confirmed_answer_en=?,submitted_via='confirmed_reference',unable_to_answer=? WHERE id=?",
                (body.answer_en, body.unable_to_answer, tid),
            )
            if turn["ordinal"] == 6:
                db.execute("UPDATE sessions SET status='completed' WHERE id=?", (session["id"],))
            return row(db, "turns", tid)

        return idempotent(session["id"], f"confirm:{tid}", body, operation)

    @app.post("/v1/exercises/{exercise_id}/audio", status_code=201)
    async def upload_audio(
        exercise_id: UUID, file: UploadFile = File(...), capture: str = Form("{}")
    ):
        eid = str(exercise_id)
        with database.connect() as db:
            exercise = row(db, "exercises", eid)
            turn, session = owner_of_turn(db, exercise["turn_id"])
        if exercise["mode"] == "dictation":
            raise APIError(409, "wrong_mode", "dictationでは文字を入力してください。")
        try:
            capture_data = json.loads(capture)
            if not isinstance(capture_data, dict) or len(capture) > 2000:
                raise ValueError()
            capture_data = {
                k: v
                for k, v in capture_data.items()
                if k
                in {
                    "sampleRate",
                    "channelCount",
                    "echoCancellation",
                    "noiseSuppression",
                    "autoGainControl",
                }
                and isinstance(v, (int, bool, float))
            }
        except (ValueError, TypeError):
            raise APIError(422, "capture_settings", "録音設定の形式が不正です。")
        data = await file.read(audio.MAX_UPLOAD + 1)
        await file.close()
        if not data or len(data) > audio.MAX_UPLOAD:
            raise APIError(413, "upload_limit", "空でない10 MiB以下の録音を使用してください。")
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
        audio_id, attempt_id = uid(), uid()
        path = audio_root / (audio_id + extension)
        path.write_bytes(data)
        try:
            metadata = await asyncio.to_thread(audio.inspect_upload, path)
            metadata.update({"capture": capture_data, "mime_type": media_type, "bytes": len(data)})
            with database.connect() as db:
                db.execute(
                    "INSERT INTO audio_files VALUES (?,?,?,?,NULL)",
                    (audio_id, session["id"], path.name, media_type),
                )
                db.execute(
                    "INSERT INTO attempts VALUES (?,?,?,?,?,?,?)",
                    (attempt_id, eid, audio_id, None, encode(metadata), None, now()),
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"attempt_id": attempt_id, "audio_id": audio_id, "audio_meta": metadata}

    @app.post("/v1/exercises/{exercise_id}/dictation", status_code=201)
    def check_dictation(exercise_id: UUID, body: DictationInput):
        identity = uid()
        with database.connect() as db:
            exercise = row(db, "exercises", str(exercise_id))
            if exercise["mode"] != "dictation":
                raise APIError(409, "wrong_mode", "dictationモードの練習を作成してください。")
            result = dictation.compare(exercise["reference_text"], body.typed_text)
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?,?)",
                (identity, str(exercise_id), None, body.typed_text, "{}", encode(result), now()),
            )
            return {"attempt_id": identity, "result": result}

    @app.post("/v1/attempts/{attempt_id}/assess")
    def assess(attempt_id: UUID, body: RequestInput):
        identity = str(attempt_id)
        with database.connect() as db:
            attempt = row(db, "attempts", identity)
            exercise = row(db, "exercises", attempt["exercise_id"])
            turn, session = owner_of_turn(db, exercise["turn_id"])

        def operation(db):
            if not attempt["audio_id"]:
                raise APIError(409, "no_audio", "dictationを発音評価することはできません。")
            record, path = saved_audio(db, attempt["audio_id"])
            if record["session_id"] != session["id"]:
                raise APIError(409, "session_mismatch", "音声のセッションが一致しません。")
            result = pronunciation.assess(
                path,
                exercise["reference_text"],
                settings.pronunciation_provider,
                json.loads(attempt["audio_meta_json"]),
            )
            db.execute("UPDATE attempts SET result_json=? WHERE id=?", (encode(result), identity))
            return result

        return idempotent(session["id"], f"assess:{identity}", body, operation)

    @app.get("/v1/speech-models")
    def speech_models():
        return providers.speech_catalog.candidates()

    @app.put("/v1/sessions/{session_id}/speech-model")
    def select_speech_model(session_id: UUID, body: SpeechModelInput):
        providers.speech_catalog.validate(body.model)
        with database.connect() as db:
            session = row(db, "sessions", str(session_id))
            saved = json.loads(session["settings_json"])
            flow = db.execute(
                "SELECT * FROM conversations WHERE id=?", (str(session_id),)
            ).fetchone()
            if flow and flow["status"] == "running":
                raise APIError(
                    409, "pause_required", "音声設定は会話を停止してから変更してください。"
                )
            if flow:
                db.execute(
                    "UPDATE conversations SET audio_id=NULL,playback_id=NULL,revision=revision+1 WHERE id=?",
                    (str(session_id),),
                )
                db.execute(
                    "UPDATE conversation_playbacks SET status='cancelled' WHERE session_id=? AND status='playing'",
                    (str(session_id),),
                )
            saved["speech_models"][body.role] = body.model
            db.execute(
                "UPDATE sessions SET settings_json=? WHERE id=?", (encode(saved), str(session_id))
            )
        return {"speech_models": saved["speech_models"]}

    @app.post("/v1/speech-preview")
    def preview_speech(body: SpeechPreviewInput):
        selection = providers.speech_catalog.validate(body.model)
        text = "Hello! Tell me about your research. What evidence supports your idea?"
        if selection == "configured":
            data = providers.speech(text, settings.tts_voice)
            if data is None:
                raise APIError(409, "tts_unavailable", "保存TTSは未設定です。")
        else:
            data = providers.speech_catalog.synthesize(text, selection)
        audio.validate_tts(data)
        return Response(data, media_type="audio/wav")

    @app.post("/v1/tts")
    def tts(body: TTSInput):
        source_id = str(body.source_id)
        with database.connect() as db:
            exercise_id = None
            if body.source_type == "question":
                turn, session = owner_of_turn(db, source_id)
                text = turn["question_en"]
            elif body.source_type == "coach_answer":
                message = row(db, "coach_messages", source_id)
                turn, session = owner_of_turn(db, message["turn_id"])
                text = json.loads(message["response_json"])["answer_en"]
            else:
                exercise = row(db, "exercises", source_id)
                exercise_id = source_id
                turn, session = owner_of_turn(db, exercise["turn_id"])
                text = exercise["reference_text"]
            if not text:
                raise APIError(409, "no_tts_text", "保存された英文がありません。")
            speech_role = (
                "coach_answer" if body.source_type == "conversation_reference" else body.source_type
            )
            selection = json.loads(session["settings_json"])["speech_models"][speech_role]
            providers.speech_catalog.validate(selection)
            if selection == "configured" and settings.tts_provider != "http":
                return {
                    "status": "unavailable",
                    "provider": settings.tts_provider,
                    "message": "保存TTSは未設定です。明示的なブラウザ読み上げを利用できます。",
                }
            voice = body.voice or settings.tts_voice
            cache_key = digest(
                encode([text, providers.speech_catalog.cache_identity(selection, voice)])
            )
            cached = db.execute(
                "SELECT id FROM audio_files WHERE session_id=? AND cache_key=?",
                (session["id"], cache_key),
            ).fetchone()
            if cached:
                assistance(db, turn["id"], exercise_id, "tts_requested")
                return {"status": "ok", "audio_id": cached["id"]}
        data = (
            providers.speech(text, voice)
            if selection == "configured"
            else providers.speech_catalog.synthesize(text, selection)
        )
        audio.validate_tts(data)
        audio_id = uid()
        path = audio_root / (audio_id + ".wav")
        path.write_bytes(data)
        try:
            with database.connect() as db:
                db.execute(
                    "INSERT INTO audio_files VALUES (?,?,?,?,?)",
                    (audio_id, session["id"], path.name, "audio/wav", cache_key),
                )
                assistance(db, turn["id"], exercise_id, "tts_requested")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"status": "ok", "audio_id": audio_id}

    @app.get("/v1/audio/{audio_id}")
    def get_audio(audio_id: UUID):
        with database.connect() as db:
            record, path = saved_audio(db, str(audio_id))
        return FileResponse(path, media_type=record["media_type"])

    @app.post("/v1/assistance", status_code=201)
    def save_assistance(body: AssistanceInput):
        tid, eid = str(body.turn_id), str(body.exercise_id) if body.exercise_id else None
        with database.connect() as db:
            row(db, "turns", tid)
            if eid and row(db, "exercises", eid)["turn_id"] != tid:
                raise APIError(409, "session_mismatch", "練習と質問の所属が一致しません。")
            assistance(db, tid, eid, body.kind)
        return {"saved": True}

    @app.get("/v1/sessions/{session_id}/export")
    def export_session(session_id: UUID):
        payload = {
            "schema_version": "1.0",
            "exported_at": now(),
            "providers": capabilities(),
            "session": snapshot(str(session_id)),
        }
        return JSONResponse(
            payload,
            headers={
                "Content-Disposition": f'attachment; filename="oral-defense-{session_id}.json"'
            },
        )

    @app.delete("/v1/sessions/{session_id}")
    def delete_session(session_id: UUID):
        with database.connect() as db:
            row(db, "sessions", str(session_id))
            files = list(
                db.execute(
                    "SELECT filename FROM audio_files WHERE session_id=?", (str(session_id),)
                )
            )
            # attempts must disappear before their audio foreign keys.
            db.execute(
                "DELETE FROM attempts WHERE exercise_id IN (SELECT e.id FROM exercises e JOIN turns t ON t.id=e.turn_id WHERE t.session_id=?)",
                (str(session_id),),
            )
            db.execute("DELETE FROM sessions WHERE id=?", (str(session_id),))
            for record in files:
                path = (audio_root / record["filename"]).resolve()
                if path.parent == audio_root:
                    path.unlink(missing_ok=True)
        return {"deleted": True}

    install_conversation(app, database, providers, tts)

    static_dir = ROOT / "frontend/dist"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()
