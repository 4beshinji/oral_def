# 設計変更履歴

## 2026-09-15 — シャドウイング仕様確定（文書のみ）

- ADR、設計、実装計画、受け入れ条件、開始指示をユーザー合意に統一。
- お手本正常再生完了で返答確定し自動会話。録音・評価は独立。
- 自由発話は任意、最終transcriptを返答とし厳密な発音評価を省く。
- 誤った旧設計はユーザー指示で削除し、旧版コピーを作らない。旧設計を含む展開元Seed ZIPも削除。展開済み実装は維持。
- 以下は過去の実装記録。新仕様の実装・検証完了を意味しない。

## 0.4 — OpenCodeモデル選択

ユーザー要求により、環境変数だけの固定LLMから、OpenCode Go/Zenの候補をGUIで選びセッションへ保存する方式に拡張する。

- モデル名の固定表を廃止。OpenCodeの公開一覧とmodels.devのprovider/modelメタデータを取得し、SDK形式からAPIを選ぶ。コードに個別モデル名や接頭辞の判定を持たない。
- GUIが開いている間は24時間ごとに自動更新。初回と期限切れ再表示時も取得し、失敗時はキャッシュを維持して5分後以降に再試行。手動更新も残す。モデル選択は自動変更しない。
- `x-opencode-session`は保存済みセッションIDと役割から決定的に生成。Examiner/Coachは別IDで、それぞれ追質問・再試行・再起動・モデル切替を通じて固定する。
- 全テキストadapterに固有User-Agentとsession headerを適用。APIキーはサーバー環境変数のみ。
- session headerはルーティング情報であり、providerの会話履歴共有には使用しない。既存のcontext分離を維持する。

## 0.3 — 2026-09-15 開発開始

設計0.2の主経路を保ったまま、展開先をアプリルートとしてP1/P2/P4の開発デモを実装。

- FastAPI / SQLiteとReact / Viteを追加。公開contextとCoach contextを明示的に分離し、API境界までsentinelテストを追加。
- 固定文練習、要求IDの保存・重複防止・中断状態を追加。
- 録音・再生・別attemptでの録り直し、字幕、dictation、短文カード、支援履歴、export/deleteを追加。
- システムffmpegがなかったため、同じffmpeg変換を行えるimageio-ffmpeg同梱バイナリを依存として採用。PATHにあるffmpegを優先する。
- TTSの未確定HTTP形式を、独立した完全endpointへinput/voice/model?/response_format=wavを送る契約として具体化。テキストendpointとは別に設定する。
- Kaldi環境・適合モデル・対照音声が未準備のため、P0報告とnullのmanifestを追加。CLI/APIはunavailableを返す。実音響評価を実装した扱いにはしない。
- 実LLM/実TTS未設定、人間の録音未検証。これらが残るためCore完成とはせず、検証済みの開発デモとして引き渡す。

