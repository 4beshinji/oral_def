# 検証記録

> 既存実装の検証記録です。AC番号は当時の試験識別子で、現在のSH/FS条件への合格を意味しません。新しい自動会話仕様は未検証です。

## 0.4 OpenCodeモデル選択・更新

2026-09-15追記。モデル名の固定表は削除。OpenCode Go/Zenの公開一覧とmodels.devのメタデータからモデル名・provider・API形式を取得する。

- 実際の公開データ取得に成功。Go/Zen合計107モデル（mockを含めると108候補）。APIキーや研究内容は送信していない。この数は確認時点の値でコードの制限値ではない。
- 31件のPythonテストが成功。新しくランダムに作った架空モデル名がコード変更なしで選べること、未対応SDK形式と任意の外部URLを拒否することを確認。
- 全3 API形式・両OpenCode providerで固有User-Agentと固定したx-opencode-sessionをHTTP fixtureで検証。Examiner/Coachの分離、別セッションでの変更、再試行・再起動・モデル切替を通した維持、秘密情報のexport除外も確認。
- 24時間内は再取得しないこと、期限経過時の更新、取得失敗時のキャッシュ維持と5分の再試行待ちを時間制御テストで確認。
- Chrome E2Eは5件成功。モデル候補選択・保存・再読込・切替、初回の自動取得、時計を進めた24時間後の自動更新を確認。テスト用のモデル名・API応答だけを使用し、外部LLMへの生成を禁止した専用サーバーで実行した。
- TypeScript/ViteのbuildとRuffの静的チェック・整形チェックが成功。本番コード・同梱データに個別のOpenCodeモデル名が残っていないことを検索で確認。
- 実OpenCodeの生成は未実施。キー未設定であり、候補取得の成功を実LLMの利用成功とは扱わない。

以下は0.3初期実装時の記録。

実施日: 2026-09-15。対象: version 0.3.0 初期実装。設計Seedの受け入れ基準と実施結果を区別する。

## 現在の完成状況

| 項目 | 状態 |
|---|---|
| 開発デモ | 質問→Coach→回答編集→固定→練習→本人確定→追質問が動作 |
| MVP-Core（P1/P2/P4） | 実装を開始し主要経路を検証。ただし実LLM・実TTS・人間の録音未検証のため完成とはしない |
| 音響API/CLI境界 | 共有のunavailable結果、保存済み録音を使う評価操作・エラー保持を実装 |
| Kaldi実音響統合（P3） | 未実装。モデル自動取得・模擬GOP・STTによる代用なし |
| 音響ゲート（P0） | 未達。依存・入手条件の先行調査を実施。対照試験未実施 |

## 環境と実行結果

Linux x86_64 / Python 3.12.3 / uv 0.12.5 / Node 23.11.1 / npm 11.19.1 / Chrome 153.0.8010.36。Python・JavaScriptの依存は`uv.lock`と`frontend/package-lock.json`に固定。主な版とライセンスは[DEPENDENCIES.md](DEPENDENCIES.md)。

- `pytest -q`: **19 passed**。情報分離・状態遷移・再試行・保存・削除・容量・providerのHTTP fixtureを検証。
- `ruff check backend scripts`: 成功。`ruff format`でPythonコードを整形。
- `npm run build`: TypeScriptチェックとVite production buildが成功。
- `npm run test:e2e`: **3 passed**。ビルド済みUIをFastAPIで配信し、Chromeで操作。
- ffmpeg 7.0.2-staticによるWAV/WebMの復号・16kHz mono変換と30秒上限の検証に成功。
- `scripts/assess_one.py`をAPIテストで生成した0.5秒の無音WAVと参照文で実行し、duration_s=0.5、status=unavailable、calibrated_score=nullを確認。CLIの入口の検証であり音響ゲートではない。
- 通常の`data/`を使って127.0.0.1:8000で起動し、`/`のHTTP 200と`/v1/capabilities`のmock/unavailableを確認。

