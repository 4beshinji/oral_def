# 保存・読取・export/backup 契約

2026-09-22更新。対象: `backend/app/db.py`, `main.py`, `sharing.py`, `history.py`。SQLite + ローカルファイル、単一プロセス。

## 読取

- `Database.read_snapshot()` は `BEGIN` で1つの読み取り時点を保持し、複数SELECTを一貫させる。ファイルI/Oやprovider処理をここで行わない。
- `Database.write_transaction()` は短い `BEGIN IMMEDIATE`。commit/rollbackを明示する。
- 通常の `GET /v1/sessions/{id}` は再開用の私的履歴を含み、直近50 turn（`?turns=`で1〜200）を返す。`turns_total` / `turns_has_more` / `turns_before` を付ける。
- 履歴は `GET /v1/sessions/{id}/turns?before=<ordinal>&limit=<n>` で古い方向へcursorページングする。
- turnページも直近20件ずつのCoach履歴・参照文・assistance、参照文ごとの直近20 attemptを含む。`*_total`と`*_before`で全件数と取得範囲を区別する。通常GETのplayback/requestは各50件、全件数は`playbacks_total` / `requests_total`。確定往復総数は`confirmed_turns_total`。
- `GET /v1/sessions/{sid}/turns/{tid}` は所属を確認して過去turnの詳細を返す。`GET /v1/turns/{tid}/exercises`、`GET /v1/exercises/{eid}/attempts`、`GET /v1/attempts/{aid}/assessments`は`{items,total,next_before}`を返す。`before`はサーバー発行cursor、`limit`は1〜100、既定20。新しい行が追加されても過去のcursor範囲は変わらない。
- `GET /v1/attempts/{aid}`は過去attemptの最新評価を含む詳細。評価runの実行状態と証拠状態を分離する。`has_recording`は取得済みの参照文/attemptだけで計算しない。
- 会話画面では古いページをIDで併合し、取得済み件数と総数を表示する。同じタブのsessionStorageへ取得範囲の先頭ordinalと開閉だけを保存し、reloadでサーバーから再取得する。録音データの端末保持契約とは独立。タブ終了後は直近ページから再度取得できる。
- 読取APIの制限は共有exportの全履歴やproviderのcontext予算を変更しない。

## 共有export 2.0

`GET /v1/sessions/{id}/export` は `mode=share` / `schema_version=2.0`。
通常snapshotを転用せず、`sharing.py::export_shared_session`が1つのread transactionで許可項目を組み立てる。

| 範囲 | 含むもの |
|---|---|
| envelope | schema_version、mode、exported_at、session |
| session | id、作成時刻、場面、状態、会話用Pack/hash、難易度3軸、全turnと総数、対応manifestと出典 |
| turn | 質問、確定返答、確定元、source exercise/playback ID、確定時刻、legacy由来状態、採用参照文、関連録音有無、transcript |
| exercise | 確定返答が参照する1件のみ。id、mode、本文/hash、origin、作成時刻、attempt |
| attempt | id、audio ID、input_kind、作成時刻、保存状態、音声形式/時間等の取得値、要求した音声処理の真偽値、transcript、評価run一覧 |
| assessment | 各runのid、実行状態、証拠状態、provider/model版、reference hash、error code、開始/終了/作成時刻 |
| 出典 | 対応manifestの明示採用segmentと本文/hash/位置、documentのhash・抽出版・provenance_role・URL・時刻 |

未確定の質問は共有するが、そのCoach案・練習参照文は共有しない。
確定後も未採用の別exerciseとその録音は私的履歴に残す。`has_recording`は共有対象の採用参照文に属する録音の有無を表し、未採用練習の録音有無を表さない。
旧データの参照元が不明/曖昧なら本文一致から推測しない。
ASR未実装のためtranscriptはnull。録音なしの正常再生による確定元を実録音や文字起こしで代用しない。

Coach私的履歴、user_note/draft、assistance操作履歴、research_brief入力欄、未採用参照文、内部requests、再開用conversation状態、全playback一覧、provider設定、機器ID、評価器の任意JSON/config/error messageは含めない。
API key・認証ファイル・サーバー保存パス・音声本体を含めない。出典URLのquery/fragmentを落とし、認証情報付きURLはnullにする。元ファイル名は共有しない。
ユーザーが入力したPackと公開会話・明示採用本文自体は共有対象であり、その本文内の内容を自動匿名化する機能ではない。

**資料接続の現状**: 会話の`pack_hash`と一致する保存manifestの最新候補だけを出す。一致候補がなければmanifestはnull、documentsは空。
別hashの新候補で会話用Packを差し替えない。segment_idsが空なら出典メタデータのみとし、本文を推測して追加しない。
開始時にmanifest IDを会話へ固定する契約は #5/RV03で未実装であり、同hash候補の一致を「開始時の採用出典の固定」の証拠にはしない。

