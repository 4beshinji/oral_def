import asyncio
import json
import sqlite3
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from . import audio, contexts, dictation, pronunciation
from .asr import ASR
from .config import ROOT, Settings
from .conversation import install_conversation
from .db import (
    Database,
    MigrationError,
    backup_database,
    digest,
    digest_bytes,
    encode,
    now,
    purge_session,
    reconcile_audio_files,
    row,
    turn_row,
    turns_for,
    uid,
    verify_backup,
)
from .documents import install_documents
from .errors import APIError
from .free_speech import install_free_speech
from .generation import generate_text, save_generation
from .history import child_page, hydrate_turn, install_history
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
from .sharing import export_shared_session


def create_app(settings=None):
    settings = settings or Settings()
    providers = Providers(settings)
    asr = ASR(settings)
    database = Database(settings.data_dir, speech_defaults=providers.speech_catalog.defaults())
    app = FastAPI(title="Oral Defense Drill", version="0.4.0")
    app.state.database = database
    app.state.providers = providers
    app.state.asr = asr
    audio_root = (settings.data_dir / "audio").resolve()

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
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def owner_of_turn(db, turn_id):
        turn = turn_row(db, turn_id)
        session = row(db, "sessions", turn["session_id"])
        if session["deletion_status"] != "active":
            raise APIError(409, "session_deleting", "削除中のセッションです。")
        return turn, session

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
        if record["save_status"] != "ready" or path.parent != audio_root or not path.is_file():
            raise APIError(404, "audio_not_found", "音声が見つかりません。")
        return record, path

    def assistance(db, turn_id, exercise_id, kind):
        turn = row(db, "turns", turn_id)
        db.execute(
            "INSERT INTO assistance (id,session_id,turn_id,exercise_id,kind,created_at) VALUES (?,?,?,?,?,?)",
            (uid(), turn["session_id"], turn_id, exercise_id, kind, now()),
        )

    def idempotent(session_id, action, body, operation):
        payload = body.model_dump(mode="json")
        request_id = payload["request_id"]
        payload_hash = digest(encode(payload))

        def replay(existing):
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

        with database.connect() as db:
            session = row(db, "sessions", session_id)
            if session["deletion_status"] != "active":
                raise APIError(409, "session_deleting", "削除中のセッションです。")
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
                return replay(existing)
            try:
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
            except sqlite3.IntegrityError:
                # A concurrent identical request won the insert; replay it.
                existing = db.execute(
                    "SELECT * FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
                if existing is None:
                    raise
                return replay(existing)
        try:
            with database.connect() as db:
                result = operation(db)
                committed = db.execute(
                    "UPDATE requests SET status='done',result_json=? WHERE request_id=? AND status='processing'",
                    (encode(result), request_id),
                )
                if committed.rowcount != 1:
                    raise APIError(409, "stale_operation", "中断された要求の結果は保存できません。")
            return result
        except sqlite3.IntegrityError as exc:
            with database.connect() as db:
                db.execute(
                    "UPDATE requests SET status='failed' WHERE request_id=? AND status='processing'",
                    (request_id,),
                )
            raise APIError(
                409, "conflict", "同時操作と競合しました。もう一度お試しください。", True
            ) from exc
        except Exception:
            with database.connect() as db:
                db.execute(
                    "UPDATE requests SET status='failed' WHERE request_id=? AND status='processing'",
                    (request_id,),
                )
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
            "asr": {
                "provider": settings.asr_provider,
                "available": asr.available(),
                "model_id": "faster-whisper-base.en" if asr.available() else None,
                "local": True,
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
                "INSERT INTO sessions (id,created_at,pack_snapshot_json,pack_hash,research_brief,scenario,settings_json,status) VALUES (?,?,?,?,?,?,?,?)",
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
                            "prepare_documents": body.prepare_documents,
                        }
                    ),
                    "active",
                ),
            )
            if body.mode in {"shadowing", "free_speech"}:
                db.execute(
                    "INSERT INTO conversations (id,mode) VALUES (?,?)", (session_id, body.mode)
                )
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

    def snapshot(session_id, turn_limit=50):
        with database.read_snapshot() as db:
            session = row(db, "sessions", session_id)
            session["pack_snapshot"] = json.loads(session.pop("pack_snapshot_json"))
            session["settings"] = json.loads(session.pop("settings_json"))
            flow = db.execute("SELECT * FROM conversations WHERE id=?", (session_id,)).fetchone()
            session["conversation"] = dict(flow) if flow else None
            total = db.execute(
                "SELECT count(*) FROM turns WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            if turn_limit is None:
                ids = [
                    record["id"]
                    for record in db.execute(
                        "SELECT id FROM turns WHERE session_id=? ORDER BY ordinal", (session_id,)
                    )
                ]
            else:
                ids = [
                    record["id"]
                    for record in db.execute(
                        "SELECT id FROM turns WHERE session_id=? ORDER BY ordinal DESC LIMIT ?",
                        (session_id, turn_limit),
                    )
                ][::-1]
            session["turns_total"] = total
            session["turns_has_more"] = len(ids) < total
            session["turns_before"] = (
                db.execute("SELECT MIN(ordinal) FROM turns WHERE id=?", (ids[0],)).fetchone()[0]
                if ids and session["turns_has_more"]
                else None
            )
            session["confirmed_turns_total"] = db.execute(
                """SELECT count(*) FROM turns t LEFT JOIN turn_submissions s ON s.turn_id=t.id
                   WHERE t.session_id=? AND COALESCE(s.answer_text,t.confirmed_answer_en) IS NOT NULL""",
                (session_id,),
            ).fetchone()[0]
            session["turns"] = [hydrate_turn(db, turn_row(db, tid)) for tid in ids]
            playbacks = child_page(db, "conversation_playbacks", "session_id", session_id, limit=50)
            session["playbacks"] = playbacks["items"]
            session["playbacks_total"] = playbacks["total"]
            for playback in session["playbacks"]:
                playback["tts_settings"] = json.loads(playback.pop("tts_settings_json"))
            requests = child_page(db, "requests", "session_id", session_id, limit=50)
            session["requests_total"] = requests["total"]
            session["requests"] = [
                {
                    **{key: item[key] for key in ("request_id", "action", "status", "created_at")},
                    "provider_configuration": json.loads(item["payload_json"]).get(
                        "provider_configuration"
                    ),
                }
                for item in requests["items"]
            ]
            session["documents"] = []
            for document in db.execute(
                "SELECT * FROM session_documents WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ):
                info = dict(document)
                info["extractor_config"] = json.loads(info.pop("extractor_config_json") or "null")
                info["segments"] = [
                    {**dict(segment), "location": json.loads(segment["location_json"] or "null")}
                    for segment in db.execute(
                        "SELECT * FROM document_segments WHERE document_id=? ORDER BY ordinal",
                        (info["id"],),
                    )
                ]
                for segment in info["segments"]:
                    segment.pop("location_json")
                session["documents"].append(info)
            manifest = db.execute(
                "SELECT * FROM session_pack_manifests WHERE session_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            session["pack_manifest"] = (
                {
                    **dict(manifest),
                    "pack": json.loads(manifest["pack_json"]),
                    "adopted": json.loads(manifest["adopted_json"]),
                }
                if manifest
                else None
            )
            return session

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: UUID, turns: int = 50):
        return snapshot(str(session_id), turn_limit=max(1, min(turns, 200)))

    @app.get("/v1/sessions/{session_id}/turns")
    def list_turns(session_id: UUID, before: int | None = None, limit: int = 20):
        sid = str(session_id)
        size = max(1, min(limit, 100))
        with database.read_snapshot() as db:
            row(db, "sessions", sid)
            if before is None:
                records = db.execute(
                    "SELECT id FROM turns WHERE session_id=? ORDER BY ordinal DESC LIMIT ?",
                    (sid, size + 1),
                ).fetchall()
            else:
                records = db.execute(
                    "SELECT id FROM turns WHERE session_id=? AND ordinal<? ORDER BY ordinal DESC LIMIT ?",
                    (sid, before, size + 1),
                ).fetchall()
            items = [hydrate_turn(db, turn_row(db, record["id"])) for record in records[:size]][
                ::-1
            ]
        next_before = items[0]["ordinal"] if len(records) > size else None
        return {"turns": items, "next_before": next_before}

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
            result, provenance = generate_text(
                providers, "examiner", messages, session_id=sid, target=target
            )
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
            save_generation(db, "turns", turn_id, provenance)
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
            turn, session = owner_of_turn(db, tid)
            flow = db.execute("SELECT * FROM conversations WHERE id=?", (session["id"],)).fetchone()
            if flow is None:
                active_turn(db, tid)
            elif flow["status"] == "ended":
                raise APIError(409, "ended", "終了済みの会話です。")
            history = [
                dict(h)
                for h in db.execute(
                    """SELECT c.* FROM coach_messages c JOIN turns t ON t.id=c.turn_id
                       WHERE c.session_id=? AND t.ordinal<=? ORDER BY c.rowid DESC LIMIT 12""",
                    (session["id"], turn["ordinal"]),
                )
            ][::-1]
            if flow is None and len(history) >= 12:
                raise APIError(409, "coach_limit", "Coachは1セッション12往復までです。")
            saved = json.loads(session["settings_json"])
            messages = contexts.coach_messages(
                pack=json.loads(session["pack_snapshot_json"]),
                research_brief=session["research_brief"],
                turns=[t for t in turns_for(db, session["id"]) if t["ordinal"] <= turn["ordinal"]],
                current_question=turn["question_en"],
                history=history,
                level=body.level,
                user_note=body.user_note,
                draft=body.draft,
                scenario=session["scenario"],
                settings={k: saved[k] for k in ("language_level", "technical_depth", "strictness")},
            )
            target = providers.catalog.from_saved(saved["role_models"][body.level])
            result, provenance = generate_text(
                providers, body.level, messages, session_id=session["id"], target=target
            )
            db.execute("BEGIN IMMEDIATE")
            owner_of_turn(db, tid)
            live_request = db.execute(
                "SELECT status FROM requests WHERE request_id=?", (str(body.request_id),)
            ).fetchone()
            live_flow = db.execute(
                "SELECT status FROM conversations WHERE id=?", (session["id"],)
            ).fetchone()
            if (
                not live_request
                or live_request["status"] != "processing"
                or (live_flow and live_flow["status"] == "ended")
            ):
                raise APIError(409, "stale_operation", "中断・終了した支援要求です。")
            message_id = uid()
            db.execute(
                "INSERT INTO coach_messages (id,session_id,turn_id,level,user_note,draft,response_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
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
            save_generation(db, "coach_messages", message_id, provenance)
            assistance(db, tid, None, "coach_" + body.level)
            return {"id": message_id, "turn_id": tid, "response": result}

        return idempotent(session["id"], f"coach:{tid}", body, operation)

    @app.post("/v1/turns/{turn_id}/exercises", status_code=201)
    def create_exercise(turn_id: UUID, body: ExerciseInput):
        tid, identity = str(turn_id), uid()
        with database.connect() as db:
            _, session = active_turn(db, tid)
            db.execute(
                "INSERT INTO exercises (id,session_id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    identity,
                    session["id"],
                    tid,
                    body.mode,
                    body.text,
                    digest(body.text),
                    body.origin,
                    now(),
                ),
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
            exercise = db.execute(
                "SELECT id FROM exercises WHERE turn_id=? AND reference_text=? ORDER BY created_at",
                (tid, body.answer_en),
            ).fetchone()
            if exercise is None:
                raise APIError(
                    409,
                    "reference_not_fixed",
                    "この回答文を先に練習用の参照文として固定してください。",
                )
            db.execute(
                "INSERT INTO turn_submissions (turn_id,session_id,answer_text,submitted_via,source_exercise_id,source_playback_id,committed_at,provenance_status) VALUES (?,?,?,?,?,NULL,?,?)",
                (
                    tid,
                    session["id"],
                    body.answer_en,
                    "confirmed_reference",
                    exercise["id"],
                    now(),
                    "exact",
                ),
            )
            db.execute(
                "UPDATE turns SET unable_to_answer=? WHERE id=?", (body.unable_to_answer, tid)
            )
            if turn["ordinal"] == 6:
                db.execute("UPDATE sessions SET status='completed' WHERE id=?", (session["id"],))
            return turn_row(db, tid)

        return idempotent(session["id"], f"confirm:{tid}", body, operation)

    @app.post("/v1/exercises/{exercise_id}/audio", status_code=201)
    async def upload_audio(
        exercise_id: UUID,
        file: UploadFile = File(...),
        capture: str = Form("{}"),
        client_attempt_key: str | None = Form(None),
        input_kind: str = Form("unknown"),
    ):
        eid = str(exercise_id)
        if input_kind not in {"shadowing_overlap", "isolated_repeat", "unknown"}:
            raise APIError(422, "input_kind", "録音種別が不正です。")
        if client_attempt_key is not None:
            client_attempt_key = client_attempt_key.strip()
            if not client_attempt_key or len(client_attempt_key) > 100:
                raise APIError(422, "attempt_key", "録音キーが不正です。")
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
        content_hash = digest_bytes(data)
        if client_attempt_key:
            with database.connect() as db:
                existing = db.execute(
                    """SELECT a.id AS attempt_id,a.audio_id,a.exercise_id,a.audio_meta_json,
                              af.content_hash
                       FROM attempts a LEFT JOIN audio_files af ON af.id=a.audio_id
                       WHERE a.session_id=? AND a.client_attempt_key=?""",
                    (session["id"], client_attempt_key),
                ).fetchone()
                if existing:
                    if (
                        existing["exercise_id"] != eid
                        or existing["audio_id"] is None
                        or existing["content_hash"] != content_hash
                    ):
                        raise APIError(
                            409,
                            "attempt_key_conflict",
                            "同じ録音キーが別の内容で使用されています。",
                        )
                    return {
                        "attempt_id": existing["attempt_id"],
                        "audio_id": existing["audio_id"],
                        "audio_meta": json.loads(existing["audio_meta_json"]),
                    }
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
        temp_path = audio_root / (audio_id + ".part" + extension)
        path = audio_root / (audio_id + extension)
        try:
            temp_path.write_bytes(data)
            media = await asyncio.to_thread(providers.work.run, audio.inspect_upload, temp_path)
            metadata = {
                **media,
                "capture": capture_data,
                "mime_type": media_type,
                "bytes": len(data),
            }
            temp_path.replace(path)
            with database.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                session_row = db.execute(
                    "SELECT deletion_status FROM sessions WHERE id=?", (session["id"],)
                ).fetchone()
                if session_row is None or session_row["deletion_status"] != "active":
                    raise APIError(409, "session_deleting", "削除中のセッションです。")
                try:
                    db.execute(
                        "INSERT INTO audio_files (id,session_id,filename,media_type,cache_key,kind,storage_key,content_hash,media_json,save_status) VALUES (?,?,?,?,NULL,?,?,?,?,?)",
                        (
                            audio_id,
                            session["id"],
                            path.name,
                            media_type,
                            "recording",
                            path.name,
                            content_hash,
                            encode(media),
                            "ready",
                        ),
                    )
                    db.execute(
                        "INSERT INTO attempts (id,session_id,exercise_id,audio_id,dictation_text,audio_meta_json,result_json,created_at,client_attempt_key,input_kind,capture_requested_json,capture_actual_json,file_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            attempt_id,
                            session["id"],
                            eid,
                            audio_id,
                            None,
                            encode(metadata),
                            None,
                            now(),
                            client_attempt_key,
                            input_kind,
                            encode(capture_data),
                            encode(media),
                            "ready",
                        ),
                    )
                except sqlite3.IntegrityError:
                    db.rollback()
                    existing = db.execute(
                        """SELECT a.id AS attempt_id,a.audio_id,a.audio_meta_json,af.content_hash,
                                  a.exercise_id
                           FROM attempts a LEFT JOIN audio_files af ON af.id=a.audio_id
                           WHERE a.session_id=? AND a.client_attempt_key=?""",
                        (session["id"], client_attempt_key),
                    ).fetchone()
                    if (
                        existing is not None
                        and existing["exercise_id"] == eid
                        and existing["content_hash"] == content_hash
                    ):
                        temp_path.unlink(missing_ok=True)
                        path.unlink(missing_ok=True)
                        return {
                            "attempt_id": existing["attempt_id"],
                            "audio_id": existing["audio_id"],
                            "audio_meta": json.loads(existing["audio_meta_json"]),
                        }
                    raise
        except Exception:
            temp_path.unlink(missing_ok=True)
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
            _, session = owner_of_turn(db, exercise["turn_id"])
            result = dictation.compare(exercise["reference_text"], body.typed_text)
            db.execute(
                "INSERT INTO attempts (id,session_id,exercise_id,audio_id,dictation_text,audio_meta_json,result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    identity,
                    session["id"],
                    str(exercise_id),
                    None,
                    body.typed_text,
                    "{}",
                    encode(result),
                    now(),
                ),
            )
            return {"attempt_id": identity, "result": result}

    def assessment_evidence(result, input_kind):
        if input_kind == "shadowing_overlap":
            return "insufficient_evidence"
        if result.get("status") == "unavailable":
            return "unavailable"
        return "uncalibrated"

    @app.post("/v1/attempts/{attempt_id}/assess")
    def assess(attempt_id: UUID, body: RequestInput):
        identity = str(attempt_id)
        with database.connect() as db:
            attempt = row(db, "attempts", identity)
            exercise = row(db, "exercises", attempt["exercise_id"])
            turn, session = owner_of_turn(db, exercise["turn_id"])
            if not attempt["audio_id"]:
                raise APIError(409, "no_audio", "dictationを発音評価することはできません。")
            record, path = saved_audio(db, attempt["audio_id"])
            if record["session_id"] != session["id"]:
                raise APIError(409, "session_mismatch", "音声のセッションが一致しません。")
        payload = body.model_dump(mode="json")
        request_id = str(payload["request_id"])
        payload_hash = digest(encode(payload))
        action = f"assess:{identity}"
        run_id = uid()
        config_snapshot = {
            "provider": settings.pronunciation_provider,
            "provider_version": None,
            "input_kind": attempt["input_kind"],
            "echo_cancellation": json.loads(attempt["capture_requested_json"] or "{}").get(
                "echoCancellation"
            ),
        }
        with database.connect() as db:
            existing = db.execute(
                "SELECT * FROM requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing:
                if (
                    existing["action"] != action
                    or existing["payload_hash"] != payload_hash
                    or existing["session_id"] != session["id"]
                ):
                    raise APIError(
                        409, "request_conflict", "このrequest_idは別の入力に使用されています。"
                    )
                if existing["status"] == "done":
                    return json.loads(existing["result_json"])
                raise APIError(
                    409,
                    existing["status"],
                    "処理中または中断済みの要求です。結果不明の再試行には新しいIDが必要です。",
                    True,
                )
            try:
                db.execute(
                    "INSERT INTO requests (request_id,session_id,action,payload_hash,payload_json,status,created_at) VALUES (?,?,?,?,?,'processing',?)",
                    (request_id, session["id"], action, payload_hash, encode(payload), now()),
                )
                db.execute(
                    "INSERT INTO assessment_runs (id,attempt_id,session_id,input_kind,execution_status,evidence_status,provider,model_version,config_json,reference_hash,result_json,error_code,error_message,request_id,started_at,finished_at,created_at) VALUES (?,?,?,?,'running','pending',?,?,?,?,NULL,NULL,NULL,?,?,NULL,?)",
                    (
                        run_id,
                        identity,
                        session["id"],
                        attempt["input_kind"],
                        settings.pronunciation_provider,
                        None,
                        encode(config_snapshot),
                        exercise["reference_hash"],
                        request_id,
                        now(),
                        now(),
                    ),
                )
            except sqlite3.IntegrityError:
                db.rollback()
                concurrent = db.execute(
                    "SELECT * FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
                if concurrent is None:
                    raise
                if concurrent["status"] == "done":
                    return json.loads(concurrent["result_json"])
                raise APIError(
                    409,
                    concurrent["status"],
                    "同じ評価要求が処理中です。結果を待つか新しいIDを使用してください。",
                    True,
                )
        try:
            result = providers.work.run(
                pronunciation.assess,
                path,
                exercise["reference_text"],
                settings.pronunciation_provider,
                json.loads(attempt["audio_meta_json"]),
            )
        except Exception as exc:
            code = getattr(exc, "body", {}).get("code", type(exc).__name__)
            with database.connect() as db:
                db.execute(
                    "UPDATE assessment_runs SET execution_status='failed',evidence_status=NULL,error_code=?,error_message=?,finished_at=? WHERE id=?",
                    (code, "評価器の実行に失敗しました。", now(), run_id),
                )
                db.execute("UPDATE requests SET status='failed' WHERE request_id=?", (request_id,))
            raise
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session_row = db.execute(
                "SELECT deletion_status FROM sessions WHERE id=?", (session["id"],)
            ).fetchone()
            if session_row is None or session_row["deletion_status"] != "active":
                db.execute(
                    "UPDATE assessment_runs SET execution_status='interrupted',finished_at=? WHERE id=?",
                    (now(), run_id),
                )
                db.execute("UPDATE requests SET status='failed' WHERE request_id=?", (request_id,))
                raise APIError(409, "session_deleting", "削除中のセッションです。")
            db.execute(
                "UPDATE assessment_runs SET execution_status='succeeded',evidence_status=?,result_json=?,finished_at=? WHERE id=?",
                (assessment_evidence(result, attempt["input_kind"]), encode(result), now(), run_id),
            )
            db.execute(
                "UPDATE requests SET status='done',result_json=? WHERE request_id=?",
                (encode(result), request_id),
            )
        return result

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
            data = providers.work.run(providers.speech, text, settings.tts_voice)
            if data is None:
                raise APIError(409, "tts_unavailable", "保存TTSは未設定です。")
        else:
            data = providers.work.run(providers.speech_catalog.synthesize, text, selection)
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
        provenance = {
            "schema_version": "1.0",
            "kind": "speech",
            "selection": selection,
            "configuration": providers.speech_catalog.cache_identity(selection, voice),
            "input_hash": digest(text),
            "started_at": now(),
        }
        data = (
            providers.work.run(providers.speech, text, voice)
            if selection == "configured"
            else providers.work.run(providers.speech_catalog.synthesize, text, selection)
        )
        provenance["finished_at"] = now()
        provenance["output_hash"] = digest_bytes(data)
        audio.validate_tts(data)
        audio_id = uid()
        path = audio_root / (audio_id + ".wav")
        path.write_bytes(data)
        try:
            with database.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if row(db, "sessions", session["id"])["deletion_status"] != "active":
                    raise APIError(409, "session_deleting", "削除中のセッションです。")
                db.execute(
                    "INSERT INTO audio_files (id,session_id,filename,media_type,cache_key,kind,storage_key,content_hash,media_json,save_status) VALUES (?,?,?,?,?,?,?,?,NULL,'ready')",
                    (
                        audio_id,
                        session["id"],
                        path.name,
                        "audio/wav",
                        cache_key,
                        "tts",
                        path.name,
                        digest_bytes(data),
                    ),
                )
                save_generation(db, "audio_files", audio_id, encode(provenance))
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
        return JSONResponse(
            export_shared_session(database, str(session_id)),
            headers={
                "Content-Disposition": f'attachment; filename="oral-defense-{session_id}.json"'
            },
        )

    @app.delete("/v1/sessions/{session_id}")
    def delete_session(session_id: UUID):
        with database.connect() as db:
            row(db, "sessions", str(session_id))
        purge_session(database, str(session_id))
        return {"deleted": True}

    @app.get("/v1/maintenance/audio")
    def audio_maintenance():
        return reconcile_audio_files(database, dry_run=True)

    @app.post("/v1/maintenance/backup", status_code=201)
    def create_backup():
        from datetime import datetime, timezone

        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = settings.data_dir / "backups" / name
        try:
            manifest = backup_database(database, destination)
            report = verify_backup(destination)
        except (MigrationError, OSError, sqlite3.Error):
            raise APIError(
                503,
                "backup_incomplete",
                "backupが完了しませんでした。保存・削除処理や空き容量を確認して再実行してください。",
                True,
            ) from None
        if not report["ok"]:
            raise APIError(503, "backup_invalid", "backupの整合性検証に失敗しました。", True)
        return {"name": name, "manifest": manifest, "verify": report}

    @app.get("/v1/maintenance/backup/{name}")
    def check_backup(name: str):
        if "/" in name or "\\" in name or ".." in name:
            raise APIError(422, "invalid_name", "backup名が不正です。")
        return verify_backup(settings.data_dir / "backups" / name)

    install_conversation(app, database, providers, tts)
    install_free_speech(app, database, providers, asr)
    install_documents(app, database, settings)
    install_history(app, database)

    static_dir = ROOT / "frontend/dist"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()