テスト環境の制限: サンドボックス内ではTestClientの非同期起動が停止した。制限外のローカル実行でテストが完了した。ネットワーク依存の取得とChrome/localhostの起動も必要な権限で実行した。テスト対象の外部APIへの資料送信は行っていない。

既知の警告: Starlette TestClientのHTTPX利用とAnyIO BlockingPortal aliasについて依存側の非推奨警告2件。テスト失敗はない。HTTPX adapterの別ライブラリへの移行はこの初期実装には含めない。

## 受け入れ基準との対応

| AC | 実施内容と判定範囲 |
|---|---|
| AC01 | mockで画面から3問分の一巡・再読込を確認。実LLMでの追質問の関連性は未確認 |
| AC02 | builderと実際のprovider呼出し境界でprivate sentinelが漏れない。本人の明示確定後だけ公開contextに現れる |
| AC03 | 録音済み参照文への変更endpointなし。新しい参照文は別exercise。元文・hashを保持 |
| AC04 | Chrome仮想マイクで録音・保存・再生・別attemptでの録り直し。人間のマイク・実TTSとの同時操作は未検証 |
| AC05 | NFKC・大文字・句読点・空白の正規化、単語欠落、短縮形と数詞が別扱いになることを確認 |
| AC06 | unavailable、phones空、calibrated_score=null、元音声の再生をAPI/画面で確認 |
| AC07 | 未実施。実音響モデルと独立対照セットなし |
| AC08 | 評価関数に504を注入し録音・固定文が残ることを確認。実Kaldi subprocessのtimeoutは未検証 |
| AC09 | 質問生成失敗後も確定回答を保持。同一要求IDの成功再送は同じ結果を返す。失敗したIDは自動再送しない |
| AC10 | 未回答で次の質問、確定済み回答の変更、未固定文の確定を409で拒否 |
| AC11 | Chrome再読込、JSON export、支援履歴とattemptの保持、APIキーと音声パスの除外を確認 |
| AC12 | セッションの子行・元録音・TTSキャッシュの削除、音声404、他セッションの音声保持を確認 |
| AC13 | 別turn/sessionのexercise関連付けを拒否。音声・TTSはUUIDだけを受け取り、生成したファイル名から解決 |
| AC14 | マイク拒否とTTS未設定で回答文が残り編集可能。自動再生拒否は手動プレーヤーを出す実装だが自動試験は未実施 |
| AC15 | mockは未知の研究結果にプレースホルダを残す。実LLMでの根拠・事実性・厳しい質問の品質レビューは未実施 |
| AC16 | 未校正結果のUIを区間・raw・品質フラグだけで構成。GOP実fixtureの画面検証は未実施。未設定時に点数を出さないことを検証 |

追加: 最大6問・追質問2回・Coach12往復、回答不能時に新しい論点へ移ること、処理中の変更をbusyで拒否、再起動時にprocessingをinterruptedにすること、TTSのセッション別キャッシュ、upload容量上限をAPIで確認。

## 再現するブラウザシナリオ

`frontend/tests/drill.spec.ts`で次を実施する。Chromeは仮想マイクを使用する。

1. 架空研究で開始、Coach全文案を取り込み編集、dictation、一致確認、再読込、3問の回答確定、exportと削除。
2. 固定文で2回録音、異なるattempt/audio ID、unavailable評価、元音声再生、再読込。
3. getUserMediaに権限拒否を注入、TTS未設定のエラーを表示、回答編集が続けられること。

実行時の画面は`frontend/test-results/practice-desktop.png`に出力する（git対象外）。テストデータは`.cache/e2e-data/`、各テストが作成したセッションは終了時に削除する。

## 残作業

