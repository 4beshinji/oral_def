import { useEffect, useRef, useState } from "react";
import { api, requestId } from "./api";
import { roleNames } from "./types";
import type { CoachMessage, ConversationState, Level, Turn } from "./types";

export default function ConversationCoach({
  turn,
  state,
  pending,
  onSaved,
  adopt,
}: {
  turn: Turn;
  state: ConversationState;
  pending: boolean;
  onSaved: (turnId: string) => Promise<void>;
  adopt: (messageId: string, turnId: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reply, setReply] = useState<
    (CoachMessage & { turn_id: string; ordinal: number }) | null
  >(null);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  async function ask(level: Level) {
    const target = { id: turn.id, ordinal: turn.ordinal };
    setBusy(true);
    setError("");
    try {
      const response = await api<CoachMessage & { turn_id: string }>(
        `/turns/${target.id}/coach`,
        {
          ...requestId(),
          level,
          user_note: note,
          draft,
        },
      );
      if (!mounted.current) return;
      setReply({
        ...response,
        level,
        user_note: note,
        ordinal: target.ordinal,
      });
      await onSaved(target.id);
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  const messages = [
    ...new Map(
      [
        ...turn.coach_messages,
        ...(reply?.turn_id === turn.id ? [reply] : []),
      ].map((message) => [message.id, message]),
    ).values(),
  ];
  const canAdopt =
    !pending &&
    state.status === "paused" &&
    ["coach_generation", "model_playback"].includes(state.stage) &&
    state.turn_id === turn.id &&
    turn.confirmed_answer_en === null;
  return (
    <section className="coach" aria-label="会話中のCoach支援">
      <h3>Coachに相談 · 会話 {turn.ordinal}</h3>
      <p className="small">
        支援の生成中も会話は続きます。案を採用する場合は、一時停止してください。
      </p>
      <label>
        Coachへのメモ
        <textarea
          aria-label="会話Coachへのメモ"
          value={note}
          maxLength={4000}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <label>
        修正したい英文
        <textarea
          aria-label="会話Coachの下書き"
          value={draft}
          maxLength={4000}
          onChange={(e) => setDraft(e.target.value)}
        />
      </label>
      <div className="button-row">
        {(
          ["meaning", "hint", "outline", "full_answer", "revision"] as Level[]
        ).map((level) => (
          <button
            key={level}
            disabled={
              busy ||
              pending ||
              state.status === "ended" ||
              (level === "revision" && !draft.trim())
            }
            onClick={() => void ask(level)}
          >
            {roleNames[level]}
          </button>
        ))}
      </div>
      {busy && <p role="status">Coachが支援を生成しています。</p>}
      {error && <p role="alert">{error}</p>}
      {reply && reply.turn_id !== turn.id && (
        <article aria-label="以前の会話への支援">
          <h4>会話 {reply.ordinal} への支援が届きました</h4>
          <p>{reply.response.explanation_ja}</p>
          {reply.response.answer_en && (
            <p lang="en">{reply.response.answer_en}</p>
          )}
          <p className="small">この支援は元の会話の履歴に保存しました。</p>
        </article>
      )}
      {messages.map((message) => (
        <article key={message.id} data-testid="coach-support">
          <h4>{roleNames[message.level]}</h4>
          <p>{message.response.explanation_ja}</p>
          {message.response.answer_en && (
            <p lang="en">{message.response.answer_en}</p>
          )}
          {message.response.needs_user_input.length > 0 && (
            <p className="small">
              確認したい情報: {message.response.needs_user_input.join("・")}
            </p>
          )}
          {message.response.answer_en && (
            <button
              disabled={!canAdopt}
              onClick={() => void adopt(message.id, turn.id)}
            >
              このCoach案を採用
            </button>
          )}
        </article>
      ))}
    </section>
  );
}
