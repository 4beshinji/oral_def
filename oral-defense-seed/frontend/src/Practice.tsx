import { useEffect, useRef, useState } from "react";
import { api, requestId } from "./api";
import type {
  Assessment,
  Attempt,
  DictationResult,
  Exercise,
  HistoryPage,
  Mode,
} from "./types";

export const modeNames: Record<Mode, string> = {
  read_aloud: "音読",
  listen_repeat: "聞いて復唱",
  dictation: "ディクテーション",
};
type Props = {
  exercise: Exercise;
  turnId: string;
  refresh: () => Promise<void>;
  onHold: (value: boolean) => void;
  error: (message: string) => void;
  speak: (source: string, id: string) => Promise<void>;
  browserSpeak: (text: string, exerciseId?: string) => Promise<void>;
  run: (fn: () => Promise<void>) => Promise<void>;
  busy: boolean;
};

export default function Practice({
  exercise,
  turnId,
  refresh,
  onHold,
  error,
  speak,
  browserSpeak,
  run,
  busy,
}: Props) {
  const [show, setShow] = useState(exercise.mode === "read_aloud");
  const [typed, setTyped] = useState("");
  const [localWorking, setWorking] = useState(false);
  const working = localWorking || busy;
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [pending, setPending] = useState<Blob | null>(null);
  const [localUrl, setLocalUrl] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<number>(0);
  const tick = useRef<number>(0);
  const capture = useRef<MediaTrackSettings>({});
  const mounted = useRef(true);
  const [olderAttempts, setOlderAttempts] = useState<Attempt[]>([]);
  const [attemptsBefore, setAttemptsBefore] = useState(
    exercise.attempts_before,
  );
  const attempts = [
    ...new Map(
      [...olderAttempts, ...exercise.attempts].map((attempt) => [
        attempt.id,
        attempt,
      ]),
    ).values(),
  ];

  useEffect(() => {
    setOlderAttempts((saved) => [
      ...new Map(
        [...saved, ...exercise.attempts].map((attempt) => [
          attempt.id,
          attempt,
        ]),
      ).values(),
    ]);
  }, [exercise.attempts]);

  async function loadOlderAttempts() {
    await action(async () => {
      const page = await api<HistoryPage<Attempt>>(
        `/exercises/${exercise.id}/attempts?before=${attemptsBefore}`,
      );
      if (!mounted.current) return;
      setOlderAttempts((saved) => [...page.items, ...saved]);
      setAttemptsBefore(page.next_before);
    });
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      clearTimeout(timer.current);
      clearInterval(tick.current);
      if (recorder.current?.state === "recording") {
        recorder.current.onstop = null;
        recorder.current.stop();
      }
      stream.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);
  useEffect(() => {
    if (!pending) {
      setLocalUrl("");
      return;
    }
    const url = URL.createObjectURL(pending);
    setLocalUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [pending]);

  async function action(fn: () => Promise<void>) {
    setWorking(true);
    try {
      await run(fn);
    } finally {
      setWorking(false);
    }
  }
  async function save(blob: Blob) {
    const form = new FormData();
    form.append("file", blob, "recording");
    form.append("capture", JSON.stringify(capture.current));
    await api(`/exercises/${exercise.id}/audio`, form);
    setPending(null);
    onHold(false);
    await refresh();
  }
  function stop() {
    clearTimeout(timer.current);
    clearInterval(tick.current);
    if (recorder.current?.state === "recording") recorder.current.stop();
    stream.current?.getTracks().forEach((track) => track.stop());
    setRecording(false);
  }
  async function start() {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)
      throw new Error(
        "このブラウザでは録音できません。localhostの対応ブラウザを使用してください。回答編集とdictationは利用できます。",
      );
    document.querySelectorAll("audio").forEach((element) => element.pause());
    window.speechSynthesis?.cancel();
    onHold(true);
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
      if (!mounted.current) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      stream.current = media;
      document.querySelectorAll("audio").forEach((element) => element.pause());
      window.speechSynthesis?.cancel();
      capture.current = media.getAudioTracks()[0].getSettings();
      const mime = [
        "audio/webm;codecs=opus",
        "audio/ogg;codecs=opus",
        "audio/mp4",
        "audio/webm",
      ].find((value) => MediaRecorder.isTypeSupported(value));
      const rec = new MediaRecorder(
        media,
        mime ? { mimeType: mime } : undefined,
      );
      recorder.current = rec;
      const chunks: BlobPart[] = [];
      rec.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      rec.onstop = () => {
        clearTimeout(timer.current);
        clearInterval(tick.current);
        media.getTracks().forEach((track) => track.stop());
        if (!mounted.current) return;
        setRecording(false);
        const blob = new Blob(chunks, { type: rec.mimeType });
        setPending(blob);
        void action(() => save(blob));
      };
      rec.onerror = () => {
        error("録音に失敗しました。取得できた音声は保存を試みます。");
        stop();
      };
      rec.start();
      setSeconds(0);
      setRecording(true);
      const started = performance.now();
      tick.current = window.setInterval(
        () => setSeconds(Math.floor((performance.now() - started) / 1000)),
        250,
      );
      timer.current = window.setTimeout(stop, 29500);
    } catch (e) {
      stream.current?.getTracks().forEach((track) => track.stop());
      onHold(false);
      throw new Error(
        e instanceof DOMException && e.name === "NotAllowedError"
          ? "マイクの使用が許可されませんでした。ブラウザの権限を確認してください。回答編集とdictationは継続できます。"
          : "マイクを開始できませんでした。接続と権限を確認してください。",
      );
    }
  }
  async function reveal() {
    if (!show)
      await api("/assistance", {
        turn_id: turnId,
        exercise_id: exercise.id,
        kind: "subtitle_shown",
      });
    setShow(!show);
  }

  return (
    <section className="panel practice" aria-label="固定文の練習">
      <div className="section-heading">
        <div>
          <span className="eyebrow">03 / PRACTICE</span>
          <h2>{modeNames[exercise.mode]}で練習</h2>
        </div>
        <span className="tag">参照文は固定済み</span>
      </div>
      <div className="reference" data-testid="reference">
        {show ? (
          <p lang="en">{exercise.reference_text}</p>
        ) : (
          <p className="muted">
            英文を隠しています。音声を聞いて練習しましょう。
          </p>
        )}
      </div>
      <div className="button-row">
        <button
          className="secondary"
          disabled={recording || working}
          onClick={() => void action(reveal)}
        >
          {show ? "英文を隠す" : "英文を表示"}
        </button>
        <button
          className="secondary"
          disabled={recording || working}
          onClick={() => void action(() => speak("exercise", exercise.id))}
        >
          基準音声を再生
        </button>
        <button
          className="quiet"
          disabled={recording || working}
          onClick={() =>
            void action(() =>
              browserSpeak(exercise.reference_text, exercise.id),
            )
          }
        >
          ブラウザで読み上げ
        </button>
      </div>
      <p className="small muted">
        ブラウザ読み上げは明示的な代替機能です。保存された基準音声にはなりません。
      </p>
      {exercise.mode === "dictation" ? (
        <div>
          <label>
            聞こえた英文
            <textarea
              lang="en"
              aria-label="聞こえた英文"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="Type what you hear…"
            />
          </label>
          <button
            disabled={working}
            onClick={() =>
              void action(async () => {
                await api(`/exercises/${exercise.id}/dictation`, {
                  typed_text: typed,
                });
                await refresh();
              })
            }
          >
            単語の差分を確認
          </button>
          <p className="small muted">
            NFKC・小文字化・句読点除去・空白整形。don'tとdo
            not、数字と数詞は別扱いです。発音の評価ではありません。
          </p>
        </div>
      ) : (
        <div className="recorder">
          <div>
            <span className={recording ? "record-dot active" : "record-dot"} />{" "}
            <strong>
              {recording
                ? `録音中 ${seconds} / 30秒`
                : "短い1〜2文を、自分の声で。"}
            </strong>
            <p className="small muted">録り直すと新しい試行として残ります。</p>
          </div>
          <button
            className={recording ? "danger" : ""}
            disabled={working || (!!pending && !recording)}
            onClick={() => (recording ? stop() : void action(start))}
          >
            {recording ? "停止して保存" : "録音を開始"}
          </button>
        </div>
      )}
      {pending && !recording && (
        <div className="notice">
          <p>
            保存待ちの録音があります。失敗した場合は同じ音声を再送できます。
          </p>
          <audio controls src={localUrl} />
          <div className="button-row">
            <button
              disabled={working}
              onClick={() => void action(() => save(pending))}
            >
              録音の保存を再試行
            </button>
            <a href={localUrl} download="oral-defense-recording">
              音声をダウンロード
            </a>
            <button
              className="quiet"
              disabled={working}
              onClick={() => {
                setPending(null);
                onHold(false);
              }}
            >
              保存待ちを破棄
            </button>
          </div>
        </div>
      )}
      <h3>
        試行履歴{" "}
        <span className="muted">
          {attempts.length} / {exercise.attempts_total}件
        </span>
      </h3>
      {attemptsBefore !== null && (
        <button
          disabled={working || recording}
          onClick={() => void loadOlderAttempts()}
        >
          以前の試行を読み込む
        </button>
      )}
      {!attempts.length && (
        <p className="muted">
          まだ試行はありません。録音やdictationの結果がここに残ります。
        </p>
      )}
      {attempts
        .slice()
        .reverse()
        .map((attempt, index) => (
          <article className="attempt" key={attempt.id}>
            <div className="section-heading">
              <strong>試行 {exercise.attempts_total - index}</strong>
              {attempt.audio_meta.duration_s !== undefined && (
                <span className="small muted">
                  {attempt.audio_meta.duration_s.toFixed(1)} 秒
                </span>
              )}
            </div>
            {attempt.audio_id && (
              <>
                <audio
                  controls
                  preload="none"
                  src={`/v1/audio/${attempt.audio_id}`}
                  onPlay={() => {
                    if (recording) {
                      document
                        .querySelectorAll("audio")
                        .forEach((element) => element.pause());
                      return;
                    }
                    void api("/assistance", {
                      turn_id: turnId,
                      exercise_id: exercise.id,
                      kind: "recording_played",
                    }).catch((e) => error(e.message));
                  }}
                />
                <button
                  className="quiet"
                  disabled={working || recording}
                  onClick={() =>
                    void action(async () => {
                      await api(`/attempts/${attempt.id}/assess`, requestId());
                      const updated = await api<Attempt>(
                        `/attempts/${attempt.id}`,
                      );
                      setOlderAttempts((saved) =>
                        saved.map((item) =>
                          item.id === updated.id ? updated : item,
                        ),
                      );
                      await refresh();
                    })
                  }
                >
                  {attempt.result ? "同じ録音で評価を再試行" : "音響評価を実行"}
                </button>
              </>
            )}
            {attempt.result &&
              ("kind" in attempt.result ? (
                <Diff result={attempt.result} />
              ) : (
                <Acoustic result={attempt.result} />
              ))}
          </article>
        ))}
    </section>
  );
}

