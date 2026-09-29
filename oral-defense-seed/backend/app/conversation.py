"""Server-owned shadowing progression; audio capture is an independent exercise."""

import json
from typing import Literal
from uuid import UUID

from pydantic import Field

from . import contexts
from .db import digest, encode, now, row, turn_row, turns_for, uid
from .errors import APIError
from .generation import generate_text, save_generation
from .question_recovery import question_keys, recovery_question
from .schemas import Input, RequestInput, TTSInput


class Control(Input):
    action: Literal["resume", "pause", "end"]
    revision: int | None = Field(default=None, ge=0)


class ModeChange(Input):
    mode: Literal["shadowing", "free_speech"]
    revision: int = Field(ge=0)


class Step(Input):
    revision: int = Field(ge=0)
    route: Literal["saved", "browser"] = "saved"


class Start(Step):
    rate: float = Field(default=0.9, ge=0.5, le=1.5)


class Reference(Input):
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    revision: int | None = Field(default=None, ge=0)
    turn_id: UUID | None = None


class AdoptReference(Input):
    revision: int = Field(ge=0)
    turn_id: UUID
    reference_id: UUID | None
    message_id: UUID


class Ended(RequestInput):
    playback_id: UUID


def install_conversation(app, database, providers, tts):
    def flow(db, sid):
        return row(db, "conversations", sid)

    def current(db, sid, revision):
        state = flow(db, sid)
        if row(db, "sessions", sid)["deletion_status"] != "active":
            raise APIError(409, "session_deleting", "削除中のセッションです。")
        if state["status"] != "running" or state["revision"] != revision:
            raise APIError(
                409, "stale_operation", "停止済み、または古い会話操作です。再開してください。"
            )
        return state

    def invalidate(db, sid):
        db.execute(
            "UPDATE conversation_playbacks SET status='cancelled' WHERE session_id=? AND status='playing'",
            (sid,),
        )

    @app.get("/v1/sessions/{session_id}/conversation")
    def get_flow(session_id: UUID):
        with database.connect() as db:
            return flow(db, str(session_id))

    @app.post("/v1/sessions/{session_id}/conversation/control")
    def control(session_id: UUID, body: Control):
        sid = str(session_id)
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = flow(db, sid)
            if body.revision is not None and state["revision"] != body.revision:
                raise APIError(
                    409, "stale_operation", "会話の状態が変わりました。表示を更新してください。"
                )
            if state["status"] == "ended":
                if body.action == "end":
                    return state
                raise APIError(409, "ended", "終了済みです。新しいセッションを開始してください。")
            if body.action == "resume":
                session = row(db, "sessions", sid)
                saved_settings = json.loads(session["settings_json"])
                if saved_settings.get("prepare_documents"):
                    manifest = db.execute(
                        "SELECT 1 FROM session_pack_manifests WHERE session_id=? AND pack_hash=? LIMIT 1",
                        (sid, session["pack_hash"]),
                    ).fetchone()
                    if manifest is None:
                        raise APIError(
                            409, "pack_not_prepared", "資料を確認してPackを確定してください。"
                        )
                    if not saved_settings.get("pack_started"):
                        saved_settings["pack_started"] = True
                        db.execute(
                            "UPDATE sessions SET settings_json=? WHERE id=?",
                            (encode(saved_settings), sid),
                        )
            invalidate(db, sid)
            status = {"resume": "running", "pause": "paused", "end": "ended"}[body.action]
            db.execute(
                "UPDATE conversations SET status=?,revision=revision+1,playback_id=NULL WHERE id=?",
                (status, sid),
            )
            return flow(db, sid)

    @app.post("/v1/sessions/{session_id}/conversation/mode")
    def change_mode(session_id: UUID, body: ModeChange):
        sid = str(session_id)
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = flow(db, sid)
            if row(db, "sessions", sid)["deletion_status"] != "active":
                raise APIError(409, "session_deleting", "削除中のセッションです。")
            if state["status"] != "paused" or state["revision"] != body.revision:
                raise APIError(
                    409, "pause_required", "会話を停止してからモードを変更してください。"
                )
            if state["mode"] == body.mode:
                return state
            invalidate(db, sid)
            stage = state["stage"]
            if stage in {"coach_generation", "model_playback", "free_speech_input"}:
                stage = "free_speech_input" if body.mode == "free_speech" else "coach_generation"
            db.execute(
                """UPDATE conversations SET mode=?,stage=?,reference_id=NULL,
                   audio_id=NULL,playback_id=NULL,revision=revision+1 WHERE id=?""",
                (body.mode, stage, sid),
            )
            return flow(db, sid)

    @app.post("/v1/sessions/{session_id}/conversation/reference")
    def revise_reference(session_id: UUID, body: Reference):
        sid = str(session_id)
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = flow(db, sid)
            if row(db, "sessions", sid)["deletion_status"] != "active":
                raise APIError(409, "session_deleting", "削除中のセッションです。")
            if (body.revision is not None and body.revision != state["revision"]) or (
                body.turn_id is not None and str(body.turn_id) != state["turn_id"]
            ):
                raise APIError(
                    409, "stale_operation", "古いお手本への操作です。表示を更新してください。"
                )
            if state["status"] != "paused" or state["stage"] not in {
                "coach_generation",
                "model_playback",
            }:
                raise APIError(409, "pause_required", "未確定のお手本を停止中に編集してください。")
            turn = turn_row(db, state["turn_id"])
            if turn["confirmed_answer_en"] is not None:
                raise APIError(409, "already_confirmed", "確定済みの参照文は変更できません。")
            invalidate(db, sid)
            eid = None
            stage = "coach_generation"
            if body.text is not None:
                eid = uid()
                db.execute(
                    "INSERT INTO exercises (id,session_id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        eid,
                        turn["session_id"],
                        turn["id"],
                        "listen_repeat",
                        body.text,
                        digest(body.text),
                        "manual",
                        now(),
                    ),
                )
                stage = "model_playback"
            db.execute(
                "UPDATE conversations SET stage=?,reference_id=?,audio_id=NULL,playback_id=NULL,revision=revision+1 WHERE id=?",
                (stage, eid, sid),
            )
            return flow(db, sid)

    @app.post("/v1/sessions/{session_id}/conversation/adopt")
    def adopt_reference(session_id: UUID, body: AdoptReference):
        sid = str(session_id)
        with database.write_transaction() as db:
            state = flow(db, sid)
            if row(db, "sessions", sid)["deletion_status"] != "active":
                raise APIError(409, "session_deleting", "削除中のセッションです。")
            if state["status"] != "paused" or state["stage"] not in {
                "coach_generation",
                "model_playback",
            }:
                raise APIError(409, "pause_required", "未確定のお手本を停止中に採用してください。")
            if (
                state["revision"] != body.revision
                or state["turn_id"] != str(body.turn_id)
                or state["reference_id"] != (str(body.reference_id) if body.reference_id else None)
            ):
                raise APIError(
                    409, "stale_operation", "古いお手本への操作です。表示を更新してください。"
                )
            turn = turn_row(db, state["turn_id"])
            if turn["confirmed_answer_en"] is not None:
                raise APIError(409, "already_confirmed", "返答は確定済みです。")
            message = row(db, "coach_messages", str(body.message_id))
            if message["session_id"] != sid or message["turn_id"] != turn["id"]:
                raise APIError(409, "message_mismatch", "このturnのCoach案ではありません。")
            text = json.loads(message["response_json"]).get("answer_en")
            if (
                message["level"] not in {"full_answer", "revision"}
                or not text
                or "[" in text
                or "]" in text
            ):
                raise APIError(409, "unreadable_reference", "採用できる英文がありません。")
            eid = uid()
            db.execute(
                "INSERT INTO exercises (id,session_id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at,generation_json) VALUES (?,?,?,'listen_repeat',?,?,'coach',?,?)",
                (eid, sid, turn["id"], text, digest(text), now(), message["generation_json"]),
            )
            invalidate(db, sid)
            db.execute(
                "UPDATE conversations SET reference_id=?,stage='model_playback',audio_id=NULL,playback_id=NULL,revision=revision+1 WHERE id=?",
                (eid, sid),
            )
            return flow(db, sid)

    @app.post("/v1/sessions/{session_id}/conversation/step")
    def step(session_id: UUID, body: Step):
        sid = str(session_id)
        # Never hold a database write transaction while waiting on a provider.
        # Pause/end can invalidate this revision even during slow generation.
        with database.connect() as db:
            state = current(db, sid, body.revision)
            session = row(db, "sessions", sid)
            saved = json.loads(session["settings_json"])
            turns = turns_for(db, sid)
            history = [
                dict(h)
                for h in db.execute(
                    "SELECT * FROM coach_messages WHERE session_id=? ORDER BY created_at DESC LIMIT 12",
                    (sid,),
                )
            ][::-1]
        stage = state["stage"]
        if stage == "free_speech_input":
            return state
        if stage in {"question_playback", "model_playback"}:
            if body.route == "browser" or state["audio_id"]:
                return state
            source_type = "question" if stage == "question_playback" else "conversation_reference"
            source_id = state["turn_id"] if source_type == "question" else state["reference_id"]
            result = tts(TTSInput(source_type=source_type, source_id=source_id))
            if not result.get("audio_id"):
                raise APIError(
                    409, "tts_unavailable", "音声を設定するか、ブラウザ読み上げを選択してください。"
                )
            with database.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current(db, sid, body.revision)
                db.execute(
                    "UPDATE conversations SET audio_id=?,revision=revision+1 WHERE id=?",
                    (result["audio_id"], sid),
                )
                return flow(db, sid)
        if stage == "question_generation":
            count = turns[-1]["follow_up_count"] if turns else 0
            messages = contexts.examiner_messages(
                pack=json.loads(session["pack_snapshot_json"]),
                research_brief=session["research_brief"],
                turns=turns,
                scenario=session["scenario"],
                settings=saved,
                follow_up_count=count,
            )
            role = "examiner"
        else:
            messages = contexts.coach_messages(
                pack=json.loads(session["pack_snapshot_json"]),
                research_brief=session["research_brief"],
                turns=turns,
                current_question=turns[-1]["question_en"],
                history=history,
                level="full_answer",
                user_note="",
                draft="",
                scenario=session["scenario"],
                settings={k: saved[k] for k in ("language_level", "technical_depth", "strictness")},
            )
            role = "full_answer"
        target = providers.catalog.from_saved(saved["role_models"][role])
        result, provenance = generate_text(providers, role, messages, session_id=sid, target=target)
        if stage == "question_generation":
            recent = set().union(*(question_keys(turn["question_en"]) for turn in turns[-12:]))
            if question_keys(result["question_en"]).intersection(recent):
                with database.connect() as db:
                    current(db, sid, body.revision)
                retry_payload = json.loads(messages[-1]["content"])
                retry_payload["rejected_question"] = result["question_en"]
                retry_payload["must_change_angle"] = True
                retry_messages = [
                    *messages[:-1],
                    {"role": "user", "content": json.dumps(retry_payload, ensure_ascii=False)},
                ]
                result, provenance = generate_text(
                    providers, role, retry_messages, session_id=sid, target=target
                )
                if question_keys(result["question_en"]).intersection(recent):
                    fallback = recovery_question(
                        session["scenario"], [turn["question_en"] for turn in turns]
                    )
                    if fallback is None:
                        raise APIError(
                            502,
                            "repeated_question",
                            "相手が同じ質問を繰り返しました。再試行してください。",
                            True,
                        )
                    metadata = json.loads(provenance)
                    metadata["model_output_hash"] = metadata["output_hash"]
                    metadata["output_hash"] = digest(encode(fallback))
                    metadata["recovery"] = "deterministic_general_question_after_repeated_output"
                    provenance = encode(metadata)
                    result = fallback
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current(db, sid, body.revision)
            if stage == "question_generation":
                if result["follow_up"] and (
                    not turns or count >= 2 or turns[-1]["unable_to_answer"]
                ):
                    raise APIError(
                        502,
                        "follow_up_limit",
                        "相手生成が追質問の上限を超えました。再試行できます。",
                        True,
                    )
                tid = uid()
                db.execute(
                    "INSERT INTO turns (id,session_id,ordinal,question_en,basis_note,follow_up_count) VALUES (?,?,?,?,?,?)",
                    (
                        tid,
                        sid,
                        len(turns) + 1,
                        result["question_en"],
                        result["basis_note"],
                        count + 1 if result["follow_up"] else 0,
                    ),
                )
                db.execute(
                    "UPDATE conversations SET turn_id=?,reference_id=NULL,audio_id=NULL,stage='question_playback',revision=revision+1 WHERE id=?",
                    (tid, sid),
                )
            else:
                answer = result.get("answer_en")
                if not answer or not answer.strip() or "[" in answer or "]" in answer:
                    raise APIError(
                        502,
                        "unreadable_reference",
                        "読み上げ可能な返答がありません。再生成してください。",
                        True,
                    )
                tid, eid, mid = state["turn_id"], uid(), uid()
                db.execute(
                    "INSERT INTO coach_messages (id,session_id,turn_id,level,user_note,draft,response_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (mid, sid, tid, role, "", "", encode(result), now()),
                )
                db.execute(
                    "INSERT INTO exercises (id,session_id,turn_id,mode,reference_text,reference_hash,reference_origin,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (eid, sid, tid, "listen_repeat", answer, digest(answer), "coach", now()),
                )
                db.execute(
                    "UPDATE conversations SET reference_id=?,audio_id=NULL,stage='model_playback',revision=revision+1 WHERE id=?",
                    (eid, sid),
                )
                save_generation(db, "coach_messages", mid, provenance)
                save_generation(db, "exercises", eid, provenance)
            if stage == "question_generation":
                save_generation(db, "turns", tid, provenance)
            return flow(db, sid)

    @app.post("/v1/sessions/{session_id}/conversation/playbacks")
    def start(session_id: UUID, body: Start):
        sid = str(session_id)
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = current(db, sid, body.revision)
            if state["stage"] not in {"question_playback", "model_playback"}:
                raise APIError(409, "wrong_stage", "再生の段階ではありません。")
            if body.route == "saved" and not state["audio_id"]:
                raise APIError(409, "no_audio", "先に音声を生成してください。")
            session = row(db, "sessions", sid)
            saved = json.loads(session["settings_json"])
            ref = (
                row(db, "exercises", state["reference_id"])
                if state["stage"] == "model_playback"
                else None
            )
            text = ref["reference_text"] if ref else turn_row(db, state["turn_id"])["question_en"]
            identity = uid()
            invalidate(db, sid)
            revision = state["revision"] + 1
            source = "coach_answer" if ref else "question"
            audio_id = state["audio_id"] if body.route == "saved" else None
            audio_record = row(db, "audio_files", audio_id) if audio_id else None
            db.execute(
                "INSERT INTO conversation_playbacks (id,session_id,turn_id,reference_id,revision,stage,reference_hash,audio_id,tts_settings_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    sid,
                    state["turn_id"],
                    ref["id"] if ref else None,
                    revision,
                    state["stage"],
                    digest(text),
                    audio_id,
                    encode(
                        {
                            "route": body.route,
                            "rate": body.rate,
                            "model": saved["speech_models"][source],
                            "audio_cache_key": audio_record["cache_key"] if audio_record else None,
                        }
                    ),
                ),
            )
            db.execute(
                "UPDATE conversations SET playback_id=?,revision=? WHERE id=?",
                (identity, revision, sid),
            )
            return {
                "state": flow(db, sid),
                "playback_id": identity,
                "text": text,
                "audio_id": audio_id,
            }

    @app.post("/v1/sessions/{session_id}/conversation/ended")
    def ended(session_id: UUID, body: Ended):
        sid, pid = str(session_id), str(body.playback_id)
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            playback = row(db, "conversation_playbacks", pid)
            if playback["session_id"] != sid:
                raise APIError(409, "session_mismatch", "再生のセッションが一致しません。")
            # Acknowledgements are transactionally saved with the commit. Both
            # request identity and playback identity suppress repeated events.
            existing = db.execute(
                "SELECT * FROM requests WHERE request_id=?", (str(body.request_id),)
            ).fetchone()
            action = f"playback_ended:{pid}"
            payload_hash = digest(encode(body.model_dump(mode="json")))
            if existing and (
                existing["session_id"] != sid
                or existing["action"] != action
                or existing["payload_hash"] != payload_hash
            ):
                raise APIError(
                    409, "request_conflict", "このrequest_idは別の通知に使用されています。"
                )

            def acknowledge(result):
                if not existing:
                    db.execute(
                        "INSERT INTO requests (request_id,session_id,action,payload_hash,payload_json,status,result_json,created_at) VALUES (?,?,?,?,?,'done',?,?)",
                        (
                            str(body.request_id),
                            sid,
                            action,
                            payload_hash,
                            encode(body.model_dump(mode="json")),
                            encode(result),
                            now(),
                        ),
                    )
                return result

            if playback["status"] == "completed":
                return acknowledge(flow(db, sid))
            state = current(db, sid, playback["revision"])
            if (
                playback["status"] != "playing"
                or state["playback_id"] != pid
                or state["turn_id"] != playback["turn_id"]
                or state["stage"] != playback["stage"]
            ):
                raise APIError(409, "stale_playback", "取消済み、または古い再生です。")
            stage = "free_speech_input" if state["mode"] == "free_speech" else "coach_generation"
            if playback["stage"] == "model_playback":
                ref = row(db, "exercises", playback["reference_id"])
                if (
                    ref["id"] != state["reference_id"]
                    or ref["turn_id"] != state["turn_id"]
                    or ref["reference_hash"] != playback["reference_hash"]
                ):
                    raise APIError(409, "reference_mismatch", "参照文の版が一致しません。")
                inserted = db.execute(
                    """INSERT INTO turn_submissions (turn_id,session_id,answer_text,submitted_via,source_exercise_id,source_playback_id,committed_at,provenance_status)
                       SELECT ?,?,?,?,?,?,?,'exact'
                       WHERE NOT EXISTS (SELECT 1 FROM turn_submissions WHERE turn_id=?)""",
                    (
                        state["turn_id"],
                        sid,
                        ref["reference_text"],
                        "shadowing_playback",
                        ref["id"],
                        pid,
                        now(),
                        state["turn_id"],
                    ),
                ).rowcount
                if inserted != 1:
                    raise APIError(409, "already_confirmed", "返答は確定済みです。")
                stage = "question_generation"
            db.execute("UPDATE conversation_playbacks SET status='completed' WHERE id=?", (pid,))
            db.execute(
                "UPDATE conversations SET stage=?,audio_id=NULL,playback_id=NULL,revision=revision+1 WHERE id=?",
                (stage, sid),
            )
            return acknowledge(flow(db, sid))
