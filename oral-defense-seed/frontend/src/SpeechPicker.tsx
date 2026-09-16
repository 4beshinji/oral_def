import {
  speechRoleNames,
  type SpeechCatalog,
  type SpeechModels,
  type SpeechRole,
} from "./types";

export default function SpeechPicker({
  catalog,
  value,
  disabled,
  onChange,
  onPreview,
}: {
  catalog: SpeechCatalog;
  value: SpeechModels;
  disabled: boolean;
  onChange: (role: SpeechRole, id: string) => void;
  onPreview: (id: string) => void;
}) {
  return (
    <section className="panel" aria-label="役割ごとの音声合成">
      <span className="eyebrow">VOICE</span>
      <h2>役割ごとの音声</h2>
      <div className="settings-grid">
        {(Object.entries(speechRoleNames) as [SpeechRole, string][]).map(
          ([role, name]) => (
            <div key={role}>
              <label>
                {name}
                <select
                  aria-label={`${name}の音声`}
                  value={value[role]}
                  disabled={disabled}
                  onChange={(event) => onChange(role, event.target.value)}
                >
                  {!catalog.models.some(
                    (model) => model.id === value[role],
                  ) && (
                    <option value={value[role]} disabled>
                      現在の候補にありません
                    </option>
                  )}
                  {catalog.models.map((model) => (
                    <option
                      key={model.id}
                      value={model.id}
                      disabled={!model.available}
                    >
                      {model.name}
                      {model.reason ? ` — ${model.reason}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="secondary small"
                disabled={
                  disabled ||
                  !catalog.models.find((model) => model.id === value[role])
                    ?.available
                }
                onClick={() => onPreview(value[role])}
              >
                {name}を試聴
              </button>
            </div>
          ),
        )}
      </div>
      <p className="small muted">
        PiperとKokoroはこのPC内で合成します。Skyのアニメ調は声を高めに加工したプリセットです。変更は次の再生から適用し、設定はセッションに保存します。試聴には固定の英文を使います。
      </p>
    </section>
  );
}
