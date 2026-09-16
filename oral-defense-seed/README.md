# Oral Defense Drill

採用仕様は[ADR](docs/adr/0001-coach-led-shadowing.md)と[設計](docs/DESIGN.md)を参照してください。標準の自動会話ループを実装しました。資料取り込み・自由発話などの進捗は[実装計画](docs/IMPLEMENTATION.md)に記載しています。

相手の発言 → Coachのお手本 → シャドウイング → 次の発言を自動で続けるローカルアプリです。お手本の正常再生完了で保存済み参照文を公開返答にします。録音は必須ではありません。

現在は **動作する開発デモ** です。ローカルQwen＋Piper/Kokoroで懇親会・ゼミ各6往復の自動進行を実機確認済みです。生成内容の事実性には課題が残ります。外部LLM/TTS・人間のマイク録音は未検証、Kaldi発音評価は未実装のため、MVP-Core／発音評価付きMVPの完成とはしていません。[検証記録](docs/VALIDATION.md)と[P0報告](docs/P0_REPORT.md)を参照してください。

## 起動

Python 3.12以上3.14未満、Node 22.12以上の対応版、uv、npmが必要です。検証時の版はVALIDATION.mdに記載しています。以下はこのREADMEのあるディレクトリで実行します。

```sh
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

ブラウザで <http://127.0.0.1:8000> を開きます。終了はCtrl+C。単一プロセス用なので`--workers`を増やさないでください。デフォルトではLLM/TTSへ研究内容を送信しません。公開モデル一覧はGUI表示中に定期取得します。ffmpegがPATHになければ、imageio-ffmpegに同梱された実行ファイルを使用します。

開発時はAPIを上記で起動し、別ターミナルから次を実行します。

```sh
npm --prefix frontend run dev
```

開発画面は <http://127.0.0.1:5173>。`/v1`は8000番のAPIに転送されます。

## このマシンのローカルQwenで起動

保存済みのQwen3.8系27BとROCmを使う場合は、`./scripts/run-local.sh`でLLMとアプリを起動できます。共有ライブラリパス、構造化出力、ローカルTTSを設定し、終了時は起動した両プロセスを停止します。設定・検証結果は[LOCAL_VALIDATION.md](docs/LOCAL_VALIDATION.md)を参照してください。

## 最初のシャドウイング会話

1. 場面（アイスブレイク・懇親会・ゼミなど）、研究概要、JSON Pack、役割別モデルを選び、セッションを作成します。
2. 音声経路を選び「開始 / 再開」を押します。設定済みTTSは対話相手と回答案のお手本の声を使用します。ブラウザ読み上げは明示的に選択できます。
3. 相手を聞き、Coachのお手本に重ねて発話します。再生が正常終了すると参照文が一度だけ確定し、自動で続きます。録音・採点・毎回の承認は不要です。
4. 「一時停止」で止められます。速度と声の変更、未確定のお手本の編集・再生成は停止中に行います。字幕とCoachの説明は独立に表示できます。
5. 再生拒否・生成失敗からは「開始 / 再開」で保存済み段階から再試行します。再読込・サーバー再起動後は停止状態になり、未通知の再生完了は推測しません。
6. 公開履歴では確定元と録音有無を確認でき、過去のお手本の反復は会話を進めません。JSON保存には参照文・hash・再生ID・音声条件・録音有無・transcriptを分けて含めます。

自動会話には6問・Coach12回の終了制限を設けません。入力はPack・概要と直近最大12往復（公開会話16,000文字、Coach履歴8,000文字の予算内）に制限します。古い会話の自動要約は未対応なので、継続中に古い情報を忘れる場合があります。mockは固定応答であり、会話品質の検証には使いません。

同時録音と単独復唱の自動会話画面への接続、URL/PDF取り込み、自由発話ASRは未実装です。録音・dictationは次の独立練習を利用できます。

## 独立練習（従来の手動操作）

新規作成時に会話モードで「独立練習」を選びます。既存セッションの確定元は変更せず、この操作を維持します。

1. 研究概要を入力します。サンプルPackは架空のBayesian optimization練習用で、実際の研究結果を含みません。PackはJSONファイルから読み込むか直接編集できます。
2. 「セッションを作成」「最初の質問を生成」を押します。mockの質問は固定応答です。
3. 質問文を表示するか、明示的にブラウザ読み上げを選びます。保存TTSは実provider設定後に利用できます。
4. Coachの意味・ヒント・骨子・全文案を選び、回答文を編集して「この文で練習」を押します。
5. 音読・復唱では録音を開始し、停止して保存します。30秒弱で自動停止します。録り直しは別の試行になります。dictationではタイプした単語を比較します。
6. 「この内容で回答した」で固定文を本人の申告として確定し、「次の質問へ」で進みます。確定した回答は変更できません。

長文は編集可能な短文カードに分けられます。カードは個別録音、全文は元の回答欄を別途固定して本人が確定します。音声は自動結合しません。回答できない場合はチェックを入れ、申告文を固定・確定すると次は別の論点へ進みます。

## 役割ごとの音声合成

初期設定は`TTS_PROVIDER=local`です。画面の「役割ごとの音声」で、対話相手・回答案のお手本・練習用のお手本を独立して選び、試聴できます。設定はセッションごとに保存します。

- 対話相手: **Kokoro Sky・アニメ調**。米語の`af_sky`を1.18倍の高さに加工し、話す速さを補正したプリセットです。特定キャラクターの声ではありません。自然なSkyやHeartにも切り替えられます。
- 回答案・練習のお手本: **Piper LJSpeech high**。米語の女性音声を加工せず使います。
- 合成はPC内で実行し、APIキーは不要です。音声モデル・声の加工・元の英文が変わると新しく合成し、同じ条件では保存音声を再利用します。

初回のみ、依存パッケージとモデル（合計約447 MiB）を導入します。このPCには導入済みです。

```sh
uv sync --locked
uv run --locked python scripts/setup_speech.py
```

モデルは`models/speech/`に保存し、Git管理対象から除外します。別の場所を使う場合は`SPEECH_MODELS_DIR`を指定してください。ダウンロード元・SHA-256は同フォルダの`downloads.json`に記録します。未導入モデルは画面で選択不可になり、自動で外部サービスに切り替えません。

従来の音声APIは`TTS_PROVIDER=http`と下記の環境変数で利用できます。`mock` / `browser`は保存音声なしで開始します。「ブラウザで読み上げ」はOS側の別機能で、上記の音声選択を使いません。

音声モデルの出典: [Piper LJSpeechモデルカード](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ljspeech/high/MODEL_CARD)、[Piperエンジン](https://github.com/OHF-Voice/piper1-gpl)、[Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx)。PiperエンジンはGPL-3.0、Kokoro ONNXはMIT、KokoroモデルはApache-2.0です。LJSpeechのモデルカードでは学習データをpublic domainとしています。

## 実APIの設定

### OpenCodeのモデルをGUIで選ぶ

OpenCodeにログイン済みなら、このPCの既存認証ファイルを自動参照します。キーのコピーやブラウザへの入力は不要です。

- 参照先は`$XDG_DATA_HOME/opencode/auth.json`、未設定なら`~/.local/share/opencode/auth.json`です。別の場所は`OPENCODE_AUTH_FILE`で指定できます。
- `opencode-go` / `opencode`の`type: api`のキーを、対応するサービスだけに使います。OAuth認証は対象外です。
- 必要時に読み直すため、OpenCode側のキー更新・ログアウトを反映します。認証ファイルは変更せず、ブラウザ・DB・export・モデル一覧キャッシュにキーを保存しません。
- 環境変数`OPENCODE_GO_API_KEY` / `OPENCODE_ZEN_API_KEY`にキーがあれば優先します。provider別変数が未設定の場合は`OPENCODE_API_KEY`も利用できます。空の値の場合は既存ファイルを参照します。

「役割ごとのモデル」で、**対話相手・文章修正・対話手本（全文案）・質問の意味・ヒント・回答の骨子**の全6用途を個別に選べます。変更はその役割の次の呼び出しから有効で、セッションに保存されます。既存セッションは従来のモデルを全役割の初期値として引き継ぎます。「文章修正」は回答欄の下書きがある場合に利用でき、意味を保って英文を修正します。

モデル名はハードコードしていません。Go/Zenの公開`/models`と[models.dev](https://models.dev/)のAPIメタデータを組み合わせ、取得した候補・形式・日時を`data/opencode-models.json`にキャッシュします。初回に接続できない場合、架空の候補は表示しません。

- GUIが表示されている間は24時間ごとに自動更新。初回・24時間以上経過した再表示時にも更新します。画面を閉じている間の常駐ジョブはありません。
- `MODEL_CATALOG_REFRESH_HOURS`で間隔を変更できます。`0`で自動更新を無効化。「候補を更新」ボタンは引き続き使えます。
- 失敗時は最後の成功データを維持し、5分後以降に再試行します。更新しても選択済みモデルは勝手に変更しません。
- API形式はモデルメタデータのSDK指定から選択します。現在はChat Completions / Responses / Messages形式に対応。未知の形式・メタデータ未登録・Google形式などは候補に「未確認または未対応」と表示し、推測で送信しません。

OpenCodeへの全LLM呼び出しに`User-Agent: oral-defense-drill/0.4.0`と`x-opencode-session`を付けます。後者は保存済みセッションUUIDと役割から生成したUUIDで、6つの役割ごとに別々。同じ役割の追質問・手動再試行・再起動・モデル切替では変化しません。要求ごとの`request_id`とは別の識別子です。公開一覧取得にも独立した固定IDと固有User-Agentを使用します。

このヘッダーはルーティングの識別子であり、provider側の会話履歴を共有するものではありません。役割別contextの分離は引き続き維持します。[OpenCode Go公式のクライアント要件](https://opencode.ai/docs/go/#where-can-i-use-it)に従った実装ですが、Goはcoding agent向けと案内されており、この用途のサービス側の受け入れや稼働を保証するものではありません。

### 環境変数の読み込みと互換API

[.env.example](.env.example)を参考にサーバーの環境変数を設定し、再起動します。`.env`は自動では読み込みません。シェルで読み込む場合は、自分で作成した`.env`に対して次を実行できます。

```sh
set -a
source .env
set +a
uv run --locked uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

