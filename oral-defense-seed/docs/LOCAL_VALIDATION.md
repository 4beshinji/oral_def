# このマシンのローカルLLM・TTS検証

2026-09-15。保存済みモデルを利用し、外部LLM/TTSへの送信や追加モデルのダウンロードは行わない構成。

## 使用した構成

| 項目 | 値 |
|---|---|
| GPU | AMD Radeon RX 6900 XT / gfx1030 / 16,368 MiB |
| ランタイム | ROCm 7.2.4、既存llama.cpp `60eeeb6` |
| モデル | `qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf`（保存名。独自派生・混合量子化） |
| モデルサイズ | 12,599,187,808 bytes |
| SHA-256 | `17ef7767533efd039d13594603bb0d9a787965759ec653b42f9221b50318e4d7`（再計算して保存済み値と一致） |
| 推論設定 | GPU 0、全層GPU、context 8192、Q8 KV、並列1、MTP draft 2、reasoning off |
| 接続先 | `http://127.0.0.1:10000/v1`、APIキーなし |
| アプリ設定 | `TEXT_PROVIDER=compatible`, `TEXT_RESPONSE_FORMAT=json_schema`, `TTS_PROVIDER=local` |
| 相手音声 | Kokoro Skyのアニメ調プリセット |
| お手本音声 | Piper LJSpeech high |

モデルは`~/code/agent/local/chat/models/qwen38/`、サーバーは同プロジェクトの`runtime/llama.cpp/build-rocm/bin/`にあった。別の保存済みモデル`~/.local/share/humeina-unit/models/Qwen3.8-27B-IQ4_XS.gguf`（約15.6 GB）も発見したが、今回の推論には使用していない。

## 起動

このREADMEの親に当たる`oral-defense-seed`ディレクトリで:

```sh
npm --prefix frontend run build
./scripts/run-local.sh
```

画面は <http://127.0.0.1:8000>。新規セッションではローカルモデルを全役割の初期値にする。Ctrl+Cでこのスクリプトが起動したアプリとLLMを終了する。既存のポート使用プロセスは停止しない。

- `ORAL_LLAMA_SERVER`、`ORAL_LLM_MODEL`で既存ファイルの場所を変更できる。
- `ORAL_GPU`の初期値は0。`ORAL_LLM_PORT`の初期値は10000。
- データは既定で`data/local-qwen/`、ログは`.cache/local-runtime/`。`ORAL_DATA_DIR`、`ORAL_LOG_DIR`で変更可能。
- 起動スクリプトはこのマシン向け。ROCmのライブラリパスを起動プロセスにだけ設定し、ドライバー・GPU設定・モデルファイルは変更しない。
- ローカル音声モデルとPython/Node依存は導入済みであることを前提とする。

既存の別サーバーに接続する場合はアプリ側だけで次を設定する:

