import { useEffect, useRef, useState } from "react";
import { api, requestId } from "./api";
import type {
  AssessmentRun,
  CoachMessage,
  Attempt,
  Exercise,
  HistoryPage,
  Session,
  Turn,
} from "./types";

function merge<T extends { id: string }>(older: T[], newer: T[]) {
  return [
    ...new Map([...older, ...newer].map((item) => [item.id, item])).values(),
  ];
}

const executionNames: Record<string, string> = {
  queued: "待機中",
  running: "処理中",
  succeeded: "処理完了",
  failed: "処理失敗",
  interrupted: "処理中断",
};
const evidenceNames: Record<string, string> = {
  unavailable: "評価器が未設定です",
  insufficient_evidence: "評価に十分な音声ではありません",
  uncalibrated: "未校正の音響指標",
  pending: "評価待ち",
};
const submissionNames: Record<string, string> = {
  shadowing_playback: "お手本の再生完了",
  confirmed_reference: "本人の確認",
  free_speech_transcript: "自由発話の音声認識",
  free_speech_manual: "自由発話の手入力",
};

function Run({ run }: { run: AssessmentRun }) {
  return (
    <div className="result" data-testid="assessment-run">
      <p>
        {executionNames[run.execution_status] || run.execution_status}
        {run.evidence_status &&
          ` · ${evidenceNames[run.evidence_status] || run.evidence_status}`}
      </p>
      {run.error_message && <p>{run.error_message}</p>}
      {run.result && (
        <p className="small">
          {[...run.result.reason_codes, ...run.result.limitations].join(" · ")}
        </p>
      )}
    </div>
  );
}

