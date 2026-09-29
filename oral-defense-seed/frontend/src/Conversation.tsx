import { useCallback, useEffect, useRef, useState } from "react";
import { api, requestId } from "./api";
import ConversationHistory, { ExerciseHistory } from "./ConversationHistory";
import ConversationCoach from "./ConversationCoach";
import ConversationRecording from "./ConversationRecording";
import FreeSpeechInput from "./FreeSpeechInput";
import type { ConversationState, Session, Turn } from "./types";

const stages: Record<string, string> = {
  question_generation: "相手の発言を生成中",
  question_playback: "相手の発言",
  coach_generation: "Coachがお手本を準備中",
  model_playback: "お手本に重ねて話しましょう",
  free_speech_input: "自由発話で返答",
};

type Props = {
  session: Session;
  asrAvailable: boolean;
  onRunning: (value: boolean) => void;
  onHold: (value: boolean) => void;
};

export default function Conversation({
  session,
  asrAvailable,
  onRunning,
  onHold,
}: Props) {
  const [snapshot, setSnapshot] = useState(session);
  const [state, setState] = useState(session.conversation!);
  const [running, setRunning] = useState(false);
  const [pending, setPending] = useState(false);
  const [recordingHold, setRecordingHold] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [route, setRoute] = useState<"saved" | "browser">("saved");
  const [rate, setRate] = useState(0.9);
  const [subtitles, setSubtitles] = useState(true);
  const [meaning, setMeaning] = useState(false);
  const [draft, setDraft] = useState("");
  const generation = useRef(0);
  const cancelAudio = useRef<(() => void) | null>(null);
  const stopCapture = useRef<(() => void) | null>(null);
  const registerStopCapture = useCallback((stop: (() => void) | null) => {
    stopCapture.current = stop;
  }, []);
  const mounted = useRef(false);
  const base = `/sessions/${session.id}/conversation`;
  const activeTurn = snapshot.turns.find((turn) => turn.id === state.turn_id);
  // A new turn id can arrive before its snapshot. Keep the Coach mounted so
  // an in-flight reply remains attached to its original request and turn.
  const supportTurn = activeTurn || snapshot.turns.at(-1);
  const reference = activeTurn?.exercises.find(
    (exercise) => exercise.id === state.reference_id,
  );

  useEffect(() => {
    mounted.current = true;
    // A reload never infers completion, even if playback had already ended.
    void api<ConversationState>(base + "/control", { action: "pause" })
      .then((value) => {
        if (mounted.current) {
          setState(value);
          setReady(true);
        }
      })
      .catch((e) => {
        if (mounted.current) {
          setError(e.message);
          setReady(true);
        }
      });
    return () => {
      mounted.current = false;
      generation.current++;
      stopCapture.current?.();
      cancelAudio.current?.();
      onRunning(false);
      void fetch("/v1" + base + "/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "pause" }),
        keepalive: true,
      }).catch(() => {});
    };
  }, [session.id]);

  function play(text: string, audioId: string | null): Promise<void> {
    document.querySelectorAll("audio").forEach((audio) => audio.pause());
    return new Promise((resolve, reject) => {
      const audio = audioId ? new Audio(`/v1/audio/${audioId}`) : null;
      const utterance = audio ? null : new SpeechSynthesisUtterance(text);
      let finished = false;
      const finish = (error?: Error) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        cancelAudio.current = null;
        if (audio) {
          audio.onended = null;
          audio.onerror = null;
          audio.onpause = null;
          audio.pause();
          audio.removeAttribute("src");
          audio.load();
        } else {
          utterance!.onend = null;
          utterance!.onerror = null;
          window.speechSynthesis.cancel();
        }
        if (error) reject(error);
        else resolve();
      };
      const timer = window.setTimeout(
        () =>
          finish(
            new Error(
              "再生が完了しませんでした。再開して再生し直してください。",
            ),
          ),
        180000,
      );
      cancelAudio.current = () => finish(new Error("再生を停止しました。"));
      if (audio) {
        audio.playbackRate = rate;
        audio.onended = () => finish();
        audio.onerror = () =>
          finish(new Error("音声再生に失敗しました。再開できます。"));
        audio.onpause = () => {
          if (!audio.ended) finish(new Error("再生が中断されました。"));
        };
        void audio
          .play()
          .catch(() =>
            finish(
              new Error(
                "再生が拒否されました。音声設定を確認し、再開してください。",
              ),
            ),
          );
      } else {
        utterance!.lang = "en-US";
        utterance!.rate = rate;
        utterance!.onend = () => finish();
        utterance!.onerror = () =>
          finish(new Error("ブラウザ読み上げに失敗しました。再開できます。"));
        window.speechSynthesis.speak(utterance!);
      }
    });
  }

  async function reload(token: number) {
    const value = await api<Session>(`/sessions/${session.id}`);
    if (mounted.current && generation.current === token) setSnapshot(value);
  }

  async function start() {
    document.querySelectorAll("audio").forEach((audio) => audio.pause());
    const token = ++generation.current;
    const valid = () => mounted.current && generation.current === token;
    setRunning(true);
    onRunning(true);
    setError("");
    try {
      let next = await api<ConversationState>(base + "/control", {
        action: "resume",
      });
      while (valid()) {
        setState(next);
        if (next.stage === "free_speech_input") {
          next = await api<ConversationState>(base + "/control", {
            action: "pause",
          });
          if (valid()) setState(next);
          break;
        }
        next = await api<ConversationState>(base + "/step", {
          revision: next.revision,
          route,
        });
        if (!valid()) break;
        setState(next);
        await reload(token);
        if (!valid()) break;
        if (!next.stage.endsWith("playback")) continue;
        // Generation and audio acquisition are separate saved stages.
        if (route === "saved" && !next.audio_id) continue;
        const playback = await api<{
          state: ConversationState;
          playback_id: string;
          text: string;
          audio_id: string | null;
        }>(base + "/playbacks", { revision: next.revision, route, rate });
        if (!valid()) break;
        setState(playback.state);
        await play(playback.text, playback.audio_id);
        if (playback.state.stage === "model_playback") stopCapture.current?.();
        if (!valid()) break;
        next = await api<ConversationState>(base + "/ended", {
          ...requestId(),
          playback_id: playback.playback_id,
        });
        if (valid()) {
          setState(next);
          await reload(token);
        }
      }
    } catch (e) {
      stopCapture.current?.();
      if (valid()) {
        setError(e instanceof Error ? e.message : "会話を停止しました。");
        try {
          const stopped = await api<ConversationState>(base + "/control", {
            action: "pause",
          });
          if (valid()) {
            setState(stopped);
            await reload(token);
          }
        } catch {
          /* Keep the original error; resume invalidates old operations. */
        }
      }
    } finally {
      if (valid()) {
        setRunning(false);
        onRunning(false);
      }
    }
  }

  async function stop(action: "pause" | "end") {
    generation.current++;
    stopCapture.current?.();
    cancelAudio.current?.();
    setRunning(false);
    setPending(true);
    try {
      setState(await api<ConversationState>(base + "/control", { action }));
      await reload(generation.current);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "停止状態を保存できませんでした。",
      );
    } finally {
      setPending(false);
      onRunning(false);
    }
  }

  async function changeMode(mode: "shadowing" | "free_speech") {
    setPending(true);
    setError("");
    try {
      const changed = await api<ConversationState>(base + "/mode", {
        mode,
        revision: state.revision,
      });
      setState(changed);
      await reload(generation.current);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "モードを変更できませんでした。",
      );
    } finally {
      setPending(false);
    }
  }

  async function revise(text?: string) {
    setPending(true);
    onRunning(true);
    setError("");
    try {
      setState(
        await api<ConversationState>(base + "/reference", {
          revision: state.revision,
          turn_id: state.turn_id,
          ...(text === undefined ? {} : { text }),
        }),
      );
      await reload(generation.current);
      setDraft("");
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "お手本を更新できませんでした。",
      );
    } finally {
      setPending(false);
      onRunning(false);
    }
  }

  async function supportSaved(turnId: string) {
    const saved = await api<Turn>(`/sessions/${session.id}/turns/${turnId}`);
    if (mounted.current)
      setSnapshot((current) => ({
        ...current,
        turns: current.turns.map((turn) => (turn.id === turnId ? saved : turn)),
      }));
  }

  async function adopt(messageId: string, turnId: string) {
    setPending(true);
    onRunning(true);
    setError("");
    try {
      setState(
        await api<ConversationState>(base + "/adopt", {
          revision: state.revision,
          turn_id: turnId,
          reference_id: state.reference_id,
          message_id: messageId,
        }),
      );
      await reload(generation.current);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
      onRunning(false);
    }
  }

  return (
    <section className="panel" aria-label="会話">
      <span className="eyebrow">COACH / CONVERSATION</span>
      <h2>
        {running
          ? stages[state.stage]
          : state.status === "ended"
            ? "会話を終了しました"
            : state.mode === "free_speech"
              ? "自由発話の会話"
              : "シャドウイング会話"}
      </h2>
      <p>
        {state.mode === "free_speech"
          ? "相手の発言を聞き、マイクで返答します。最終文字起こしで返答を確定し、次の発言に進みます。"
          : "相手を聞き、お手本に重ねて話します。お手本の再生完了で返答を確定し、自動で次へ進みます。録音は不要です。"}
      </p>
      <div className="button-row">
        <label>
          会話モード
          <select
            aria-label="現在の会話モード"
            value={state.mode}
            disabled={
              !ready ||
              running ||
              pending ||
              recordingHold ||
              state.status !== "paused"
            }
            onChange={(event) =>
              void changeMode(event.target.value as "shadowing" | "free_speech")
            }
          >
            <option value="shadowing">Coachのお手本</option>
            <option value="free_speech">自由発話</option>
          </select>
        </label>
        <label>
          音声経路
          <select
            aria-label="会話の音声経路"
            disabled={running || pending}
            value={route}
            onChange={(e) => setRoute(e.target.value as typeof route)}
          >
            <option value="saved">設定済みTTS</option>
            <option value="browser">ブラウザ読み上げ</option>
          </select>
        </label>
        <label>
          速度
          <select
            aria-label="会話の速度"
            disabled={running || pending}
            value={rate}
            onChange={(e) => setRate(Number(e.target.value))}
          >
            {[0.7, 0.9, 1, 1.2].map((value) => (
              <option key={value} value={value}>
                {value}×
              </option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={subtitles}
            onChange={(e) => setSubtitles(e.target.checked)}
          />
          字幕
        </label>
        <label>
          <input
            type="checkbox"
            checked={meaning}
            onChange={(e) => setMeaning(e.target.checked)}
          />
          Coachの説明
        </label>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="button-row conversation-controls">
        <button
          disabled={
            !ready ||
            running ||
            pending ||
            recordingHold ||
            state.status === "ended"
          }
          onClick={() => void start()}
        >
          開始 / 再開
        </button>
        <button
          disabled={!running || pending}
          onClick={() => void stop("pause")}
        >
          一時停止
        </button>
        <button
          className="secondary"
          disabled={
            !ready || pending || recordingHold || state.status === "ended"
          }
          onClick={() => void stop("end")}
        >
          会話を終了
        </button>
      </div>
      {activeTurn && (
        <article aria-label="現在の会話">
          <h3>相手 · {activeTurn.ordinal}</h3>
          {subtitles && <p lang="en">{activeTurn.question_en}</p>}
          {state.mode === "shadowing" && <h3>Coachのお手本</h3>}
          {state.mode === "shadowing" && subtitles && reference && (
            <p lang="en">{reference.reference_text}</p>
          )}
          {meaning &&
            activeTurn.coach_messages.map((message) => (
              <p key={message.id}>{message.response.explanation_ja}</p>
            ))}
        </article>
      )}
      {supportTurn && (
        <ConversationCoach
          turn={supportTurn}
          state={state}
          pending={pending}
          onSaved={supportSaved}
          adopt={adopt}
        />
      )}
      {state.mode === "free_speech" &&
        state.stage === "free_speech_input" &&
        state.status !== "ended" && (
          <FreeSpeechInput
            sessionId={session.id}
            asrAvailable={asrAvailable}
            state={state}
            onState={setState}
            onCommitted={() => {
              void reload(generation.current);
              void start();
            }}
            onHolding={(value) => {
              setRecordingHold(value);
              onHold(value);
            }}
          />
        )}
      {state.mode === "shadowing" && (
        <ConversationRecording
          sessionId={session.id}
          reference={reference}
          paused={ready && !running && !pending && state.status === "paused"}
          overlapAllowed={
            running &&
            !pending &&
            state.status === "running" &&
            state.stage === "model_playback" &&
            state.playback_id !== null
          }
          onHolding={(value) => {
            setRecordingHold(value);
            onHold(value);
          }}
          onSaved={() => reload(generation.current)}
          registerStop={registerStopCapture}
        />
      )}
      {reference && (
        <ExerciseHistory
          key={reference.id}
          exercise={reference}
          disabled={running}
        />
      )}
      {!running &&
        state.status === "paused" &&
        ["coach_generation", "model_playback"].includes(state.stage) && (
          <details>
            <summary>お手本を修正・再生成</summary>
            <label>
              新しい参照文
              <textarea
                aria-label="新しい参照文"
                value={draft}
                maxLength={4000}
                disabled={pending}
                placeholder={reference?.reference_text || "伝えたい英文"}
                onChange={(e) => setDraft(e.target.value)}
              />
            </label>
            <button
              disabled={pending || !draft.trim()}
              onClick={() => void revise(draft)}
            >
              この文を採用
            </button>
            <button disabled={pending} onClick={() => void revise()}>
              Coachで再生成（再開時）
            </button>
          </details>
        )}
      <ConversationHistory
        session={snapshot}
        disabled={running || pending}
        repeat={(text) => {
          setPending(true);
          void play(text, null)
            .catch((e) => setError(e.message))
            .finally(() => setPending(false));
        }}
      />
    </section>
  );
}
