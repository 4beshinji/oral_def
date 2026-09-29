# 検証記録

## 2026-09-29: Kaldi GOP候補の実音声スモーク

- Kaldi公式ソースをCPUでビルドし、公開M13 chainモデルとSpeechOcean762の実話者録音1件でMFCC・i-vector・モデル出力・アラインメント・GOP計算まで実行した。GOPの21音素値は出たが、全非無声音素が1フレームに潰れ、通常設定では333フレーム中312を無音へ割り当てた。3倍間引きでも111中90フレームが無音だった。[測定値](validation/kaldi-p0-smoke-2026-09-29.json)と[P0報告](P0_REPORT.md)を参照。
- この候補は正常読みの有効時刻区間ゲートを通らないため、音響providerは`unavailable`のまま。1件の失敗から一般的な誤音診断精度を推測しない。適合non-chainモデルと別話者の対照実験が必要。

## 2026-09-29: 自由発話FS01〜FS04の実装検証

- `faster-whisper==1.2.1`、固定した英語`base.en`モデルをCPU int8で実行。モデルrevisionとSHA-256は`setup_asr.py`で固定・照合する。ローカルPiperで合成した「I have not run the experiment yet.」のWAVを、自由発話の録音upload→実ASR→最終返答確定APIへ通し、同文の認識、`free_speech_transcript`の確定元、録音有無を確認した。人間のマイク音声・認識精度の測定ではない。
- `backend/tests/test_free_speech.py`で同キー録音upload・同要求の確定重複抑止、無音/ASR失敗時の未確定、手入力の別由来、停止中のモード変更後に届く旧認識結果の不採用を検証。旧認識の画面後処理が新しい会話状態を停止しないよう、停止要求のrevision不一致も409で確認した。migration 11は空DB、旧DB、turn・確定返答・会話状態・音声ファイルのあるv10 fixtureで保持と整合性を確認した。実利用DBの非空コピーは未入手。
- 全backend `uv run --locked pytest -q`: **182 passed**。全Playwright: **17 passed**（自由発話の手入力と次問への進行、Chrome仮想マイクの録音→ASR未設定のエラー→reload後も録音保持、過去録音の表示を含む）。TypeScript/Vite build、Ruff check/format、Prettier、`git diff --check`成功。実ASRのChrome経由操作と人間マイク品質は未確認。
- 音響的な発音点数は実装していない。ASR transcriptの一致を発音評価とみなさない。FS01〜FS04のAPI状態遷移は確認したが、実録音を含む受け入れは継続する。

## 2026-09-29: RV06 実モデル13往復と未提示計画の抑止

- Radeon RX 6900 XT上の保存済みQwen GGUF、`json_schema`、temperature 0、ローカルPiper/Kokoroでゼミの合成概要をブラウザから13往復実行。最終の[質問・公開返答記録](validation/real-seminar-13-2026-09-29.json)は13確定、保存音声の正常再生26件、約411秒、ブラウザエラー0、疑問詞以降を含む同一質問0件。実人間マイクは使っていない。
- モデルが同じ質問を返す場合、直近12問との一致を確認して再生成し、なお重複する場合は事実を断定しない汎用質問へ切り替える。今回の回復質問は6件。前置きを変えた同じ疑問文、既出論点に近い回復候補も検出・回避する回帰を追加した。
- Coachの候補は本人入力の概要・メモ・下書きだけでモデル審査し、明示されない一人称の計画・結果表現は文字列照合でも拒否する。今回の候補置換は3件。モデル審査単独では未提示計画を見逃したため、両方の判定を生成来歴へ保存した。後続の時制表現に「実験に取り組んでいる」とも読める文が2件あり、この実行後に進捗表現の追加ガードとCoachプロンプトを修正した。追加ガードは単体・API回帰で確認し、実モデル13往復の再実行はしていない。
- 途中の実モデル検証では7問目の同一質問による停止、前置きのみ異なる質問の再発、Coachが未提示の複数試行計画を本人の計画とした事例を検出した。最終実行は機械的な13往復・録音なしの自動進行を通過したが、狭い合成概要では返答が定型化し、回復質問への依存が多い。未知の資料・人間の学術レビュー・一般的な事実性の合格は主張しない。
- 追加後の全backend回帰は`uv run --locked pytest -q` **175 passed**（進捗表現ガード追加前）。Ruff check/format、`git diff --check`成功。全Playwrightは**15 passed**で、単独復唱の失敗→reload→同キー再送、同時録音の次問前停止、51ターン履歴も確認。進捗表現ガード後の最終回帰は下記追記を参照。
- 進捗表現ガードと質問重複検出後、`uv run --locked pytest -q` **176 passed**、全Playwright **15 passed**、TypeScript/Vite build、Ruff check/format、Prettier、`git diff --check`成功。進捗表現ガードの実モデル再実行はしていない。
- その後、録音rename故障と再起動後の旧質問生成に対する故障注入を追加。対象試験は`test_recording.py` **11 passed**、`test_nonblocking.py` **9 passed**。全回帰の更新件数は最終チェックに記録する。
- 削除失敗の例外文と研究概要に秘密sentinelを入れて再起動を行い、DBには例外型だけを残しログにsentinelを出さないことを`test_deletion.py` **6 passed**の一例で確認。実運用ログ全体の監査ではない。
- 上記の録音・中断・削除故障注入後の全backend回帰は**178 passed**（依存側の非推奨警告2件）。Ruff check/format、変更frontendのPrettier、`git diff --check`成功。最後のUI変更後の全Playwrightは**15 passed**、TypeScript/Vite build成功。

