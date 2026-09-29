# 会話操作の契約

2026-09-22。#2/#3/#4/#6、RV04。サーバーのconversation revision・turn・referenceを正本とする。

| 操作 | 対象・段階 | pauseと進行 | 遅延結果・競合 |
|---|---|---|---|
| 字幕/説明の表示 | 表示中turn。全段階 | pause不要。表示だけ | 返答を確定しない |
| 意味/ヒント/骨子/全文案/文章修正 | 要求時に指定したturn。相手発言生成後 | pause不要。生成中も再生と次turnを進める | 元turnの私的履歴に保存。次turnの案として扱わない。終了/削除後の結果は拒否 |
| 支援結果の閲覧 | 元turnの履歴 | pause不要 | 公開返答や現行参照文を変更しない |
| 支援の全文案を採用 | 現行turn、未確定、coach_generation/model_playback | pause必須。停止後のrevisionを送信。再開時に新参照文を再生 | turn/revision/参照版/Coach message所属が不一致なら409。本文は保存済みmessageから解決 |
| 手動編集/再生成 | 現行turn、未確定、coach_generation/model_playback | pause必須。新版を追加し旧再生を無効化 | 画面は対象turn/revisionを送信。旧完了・古い編集を拒否 |
| 速度/音声経路/声 | 次の再生 | pause必須。保存TTS設定変更は関連音声を無効化 | 既に保存された再生条件は不変 |
| 役割別テキストモデル | 次の生成要求 | 発行済み要求は元のモデルで完了。後続要求から新設定 | 生成時のprovider/model/prompt hashは業務履歴へ保存し遡及変更しない |
| 過去のお手本/録音を再生 | 確定済みturn、元attempt | 会話停止中のみ。会話再開時に過去音声を止める | playback完了通知を送らず、会話を進めない |
| お手本と同時録音 | 録音開始時のturn/reference | 録音/保存/評価は進行条件にしない。次の相手音声前に録音だけ閉じる | Blobと元所属・再送キーを一体管理。混入条件はshadowing_overlap |
| 単独復唱 | 現行の不変reference | pauseを保存して全お手本音声を停止してから録音。明示再開まで会話停止 | 新attemptとして保存。isolated_repeatを使用。upload失敗時は同じキーで再送。再生完了を偽装しない |
| 会話モード変更 | 停止中の未確定turnまたは次turn | pause後のrevisionを指定。shadowingとfree_speechを切替 | 旧再生を取消し、旧版の完了通知・ASR結果は返答に採用しない。確定済み履歴は不変 |
| 自由発話の録音 | 現行free_speech_inputのturn | 最大29秒で停止し、元turnと再送キーで保存。音声認識までは返答未確定 | upload失敗ならIndexedDBの同じBlobを再送。保存済み音声は同turnで再認識可能 |
| 自由発話の最終認識 | 録音ID、turn、revision | 実行時のみrunningへ再開。空でない最終transcriptで一度だけ返答を確定し次問へ | 無音・失敗は未確定、録音を保持。pause/モード変更/削除後の遅延結果は不採用 |
| 自由発話の代替入力 | 現行free_speech_inputのturn | 明示入力を`free_speech_manual`で一度だけ確定し次問へ | 音声認識由来と混同しない。発音点数を出さない |

Coach生成中は同じ画面からの支援要求を一つに制限するが、停止・終了は常に操作できる。
自動会話のCoach支援に12回の終了制限を置かず、入力は直近最大12履歴/8,000文字に制限する。
生成失敗はエラー表示と明示再試行とし、無制限に自動再送しない。
録音保持期間・容量・reload/タブ終了の保証は[録音保存契約](RECORDING_RETENTION.md)に定める。
