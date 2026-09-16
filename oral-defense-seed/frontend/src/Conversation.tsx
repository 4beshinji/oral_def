import { useEffect, useRef, useState } from "react";
import { api, requestId } from "./api";
import type { ConversationState, Session } from "./types";

const stages: Record<string, string> = {
  question_generation: "相手の発言を生成中",
  question_playback: "相手の発言",
  coach_generation: "Coachがお手本を準備中",
  model_playback: "お手本に重ねて話しましょう",
};

type Props = { session: Session; onRunning: (value: boolean) => void };

export default function Conversation({ session, onRunning }: Props) {
  const [snapshot, setSnapshot] = useState(session);
  const [state, setState] = useState(session.conversation!);
  const [running, setRunning] = useState(false);
  const [pending, setPending] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [route, setRoute] = useState<"saved" | "browser">("saved");
  const [rate, setRate] = useState(0.9);
  const [subtitles, setSubtitles] = useState(true);
  const [meaning, setMeaning] = useState(false);
  const [draft, setDraft] = useState("");
  const generation = useRef(0);
  const cancelAudio = useRef<(() => void) | null>(null);
  const mounted = useRef(false);
  const base = `/sessions/${session.id}/conversation`;
  const activeTurn = snapshot.turns.find((turn) => turn.id === state.turn_id);
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

  async function revise(text?: string) {
    setPending(true);
    onRunning(true);
    setError("");
    try {
      setState(
        await api<ConversationState>(
          base + "/reference",
          text === undefined ? {} : { text },
        ),
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

  return (
    <section className="panel" aria-label="シャドウイング会話">
      <span className="eyebrow">COACH / SHADOWING</span>
      <h2>
        {running
          ? stages[state.stage]
          : state.status === "ended"
            ? "会話を終了しました"
            : "シャドウイング会話"}
      </h2>
      <p>
        相手を聞き、お手本に重ねて話します。お手本の再生完了で返答を確定し、自動で次へ進みます。録音は不要です。
      </p>
      <div className="button-row">
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
      <div className="button-row">
        <button
          disabled={!ready || running || pending || state.status === "ended"}
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
          disabled={!ready || pending || state.status === "ended"}
          onClick={() => void stop("end")}
        >
          会話を終了
        </button>
      </div>
      {activeTurn && (
        <article aria-label="現在の会話">
          <h3>相手 · {activeTurn.ordinal}</h3>
          {subtitles && <p lang="en">{activeTurn.question_en}</p>}
          <h3>Coachのお手本</h3>
          {subtitles && reference && (
            <p lang="en">{reference.reference_text}</p>
          )}
          {meaning &&
            activeTurn.coach_messages.map((message) => (
              <p key={message.id}>{message.response.explanation_ja}</p>
            ))}
        </article>
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
      <details>
        <summary>
          確定済み公開会話 (
          {snapshot.turns.filter((turn) => turn.confirmed_answer_en).length}
          往復)
        </summary>
        {snapshot.turns
          .filter((turn) => turn.confirmed_answer_en)
          .map((turn) => (
            <article key={turn.id}>
              <p lang="en">相手: {turn.question_en}</p>
              <p lang="en">返答: {turn.confirmed_answer_en}</p>
              <p className="small">
                確定元:{" "}
                {turn.submitted_via === "shadowing_playback"
                  ? "お手本の再生完了"
                  : "本人の確認"}{" "}
                · 録音: {turn.has_recording ? "あり" : "なし"}
              </p>
              <button
                className="secondary"
                disabled={running || pending}
                onClick={() => {
                  setPending(true);
                  void play(turn.confirmed_answer_en!, null)
                    .catch((e) => setError(e.message))
                    .finally(() => setPending(false));
                }}
              >
                過去のお手本をブラウザで反復
              </button>
            </article>
          ))}
      </details>
    </section>
  );
}