```sh
TEXT_PROVIDER=compatible \
TEXT_BASE_URL=http://127.0.0.1:10000/v1 \
TEXT_MODEL=qwen38-27b-abliterated \
TEXT_RESPONSE_FORMAT=json_schema \
TTS_PROVIDER=local MODEL_CATALOG_REFRESH_HOURS=0 \
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

`TEXT_RESPONSE_FORMAT=json_schema`は対応するChat Completionsサーバー向けの任意設定。他の接続の既定は`json_object`を維持する。Responses/Messages形式の要求には適用しない。

## 再現する実provider検証

起動後、別ターミナルで:

```sh
cd frontend
VALIDATION_OUTPUT=../../.cache/local-validation/results \
node scripts/validate-local.mjs
```

同じJSON Packと「実験前・成果なし」の架空研究概要で、懇親会とゼミを各6往復。実LLM、実TTS WAV生成、HTTP取得、Chromeでの実再生完了、公開返答確定まで通す。役割別モデルをmockへ置き換えず、再生完了イベントも偽装しない。字幕は表示、再生速度は1.2倍。録音は行わない。結果のJSONとスクリーンショットを指定先に保存する。

Chromeはheadlessで、`--autoplay-policy=no-user-gesture-required`を指定する。これは音声デコード・再生終了からの進行確認であり、通常ブラウザのautoplay許可、人間による聴感や発音練習の評価ではない。

## 実検証で修正した点

1. 既存バイナリの共有ライブラリ参照が切れていた。起動プロセスの`LD_LIBRARY_PATH`にバイナリのあるディレクトリとROCmライブラリを追加。
2. `json_object`指定でも実モデルが通常の英文を返し、1往復後に形式検証が失敗した。選択可能なJSON Schema出力を追加。
3. Pydanticスキーマの文字数上限がllama.cppの文法展開サイズ制限にかかった。生成側のスキーマから文字数/配列数の上下限を除き、受信後のPydantic検証は維持。JSONの項目・型・必須条件・余分な項目の禁止は生成にも適用。
4. 3往復後に追質問上限違反で停止した。新しい話題へ移る必要がある場合は生成スキーマの`follow_up`をfalseに制限し、通常の返答検査も維持。これは話題の意味的な新規性を保証するものではない。
5. 懇親会が方法論の試問に偏った。場面ごとの役割を明確化し、挨拶・関心・研究生活などを含めるよう相手のプロンプトを調整。

### 生成品質の評価範囲

懇親会の完走出力では、研究概要にない「非滑らか・ノイズのある実験条件」や将来計画をCoachが補う例があった。また、話題を変えるフラグがfalseでも、意味としては過去の質問に近い場合がある。懇親会の挨拶は出たが、会話はなお技術寄りだった。

このため、会話の完走を生成内容の全面合格とは扱わない。懇親会の完走後、Coachに個人の研究条件・選択・結果の補完を禁止する指示を追加し、ゼミと固定質問で確認する。記録JSONの`prompt_note`で指示追加の前後を区別する。人間の研究事実の確認や、未接続のURL/PDF資料取り込みを代替するものではない。

## 実行結果

| 場面 | 確定した往復 | 実音声の正常再生完了 | 開始〜停止確認 | WAVの合計長（速度変更前） |
|---|---:|---:|---:|---:|
| 懇親会 | 6 | 12本 | 235.2秒 | 146.3秒 |
| ゼミ | 6 | 12本 | 283.5秒 | 183.7秒 |

- 全12返答の確定元は`shadowing_playback`。録音は0件、ブラウザの実行時エラーは0件。全24本に保存済み音声IDがあり、WAVを読み取れることも確認。
- 相手音声は24,000 Hz、お手本は22,050 Hz。Chromeによる実際の再生終了で進行し、アプリ側から完了を偽装していない。
- 観測したGPUメモリ使用量は約12.2 GiB。短文生成はログ上でおおむね毎秒30〜37トークン程度。単発の動作確認であり、厳密な速度比較や長時間安定性の試験ではない。
- JSON Schema導入後の最終完走では、形式エラーと追質問上限エラーは再発しなかった。話題の意味的な切替や事実に忠実な返答は別の品質課題として残る。
- Python/APIの回帰確認は**45 passed**。構造化出力の設定、必須項目、追質問の制約、生成側で長さ制限を省いても受信後に長文を拒否することを含む。Ruff、スクリプトの構文・整形検査も成功。

再現用の研究概要、公開会話全12往復、音声の長さ・参照hashは[保存した検証結果](validation/local-qwen-2026-09-15.json)を参照。元のSQLite・WAV・画面画像はワークスペースの`.cache/local-validation/`に保持している。

### 固定4質問による事実性チェック

最終のCoach指示を使い、研究概要にない詳細を質問した実Qwenの出力も結果JSONへ保存した。

- 数値的な改善結果: 実験未実施・数値なしと答えた。
- ベンチマーク名: 未指定なのにSphere/Rosenbrockを本人の計画として述べた。
- 評価回数・反復数: 未決と述べつつ、未提示の「数十回・数回」を補った。
- 自己紹介: 将来の比較計画を「現在比較している」と表現した。

したがって、**この4例では数値成果の質問は適切だったが、残る3例に出典のない具体化または時制のずれがあった**。プロンプトだけで事実性の要件を満たしたとは判断しない。接続・自動進行は確認済みでも、このモデルによる本人の研究説明を事実に忠実な完成機能とは扱わない。モデル比較や、本人の既知事実との照合は後続の品質改善課題とする。

起動スクリプトの実行でもLLM healthとアプリcapabilitiesのHTTP 200、および`compatible`・`json_schema`・ローカル接続先を確認した。終了後は8000/10000番ポートの待受がなくなり、検証用LLMのGPUメモリが解放された。検証サーバーは常駐させていない。

## 2026-09-29: 長い会話と人間録音の確認手順

13往復の自動検証は起動後に次で再現できる。結果は指定先に保存する。2026-09-29の[実測記録](validation/real-seminar-13-2026-09-29.json)は、ローカルQwen・Piper/Kokoro、Chromeの実再生完了、録音なしで13往復・音声26件を確認した。重複時の回復質問6件と、本人未提示の計画候補の置換3件があり、学術内容の一般的な正確性は確認していない。

```sh
cd frontend
VALIDATION_SCENARIOS=seminar VALIDATION_TURNS=13 \
VALIDATION_OUTPUT=../../.cache/local-validation/long-run \
node scripts/validate-local.mjs
```

人間のマイクでの最終確認は未実施。実施時はブラウザで会話を開始し、お手本再生中の同時録音を1件、停止中の単独復唱を1件行う。録音を再生して声・お手本の混入を聴き、元のturnと参照文にattemptが残ること、単独復唱の再読込後の再送、同時録音の`insufficient_evidence`、pause/resume中のマイク停止を確認する。識別できる実録音や研究内容は共有exportへ載せる前に内容を確認する。音響P0の精度判定は[P0報告](P0_REPORT.md)の別条件で行う。