## 2026-09-29: RV02 固定事実性ケースの実モデル再判定

- ROCm 7.2.4で`gfx1030`/Radeon RX 6900 XTを認識。保存済み12.6 GB GGUFを既存llama.cppでGPU 0へ読み込み、`/health`成功後に6件生成。検証後にサーバーを停止。
- 対象: `compatible/qwen38-27b-abliterated`、ローカル`127.0.0.1:10000/v1`、`json_schema`、temperature 0、各ケース1回。入力、出力、prompt/output hash、判定理由は[結果JSON](validation/factuality-after-2026-09-29.json)。外部LLMへの送信なし。
- 凍結した6ケースを意味で判定して**6/6 passed**。旧[結果](validation/factuality-before-2026-09-22.json)の3/6から、評価回数・時制・参考論文の成果混同の既知失敗が今回の出力では再発しなかった。
- 1モデル・各1回の固定例に限る。未知の質問や別資料への一般的な正確性、長い会話での安定性、人間による学術内容レビューを証明しない。

## 2026-09-29: 資料準備とPack固定

- 環境: Python 3.12、mock text/TTS、Chrome Playwright。実LLM・実論文へのネットワーク取得は行っていない。
- `uv run --locked pytest -q backend/tests/test_documents.py backend/tests/test_conversation.py`: **23 passed**。PDF uploadとサイズ/形式拒否、準備前の開始拒否、採用segment・Pack/hash/共有exportの一致、mock provider入力、開始後の再固定拒否を確認。
- `npm --prefix frontend run build`: 成功。`npm --prefix frontend run test:e2e -- --grep 'prepare a brief'`: **1 passed**。概要追加・採用・会話画面への移行と固定Packを確認。
- 全backend回帰の初回は会話音声設定1件が失敗（新規会話の初期状態をrunningにしたため）。初期状態を既存のpausedへ戻し、`uv run --locked pytest -q`で**159 passed**、全Playwrightで**13 passed**。Ruff check/formatも成功。
- URL取得時はDNSで確認した公開IPへ接続し、元のHost/TLS名を維持するよう変更。接続先・Host・SNIをmock transportで確認。変更後の全backend回帰は**160 passed**。
- PDF抽出をイベントループ外へ移し、抽出中のsession削除後は資料を保存しないことを並行fixtureで確認。Pack固定中のresumeも同じ固定版を参照。資料fixtureは**17 passed**。
- これは資料経路の状態・保存境界のfixture検証。実モデルの事実性、OCR、URLの実ネットワーク、並行競合を合格扱いにしない。

## 2026-09-29: 会話の単独復唱