function AttemptHistory({
  attempt,
  disabled,
}: {
  attempt: Attempt;
  disabled: boolean;
}) {
  const [runs, setRuns] = useState<HistoryPage<AssessmentRun> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [missing, setMissing] = useState(false);
  async function load() {
    setBusy(true);
    setError("");
    try {
      const page = await api<HistoryPage<AssessmentRun>>(
        `/attempts/${attempt.id}/assessments${runs?.next_before ? `?before=${runs.next_before}` : ""}`,
      );
      setRuns({ ...page, items: merge(page.items, runs?.items || []) });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function assess() {
    setBusy(true);
    setError("");
    try {
      await api(`/attempts/${attempt.id}/assess`, requestId());
      setRuns(
        await api<HistoryPage<AssessmentRun>>(
          `/attempts/${attempt.id}/assessments`,
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="attempt" data-testid="history-attempt">
      <p>
        録音条件:{" "}
        {attempt.input_kind === "shadowing_overlap"
          ? "お手本と同時"
          : attempt.input_kind === "isolated_repeat"
            ? "単独復唱"
            : "不明"}
      </p>
      {attempt.audio_id && attempt.audio_available && !missing ? (
        <audio
          controls
          preload="none"
          src={`/v1/audio/${attempt.audio_id}`}
          onError={() => setMissing(true)}
          onPlay={(event) => {
            if (disabled) event.currentTarget.pause();
            else
              document.querySelectorAll("audio").forEach((audio) => {
                if (audio !== event.currentTarget) audio.pause();
              });
          }}
        />
      ) : (
        attempt.audio_id && <p>録音ファイルを読み込めません。</p>
      )}
      {missing && <p role="alert">録音ファイルを読み込めません。</p>}
      {disabled && attempt.audio_id && (
        <p className="small">会話を一時停止すると過去の録音を再生できます。</p>
      )}
      {attempt.dictation_text && <p lang="en">{attempt.dictation_text}</p>}
      {!runs && attempt.assessment && <Run run={attempt.assessment} />}
      {runs?.items.map((run) => (
        <Run key={run.id} run={run} />
      ))}
      {runs && (
        <p>
          評価履歴 {runs.items.length} / {runs.total}件
        </p>
      )}
      {(!runs || runs.next_before !== null) && (
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void load()}
        >
          {runs ? "以前の評価を読み込む" : "評価履歴を表示"}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
      {attempt.audio_id && (
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void assess()}
        >
          {runs?.total || attempt.assessment
            ? "同じ録音で評価を再試行"
            : "音響評価を実行"}
        </button>
      )}
    </article>
  );
}

export function ExerciseHistory({
  exercise,
  disabled,
}: {
  exercise: Exercise;
  disabled: boolean;
}) {
  const [attempts, setAttempts] = useState(exercise.attempts);
  const [before, setBefore] = useState(exercise.attempts_before);
  const [total, setTotal] = useState(exercise.attempts_total);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setAttempts((saved) => merge(saved, exercise.attempts));
    setBefore(exercise.attempts_before);
    setTotal(exercise.attempts_total);
  }, [exercise]);
  async function load() {
    setBusy(true);
    setError("");
    try {
      const page = await api<HistoryPage<Attempt>>(
        `/exercises/${exercise.id}/attempts?before=${before}`,
      );
      setAttempts((saved) => merge(page.items, saved));
      setBefore(page.next_before);
      setTotal(page.total);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="参照文と試行">
      <p lang="en">{exercise.reference_text}</p>
      <p>
        試行 {attempts.length} / {total}件
      </p>
      {before !== null && (
        <button disabled={busy} onClick={() => void load()}>
          以前の試行を読み込む
        </button>
      )}
      {error && <p role="alert">{error}</p>}
      {attempts.map((attempt) => (
        <AttemptHistory
          key={attempt.id}
          attempt={attempt}
          disabled={disabled}
        />
      ))}
    </section>
  );
}

function TurnDetails({
  sessionId,
  turnId,
  disabled,
}: {
  sessionId: string;
  turnId: string;
  disabled: boolean;
}) {
  const [turn, setTurn] = useState<Turn | null>(null);
  const [freeSpeechRecordings, setFreeSpeechRecordings] = useState<
    { id: string; audio_id: string; audio_meta: { duration_s?: number } }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function load() {
    setBusy(true);
    setError("");
    try {
      if (!turn) {
        const saved = await api<Turn>(`/sessions/${sessionId}/turns/${turnId}`);
        setTurn(saved);
        if (
          saved.has_recording &&
          saved.submitted_via?.startsWith("free_speech")
        ) {
          const result = await api<{
            recordings: {
              id: string;
              audio_id: string;
              audio_meta: { duration_s?: number };
            }[];
          }>(`/sessions/${sessionId}/free-speech/recordings?turn_id=${turnId}`);
          setFreeSpeechRecordings(result.recordings);
        }
      } else {
        const page = await api<HistoryPage<Exercise>>(
          `/turns/${turnId}/exercises?before=${turn.exercises_before}`,
        );
        setTurn({
          ...turn,
          exercises: merge(page.items, turn.exercises),
          exercises_before: page.next_before,
          exercises_total: page.total,
        });
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function loadCoach() {
    if (!turn) return;
    setBusy(true);
    setError("");
    try {
      const page = await api<HistoryPage<CoachMessage>>(
        `/turns/${turnId}/coach-messages?before=${turn.coach_messages_before}`,
      );
      setTurn({
        ...turn,
        coach_messages: merge(page.items, turn.coach_messages),
        coach_messages_before: page.next_before,
        coach_messages_total: page.total,
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      {(!turn || turn.exercises_before !== null) && (
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void load()}
        >
          {turn ? "以前の参照文を読み込む" : "参照文・録音・評価を表示"}
        </button>
      )}
      {turn && (
        <p>
          参照文 {turn.exercises.length} / {turn.exercises_total}件
        </p>
      )}
      {turn && (
        <details>
          <summary>
            この会話のCoach支援 ({turn.coach_messages.length} /{" "}
            {turn.coach_messages_total}件)
          </summary>
          {turn.coach_messages_before !== null && (
            <button disabled={busy} onClick={() => void loadCoach()}>
              以前のCoach支援を読み込む
            </button>
          )}
          {turn.coach_messages.map((message) => (
            <article key={message.id}>
              <p>{message.response.explanation_ja}</p>
              {message.response.answer_en && (
                <p lang="en">{message.response.answer_en}</p>
              )}
            </article>
          ))}
        </details>
      )}
      {turn?.exercises.map((exercise) => (
        <ExerciseHistory
          key={exercise.id}
          exercise={exercise}
          disabled={disabled}
        />
      ))}
      {freeSpeechRecordings.map((recording) => (
        <article key={recording.id} className="attempt">
          <p>
            自由発話の録音 ·{" "}
            {recording.audio_meta.duration_s?.toFixed(1) || "?"}秒
          </p>
          <audio
            controls
            preload="none"
            src={`/v1/audio/${recording.audio_id}`}
            onError={() => setError("録音ファイルを読み込めません。")}
            onPlay={(event) => {
              if (disabled) event.currentTarget.pause();
              else
                document.querySelectorAll("audio").forEach((audio) => {
                  if (audio !== event.currentTarget) audio.pause();
                });
            }}
          />
        </article>
      ))}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

export default function ConversationHistory({
  session,
  disabled,
  repeat,
}: {
  session: Session;
  disabled: boolean;
  repeat: (text: string) => void;
}) {
  const [turns, setTurns] = useState(session.turns);
  const [before, setBefore] = useState(session.turns_before);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const mounted = useRef(false);
  const storageKey = `oral-history:${session.id}`;

  useEffect(() => {
    setTurns((saved) =>
      merge(saved, session.turns).sort((a, b) => a.ordinal - b.ordinal),
    );
    // A conversation with no turns can acquire its first page while running.
    if (session.turns_before === null) setBefore(null);
  }, [session]);

  useEffect(() => {
    mounted.current = true;
    let saved: { floor?: number; open?: boolean } = {};
    try {
      saved = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
    } catch {
      /* Storage can be unavailable. */
    }
    setOpen(Boolean(saved.open));
    if (
      saved.floor &&
      session.turns_before &&
      session.turns_before > saved.floor
    ) {
      setBusy(true);
      void (async () => {
        let cursor: number | null = session.turns_before;
        try {
          while (mounted.current && cursor !== null && cursor > saved.floor!) {
            const page: { turns: Turn[]; next_before: number | null } =
              await api(`/sessions/${session.id}/turns?before=${cursor}`);
            if (!mounted.current) return;
            setTurns((existing) =>
              merge(page.turns, existing).sort((a, b) => a.ordinal - b.ordinal),
            );
            cursor = page.next_before;
            setBefore(cursor);
          }
        } catch (e) {
          if (mounted.current) setError((e as Error).message);
        } finally {
          if (mounted.current) setBusy(false);
        }
      })();
    }
    return () => {
      mounted.current = false;
    };
  }, [session.id]);

  function remember(floor: number | undefined, expanded: boolean) {
    try {
      sessionStorage.setItem(
        storageKey,
        JSON.stringify({ floor, open: expanded }),
      );
    } catch {
      /* History remains available through the API. */
    }
  }
  async function load() {
    setBusy(true);
    setError("");
    try {
      const page = await api<{ turns: Turn[]; next_before: number | null }>(
        `/sessions/${session.id}/turns?before=${before}`,
      );
      if (!mounted.current) return;
      setTurns((saved) =>
        merge(page.turns, saved).sort((a, b) => a.ordinal - b.ordinal),
      );
      setBefore(page.next_before);
      remember(page.turns[0]?.ordinal, true);
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <details
      open={open}
      onToggle={(event) => {
        setOpen(event.currentTarget.open);
        if (!busy) remember(turns[0]?.ordinal, event.currentTarget.open);
      }}
    >
      <summary>確定済み公開会話 ({session.confirmed_turns_total}往復)</summary>
      <p>
        履歴取得済み {turns.length} / 全{session.turns_total}ターン
      </p>
      {before !== null && (
        <button disabled={busy} onClick={() => void load()}>
          以前の会話を読み込む
        </button>
      )}
      {busy && <p role="status">履歴を読み込んでいます。</p>}
      {error && <p role="alert">{error}</p>}
      {turns
        .filter((turn) => turn.confirmed_answer_en !== null)
        .map((turn) => (
          <article
            key={turn.id}
            data-testid="history-turn"
            data-ordinal={turn.ordinal}
          >
            <h3>会話 {turn.ordinal}</h3>
            <p lang="en">相手: {turn.question_en}</p>
            <p lang="en">返答: {turn.confirmed_answer_en}</p>
            <p className="small">
              確定元: {submissionNames[turn.submitted_via || ""] || "出典不明"}{" "}
              · 録音: {turn.has_recording ? "あり" : "なし"}
            </p>
            <button
              className="secondary"
              disabled={disabled}
              onClick={() => repeat(turn.confirmed_answer_en!)}
            >
              返答をブラウザで読み上げ
            </button>
            <TurnDetails
              sessionId={session.id}
              turnId={turn.id}
              disabled={disabled}
            />
          </article>
        ))}
    </details>
  );
}
