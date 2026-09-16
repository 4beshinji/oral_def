# P0 音響実現性 — 未達

調査日: 2026-09-15。モデルの取得・ビルド・音声評価は未実施。

Core実装後の追記: imageio-ffmpeg 0.6.0同梱のffmpeg 7.0.2-staticで、WAV/WebMの変換と長さ確認は実行できた。これはKaldi実行・GOP計算・学習者発音の評価ではない。

## 確認できた事実

- この環境は Python 3.12.3 / Node 23.11.1。PATH上にKaldi、compute-gop、ffmpeg、ffprobeはなかった。音声変換にはPython依存のimageio-ffmpegが同梱する実行ファイルを利用する構成を採用する。
- [公式recipe](https://github.com/kaldi-asr/kaldi/blob/master/egs/gop_speechocean762/s5/run.sh) はLibriSpeechのnon-chain TDNN、i-vector extractor、同じモデルに対応するlangを要求する。MFCC、i-vector、ネットワーク出力、alignment、phone mappingを経てcompute-gopを実行する。
- [公式説明](https://github.com/kaldi-asr/kaldi/blob/master/egs/gop_speechocean762/README.md) はnon-chain TDNNを使用する。公開されている[モデルm13](https://kaldi-asr.org/models/m13)はchain TDNN-Fであり、この経路の交換可能なモデルとしては扱えない。
- recipeではstressと語内位置の印を除いたphone mappingを使用する。raw GOPは対数事後確率に基づく指標であり、百分率へ変換しない。
- [Kaldiソース](https://github.com/kaldi-asr/kaldi/blob/master/COPYING)はApache 2.0。[SpeechOcean762配布ページ](https://www.openslr.org/101/)はCC BY 4.0と記載。モデル重み・辞書の利用条件は別途確認が必要。

## 不足・ブロック

使用可能なKaldi build、適合するnon-chainモデル・辞書・phone mappingがローカルにない。採用可能な重み一式とライセンスをまだ確定できていない。巨大な学習処理を暗黙に始めず、manifestの版・hashはnullのまま保持する。

実話者のdev/test録音、固定した品質閾値、無音10・別文20・正常20・途中切断10の対照結果はない。ビルド時間、warm推論時間、RAM/VRAMも未測定。架空の成功結果やモデルhashは記録しない。

## 次の実作業

1. 上記recipeに適合するnon-chainモデル一式と辞書の入手条件を確定し、Kaldi commitとファイルSHA-256を実測して記録する。
2. ローカルで事前ビルド・warm-upを行い、単一録音を処理するコマンドを作る。APIリクエスト内でdownload/buildしない。
3. 辞書・phone inventory・stress扱い・要求sample rate・raw式を選定版から記録する。
4. devで品質条件を固定し、話者を分離したtestでDESIGN §7の棄却ゲートを実測する。

当面のCLI/APIは同じunavailable結果を返す。Kaldiの実処理は未実装であり、この接続境界を実音響統合やP0合格と呼ばない。Coreの録音保存・再生・dictationの実装を進める。
