# DBマイグレーション運用

対象: `backend/app/db.py` の `MIGRATIONS`。SQLite + ローカルファイル、単一プロセスを前提とする。

## 仕組み

- `Database` 初期化時に `schema_migrations(version, name, checksum, applied_at)` を用意し、未適用のmigrationを番号順に1トランザクションで適用する。
- 適用済みversionのchecksum・nameが登録内容と一致しない場合、または登録に無い未来のversionを検出した場合は `MigrationError` で起動を止める。既存DBを空DBとして作り直さない。
- migration 1 `initial_schema` は初期コミット形式のDDLを `IF NOT EXISTS` で持つ。旧DB（`schema_migrations` が無く `sessions` が存在する）は migration 1 を適用済みbaselineとして認識し、以降の差分だけを適用する。
- migration 2 `session_settings_backfill` は起動時にmainで行っていたsettings JSON補完を版付き移行へ移したもの。`Database(data_dir, speech_defaults=...)` の値を使う。
- checksumは `ast.dump` で正規化する。関数の再フォーマットでは変化せず、論理変更でのみ変化する。**適用済みmigrationの本文は書き換えず、新しい番号を追加する。**

## 採番・実行順のルール

- versionは連番の整数。重複は `MigrationError`。
- PRごとに1つ以上のmigrationを追加してよいが、既存versionの編集は禁止。番号は `MIGRATIONS` の末尾に追加する。
- 複数PRが並行する場合は番号が衝突しうる。マージ時に後続PRが採番し直す。
- migration本体はDB書込のみ。外部LLM/TTS/評価器の呼び出しやファイル削除を入れない（再起動回復は `Database.recover` に分離する）。

## 表再構築が必要な移行

SQLiteで制約追加・列型変更・FK追加を行う場合は、公式の12手順に従い新表を作って移す。1 migration内で完結させ、`PRAGMA foreign_keys` は接続時にONのため、必要なら `PRAGMA legacy_alter_table` ではなく「新表作成→INSERT SELECT→旧表DROP→RENAME」の順で行う。外部キー参照中の表のDROP順に注意する。参考: https://www.sqlite.org/lang_altertable.html

## 移行前検査

未適用migrationがあると、適用前に `inspect_database` が複製DB上で以下を報告する（自動修復しない）。

- `PRAGMA foreign_key_check` 違反
- JSON列の不正、enum外の状態値
- `exercises.reference_hash` と本文の不一致
- turn/session、playback/session、attempt音声/sessionの所属不一致
- `audio_files` に対応する実ファイルの欠損

修復が必要な場合は、旧アプリを停止した整合した複製をbackupしてから手作業で直す。推測で履歴を書き換えない。

## 検証時のruntime

- アプリがリンクするSQLite: `3.45.1`（`Database.sqlite_version`、`uv run python -c "import sqlite3; print(sqlite3.sqlite_version)"`）。
- 接続ごとに `foreign_keys=ON`、`busy_timeout=10000`、`synchronous=NORMAL`、WALを明示する。
- 完全backup/restoreの実装は #20。ここではbackupが旧アプリ停止後の複製であることを前提にする。
