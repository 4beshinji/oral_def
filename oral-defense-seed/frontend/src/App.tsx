import { useEffect, useRef, useState } from "react";
import { api, requestId } from "./api";
import Practice, { modeNames } from "./Practice";
import Conversation from "./Conversation";
import ModelPicker from "./ModelPicker";
import SpeechPicker from "./SpeechPicker";
import type { SpeechCatalog, SpeechModels } from "./types";
import { roleNames, type TextRole, type RoleModels } from "./types";
import type {
  Capabilities,
  CoachMessage,
  Exercise,
  Level,
  Mode,
  ModelList,
  Session,
  Turn,
} from "./types";

const levelNames: Record<Level, string> = {
  meaning: "質問の意味",
  hint: "ヒント",
  outline: "回答の骨子",
  full_answer: "全文案",
  revision: "文章修正",
};

export default function App() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [speechCatalog, setSpeechCatalog] = useState<SpeechCatalog | null>(
    null,
  );
  const [newSpeech, setNewSpeech] = useState<SpeechModels>({
    question: "configured",
    coach_answer: "configured",
    exercise: "configured",
  });
  const previewUrl = useRef<string>("");
  useEffect(
    () => () => {
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    },
    [],
  );
  const [models, setModels] = useState<ModelList | null>(null);
  const [newModels, setNewModels] = useState<RoleModels>(
    Object.fromEntries(
      Object.keys(roleNames).map((role) => [role, ""]),
    ) as RoleModels,
  );
  const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [pack, setPack] = useState("");
  const [brief, setBrief] = useState("");
  const [scenario, setScenario] = useState("seminar");
  const [language, setLanguage] = useState("standard");
  const [depth, setDepth] = useState("research");
  const [strictness, setStrictness] = useState("supportive");
  const [working, setBusy] = useState(false);
  const [conversationRunning, setConversationRunning] = useState(false);
  const busy = working || conversationRunning;
  const [sessionMode, setSessionMode] = useState("shadowing");
  const [hold, setHold] = useState(false);
  const [error, setError] = useState("");
  const [turnIndex, setTurnIndex] = useState(-1);
  const [ttsUrl, setTtsUrl] = useState("");
  const player = useRef<HTMLAudioElement>(null);
  const turn = session?.turns[turnIndex] ?? session?.turns.at(-1);
  const selectedText = session?.settings.text_model;

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      api<Capabilities>("/capabilities"),
      api<Session[]>("/sessions"),
      api<unknown>("/packs/sample"),
      api<ModelList>("/text-models"),
      api<SpeechCatalog>("/speech-models"),
    ])
      .then(async ([caps, list, sample, catalog, speech]) => {
        if (cancelled) return;
        setCapabilities(caps);
        setSpeechCatalog(speech);
        setNewSpeech(speech.defaults);
        setModels(catalog);
        setNewModels(
          Object.fromEntries(
            Object.keys(roleNames).map((role) => [role, catalog.default_id]),
          ) as RoleModels,
        );
        setSessions(list);
        setPack(JSON.stringify(sample, null, 2));
        const saved = localStorage.getItem("oral-defense-session");
        if (saved && list.some((s) => s.id === saved)) {
          const loaded = await api<Session>(`/sessions/${saved}`);
          if (!cancelled) setSession(loaded);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (hold) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hold]);

  useEffect(() => {
    if (!models || models.next_refresh_at === null) return;
    let cancelled = false;
    let timer: number;
    const check = async () => {
      if (
        document.visibilityState === "visible" &&
        Date.now() / 1000 >= models.next_refresh_at!
      ) {
        try {
          const updated = await api<ModelList>("/text-models/refresh", {
            force: false,
          });
          if (!cancelled) setModels(updated);
          return;
        } catch {
          /* Keep the current choices after a transport failure. */
        }
      }
      if (!cancelled) timer = window.setTimeout(check, 60000);
    };
    timer = window.setTimeout(check, 1000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [models]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作に失敗しました。");
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    if (session) setSession(await api<Session>(`/sessions/${session.id}`));
    setSessions(await api<Session[]>("/sessions"));
  }
  async function selectSession(id: string) {
    window.speechSynthesis?.cancel();
    player.current?.pause();
    setTtsUrl("");
    setTurnIndex(-1);
    if (!id) {
      setSession(null);
      localStorage.removeItem("oral-defense-session");
      return;
    }
    setSession(await api<Session>(`/sessions/${id}`));
    localStorage.setItem("oral-defense-session", id);
  }
  async function speak(source_type: string, source_id: string) {
    window.speechSynthesis?.cancel();
    document.querySelectorAll("audio").forEach((element) => element.pause());
    const result = await api<{
      status: string;
      audio_id?: string;
      message?: string;
    }>("/tts", { source_type, source_id });
    if (!result.audio_id)
      throw new Error(
        result.message || "保存TTSは未設定です。ブラウザ読み上げを選べます。",
      );
    const url = "/v1/audio/" + result.audio_id;
    setTtsUrl(url);
    if (player.current) {
      player.current.src = url;
      try {
        await player.current.play();
      } catch {
        throw new Error(
          "自動再生できませんでした。上部の音声プレーヤーから再生してください。",
        );
      }
    }
  }
  async function browserSpeak(text: string, exerciseId?: string) {
    if (!window.speechSynthesis)
      throw new Error("このブラウザは読み上げに対応していません。");
    await api("/assistance", {
      turn_id: turn!.id,
      exercise_id: exerciseId,
      kind: "browser_tts_played",
    });
    document.querySelectorAll("audio").forEach((element) => element.pause());
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-US";
    utterance.rate = 0.9;
    utterance.onerror = (event) => {
      if (!["canceled", "interrupted"].includes(event.error))
        setError(
          "ブラウザ読み上げに失敗しました。利用できる英語の音声を確認してください。",
        );
    };
    window.speechSynthesis.speak(utterance);
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-icon">od.</span>
          <div>
            <strong>Oral Defense Drill</strong>
            <span>研究を、自分の言葉で話す。</span>
          </div>
        </div>
        <span className="tag">LOCAL WORKSPACE</span>
      </header>
      <main>
        <div className="page-heading">
          <div>
            <span className="eyebrow">RESEARCH / SPEAKING PRACTICE</span>
            <h1>答えをつくる。声にする。</h1>
            <p className="muted">
              英語の質疑応答を、ひとつの質問から練習しましょう。
            </p>
          </div>
          <div className="session-picker">
            <label>
              保存したセッション
              <select
                aria-label="保存したセッション"
                disabled={busy || hold}
                value={session?.id || ""}
                onChange={(e) => void run(() => selectSession(e.target.value))}
              >
                <option value="">新しいセッション</option>
                {sessions.map((s, i) => (
                  <option key={s.id} value={s.id}>
                    {s.research_brief.slice(0, 32) ||
                      `セッション ${sessions.length - i}`}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        {capabilities && (
          <details className="settings-info">
            <summary>役割ごとの接続先と保存方針 · 発音評価は未提供</summary>
            {session ? (
              (
                Object.entries(session.settings.role_models) as [
                  TextRole,
                  typeof selectedText,
                ][]
              ).map(([role, model]) => (
                <p key={role}>
                  {roleNames[role]}: {model?.id} /{" "}
                  {model?.endpoint || "外部送信なし"}
                </p>
              ))
            ) : (
              <p>各役割のモデルは下の選択欄で確認・変更できます。</p>
            )}
            <p>
              設定済み音声API: {capabilities.tts.endpoint || "未設定"} /{" "}
              {capabilities.tts.provider}
            </p>
            <p>{capabilities.retention}</p>
            <p>
              ブラウザ読み上げはOS・ブラウザの音声サービスを使用します。実LLM・実TTS・発音ゲートの検証が完了するまでは開発デモです。
            </p>
          </details>
        )}
        {error && (
          <div role="alert" className="error">
            <span>{error}</span>
            <button
              className="quiet"
              onClick={() => setError("")}
              aria-label="エラーを閉じる"
            >
              ×
            </button>
          </div>
        )}
        <div role="status" className="busy-status">
          {busy
            ? "処理しています…"
            : hold
              ? "録音・保存待ちです。セッションの切り替えは保存後にできます。"
              : ""}
        </div>
        {models && (
          <ModelPicker
            catalog={models}
            value={
              session
                ? (Object.fromEntries(
                    Object.entries(session.settings.role_models).map(
                      ([role, model]) => [role, model.id],
                    ),
                  ) as RoleModels)
                : newModels
            }
            disabled={busy || hold}
            onChange={(role: TextRole, id: string) => {
              if (session)
                void run(async () => {
                  await api(
                    `/sessions/${session.id}/text-model`,
                    { role, text_model: id },
                    "PUT",
                  );
                  await refresh();
                });
              else setNewModels((current) => ({ ...current, [role]: id }));
            }}
            onRefresh={() =>
              void run(async () =>
                setModels(await api<ModelList>("/text-models/refresh", {})),
              )
            }
          />
        )}
        {speechCatalog && (
          <SpeechPicker
            catalog={speechCatalog}
            value={session?.settings.speech_models || newSpeech}
            disabled={busy || hold}
            onChange={(role, model) => {
              if (session)
                void run(async () => {
                  await api(
                    `/sessions/${session.id}/speech-model`,
                    { role, model },
                    "PUT",
                  );
                  await refresh();
                });
              else setNewSpeech((current) => ({ ...current, [role]: model }));
            }}
            onPreview={(model) =>
              void run(async () => {
                document
                  .querySelectorAll("audio")
                  .forEach((element) => element.pause());
                window.speechSynthesis?.cancel();
                const response = await fetch("/v1/speech-preview", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ model }),
                  signal: AbortSignal.timeout(65000),
                });
                if (!response.ok) {
                  const error = await response.json().catch(() => ({}));
                  throw new Error(
                    error.message || "試聴音声を生成できませんでした。",
                  );
                }
                if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
                const url = URL.createObjectURL(await response.blob());
                previewUrl.current = url;
                setTtsUrl(url);
                if (player.current) {
                  player.current.src = url;
                  try {
                    await player.current.play();
                  } catch {
                    throw new Error(
                      "上部の音声プレーヤーから再生してください。",
                    );
                  }
                }
              })
            }
          />
        )}
        <audio
          className={ttsUrl ? "tts-player" : "hidden"}
          ref={player}
          controls
          aria-label="基準音声プレーヤー"
        />
        {!session ? (
          <section className="panel setup">
            <span className="eyebrow">START A SESSION</span>
            <h2>今回、何を話しますか。</h2>
            <p className="muted">
              サンプルPackは架空のBayesian
              optimizationの練習用です。本人の研究成果は入力した内容だけを使います。
            </p>
            <label>
              研究概要
              <textarea
                aria-label="研究概要"
                rows={5}
                maxLength={4000}
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
                placeholder="研究の目的、手法、確認できた事実、まだ分からないことを日本語か英語で。"
              />
            </label>
            <label>
              会話モード
              <select
                aria-label="会話モード"
                value={sessionMode}
                onChange={(e) => setSessionMode(e.target.value)}
              >
                <option value="shadowing">シャドウイング（自動会話）</option>
                <option value="independent">
                  独立練習（手動で回答を確定）
                </option>
              </select>
            </label>
            <div className="settings-grid">
              <label>
                場面
                <select
                  value={scenario}
                  onChange={(e) => setScenario(e.target.value)}
                >
                  <option value="icebreaker">アイスブレイク</option>
                  <option value="networking">懇親会</option>
                  <option value="seminar">ゼミでの質疑</option>
                  <option value="lab_defense">厳しい追質問</option>
                </select>
              </label>
              <label>
                英語の難しさ
                <select
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                >
                  <option value="simple">やさしい</option>
                  <option value="standard">標準</option>
                  <option value="advanced">高度</option>
                </select>
              </label>
              <label>
                専門的な深さ
                <select
                  value={depth}
                  onChange={(e) => setDepth(e.target.value)}
                >
                  <option value="introductory">入門</option>
                  <option value="research">研究</option>
                </select>
              </label>
              <label>
                厳しさ
                <select
                  value={strictness}
                  onChange={(e) => setStrictness(e.target.value)}
                >
                  <option value="supportive">補助的</option>
                  <option value="strict">厳しい</option>
                </select>
              </label>
            </div>
            <details>
              <summary>Expert Packを確認・編集</summary>
              <label>
                JSONファイルを読み込む
                <input
                  type="file"
                  accept=".json,application/json"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file)
                      void run(async () => {
                        if (file.size > 32000)
                          throw new Error(
                            "Packファイルが大きすぎます。8000文字以内のJSONを使用してください。",
                          );
                        const content = await file.text();
                        JSON.parse(content);
                        setPack(content);
                      });
                  }}
                />
              </label>
              <label>
                Pack JSON
                <textarea
                  className="code-input"
                  value={pack}
                  onChange={(e) => setPack(e.target.value)}
                  rows={12}
                />
              </label>
            </details>
            <button
              disabled={
                busy ||
                !pack ||
                !Object.values(newModels).every((id) =>
                  models?.models.some((m) => m.id === id && m.available),
                )
              }
              onClick={() =>
                void run(async () => {
                  const result = await api<{ session_id: string }>(
                    "/sessions",
                    {
                      pack: JSON.parse(pack),
                      mode: sessionMode,
                      research_brief: brief,
                      scenario,
                      role_models: newModels,
                      speech_models: newSpeech,
                      settings: {
                        language_level: language,
                        technical_depth: depth,
                        strictness,
                      },
                    },
                  );
                  await selectSession(result.session_id);
                  setSessions(await api("/sessions"));
                })
              }
            >
              セッションを作成 →
            </button>
          </section>
        ) : (
          <>
            <div className="session-bar">
              <span>
                {{
                  icebreaker: "アイスブレイク",
                  networking: "懇親会",
                  seminar: "ゼミ",
                  lab_defense: "研究討論",
                }[session.scenario] || session.scenario}{" "}
                <span className="muted">
                  {session.conversation ? "/ 自動会話" : "/ 最大6問"}
                </span>
              </span>
              <nav aria-label="質問履歴">
                {session.turns.map((t, index) => (
                  <button
                    key={t.id}
                    disabled={busy || hold}
                    className={turn?.id === t.id ? "step active-step" : "step"}
                    onClick={() => {
                      setTurnIndex(index);
                      player.current?.pause();
                      window.speechSynthesis?.cancel();
                    }}
                  >
                    Q{t.ordinal}
                    {t.confirmed_answer_en ? " ✓" : ""}
                  </button>
                ))}
              </nav>
              <div className="button-row">
                <a
                  className="small"
                  href={`/v1/sessions/${session.id}/export`}
                  download
                >
                  JSON保存
                </a>
                <button
                  className="quiet small"
                  disabled={busy || hold}
                  onClick={() => {
                    if (
                      window.confirm(
                        "このセッションの会話・録音を削除します。元に戻せません。",
                      )
                    )
                      void run(async () => {
                        await api(
                          `/sessions/${session.id}`,
                          undefined,
                          "DELETE",
                        );
                        await selectSession("");
                        setSessions(await api("/sessions"));
                      });
                  }}
                >
                  削除
                </button>
              </div>
            </div>
            {session.conversation ? (
              <Conversation
                key={session.id}
                session={session}
                onRunning={setConversationRunning}
              />
            ) : !turn ? (
              <section className="panel empty">
                <h2>準備ができました。</h2>
                <p>最初の短い質問から始めましょう。</p>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(
                        `/sessions/${session.id}/question`,
                        requestId(),
                      );
                      await refresh();
                    })
                  }
                >
                  最初の質問を生成
                </button>
              </section>
            ) : (
              <TurnWorkspace
                key={turn.id}
                turn={turn}
                session={session}
                busy={busy}
                hold={hold}
                run={run}
                refresh={refresh}
                onHold={setHold}
                error={setError}
                speak={speak}
                browserSpeak={browserSpeak}
                next={() =>
                  run(async () => {
                    await api(`/sessions/${session.id}/question`, requestId());
                    setTurnIndex(-1);
                    await refresh();
                  })
                }
              />
            )}
          </>
        )}
      </main>
      <footer>
        <span>ORAL DEFENSE DRILL</span>
        <span>既知文の反復練習 · 音声認識なし · ローカル保存</span>
      </footer>
    </div>
  );
}