function Diff({ result }: { result: DictationResult }) {
  return (
    <div className="result">
      <p>
        {result.matches
          ? "正規化後の単語が一致しました。"
          : "単語に差分があります。"}
      </p>
      <div lang="en">
        {result.changes.map((change, index) => (
          <span key={index} className="diff-part">
            {change.kind === "equal" ? (
              change.expected.join(" ")
            ) : (
              <>
                <del>{change.expected.join(" ")}</del>{" "}
                <ins>{change.actual.join(" ")}</ins>
              </>
            )}{" "}
          </span>
        ))}
      </div>
    </div>
  );
}

function Acoustic({ result }: { result: Assessment }) {
  return (
    <div className="result">
      <p>
        {result.status === "unavailable"
          ? "発音評価は利用できません。録音の再生と反復は利用できます。"
          : result.status === "insufficient_evidence"
            ? "この音声は評価に十分な証拠が得られませんでした。"
            : "実験的な音響指標（未校正）"}
      </p>
      {result.reason_codes.map((reason) => (
        <code key={reason}>{reason} </code>
      ))}
      {result.status === "ok" && (
        <table>
          <thead>
            <tr>
              <th>音素</th>
              <th>区間（秒）</th>
              <th>raw GOP</th>
              <th>品質フラグ</th>
            </tr>
          </thead>
          <tbody>
            {result.phones.map((phone, index) => (
              <tr key={index}>
                <td>{phone.expected_phone}</td>
                <td>
                  {phone.start_s.toFixed(2)}–{phone.end_s.toFixed(2)}
                </td>
                <td>{phone.raw_gop.toFixed(3)}</td>
                <td>{phone.quality_flags.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="small muted">{result.limitations.join(" ")}</p>
    </div>
  );
}