1. 本人が選んだ実テキストAPI・実TTSの設定で固定シナリオを確認する。未知の成果を補っていないか、追質問が本人の回答に対応するかを人間が確認する。
2. 人間のマイクで録音・再録音・TTS停止・自動停止・ブラウザの音声品質を確認する。
3. [P0_REPORT.md](P0_REPORT.md)に従いKaldiの適合モデル・辞書・版・hash・入手条件を確定。単一音声CLIと独立対照試験を実装・実行する。
4. P0が通った実処理をAPIへ接続し、原音時刻・OOV・棄却理由・raw値の契約を検証する。それまでは発音評価付きMVPは未達。

未固定の画面内draftは再読込で失われる。固定済み参照文、保存されたCoach応答、処理要求の入力はSQLiteに残る。単一ユーザー・単一サーバープロセス・localhostの範囲で運用する。


## 2026-09-15: 既存認証の参照と役割別モデル設定

- OpenCodeの既存auth.jsonを読み取り専用で参照。ローカルPCのGoキーを取得できることを値を出力せず確認。実サービスへの生成要求は実施していません。
- HTTP fixtureで全6役割の送信モデル、役割別セッションID、個別変更、再起動後の保持、再試行を確認。認証の更新・削除・不正形式・環境変数優先と、公開データへの非保存も確認。
- バックエンドテスト、Ruff、TypeScript/Viteビルドが成功。
- Playwright: **5 passed**。文章修正と対話相手の独立した選択、他役割の保持、再読込を既存シナリオに追加。
- ローカルAPIテストはサンドボックス内で停止したため、制限外で実施しました。


## 2026-09-15: 用途別のローカル音声合成

- Piper LJSpeech highとKokoro v1.0を導入。モデル約447 MiB、取得元とSHA-256は`models/speech/downloads.json`に記録。
- 実モデルの固定英文合成: Piperは22,050 Hz / 4.40秒のWAVを約1.38秒で生成。Kokoro Skyアニメ調は24,000 Hz / 4.30秒のWAVを約1.63秒で生成。CPU上の単発測定であり、品質評価・性能保証ではありません。
- Kokoro ONNX 0.4.9がinput_ids形式のモデルへint32のspeedを送るため、配布モデルのfloat32入力に合わせて変換。実推論で確認。
- バックエンド既存34テストと追加の音声テスト1件が成功。追加テストは3用途のルーティング、声変更時のキャッシュ分離、試聴、再起動後の保持、未導入モデルの拒否を確認。
- 合成の主観的な自然さ・アニメらしさと、人間の発音訓練への効果は未評価。画面の固定文試聴で確認できます。
- 実API経由でも対話相手（24,000 Hz）・練習のお手本（22,050 Hz）・固定文試聴のWAV生成を確認。一時セッションを使用。
- Playwright **6 passed**。音声3用途の個別選択と再読込後の保持を追加。TypeScript/Viteビルド、Ruff、Prettierも成功。

## 2026-09-15: Coach主導の標準ループ

### 実装範囲

新規セッションの標準をshadowingに変更。`conversation.py`と`Conversation.tsx`で相手生成→相手再生→Coach生成→お手本再生→公開返答確定を接続した。`conversations`に段階とrevision、`conversation_playbacks`に対象turn・参照文版（exercise ID）・hash・音声ID・音声条件を保存する。既存DBへはテーブルを追加し、既存の`confirmed_reference`や独立練習は書き換えない。

生成要求は期待revisionを指定し、停止・版変更後の結果を保存しない。生成中はDB書込トランザクションを保持せず、停止を受け付ける。完了通知はrequest IDとplayback IDを検査し、保存参照文による返答確定と次段階の保存を同じトランザクションで行う。次の生成は別要求なので、失敗しても確定返答は残る。単一プロセスでの運用を前提とする。

参照文は編集でも上書きせず、新しいexerciseを作る。再生は一つのWAVまたは一つのブラウザutteranceを使用し、音声分割は未導入。ブラウザ読み上げは明示選択で、声の実体はOS/ブラウザに依存する。保存TTSの再生記録には音声ID・cache key・モデル・速度を残す。

