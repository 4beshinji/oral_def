import { useEffect, useState } from "react";
import { api } from "./api";
import type { Session } from "./types";

type Segment = { id: string; text: string; location: Record<string, number> };
type Source = {
  id: string;
  source_type: string;
  original_name: string | null;
  original_url: string | null;
  provenance_role: "learner_work" | "reference";
  extraction_status: string;
  error_code: string | null;
  segments: Segment[];
};

export default function DocumentPreparation({
  session,
  onReady,
}: {
  session: Session;
  onReady: () => Promise<void>;
}) {
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [brief, setBrief] = useState("");
  const [url, setUrl] = useState("");
  const [role, setRole] = useState<"learner_work" | "reference">("reference");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void api<Source[]>(`/sessions/${session.id}/documents`)
      .then((items) => {
        if (cancelled) return;
        setSources(items);
        setSelected(
          items.flatMap((item) => item.segments.map((segment) => segment.id)),
        );
      })
      .catch((cause) => {
        if (!cancelled) setError(cause.message);
      });
    return () => {
      cancelled = true;
    };
  }, [session.id]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "資料処理に失敗しました。",
      );
    } finally {
      setBusy(false);
    }
  }

  function addSource(item: Source) {
    setSources((current) => [...current, item]);
    if (item.extraction_status === "succeeded")
      setSelected((current) => [
        ...current,
        ...item.segments.map((segment) => segment.id),
      ]);
  }

  return (
    <section className="panel setup">
      <span className="eyebrow">SOURCE PREPARATION</span>
      <h2>会話に使う資料を選ぶ</h2>
      <p className="muted">
        採用する段落を選んでから開始します。本人の研究と参考資料は区別して会話に渡します。
      </p>
      <label>
        資料の位置づけ
        <select
          value={role}
          onChange={(event) => setRole(event.target.value as typeof role)}
        >
          <option value="reference">参考資料</option>
          <option value="learner_work">本人の研究</option>
        </select>
      </label>
      <label>
        研究概要・メモ
        <textarea
          value={brief}
          onChange={(event) => setBrief(event.target.value)}
          rows={4}
        />
      </label>
      <button
        disabled={busy || !brief.trim()}
        onClick={() =>
          void run(async () => {
            const item = await api<Source>(
              `/sessions/${session.id}/documents`,
              {
                source_type: "brief",
                provenance_role: role,
                text: brief,
              },
            );
            addSource(item);
            setBrief("");
          })
        }
      >
        メモを追加
      </button>
      <label>
        論文URL
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://..."
        />
      </label>
      <button
        disabled={busy || !url.trim()}
        onClick={() =>
          void run(async () => {
            const item = await api<Source>(
              `/sessions/${session.id}/documents`,
              {
                source_type: "url",
                provenance_role: role,
                url,
              },
            );
            addSource(item);
            setUrl("");
          })
        }
      >
        URLを取得
      </button>
      <label>
        PDFをアップロード（5 MiBまで、文字を抽出できるPDF）
        <input
          type="file"
          accept=".pdf,application/pdf"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            void run(async () => {
              const data = new FormData();
              data.append("file", file);
              data.append("provenance_role", role);
              addSource(
                await api<Source>(
                  `/sessions/${session.id}/documents/pdf`,
                  data,
                ),
              );
              event.target.value = "";
            });
          }}
        />
      </label>
      {sources.map((source) => (
        <div key={source.id} className="source-card">
          <strong>
            {source.original_name || source.original_url || "概要・メモ"}
          </strong>
          <span className="muted">
            {source.provenance_role === "learner_work"
              ? "本人の研究"
              : "参考資料"}
          </span>
          {source.extraction_status !== "succeeded" ? (
            <p role="alert">
              本文を抽出できませんでした（{source.error_code}）。
            </p>
          ) : (
            source.segments.map((segment) => (
              <label key={segment.id} className="source-segment">
                <input
                  type="checkbox"
                  checked={selected.includes(segment.id)}
                  onChange={(event) =>
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, segment.id]
                        : current.filter((id) => id !== segment.id),
                    )
                  }
                />
                <span>
                  {Object.entries(segment.location)
                    .map(([key, value]) => `${key} ${value}`)
                    .join(" / ")}
                  : {segment.text.slice(0, 500)}
                </span>
              </label>
            ))
          )}
        </div>
      ))}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button
        disabled={busy || selected.length === 0}
        onClick={() =>
          void run(async () => {
            const adopted = sources
              .map((source) => ({
                document_id: source.id,
                segment_ids: source.segments
                  .filter((segment) => selected.includes(segment.id))
                  .map((segment) => segment.id),
              }))
              .filter((source) => source.segment_ids.length > 0);
            await api(`/sessions/${session.id}/pack-manifest`, {
              schema_version: "1.0",
              pack: session.pack_snapshot,
              adopted,
            });
            await api(`/sessions/${session.id}/conversation/control`, {
              action: "resume",
            });
            await onReady();
          })
        }
      >
        資料を確定して会話画面へ
      </button>
    </section>
  );
}
