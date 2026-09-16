import { useState } from "react";
import {
  roleNames,
  type ModelList,
  type RoleModels,
  type TextRole,
} from "./types";

export default function ModelPicker({
  catalog,
  value,
  disabled,
  onChange,
  onRefresh,
}: {
  catalog: ModelList;
  value: RoleModels;
  disabled: boolean;
  onChange: (role: TextRole, id: string) => void;
  onRefresh: () => void;
}) {
  const [search, setSearch] = useState("");
  const groups: Record<string, string> = {
    "opencode-go": "OpenCode Go",
    opencode: "OpenCode Zen",
    compatible: "設定済み互換API",
    mock: "ローカルデモ",
  };
  const models = catalog.models.filter(
    (m) =>
      Object.values(value).includes(m.id) ||
      `${groups[m.provider]} ${m.id} ${m.name || ""}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <section className="panel model-picker" aria-label="LLMモデル選択">
      <div className="section-heading">
        <div>
          <span className="eyebrow">LANGUAGE MODEL</span>
          <h2>役割ごとのモデル</h2>
        </div>
        <button
          className="secondary small"
          disabled={disabled}
          onClick={onRefresh}
        >
          候補を更新
        </button>
      </div>
      <div className="settings-grid">
        <label>
          候補を絞り込む
          <input
            type="search"
            value={search}
            disabled={disabled}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Go / モデル名"
          />
        </label>
        {(Object.entries(roleNames) as [TextRole, string][]).map(
          ([role, name]) => {
            const selected = catalog.models.find((m) => m.id === value[role]);
            return (
              <label className="model-select" key={role}>
                {name}
                <select
                  aria-label={`${name}のモデル`}
                  value={value[role]}
                  disabled={disabled}
                  onChange={(e) => onChange(role, e.target.value)}
                >
                  {!selected && (
                    <option value={value[role]} disabled>
                      {value[role] || "モデルを選択"}（現在の候補にありません）
                    </option>
                  )}
                  {Object.entries(groups).map(([provider, label]) => (
                    <optgroup key={provider} label={label}>
                      {models
                        .filter((m) => m.provider === provider)
                        .map((m) => (
                          <option
                            key={m.id}
                            value={m.id}
                            disabled={!m.available}
                          >
                            {m.id}
                            {m.reason ? ` — ${m.reason}` : ""}
                          </option>
                        ))}
                    </optgroup>
                  ))}
                </select>
                {selected?.reason && (
                  <span className="small">{selected.reason}</span>
                )}
              </label>
            );
          },
        )}
      </div>
      <p className="small muted">
        {catalog.checked_at
          ? `提供一覧の最終取得: ${new Date(catalog.checked_at).toLocaleString()}`
          : "公開モデル一覧はまだ取得されていません。"}
        。
        {catalog.auto_refresh_hours
          ? `画面を開いている間は${catalog.auto_refresh_hours}時間ごとに自動更新。`
          : "自動更新は無効です。"}
        モデル変更は次の質問・Coachから適用します。
      </p>
      {catalog.refresh_error && (
        <p role="status" className="small">
          {catalog.refresh_error}
        </p>
      )}
      <details className="small muted">
        <summary>接続設定</summary>
        <p>
          このPCのOpenCodeに保存済みのGo /
          Zen認証情報を自動参照します。キーのコピーやブラウザへの入力は不要です。環境変数で指定したキーがある場合はそちらを優先します。
        </p>
        <p>
          候補更新は公開モデル一覧のみ取得します。モデルを選んで質問・Coachを実行すると、研究概要などの必要な入力を選択したサービスへ送信します。
        </p>
      </details>
    </section>
  );
}