- Chrome仮想マイクで会話停止中の録音、uploadを一回遮断した時のIndexedDB保存、reload後の再送、元参照文への一attempt保存、評価器未設定の状態表示を確認。`npm --prefix frontend run test:e2e -- --grep 'isolated recording survives'`: **1 passed**。
- お手本を止めずに録音し、再生完了時にマイクを閉じて次ターンへ進むケースもChrome仮想マイクで確認。元attemptは`shadowing_overlap`、評価runは`insufficient_evidence`。`--grep 'overlap recording closes'`: **1 passed**。
- 同時録音接続後の全Playwright回帰は**15 passed**。
- `uv run --locked pytest -q backend/tests/test_nonblocking.py`: **8 passed**。同sessionの評価遅延中に次turnへ進むこと、次turn後に元参照文へ録音を送ること、削除中に完了する録音・評価を拒否することを確認。
- `uv run --locked pytest -q backend/tests/test_recording.py`: **10 passed**。部分書込とDB登録失敗を注入し、一時/確定音声とattempt/audio行を残さないことを確認。
- 全回帰: `uv run --locked pytest -q` **162 passed**、`npm --prefix frontend run test:e2e` **14 passed**、TypeScript/Vite build、Ruff check/format、変更TypeScriptのPrettier、`git diff --check`が成功。
- 人間マイクでの音声混入量、ブラウザ保存容量不足、rename自体の故障は未確認。

## 2026-09-29: 旧DBの音声・評価移行

- `uv run --locked pytest -q backend/tests/test_migrations.py`: **13 passed**。初期schemaから、独立練習とshadowingの確定turn、複数attempt、録音ファイル、旧評価結果を移行し、元bytes・件数・hash・確定元・FK/integrityを確認。
- `uv run --locked pytest -q backend/tests/test_submissions.py`: **5 passed**。返答確定途中のplayback更新失敗を注入し、同一トランザクションの全変更が戻ることを確認。
- 実利用DBコピー、移行途中の全内容比較は未実施。

## 2026-09-22: 実装再開 — A01ターン所属・A06履歴画面

対象: `d4c0be3`を基点とする作業ツリー（先行の共有export/backup変更を含む）。担当 #9/#3/#4/#6、RV01/RV06、SH05/SH12/SH14/SH15/SH16の該当部分。

- Python 3.12.3、SQLite 3.45.1、Node 24.18.0、localhostのChrome。LLM/TTSはmock、録音はfixtureまたはChrome仮想マイク。
- `uv run --locked pytest -q`: **147 passed**。TestClientがサンドボックス内では停止するため制限外で実行。uvキャッシュは書込可能な`/tmp/oral-def-uv-cache`を指定。Starlette/httpx/anyioの既存非推奨警告2件。
- `npm --prefix frontend run test:e2e`: **10 passed**。ローカルテストサーバーとChromeを制限外で実行。TypeScript/Vite build、Ruff check/format、変更TypeScriptのPrettier、`git diff --check`も成功。
- A01: migration 9を追加。playback/conversation/assistance/submissionの同turn参照、再生出典と親所属の不変性、別turn INSERT/UPDATE拒否を検証。legacy/版8 DBに既存誤参照がある場合、業務データを変更せず移行停止。空DBとlegacy最終schemaの一致、FK/integrityを確認。
- A06: 通常GETの50ターン・50 playback/request、各turnの20参照文・参照文ごとの20 attemptを制限。全件数とcursorを別に返し、ページ外の録音あり判定を維持。51ターンから52ターン追加後のreloadで、全履歴に重複・欠落なく到達。23参照文・23 attempt・23評価runの境界をAPI/画面で確認。最古の録音をChromeで再生し、履歴閲覧による会話進行がないことを確認。
- 既存の13往復mock/context予算、共有export、別data dirへのbackup/restore、停止・再開、dictation、独立練習を回帰検証。画面記録は`frontend/test-results/conversation-history.png`（git対象外）。

これらは状態制御・保存・画面のfixture証拠。実LLM13往復の品質、人間マイク、Coach支援/資料/同時録音の未接続経路、A02〜A05/A07と #9-B/#7-Bの最終受け入れは未完了。

> 日付ごとの実測記録です。過去のAC番号は当時の試験識別子で、現在のSH/FS条件全体への合格を意味しません。MVPの現在の範囲と未達は[実装計画](IMPLEMENTATION.md)を参照してください。

## 2026-09-22 RV01共有exportとbackup/restore

