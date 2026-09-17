# 保存・読取・export/backup 契約

対象: `backend/app/db.py`, `main.py`。SQLite + ローカルファイル、単一プロセス。

## 読取

- `Database.read_snapshot()` は `BEGIN` で1つの読み取り時点を保持し、複数SELECTを一貫させる。ファイルI/Oやprovider処理をここで行わない。
- `Database.write_transaction()` は短い `BEGIN IMMEDIATE`。commit/rollbackを明示する。
- 通常の `GET /v1/sessions/{id}` は直近50 turn（`?turns=`で1〜200）だけ返し、`turns_total` / `turns_has_more` / `turns_before` を付ける。
- 履歴は `GET /v1/sessions/{id}/turns?before=<ordinal>&limit=<n>` で古い方向へcursorページングする。重複・欠落しない。

## 共有export

- `GET /v1/sessions/{id}/export` は `mode=share` / `schema_version=1.0`。公開会話・確定元・録音有無・評価・資料・Pack manifestを含む。
- API key・認証ファイル・ローカル絶対パス・requests内部payloadは含めない。
- schema versionは互換方針の単位。破壊的変更時はversionを上げ、旧export利用者向けに差分を文書化する。

## 完全backup / restore

- `backup_database()` はSQLite Backup APIで整合したDB複製を取得し、外部ファイルをmanifest（storage_key / bytes / sha256 / kind）で対応付ける。DB複製だけを完全backupと呼ばない。
- `verify_backup()` は `integrity_check`、`foreign_key_check`、migration version、各ファイルの存在・サイズ・hash、extra fileを検査する。中断・不足・改変は成功扱いしない。
- restore時は別データディレクトリで `verify_backup` を通してから利用する。検証なしに上書きしない。
- エンドポイント `POST /v1/maintenance/backup`（作成+検証）、`GET /v1/maintenance/backup/{name}`（検証）はサーバー生成名のみを扱う。

## requests

- `requests` は冪等性・処理管理のみ。公開会話・録音・評価・資料の正本は業務データ側にあり、requestsを削除・整理しても失われない。