### 自動試験と受け入れ条件の対応

- API: mockの13往復、録音なしの確定、相手再生/TTS取得による非確定、重複・取消・他session・旧版通知、request ID競合、参照文編集、確定後の生成失敗、生成中停止、再起動、後着録音の元exerciseへの保存、子データ削除を検証。SH02〜SH05、SH07〜SH09、SH12、SH15、SH16の状態制御を対象とする。
- context: Coachの私的sentinelを相手providerへ渡さず、再生完了後の採用文だけ公開。Packと概要を保ち、直近最大12件、公開会話16,000文字・Coach履歴8,000文字の予算で入力を制限する。古い会話の要約・事実抽出は未導入。単一の履歴項目が予算を超える場合、その項目より前も含め履歴から除外する。
- TTS: fixture WAVでお手本にcoach_answerの声を使用すること、停止中の音声設定変更で保存音声の関連を無効化すること、再生速度の保存を確認。
- Playwright: mock LLMと合成したブラウザ音声イベントで7往復の自動継続、録音なし、停止、再読込、過去のお手本の反復、再生失敗・再試行を確認。従来のモデル/音声選択、dictation、仮想マイク、保存・削除も回帰確認。

### 未検証・後続作業

- 実LLM/TTSによる6往復以上の関連した会話、場面に即した生成品質、実機のautoplay・ブラウザ音声品質は未検証。SH01・SH10と第1段階の完了条件全体は未達。
- 会話画面の同時録音と単独復唱、upload失敗時の画面内再送、評価遅延との分離は未接続。SH06は録音不要と後着uploadのAPI確認に限定。SH13の音響対照は未実施。
- 字幕・Coach説明・速度・過去反復は利用可能。説明は全文案に付随する説明で、任意の意味質問やヒントの会話画面への追加は後続作業。SH14は部分実装。
- URL/PDF取り込み（SH11）、自由発話（FS01〜FS04）、音響adapterは未実装。発音点数や音響精度の合格は主張しない。
- 今回は**標準ループのみ**。API/E2Eのmock・fixture成功を実provider品質や人間の録音品質の証拠にしない。

### 実行結果

- Python/API: **44 passed**（既存35件＋会話9件）。ローカルTestClientがサンドボックス内で停止したため、同じテストを制限外で実行した。
- Playwright: **9 passed**（既存6件＋自動会話3件）。ローカルサーバーとChromeを制限外で起動し、外部LLMへの送信を禁止したfixtureで実行した。
- Ruff、変更Pythonファイルのformat検査、TypeScript/Vite buildは成功。
- FastAPI/Starletteのテスト依存に非推奨警告2件あり。テスト失敗はなし。

## 2026-09-15: ROCm・ローカルQwen・実TTSの接続検証

- 保存済みQwen3.8系27B（12.6 GBの混合量子化GGUF）をRX 6900 XTで実行。既存llama.cppとROCmを使用し、モデルやドライバーは変更していない。
- 懇親会6往復・ゼミ6往復、実WAV 24本のChrome再生完了から公開返答確定まで確認。外部LLM/TTS、mock、偽の再生完了通知は使用していない。録音は0件。
- 実検証でJSON形式の不一致と追質問上限違反を検出。任意の`TEXT_RESPONSE_FORMAT=json_schema`と追質問条件の構造化制約を追加し、再検証で完走した。
- 生成内容には未提示の条件を補う傾向や質問の重複があり、プロンプトを調整。SH01の実providerによる自動進行は確認したが、SH10など生成内容の品質を全面合格とはしない。
- 回帰: Python/API **45 passed**。ローカル起動スクリプトと実providerのブラウザ検証スクリプトを追加。
- 構成、SHA-256、時間、会話全文、制約は[LOCAL_VALIDATION.md](LOCAL_VALIDATION.md)と[結果JSON](validation/local-qwen-2026-09-15.json)に記録。