type WorkspaceProps = {
  turn: Turn;
  session: Session;
  busy: boolean;
  hold: boolean;
  run: (fn: () => Promise<void>) => Promise<void>;
  refresh: () => Promise<void>;
  onHold: (v: boolean) => void;
  error: (message: string) => void;
  speak: (source: string, id: string) => Promise<void>;
  browserSpeak: (text: string, exerciseId?: string) => Promise<void>;
  next: () => Promise<void>;
};

function TurnWorkspace({
  turn,
  session,
  busy,
  hold,
  run,
  refresh,
  onHold,
  error,
  speak,
  browserSpeak,
  next,
}: WorkspaceProps) {
  const [draft, setDraft] = useState(
    turn.confirmed_answer_en || turn.exercises.at(-1)?.reference_text || "",
  );
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<Mode>("read_aloud");
  const [exerciseId, setExerciseId] = useState(turn.exercises.at(-1)?.id || "");
  const [showQuestion, setShowQuestion] = useState(false);
  const [cards, setCards] = useState<string[]>([]);
  const [unable, setUnable] = useState(false);
  const confirmed = turn.confirmed_answer_en !== null;
  const exercise = turn.exercises.find((e) => e.id === exerciseId);
  const fixedFull = turn.exercises.find(
    (e) => e.reference_text === draft.trim(),
  );
  const disabled = busy || hold;

  async function freeze(text: string) {
    const origin = turn.coach_messages.some(
      (c) => c.response.answer_en === text.trim(),
    )
      ? "coach"
      : "manual";
    const saved = await api<Exercise>(`/turns/${turn.id}/exercises`, {
      mode,
      text,
      origin,
    });
    setExerciseId(saved.id);
    await refresh();
  }
  return (
    <>
      <section className="panel question">
        <div className="section-heading">
          <div>
            <span className="eyebrow">01 / EXAMINER</span>
            <h2>Question {String(turn.ordinal).padStart(2, "0")}</h2>
          </div>
          <span className="tag">
            {turn.follow_up_count
              ? `追質問 ${turn.follow_up_count} / 2`
              : "新しい論点"}
          </span>
        </div>
        {showQuestion ? (
          <p className="question-text" lang="en">
            {turn.question_en}
          </p>
        ) : (
          <p className="question-text muted">
            まずは、質問を聞いてみましょう。
          </p>
        )}
        <div className="button-row">
          <button
            disabled={disabled}
            onClick={() => void run(() => speak("question", turn.id))}
          >
            質問の音声を再生
          </button>
          <button
            className="secondary"
            disabled={disabled}
            onClick={() =>
              void run(async () => {
                if (!showQuestion)
                  await api("/assistance", {
                    turn_id: turn.id,
                    kind: "subtitle_shown",
                  });
                setShowQuestion(!showQuestion);
              })
            }
          >
            {showQuestion ? "質問文を隠す" : "質問文を表示"}
          </button>
          <button
            className="quiet"
            disabled={disabled}
            onClick={() => void run(() => browserSpeak(turn.question_en))}
          >
            ブラウザで読み上げ
          </button>
        </div>
        {showQuestion && (
          <p className="small muted">根拠メモ: {turn.basis_note}</p>
        )}
      </section>
      <div className="workspace-grid">
        <section className="panel answer">
          <span className="eyebrow">02 / YOUR ANSWER</span>
          <h2>回答を、自分の言葉に。</h2>
          <p className="muted small">
            Coachの案を編集して、短い英文を固定しましょう。
          </p>
          <label>
            回答文
            <textarea
              aria-label="回答文"
              lang="en"
              value={draft}
              maxLength={4000}
              disabled={confirmed || hold}
              onChange={(e) => setDraft(e.target.value)}
              rows={7}
              placeholder="Write a short answer, or start with your coach…"
            />
          </label>
          {!confirmed && (
            <>
              <div className="button-row">
                <label className="grow">
                  練習モード
                  <select
                    aria-label="練習モード"
                    value={mode}
                    disabled={disabled}
                    onChange={(e) => setMode(e.target.value as Mode)}
                  >
                    {Object.entries(modeNames).map(([key, name]) => (
                      <option key={key} value={key}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  disabled={disabled || !draft.trim()}
                  onClick={() => void run(() => freeze(draft))}
                >
                  この文で練習
                </button>
              </div>
              <button
                className="quiet small"
                disabled={disabled || !draft.trim()}
                onClick={() =>
                  setCards(
                    draft
                      .match(/[^.!?]+[.!?]+|[^.!?]+$/g)
                      ?.map((s) => s.trim())
                      .filter(Boolean) || [],
                  )
                }
              >
                長い回答を短文カードに分ける
              </button>
              {cards.length > 0 && (
                <div className="cards">
                  <p className="small muted">
                    分割位置を確認・編集してください。全文回答は上の回答文を別途固定して確定します。
                  </p>
                  {cards.map((text, i) => (
                    <div key={i}>
                      <label>
                        短文カード {i + 1}
                        <textarea
                          lang="en"
                          value={text}
                          disabled={disabled}
                          onChange={(e) =>
                            setCards(
                              cards.map((v, j) =>
                                i === j ? e.target.value : v,
                              ),
                            )
                          }
                        />
                      </label>
                      <button
                        className="secondary small"
                        disabled={disabled || !text.trim()}
                        onClick={() => void run(() => freeze(text))}
                      >
                        このカードで練習
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <div className="confirm-area">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={unable}
                    disabled={disabled}
                    onChange={(e) => {
                      setUnable(e.target.checked);
                      if (e.target.checked)
                        setDraft(
                          "I cannot answer this yet. I need to study this question.",
                        );
                    }}
                  />
                  この質問にはまだ回答できない（次は学習課題へ）
                </label>
                <button
                  className="secondary"
                  disabled={disabled || !fixedFull}
                  onClick={() =>
                    void run(async () => {
                      await api(`/turns/${turn.id}/confirm`, {
                        ...requestId(),
                        answer_en: draft.trim(),
                        unable_to_answer: unable,
                      });
                      await refresh();
                    })
                  }
                >
                  この内容で回答した
                </button>
                <p className="small muted">
                  本人の自己申告として固定文をExaminerに渡します。録音が文と一致した証明ではありません。
                </p>
              </div>
            </>
          )}
          {confirmed && (
            <div className="notice">
              <strong>回答を確定しました。</strong>
              <p className="small">確定方法: 本人が参照文を確認</p>
              {turn.ordinal === session.turns.length &&
                session.turns.length < 6 && (
                  <button disabled={disabled} onClick={() => void next()}>
                    次の質問へ →
                  </button>
                )}
              {session.status === "completed" && (
                <p>
                  6問が終了しました。お疲れさまでした。セッション選択から新しい練習を始められます。
                </p>
              )}
            </div>
          )}
        </section>
        <aside className="panel coach">
          <span className="eyebrow">PRIVATE / COACH</span>
          <h2>考えるためのサポート</h2>
          <p className="small muted">
            ここでの相談や未採用の案はExaminerに渡りません。
          </p>
          <label>
            日本語メモ
            <textarea
              aria-label="日本語メモ"
              value={note}
              disabled={confirmed || disabled}
              maxLength={4000}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="伝えたいこと、迷っていること…"
            />
          </label>
          <div className="coach-actions">
            {Object.entries(levelNames).map(([key, label]) => (
              <button
                className="secondary"
                key={key}
                disabled={
                  confirmed || disabled || (key === "revision" && !draft.trim())
                }
                onClick={() =>
                  void run(async () => {
                    await api<CoachMessage>(`/turns/${turn.id}/coach`, {
                      ...requestId(),
                      level: key,
                      user_note: note,
                      draft,
                    });
                    await refresh();
                  })
                }
              >
                {label}
              </button>
            ))}
          </div>
          {!turn.coach_messages.length && (
            <div className="coach-empty">
              まずは「質問の意味」から。
              <br />
              必要な分だけ助けを借りましょう。
            </div>
          )}
          {turn.coach_messages
            .slice()
            .reverse()
            .map((message) => (
              <article className="coach-message" key={message.id}>
                <span className="tag">{levelNames[message.level]}</span>
                <p>{message.response.explanation_ja}</p>
                {message.response.answer_en && (
                  <>
                    <blockquote lang="en">
                      {message.response.answer_en}
                    </blockquote>
                    <div className="button-row">
                      <button
                        className="secondary small"
                        disabled={confirmed || disabled}
                        onClick={() => setDraft(message.response.answer_en!)}
                      >
                        回答欄に取り込む
                      </button>
                      <button
                        className="quiet small"
                        disabled={disabled}
                        onClick={() =>
                          void run(() => speak("coach_answer", message.id))
                        }
                      >
                        案を聞く
                      </button>
                    </div>
                  </>
                )}
                {message.response.needs_user_input.length > 0 && (
                  <p className="small muted">
                    本人の入力が必要:{" "}
                    {message.response.needs_user_input.join("、")}
                  </p>
                )}
              </article>
            ))}
        </aside>
      </div>
      {turn.exercises.length > 0 && (
        <label className="exercise-picker">
          固定した練習文
          <select
            aria-label="固定した練習文"
            disabled={disabled}
            value={exerciseId}
            onChange={(e) => setExerciseId(e.target.value)}
          >
            {turn.exercises.map((e, index) => (
              <option key={e.id} value={e.id}>
                {index + 1}. {modeNames[e.mode]} —{" "}
                {e.reference_text.slice(0, 65)}
              </option>
            ))}
          </select>
        </label>
      )}
      {exercise && (
        <Practice
          key={exercise.id}
          exercise={exercise}
          turnId={turn.id}
          refresh={refresh}
          onHold={onHold}
          error={error}
          speak={speak}
          browserSpeak={browserSpeak}
          run={run}
          busy={busy}
        />
      )}
    </>
  );
}