### 1.0からの互換方針

従来の1.0は私的session snapshotを`mode=share`として返していた。2.0は共有範囲を縮小する破壊的変更であり、旧来の私的内容を返す互換モードは設けない。
既に保存した1.0ファイルは書き換えない。JSON import機能はない。

- `session.turns[*].confirmed_answer_en` / `submitted_via`、採用exerciseの`reference_text` / `reference_hash`、audio IDは維持する。
- 全exercise/coach_messages/assistance/requests/playbacks/research_brief等を前提とする利用側は、ローカル再開には通常GET、復元には完全backupを使う。
- provider生結果の`attempt.result/assessment`は共有しない。共有側は`attempt.assessments`の実行状態・証拠状態を読む。
- フロントエンドの保存リンクを「共有JSON保存」と表示。E2Eでは私的assistanceは通常GETから読み、共有JSONにはないことを確認する。

## 完全backup / restore

- `backup_database()` は新規ディレクトリだけを作成する。既存backupや不完全な保存先を上書きしない。
- SQLite Backup APIで整合したDB複製を取得し、音声一覧とmigration版は**複製済みDB**から読む。後から追加された元DBの音声を混ぜない。
- ファイルはサーバー生成の相対storage keyのみ。readyでない音声、削除処理中session、欠損音声、保存済み音声hashの不一致はbackup失敗として明示再試行する。削除・ファイルcopy中の競合で欠損した場合も成功にしない。
- manifest 1.1はDBファイルのbytes/sha256と音声のstorage_key / audio_id / bytes / sha256 / kind、migration版を持つ。全copy終了後に一時manifestをrenameする。資料は現状DB内の抽出本文・segment・hash・manifestを保存し、原PDFファイルは保存していない。
- `verify_backup()` は読取専用でDB hash、integrity/FK、既知migrationの版/name/checksum、DBの全音声参照とmanifest、ファイルの存在・サイズ・hash、missing/extra、不正パス/symlinkを照合する。不正JSON・壊れたDBも失敗reportで返し、修復しない。
- 旧manifest 1.0も検証できるが、当時含まれていないDBファイルhashは検査できない。音声・DB参照・migration・integrity/FKは検査する。
- `POST /v1/maintenance/backup`（JSON `{}`）はサーバー生成名で作成・検証し、成功時201。未完了・検証失敗はretryableな503。`GET /v1/maintenance/backup/{name}`は保存済みbackupの検証のみ。
- 完全backupは私的履歴・研究概要・内部要求と音声も含む復元用データ。共有JSONの代わりに配布するものではない。

### 別data dirへの復元手順

アプリを停止し、バックアップ名を実際の値へ置き換える。復元先には存在しないディレクトリを指定する。
アプリルートから実行する例:

```sh
uv run --locked python - data/backups/BACKUP_NAME data-restored <<'PY'
import shutil
import sys
from pathlib import Path
from backend.app.db import verify_backup

source, destination = map(Path, sys.argv[1:])
report = verify_backup(source)
if not report["ok"]:
    raise SystemExit(report)
shutil.copytree(source, destination)  # 既存data dirへの上書きを拒否
report = verify_backup(destination)
if not report["ok"]:
    raise SystemExit(report)
print("復元検証成功:", destination)
PY
DATA_DIR="$PWD/data-restored" uv run --locked uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

検証成功後にアプリから履歴・資料・音声を開く。起動時の回復処理や通常利用で復元先DBは更新されるため、元backupは別に保持する。
元backupのmanifestで稼働後のDBを再検証する手順ではない。
今回のfixtureは元data dirを削除した後に別data dirから読めることまで確認した。新規資料ファイルや機能の追加後は #9-Bで再確認する。

## requests

`requests` は冪等性・処理管理。公開会話・録音・評価run・資料の正本は業務データ側にあり、今回のfixtureではrequestsを削除しても保持される。
migration 10以降の生成来歴は質問・Coach message・exercise・TTS audioの`generation_json`へ不変保存する（[監査A04](DB_AUDIT.md)）。通常のturn詳細は`generation`として返す。旧データの来歴は推測せずnull。モデル/音声設定の変更、requests整理、再起動で遡及変更しない。共有exportでは生成設定を除外する。
今回requestsを削除する運用APIや自動掃除は追加しない。履歴が残ることと、旧要求IDでの再送が安全に可能な期間は別の契約である。

## 処理数の上限

アプリインスタンスごとに、LLM/TTS生成・録音解析・音響評価の重い処理を合計4件まで受け付ける。枠を待つキューは設けず、満杯はretryableな503 `capacity`。完了/例外時に枠を解放する。pause/end/再生完了/GETはこの枠を使用しない。自動で無制限に再送せず、UIで明示再試行する。単一workerで運用する。
