import { useEffect, useRef, useState } from "react";
import { api, requestId } from "./api";
import {
  listPendingFreeSpeech,
  removePendingFreeSpeech,
  savePendingFreeSpeech,
  type PendingFreeSpeech,
} from "./pendingFreeSpeech";
import type { ConversationState } from "./types";

type SavedRecording = {
  id: string;
  turn_id: string;
  audio_id: string;
  audio_meta: { duration_s?: number };
  transcriptions: { id: string; status: string; text: string | null }[];
};

type Props = {
  sessionId: string;
  asrAvailable: boolean;
  state: ConversationState;
  onState: (state: ConversationState) => void;
  onCommitted: () => void;
  onHolding: (value: boolean) => void;
};

export default function FreeSpeechInput({
  sessionId,
  asrAvailable,
  state,
  onState,
  onCommitted,
  onHolding,
}: Props) {
  const [pending, setPending] = useState<PendingFreeSpeech[]>([]);
  const [saved, setSaved] = useState<SavedRecording[]>([]);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [manual, setManual] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef(0);
  const active = useRef(true);
  const turnId = state.turn_id;
  const currentTurn = useRef(turnId);
  currentTurn.current = turnId;
  const base = `/sessions/${sessionId}/free-speech`;

  async function refresh() {
    const [local, remote] = await Promise.all([
      listPendingFreeSpeech(sessionId),
      api<{ recordings: SavedRecording[] }>(
        base + `/recordings?turn_id=${turnId}`,
      ),
    ]);
    if (active.current && currentTurn.current === turnId) {
      setPending(local.filter((item) => item.turnId === turnId));
      setSaved(remote.recordings.filter((item) => item.turn_id === turnId));
    }
  }

  useEffect(() => {
    active.current = true;
    void refresh().catch(() =>
      setError("保存済み録音を読み込めませんでした。"),
    );
    return () => {
      active.current = false;
      clearTimeout(timer.current);
      if (recorder.current?.state === "recording") recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
      onHolding(false);
    };
  }, [sessionId, turnId]);

  function stop() {
    clearTimeout(timer.current);
    if (recorder.current?.state === "recording") recorder.current.stop();
    stream.current?.getTracks().forEach((track) => track.stop());
  }

  async function upload(item: PendingFreeSpeech) {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", item.blob, "response");
      form.append("turn_id", item.turnId);
      form.append("revision", String(state.revision));
      form.append("client_key", item.key);
      form.append("capture", JSON.stringify(item.capture));
      await api(base + "/recordings", form);
      await removePendingFreeSpeech(item.key);
      await refresh();
    } catch (cause) {
      setError(
        `${cause instanceof Error ? cause.message : "保存に失敗しました。"} 録音はブラウザに保持しています。`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function begin() {
    if (
      !turnId ||
      busy ||
      recording ||
      !navigator.mediaDevices?.getUserMedia ||
      !window.MediaRecorder
    ) {
      setError("このブラウザでは録音できません。");
      return;
    }
    setError("");
    onHolding(true);
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!active.current || currentTurn.current !== turnId) {
        media.getTracks().forEach((track) => track.stop());
        onHolding(false);
        return;
      }
      stream.current = media;
      const capture = media.getAudioTracks()[0].getSettings();
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
        media.getTracks().forEach((track) => track.stop());
        setRecording(false);
        const blob = new Blob(chunks, { type: rec.mimeType });
        if (!blob.size) {
          setError("録音が空です。もう一度お試しください。");
          onHolding(false);
          return;
        }
        const item: PendingFreeSpeech = {
          key: crypto.randomUUID(),
          sessionId,
          turnId,
          blob,
          capture: Object.fromEntries(
            Object.entries(capture).filter(
              ([, value]) =>
                typeof value === "number" || typeof value === "boolean",
            ),
          ),
          createdAt: Date.now(),
        };
        void savePendingFreeSpeech(item)
          .then(() => {
            setPending((items) => [...items, item]);
            return upload(item);
          })
          .catch(() => {
            setPending((items) => [...items, item]);
            setError(
              "ブラウザへの保存に失敗しました。この画面を閉じる前に再送してください。",
            );
          })
          .finally(() => onHolding(false));
      };
      rec.onerror = () => {
        setError("録音に失敗しました。取得分の保存を試みます。");
        stop();
      };
      rec.start();
      setRecording(true);
      timer.current = window.setTimeout(stop, 29000);
    } catch (cause) {
      stream.current?.getTracks().forEach((track) => track.stop());
      onHolding(false);
      setError(
        cause instanceof DOMException && cause.name === "NotAllowedError"
          ? "マイクの使用が許可されませんでした。"
          : "マイクを開始できませんでした。",
      );
    }
  }

  async function commit(path: string, input: Record<string, unknown>) {
    if (!turnId) return;
    setBusy(true);
    setError("");
    let resumedRevision: number | null = null;
    let committed = false;
    try {
      const live = await api<ConversationState>(
        `/sessions/${sessionId}/conversation/control`,
        { action: "resume" },
      );
      resumedRevision = live.revision;
      onState(live);
      const result = await api<{ status: string; text: string | null }>(
        base + path,
        {
          ...requestId(),
          turn_id: turnId,
          revision: live.revision,
          ...input,
        },
      );
      await refresh();
      if (result.status === "succeeded") {
        committed = true;
        onCommitted();
      } else {
        setError(
          result.status === "no_speech"
            ? "音声を認識できませんでした。録音から再試行するか、英文を入力してください。"
            : "処理中に会話が変更されました。保存状態を確認してください。",
        );
      }
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "返答を確定できませんでした。",
      );
    } finally {
      if (resumedRevision !== null && !committed) {
        try {
          onState(
            await api<ConversationState>(
              `/sessions/${sessionId}/conversation/control`,
              { action: "pause", revision: resumedRevision },
            ),
          );
        } catch {
          /* Preserve the original error. */
        }
      }
      setBusy(false);
    }
  }

  return (
    <section className="panel practice" aria-label="自由発話の返答">
      <h3>自由発話で返答</h3>
      <p>
        録音後に音声認識し、得られた英文を返答として確定します。無音や失敗では確定しません。
      </p>
      {!asrAvailable && (
        <p>
          音声認識モデルは未設定です。録音を保存するか、下の欄で英文を入力できます。
        </p>
      )}
      <button
        disabled={busy || !turnId}
        onClick={() => (recording ? stop() : void begin())}
      >
        {recording ? "録音を停止" : "マイクで録音"}
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {pending.map((item) => (
        <div key={item.key} className="attempt">
          <p>ブラウザに保存済みの録音</p>
          <button
            disabled={busy || recording}
            onClick={() => void upload(item)}
          >
            録音を再送
          </button>
          <button
            disabled={busy || recording}
            onClick={() => void removePendingFreeSpeech(item.key).then(refresh)}
          >
            破棄
          </button>
        </div>
      ))}
      {saved.map((item) => (
        <div key={item.id} className="attempt">
          <p>録音済み · {item.audio_meta.duration_s?.toFixed(1) || "?"}秒</p>
          <audio controls preload="none" src={`/v1/audio/${item.audio_id}`} />
          <button
            disabled={busy || recording}
            onClick={() =>
              void commit("/transcribe", { recording_id: item.id })
            }
          >
            この録音を認識して返答
          </button>
          {item.transcriptions.map((run) => (
            <p key={run.id}>
              {run.status}
              {run.text ? `: ${run.text}` : ""}
            </p>
          ))}
        </div>
      ))}
      <label>
        音声認識を使わず入力する英文
        <textarea
          aria-label="自由発話の代替入力"
          value={manual}
          maxLength={4000}
          onChange={(event) => setManual(event.target.value)}
        />
      </label>
      <button
        disabled={busy || recording || !manual.trim()}
        onClick={() => void commit("/manual", { text: manual.trim() })}
      >
        入力文を返答として確定
      </button>
    </section>
  );
}