キーは環境変数または既存のOpenCode認証ファイルから読み込みます。画面の「接続先と保存方針」に送信先とモデルを表示します。providerを明示設定すると、LLMにはPack・研究概要・役割別の履歴、TTSには選択した保存英文を送信します。

| 用途 | 設定とHTTP契約 |
|---|---|
| テキスト | `TEXT_PROVIDER=compatible`、`TEXT_BASE_URL`、`TEXT_MODEL`、必要なら`TEXT_API_KEY`。BASE_URLの末尾に`/chat/completions`を追加してPOST。`messages`、`model`、`response_format={type:json_object}`、`max_tokens`を受け取る互換APIが対象 |
| TTS | `TTS_PROVIDER=http`、`TTS_BASE_URL`（完全な音声endpoint）、必要なら`TTS_MODEL`・`TTS_API_KEY`、`TTS_VOICE`。`{input,voice,model?,response_format:"wav"}`をPOSTし、PCM WAV本体を受け取るAPIが対象 |
| ブラウザ音声 | 「ブラウザで読み上げ」を押したときだけ実行。OS・ブラウザの音声サービスを使い、基準音声として保存しない |
| 発音 | 既定`PRONUNCIATION_PROVIDER=unavailable`。`kaldi`を指定してもP0未達のためunavailable。モデルの自動取得や別のクラウド評価器への切替は行わない |

