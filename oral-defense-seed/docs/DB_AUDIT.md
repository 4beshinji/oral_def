# #9-A: DB契約と検証証拠の照合

2026-09-22。担当 #9、所見 RV01/RV07、関連 SH05/SH07/SH12/SH15。
対象は `d4c0be3aa7ddc89d6687526e63855eb25af13045` と今回の作業ツリー差分。
同日にGitHubから読み取った #10/#11/#12/#13/#15/#16/#18/#19/#20 の「受け入れ条件」を、記載順に対応付ける。
子Issueのclosedは合格判定に使わない。GitHub Issueへの書き込みは行っておらず、本書を親 #9 への転記元とする。

「確認」は記載したfixtureの範囲、「部分」は条件の一部だけに証拠があるもの、「未検証」は対応試験なし、「不一致」はコードまたは再現操作で不足を確認したもの。
実provider、人間録音、既存利用DBのコピーによる移行、機能統合後の #9-B は今回の証拠に含めない。
実行結果・環境は [VALIDATION](VALIDATION.md#2026-09-22-rv01共有exportとbackuprestore)、仕様は [PERSISTENCE](PERSISTENCE.md)。

## #10 migration

実装: `db.py::Database.migrate/_apply/inspect_database` と番号1〜10のmigration。
以下の `migrations::` は `backend/tests/test_migrations.py::test_` を表す。

| 順 | 受け入れ条件 | 証拠と不足 |
|---|---|---|
| 1 | 空DBと旧DBが同じ最終schema | 部分: `migrations::fresh_database_records_migrations_and_is_idempotent`、`legacy_database_is_baselined_and_settings_backfilled`。両schemaの直接比較は未検証 |
| 2 | 二度の起動・移行で増殖しない | 確認: `migrations::fresh_database_records_migrations_and_is_idempotent` |
| 3 | 移行失敗の原子性 | 確認: `migrations::failed_migration_does_not_record_or_partially_apply` |
| 4 | 旧独立練習・shadowing・複数attemptの移行 | 部分: `migrations::legacy_confirmed_turn_backfill_provenance` は確定元と複数参照文。実音声付き複数attempt/旧評価は未検証 |
| 5 | 不整合報告、黙った削除・意味変更なし | 部分: `migrations::inspect_database_reports_without_repairing`。全不整合種別・移行途中の保持比較は未検証 |
| 6 | 件数・本文/hash・確定元・音声を移行前後比較 | 部分: `migrations::legacy_confirmed_turn_backfill_provenance`。実音声・全件数比較は未検証 |
| 7 | FK/integrity検査 | 部分: 旧DB fixtureでFK、今回の復元fixtureで両検査。旧DB移行に両検査を揃える作業は残る |
| 8 | 明示列INSERT後のbackend回帰 | 確認: backend全体。既存migration本文は今回変更なし |

## #11 所属・不変性

実装: `db.py::_V3_TABLES/_V3_AFTER`、conversationとAPIの所属検証。
`constraints::` は `backend/tests/test_db_constraints.py::test_`。

| 順 | 受け入れ条件 | 証拠と不足 |
|---|---|---|
| 1 | 別session/turnの組合せを直接SQLで拒否 | **不一致**: `constraints::cross_session_children_are_rejected` は別sessionのINSERTのみ。同sessionの別turn参照をmodel playbackへINSERTできた（下記 A01） |
| 2 | 不在・別sessionの現在playbackを拒否 | 確認: `constraints::conversation_current_playback_must_exist_and_belong` |
| 3 | questionのNULL reference | 確認: `constraints::playback_stage_reference_rules` |
| 4 | modelのNULL reference拒否 | 確認: 同上 |
| 5 | playing一意、取消後の再開 | 確認: `constraints::only_one_playing_per_session_and_recancel` |
| 6 | 本文/hash/所属のUPDATE拒否、新版追加 | 部分: `constraints::reference_is_immutable_and_new_version_is_allowed`。有効な別所属へのUPDATEの網羅は残る |
| 7 | pause/resume/reload/再生成/削除 | 確認: `test_conversation.py` と `test_existing_shadowing_flow_still_passes_constraints`、Chromeの会話3ケース |
| 8 | 旧DB誤所属の事前検出 | 部分: `test_migrations.py` の不整合fixture。全組合せの旧DB移行停止・保持は未検証 |

## #12 公開返答

実装: `db.py::turn_submissions/turn_row`、`conversation.py::ended`、`main.py::confirm`。
`submissions::` は `backend/tests/test_submissions.py::test_`。

| 順 | 受け入れ条件 | 証拠と不足 |
|---|---|---|
| 1 | 同通知・別requestでも一確定 | 確認: `submissions::shadowing_confirmation_writes_one_submission_with_sources` |
| 2 | 旧版・取消・別session・停止後の拒否 | 確認: `submissions::cancelled_playback_does_not_write_submission` と `test_conversation.py` の取消/旧版ケース |
| 3 | 確定TX途中の故障で片側だけ残らない | 未検証: BEGIN IMMEDIATEは存在。commit直前の故障注入は残る |
| 4 | 次生成失敗でも返答保持、再試行一意 | 確認: `test_conversation.py::test_generation_failure_preserves_commit_and_retry_no_duplicate` |
| 5 | 公開履歴はturns+submissions、私的案なし | 確認: provider境界sentinel、今回の `test_exports.py`（採用参照文のみ共有） |
| 6 | legacy出典不明・旧API/export互換 | 部分: legacy provenance fixture。API保持、共有exportは意図的に2.0へ変更し差分を文書化。全旧データの互換は未検証 |
| 7 | 独立練習とshadowing回帰 | 確認: `submissions::independent_confirm_uses_same_register`、backend/Chrome |

## #13 録音attempt

実装: `db.py::_RECORDING_DDL`、`main.py::upload_audio`。
`recording::` は `backend/tests/test_recording.py::test_`。

| 順 | 受け入れ条件 | 証拠と不足 |
|---|---|---|
| 1 | 保存成功後の再送で増殖しない | 確認: `recording::same_key_resend_returns_same_attempt`（通信応答消失は同body再送で模擬） |
| 2 | 同キー・異内容/異所属を409 | 確認: `recording::same_key_conflicting_content_or_owner_is_rejected` |
| 3 | 同時再送一件、録り直し別件 | 確認: `test_nonblocking.py::test_concurrent_same_key_upload_writes_one_attempt`、`recording::retake_uses_new_key_and_new_attempt` |
| 4 | 参照文再生成後も録音所属保持 | 部分: 不変exercise/新版作成の試験。遅延uploadとの組合せは未検証 |
| 5 | 過去turnへの遅延upload | **部分**: `recording::late_upload_stays_on_original_exercise` はconfirm後にuploadするが次turnを生成していない。名称・コメントだけで合格にしない |
| 6 | 未完了・不正形式・容量超過・書込失敗 | 部分: 形式/容量/not-readyのAPI試験あり。write/rename/commit故障注入は未検証 |
| 7 | 旧録音をisolatedと推測しない | 部分: migration defaultは`unknown`。音声付き旧DBの移行fixtureは残る |
| 8 | Practice/Conversation共通API | 部分: PracticeのChrome録音は確認。Conversation録音UIは #4 の未実装 |

## #15 削除寿命

実装: `db.py::purge_session/recover/reconcile_audio_files`。
`deletion::` は `backend/tests/test_deletion.py::test_`。

| 順 | 受け入れ条件 | 証拠と不足 |
|---|---|---|
| 1 | unlink失敗後にdeleting・対象保持 | 確認: `deletion::unlink_failure_is_resumable` |
| 2 | 再起動・削除再送で完了 | 確認: 上記と `deletion::restart_resumes_deletion` |
| 3 | 削除中の遅延upload/生成/評価が再作成しない | 部分: `deletion::upload_and_generation_are_rejected_while_deleting` は削除中の新uploadのみ。既に実行中の3経路との競合は未検証 |
| 4 | 他sessionファイル保持 | 確認: `deletion::delete_leaves_other_session_files` |
| 5 | missing音声の検出・表示 | 部分: `deletion::reconcile_reports_and_cleans_stray_files` とaudio 404。画面表示は未検証 |
| 6 | 処理中・参照中を清掃しない | 部分: `deletion::reconcile_keeps_processing_temp_files` は新しい一時ファイル。猶予期間を越えた処理中ファイルは未検証 |
| 7 | 正常削除の子行・ファイル・404 | 確認: `test_drill.py::test_record_retake_unavailable_export_and_delete`、資料cascade、Chrome削除 |
| 8 | 削除ログに秘密情報なし | 部分: 保存するerrorは例外型のみ。caplogによるログsentinelは未検証 |

## #16 評価履歴

実装: `db.py::_ASSESSMENT_DDL/_backfill_assessment_runs`、`main.py::assess`。
`assessment::` は `backend/tests/test_assessment.py::test_`。

| 順 | 受け入れ条件 | 証拠と不足 |
|---|---|---|
| 1 | 同録音2評価で2run | 確認: `assessment::reassessment_appends_and_preserves_history` |
| 2 | 同要求再送一件、異入力キー拒否 | 部分: `assessment::same_request_resend_does_not_add_run`。異attemptへのキー再使用は未検証 |
| 3 | 失敗した再評価が旧成功を消さない | 確認: `assessment::failed_reassessment_keeps_earlier_success` |
| 4 | 未設定/不足/失敗を0点にしない | 確認: 上記と`test_drill.py`のunavailable/timeout。実音響診断は対象外 |
| 5 | succeeded+insufficientを表現 | 確認: `assessment::shadowing_overlap_is_insufficient_evidence` とrun保存処理 |
| 6 | overlap/isolatedの区別 | 部分: overlap fixtureあり。実録音の混入判定ではない |
| 7 | 次turnへ進んでも元attemptへ保存 | 未検証: slow assessment試験は別session作成のみ（下記 #18-1） |
| 8 | deleting/削除後の遅延結果拒否 | 未検証: 保存前lifecycle確認はあるが、並行削除の故障注入が残る |
| 9 | 再起動でinterrupted | 確認: `assessment::restart_marks_running_run_interrupted` |
| 10 | provider/版/config/reference hash追跡 | 部分: `assessment::reassessment_appends_and_preserves_history`。設定変更後の非遡及は未検証 |
| 11 | 誤所属拒否、旧dictation保持 | 部分: FKと新規dictationの`assessment::dictation_is_not_an_assessment_run`。旧データ移行と誤所属SQLの専用試験は残る |

## #18 並行処理

実装: `main.py`のclaim/外部処理/commit、`conversation.py`のrevision検査。
`nonblocking::` は `backend/tests/test_nonblocking.py::test_`。

| 順 | 受け入れ条件 | 証拠と不足 |
|---|---|---|
| 1 | upload/評価中もended/pause可能 | **部分**: `nonblocking::slow_assessment_does_not_block_new_work` は別session作成とGETのみ。同sessionのended/pauseは未検証 |
| 2 | pause/end後の古いLLM/TTS結果を拒否 | 部分: `test_conversation.py::test_pause_during_provider_call_discards_late_generation` はpause/LLM。end/TTSの組合せは残る |
| 3 | 会話進行後も元録音/評価へ保存 | 部分: #13-5/#16-7参照。次turn中の完了は未検証 |
| 4 | 同時step/ended/upload一意 | 部分: step/upload並行とended逐次重複は確認。同時ended専用ケースは残る |
| 5 | delete後の遅延完了で復活しない | 未検証: #15-3/#16-8と同じ不足 |
| 6 | 外部処理中の短いwrite TX | 部分: `nonblocking::slow_provider_does_not_serialize_other_sessions` とslow assessment。upload/TTS/SQLite busyの計測は残る |
| 7 | 別sessionを共通lockで409にしない | 確認: 上記slow providerと `test_drill.py::test_operations_are_independent_of_a_process_wide_lock` |
| 8 | モデル変更・再起動・再試行で実行時snapshot保持 | **不一致/部分**: 生成のprovider設定はrequests内にあり、業務データに独立した来歴がない。評価runは専用configを持つ。requests整理後の生成来歴は保証されない |
| 9 | pause回帰、処理数/再試行上限 | 部分: pauseと無制限自動再試行なし。明示したサーバー処理数上限・旧実行者の再claim試験は未達 |

## #19 資料・Pack

実装: `documents.py`、`db.py::_DOCUMENTS_DDL`。
`documents::` は `backend/tests/test_documents.py::test_`。

| 順 | 受け入れ条件 | 証拠と不足 |
|---|---|---|
| 1 | 概要だけで開始 | **部分**: 概要保存は`documents::brief_becomes_tracked_document_and_pinned_manifest`。現session作成は既存Packが必須。概要→Pack生成は #5/RV03 |
| 2 | PDF/URLのhash・抽出版・位置 | 確認: `documents::url_html_extraction_and_location/url_pdf_extraction`。URL取得fixtureのみ、PDF uploadは未実装 |
| 3 | 抽出失敗の採用拒否 | 確認: `documents::failed_extraction_is_not_adoptable` |
| 4 | 後日のURL変更で会話背景不変 | **不一致/部分**: `documents::later_url_change_does_not_rewrite_pinned_manifest` は保存manifestのみ。manifestと会話用`pack_snapshot_json`を同時固定していない |
| 5 | 本人の成果と参考資料を区別 | 確認（保存のみ）: `provenance_role`とbrief fixture。providerが区別して使う経路は未接続 |
| 6 | 内部URL/redirect拒否 | 確認: `documents::internal_targets_are_rejected/redirect_to_internal_address_is_rejected`。実ネットワーク/名前解決後のIP変更への耐性は未検証 |
| 7 | 文書内命令をsystemにしない | 部分: `documents::prompt_injection_stays_in_segment_data` は保存確認。会話providerへの資料接続後に境界試験が必要 |
| 8 | 資料の削除寿命 | 部分: `documents::session_delete_cascades_documents` はDB内本文/segment。原PDF等は未保存のため外部資料ファイルの削除は未検証 |
| 9 | 採用segmentのexport追跡 | 確認（限定）: `test_exports.py::test_share_includes_only_adopted_source_segments_matching_session_pack`。同hash manifestの候補選択であり、開始時の固定を保証しない |

## #20 read/export/backup

実装: `db.py::read_snapshot/backup_database/verify_backup`、`main.py::snapshot/list_turns`、新設`sharing.py`。
`persistence::` は `backend/tests/test_persistence.py::test_`。

| 順 | 受け入れ条件 | 証拠と不足 |
|---|---|---|
| 1 | read中のstate/返答の時点一致 | 確認: `persistence::read_snapshot_keeps_one_view_during_a_concurrent_commit`。SELECTの間に実APIで確定して旧/新の一貫したviewを検証 |
| 2 | 100+turn/attemptで通常GET制限 | 部分: `persistence::normal_snapshot_is_bounded_and_pageable` は120turn。attempt増大時・playback/request全件読取の上限は残る |
| 3 | pagination重複/欠落/reload | 部分: 上記cursor120turn。新turn追加中・画面接続・過去attempt詳細は #3/#4/#6/RV06 |
| 4 | 共有exportのprivate/秘密/パス除外 | 確認: 新設`test_exports.py`。2.0の明示許可項目と採用参照文だけを出力。ユーザーがPack/公開本文に書いた内容は共有対象 |
| 5 | 別data dir復元後のDB/音声/資料整合 | 確認: `persistence::restore_into_another_data_dir_reads_audio_documents_and_private_history`。元data dir削除後にアプリGET、実音声bytes、DB内資料本文/hashとmanifestを比較 |
| 6 | 中断/不足を成功扱いしない | 確認: `persistence::backup_incomplete_is_not_ok`、`backup_rejects_incomplete_or_invalid_manifest`、`backup_does_not_publish_manifest_after_file_loss`、`backup_requires_complete_audio_and_no_deletion_in_progress`、backup APIの503 |
| 7 | requests整理で業務正本を失わない | 部分: 復元fixtureでrequests全削除後も公開返答/録音/評価/資料を保持。#18の生成provider来歴は別途不足 |
| 8 | export version/互換方針 | 確認: [PERSISTENCE](PERSISTENCE.md)、README、Chromeのローカル履歴と共有export分離 |

## 親 #9 の残作業と期限

| 作業ID | 所見・担当 | 不足と完了期限 |
|---|---|---|
| A01 | RV01 / #11/#12、利用 #3/#4 | **修正・fixture確認済み**。migration 9で同turn参照・再生の不変性を保証。直接INSERT/UPDATE、新規/旧DB、移行停止時の保持を検証（下記追記） |
| A02 | RV01 / #10/#12 | 音声付き旧DB（独立練習・shadowing・複数attempt・旧評価）を複製しschema/件数/hash/FK/integrity比較、確定途中の故障注入。#9-Bまで |
| A03 | RV01/RV05 / #13/#15/#16/#18 | 元turnの遅延upload/評価と次turn、pause/end/delete、書込/rename/commit故障、再claim旧実行者、ログsentinel。#4/#6の完成前、統合回帰は #9-B |
| A04 | RV01 / #18/#20 | **実装・fixture確認済み**。migration 10で質問/Coach/参照文/TTSへ不変の生成来歴を保存。外部処理はアプリごとに4件上限。requests削除・モデル変更・再起動の非遡及、満杯時のpause/GETを確認。旧データの来歴は推測せずnull |
| A05 | RV03 / #5/#19 | 準備状態→Pack生成→manifest/採用segment/会話Packの同時固定。PDF upload、provider入力とexportの一致、開始/削除競合。資料機能完成前 |
| A06 | RV06 / #3/#4/#6/#20 | **API/画面と読取件数制御を実装・fixture確認**。51turn→52turnとreload、23参照文/attempt/評価の境界。13往復のmock入力予算は既存試験、実LLMは #7で未検証 |
| A07 | RV01/RV07 / #9/#7 | 機能接続後の #9-Bで今回のexport/backup/restore試験も再利用・再実行し、条件→証拠→未達を再判定。実LLM/TTS/人間マイクは #7-A/#7-B |

A01の再現は一時DB内で、同sessionにturn 1/2を作り、turn 2のexerciseをturn 1のmodel playbackへ関連付けるINSERTが成功したもの。
外部キーは `(reference_id, session_id)` を検証するが、その参照文の`turn_id`との一致は保証していない。
既存APIから同じ不正状態を作れることまで立証したものではなく、直接SQL契約の不一致として扱う。

今回修正した差分は共有exportの私的情報除外、backup一覧の複製DBとの一致、manifestとDB参照の照合、破損・不足・余剰・不正パスの検査。
上記の残作業があるため #9全体、RV01全体、MVP全体の完成は宣言しない。

## 2026-09-22 実装再開: A01 / A06

上記の初回照合表は当時の証拠を示す。以下を新しい差分として追加する。

- A01: `test_db_constraints.py` の同session/別turn INSERT・UPDATE、playbackと参照版の組合せ、親ID/所属とplayback出典の不変性を追加。`test_migrations.py` はlegacy/版8の不整合で起動停止・業務データ保持、新規/legacy最終schema一致を検証。既存migration 1〜8は変更していない。
- A06: `history.py` に参照文/attempt/assessmentの詳細・cursor API、通常snapshotの子件数制限を追加。`test_history.py` とPlaywrightの51ターンfixtureで、52ターン目の追加後も重複・欠落なく旧履歴へ到達し、以前の録音と23件の評価runを表示する。録音あり判定は取得ページ外も含める。
- 実providerの事実性、人間マイク、A02〜A05/A07、#9-Bの最終判定は引き続き未完了。試験件数・制限はVALIDATIONの実装再開項を参照。

## 2026-09-22: A04・Coach支援

- `generation.py` とmigration 10: 生成対象model/provider/protocol/endpoint、prompt/input/output hash、開始終了時刻を業務行に保存。TTSは選択モデルと実行時の音声設定を保存。APIキーを含むtarget全体を保存しない。旧来歴の推測backfillはしない。
- `test_generation.py`: 実行中モデル変更後も旧出力の来歴を保持、後続は新設定、requests全削除と再起動後も保持、書換拒否、TTS設定変更の非遡及を検証。4件同時処理中の5件目は503で拒否し、pause/GETは待たず、枠解放後は成功する。
- `test_coach_support.py`: 支援生成中に同sessionの再生完了・次turn・pauseが可能、結果は元turnへ保存。end/delete/recoveryの遅延結果は拒否。これはA03のCoach経路の証拠であり、録音・評価全経路の完了ではない。

## 2026-09-29: A05 資料準備とPack固定

- 新規セッションで資料準備を選ぶと会話を停止状態で作成。概要・URL・PDFを保存し、採用segmentを選ぶ。manifest追加と会話用Pack/hash更新を単一DBトランザクションで行う。開始後はPack再固定を拒否する。
- Packの`source_material`に採用segmentの本文、位置、`learner_work`/`reference`を保存。会話provider入力と共有exportは同じPack/hashを読む。
- `test_documents.py`でPDF upload/容量/不正形式、準備前の開始拒否、固定後のprovider入力、共有export一致、開始後の再固定拒否を確認。Chrome fixtureで概要入力から会話画面まで確認。
- PDF抽出をイベントループ外へ移し、削除中に抽出が完了しても資料が復活しないことを並行fixtureで確認。Pack固定の判定と更新をBEGIN IMMEDIATEで直列化した。
- 固定中に同sessionのresumeを送るfixtureでも、Pack/hashとmanifestを同時確定した後の状態を確認。A05全体の完了判定は保留。URLの実ネットワーク条件、実モデルでの資料利用品質、OCR未対応を別途扱う。

## 2026-09-29: A03 単独復唱の遅延処理

- 会話画面で停止中の単独復唱を録音し、IndexedDBへ元exercise IDと再送キーを保存してからuploadする。upload失敗、reload、同キー再送、評価状態表示をChrome仮想マイクで確認。
- `test_nonblocking.py`で評価処理中に同sessionがpause→resume→次turnへ進めることと、次turn後のuploadが元exerciseに残ることを確認。
- 同時シャドウイング録音はブラウザ仮想マイクで追加確認し、次の相手音声前の停止と`insufficient_evidence`を検証した。削除中に遅れて完了するupload/評価は`test_nonblocking.py`で拒否し、音声ファイルと評価runが復活しないことを確認。`test_recording.py`で部分書込・DB登録失敗を注入し、一時/確定ファイルとDB行が残らないことを確認。人間マイク、rename固有の故障は未完了。A03全体の完了判定は保留。
- 後続で`test_recording.py`へrename失敗を注入し、一時ファイル/DB行を残さず同じ録音キーで再送できることを確認。`idempotent`は結果確定を`status=processing`の要求だけに限定し、再起動で`interrupted`となった旧質問生成が戻ってもturnをcommitできないことを`test_nonblocking.py`で確認した。削除失敗例外と研究概要に同じsentinelを入れ、DBの`deletion_error`が例外型だけで、再起動後のログにsentinelが出ないことも`test_deletion.py`で確認。人間マイクは残る。

## 2026-09-29: A02 音声付き旧DBの移行fixture

- `test_migrations.py`に旧schemaの独立練習・shadowing、2件の録り直しと1件の別turn録音、旧評価結果を持つDBを追加。移行後のturn/exercise/attempt/評価run件数、元音声bytes、参照文hash、確定元、`input_kind=unknown`、FK/integrityを比較した。
- `test_migrations.py`は**13 passed**。`test_submissions.py`で返答確定途中のplayback更新失敗を注入し、submission・request・会話状態がすべてrollbackされ、同じ通知で再試行できることを確認。実利用DBのコピーと全件比較は残るためA02全体の完了判定は保留。
- ローカル`data/drill.sqlite3`を読取専用で確認したところschema 1〜10適用済み・整合性okだが、session/turn/exercise/attempt/audio/assessment/document/manifestはいずれも0件で、保存音声も0件だった。利用記録を持つ実DBの移行検証には使えないため、原DBには変更を加えずfixtureの結果を保持する。
- `CONVERSATION_OPERATIONS.md`に操作表。Coach案採用は停止・未確定・turn/reference/revision/message所属を確認。閲覧と生成は公開返答を確定しない。

## 2026-09-29: #9-B 統合回帰の再実行

- 資料準備、会話中支援、履歴、録音・評価、共有export/backup/restore、旧DB移行の接続後に全backend回帰**178 passed**、全Playwright**15 passed**を再実行。資料固定中の開始、次turn後の元録音・評価への保存、削除中の遅延処理、部分書込、確定途中のrollback、録音rename失敗、再起動後の旧実行者、削除ログsentinelを含む。結果は[VALIDATION](VALIDATION.md)に記録した。
- 実モデル・実TTSのゼミ13往復は機械的に完走したが、回復質問6件と候補返答の保守的な置換3件を要した。学術内容の人間レビュー、実利用DBの全件移行、実URL/PDFの資料利用品質、人間マイク、音響P0はこの統合回帰に含まない。親 #9 と #7-B の最終合格判定はこれらの不足と残るA02/A03/A05/A07を照合して行う。
