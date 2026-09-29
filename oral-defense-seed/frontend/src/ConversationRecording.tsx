import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import {
  listPending,
  removePending,
  savePending,
  type PendingRecording,
} from "./pendingRecordings";
import type { Exercise } from "./types";

type Props = {
  sessionId: string;
  reference: Exercise | undefined;
  paused: boolean;
  overlapAllowed: boolean;
  onHolding: (value: boolean) => void;
  onSaved: () => Promise<void>;
  registerStop: (stop: (() => void) | null) => void;
};

function PendingAudio({ blob }: { blob: Blob }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    const created = URL.createObjectURL(blob);
    setUrl(created);
    return () => URL.revokeObjectURL(created);
  }, [blob]);
  return <audio controls src={url} />;
}

export default function ConversationRecording({
  sessionId,
  reference,
  paused,
  overlapAllowed,
  onHolding,
  onSaved,
  registerStop,
}: Props) {
  const [items, setItems] = useState<PendingRecording[]>([]);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef(0);
  const active = useRef(true);

  const stop = useCallback(() => {
    clearTimeout(timer.current);
    if (recorder.current?.state === "recording") recorder.current.stop();
    stream.current?.getTracks().forEach((track) => track.stop());
  }, []);

  useEffect(() => {
    registerStop(stop);
    return () => registerStop(null);
  }, [registerStop, stop]);

  useEffect(() => {
    active.current = true;
    void listPending(sessionId)
      .then((saved) => {
        if (active.current) setItems(saved);
      })
      .catch(() => setError("保存待ち録音を読み込めませんでした。"));
    return () => {
      active.current = false;
      clearTimeout(timer.current);
      if (recorder.current?.state === "recording") recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
    };
  }, [sessionId]);

  async function upload(item: PendingRecording) {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", item.blob, "recording");
      form.append("capture", JSON.stringify(item.capture));
      form.append("client_attempt_key", item.key);
      form.append("input_kind", item.kind);
      await api(`/exercises/${item.exerciseId}/audio`, form);
      await removePending(item.key);
      if (active.current)
        setItems((current) =>
          current.filter((saved) => saved.key !== item.key),
        );
      await onSaved();
    } catch (cause) {
      if (active.current)
        setError(
          `${cause instanceof Error ? cause.message : "保存に失敗しました。"} 録音はこのブラウザに保持しました。再送できます。`,
        );
    } finally {
      if (active.current) setBusy(false);
    }
  }

  async function begin(kind: PendingRecording["kind"]) {
    if (
      !reference ||
      recording ||
      busy ||
      (kind === "isolated_repeat" ? !paused : !overlapAllowed)
    )
      return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError("このブラウザでは録音できません。");
      return;
    }
    setError("");
    onHolding(true);
    try {
      if (kind === "isolated_repeat") {
        document.querySelectorAll("audio").forEach((audio) => audio.pause());
        window.speechSynthesis?.cancel();
      }
      const media = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
      if (!active.current) {
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
      const exerciseId = reference.id;
      rec.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      rec.onstop = () => {
        clearTimeout(timer.current);
        media.getTracks().forEach((track) => track.stop());
        if (active.current) setRecording(false);
        const blob = new Blob(chunks, { type: rec.mimeType });
        if (!blob.size) {
          if (active.current)
            setError("録音データが空です。もう一度録音してください。");
          onHolding(false);
          return;
        }
        const item: PendingRecording = {
          key: crypto.randomUUID(),
          sessionId,
          exerciseId,
          kind,
          blob,
          capture: Object.fromEntries(
            [
              "sampleRate",
              "channelCount",
              "echoCancellation",
              "noiseSuppression",
              "autoGainControl",
            ]
              .map((key) => [key, capture[key as keyof MediaTrackSettings]])
              .filter((entry) => entry[1] !== undefined),
          ) as Record<string, number | boolean>,
          createdAt: Date.now(),
        };
        void savePending(item)
          .then(() => {
            if (active.current) setItems((current) => [...current, item]);
            void upload(item);
          })
          .catch(() => {
            if (active.current) {
              setItems((current) => [...current, item]);
              setError(
                "録音をブラウザに保存できませんでした。この画面を閉じる前に再送してください。",
              );
            }
          })
          .finally(() => onHolding(false));
      };
      rec.onerror = () => {
        setError("録音に失敗しました。取得できた音声の保存を試みます。");
        stop();
      };
      rec.start();
      setRecording(true);
      timer.current = window.setTimeout(stop, 29500);
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

  return (
    <section className="panel practice" aria-label="会話の録音">
      <h3>任意録音</h3>
      <p>
        お手本に重ねる録音と、一時停止後の単独復唱を選べます。録音と評価は会話の進行条件ではありません。
      </p>
      <button
        disabled={(!paused && !overlapAllowed) || !reference || busy}
        onClick={() =>
          recording
            ? stop()
            : void begin(paused ? "isolated_repeat" : "shadowing_overlap")
        }
      >
        {recording
          ? "録音を停止して保存"
          : paused
            ? "単独復唱を録音"
            : "お手本と同時録音"}
      </button>
      {!paused && !overlapAllowed && (
        <p className="small">お手本再生中、または一時停止中に録音できます。</p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {items.length > 0 && (
        <div>
          <h4>保存待ち録音 ({items.length})</h4>
          {items.map((item) => (
            <div key={item.key} className="attempt">
              <p>元の参照文: {item.exerciseId.slice(0, 8)}</p>
              <PendingAudio blob={item.blob} />
              <button disabled={busy} onClick={() => void upload(item)}>
                再送
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void removePending(item.key).then(() =>
                    setItems((current) =>
                      current.filter((saved) => saved.key !== item.key),
                    ),
                  )
                }
              >
                破棄
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