担当 #9-A/#20、所見 RV01/RV07、関連 SH05/SH07/SH12/SH15。
対象: `d4c0be3aa7ddc89d6687526e63855eb25af13045` + 今回の未commit差分（`sharing.py`、`db.py`、`main.py`、API/E2E試験、共有保存ラベルと文書）。
変更前から存在した計画文書の差分は保持した。子Issueの条件との照合と未達は[DB_AUDIT](DB_AUDIT.md)。

環境: Linux x86_64、Python 3.12.3、SQLite 3.45.1、uv 0.12.5、Node v24.18.0、npm 11.16.0、Chrome 153.0.8010.36。既存のlockと環境を使用。
providerはmock、評価器はunavailable。音声は無音WAV fixtureまたはChrome仮想マイク。外部LLMへの資料送信、実音声モデル・人間マイクの追加検証は行っていない。

| コマンド | 結果 |
|---|---|
| `UV_CACHE_DIR=/tmp/oral-def-uv uv run --locked pytest -q` | **129 passed**、18.61秒。依存側の既知の非推奨警告2件 |
| `uv run --locked ruff check backend scripts` | 成功（同じ一時UV_CACHE_DIRを使用） |
| `uv run --locked ruff format --check backend scripts` | 34ファイル成功（同上） |
| `npm --prefix frontend run build` | TypeScript/Vite成功 |
| `npm --prefix frontend run test:e2e` | **9 passed**、13.9秒。専用localhostサーバーとChrome |
| `git diff --check` | 成功 |

制限環境内のTestClientは出力前に停止したため中断し、ローカル通常環境で実行した。書込不可の標準uv cacheは使わず一時cacheを指定。Chromeもlocalhostを利用可能な環境で実行した。

今回の挙動変更:

- 共有exportをschema 2.0へ変更。再開用snapshotから分離した許可項目方式とし、私的Coach履歴・メモ・未採用参照文・内部要求・provider設定・機器ID・任意の評価JSONを除外。既存のローカル履歴は保持する。
- 正常再生前の参照文を共有せず、確定した参照文のみ録音/評価の所属を保って出力。録音なし・transcript null・確定元を区別する。
- 会話用Packと異なるhashの新manifestを共有Packにしない。明示採用segmentだけを含め、余分な資料/segment・URL query等を除外。開始時のmanifest固定は未実装のためRV03合格にはしない。
- backupの一覧を元DBではなく複製済みDBから作り、音声追加/削除との競合を検査。既存保存先の上書きを拒否し、完成manifestを最後に公開。
- DB指紋を持つbackup manifest 1.1と旧1.0の検証。DB参照から消えたmanifest項目、余剰・欠損・破損・重複・不正パス/symlink・未完了保存・削除中を成功扱いしない。APIは作成失敗時503を返し、内部パスを返さない。

追加試験では修正前にbackupの7ケースが失敗した（未掲載音声、余剰ファイル、不正JSON、壊れたDB、未知manifest版、複製直後の音声追加、既存保存先上書き）。修正後は全件成功。
復元fixtureは公開返答、Coach私的note、録音、評価run、資料本文/hash、Pack manifestを作り、requests削除後も保持されることを確認。
backupを別data dirへcopy/verifyし、元data dirを削除して新しいアプリを起動した。通常GET/exportと実音声bytes、DBのFK/integrityを比較して成功した。
read snapshot試験はstate読取後・turn読取前に実APIから返答を確定し、同一snapshotが旧state/未確定turn、次GETが新state/確定turnを返すことを確認した。

構造整理は共有DTO組立の`sharing.py`への分離と、backup検証処理の分割。適用済みmigration、会話確定・録音APIの挙動は変更していない。
画面変更は保存リンクの「共有JSON保存」表記。既存3問練習E2Eで通常GETの私的assistance保持と共有側の除外を確認した。
[スクリーンショット](../frontend/test-results/practice-desktop.png)を生成・目視確認（ローカル生成物、Git対象外）。

未達: [DB_AUDITのA01〜A07](DB_AUDIT.md#親-9-の残作業と期限)。同session内別turn参照の直接SQL制約の不足を一時DBで再現し、その他の故障注入・生成来歴・資料固定・過去詳細取得の不足を条件ごとに記録した。
GitHub Issueへの転記、既存利用DBの移行、人間の #7-A/#7-B、機能接続後の #9-B は未実施。今回の129件/9件をRV01全体やMVP全体の合格証拠にはしない。

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