実APIの資格情報をアプリ内に新規保存しません。HTTP fixtureに加え、ローカルQwenでは実会話の自動進行を確認済みです（[実機検証](docs/LOCAL_VALIDATION.md)）。生成品質の全面検証、外部サービスの音声品質・課金は未検証です。失敗した要求は自動再送しません。手動再試行には新しい要求IDを使い、結果不明の場合は再課金される可能性があります。

## 保存と削除

デフォルトの`data/drill.sqlite3`に会話・Coach履歴・固定文・試行・支援操作・要求状態、`data/audio/`に元音声とTTS音声を保存します。`DATA_DIR`で変更できます。削除するまで保存します。参照文は作成直後から変更不可です。

セッション一覧から再開できます。「JSON保存」は版付きデータをexportし、APIキー・サーバーのファイルパス・音声本体を含みません。音声IDは記録されますが、JSONは音声のバックアップではありません。「削除」は確認後に子データと音声も削除し、元に戻せません。未固定の編集文は画面内だけなので、再読込前に固定してください。

## 検証コマンド

```sh
uv run --locked pytest -q
uv run --locked ruff check backend scripts
uv run --locked ruff format --check backend scripts
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

E2Eは空いている8000番ポートを使用し、専用の`.cache/e2e-data/`にテストデータを保存します。既定で`/usr/bin/google-chrome`を使い、別のChromiumは`CHROME_BIN=/absolute/path`で指定できます。仮想マイクによる試験なので人間の発音評価の証拠にはなりません。

P0のCLI境界は次で実行できます。入力の音声変換・長さ確認を行い、APIと同じunavailable結果を返します。raw GOPはまだ計算しません。

```sh
uv run --locked python scripts/assess_one.py /absolute/path/recording.wav "Your fixed reference sentence."
```

## 構成

```text
backend/app/       FastAPI、SQLite、役割別context、provider、音声・dictation
backend/tests/     情報分離・順序・保存・失敗・入力境界のテスト
frontend/src/      React / TypeScriptの1ページ練習UI
frontend/tests/    Chromeでの操作・仮想マイク・再読込の検証
prompts/          Examiner / Coachの独立プロンプト
examples/         サンプルPackとシナリオ
scripts/          音響CLIの入口
models/manifest.json  未取得のモデル・辞書・版を明示
docs/             設計・P0報告・受け入れ基準・検証記録
data/             ローカルDB・音声（git対象外）
```

設計の入口は[CODEX_START.md](CODEX_START.md)、採用仕様は[DESIGN.md](docs/DESIGN.md)。依存は`uv.lock`と`frontend/package-lock.json`で固定しています。[依存・ライセンスの所在](docs/DEPENDENCIES.md)も参照してください。
