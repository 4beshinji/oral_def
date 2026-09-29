# P0 音響実現性 — 未達

初回調査日: 2026-09-15。実音声を使った追加検証日: 2026-09-29。

Core実装後の追記: imageio-ffmpeg 0.6.0同梱のffmpeg 7.0.2-staticで、WAV/WebMの変換と長さ確認は実行できた。これはKaldi実行・GOP計算・学習者発音の評価ではない。

## 確認できた事実

- この環境は Python 3.12.3 / Node 23.11.1。PATH上にKaldi、compute-gop、ffmpeg、ffprobeはなかった。音声変換にはPython依存のimageio-ffmpegが同梱する実行ファイルを利用する構成を採用する。
- [公式recipe](https://github.com/kaldi-asr/kaldi/blob/master/egs/gop_speechocean762/s5/run.sh) はLibriSpeechのnon-chain TDNN、i-vector extractor、同じモデルに対応するlangを要求する。MFCC、i-vector、ネットワーク出力、alignment、phone mappingを経てcompute-gopを実行する。
- [公式説明](https://github.com/kaldi-asr/kaldi/blob/master/egs/gop_speechocean762/README.md) はnon-chain TDNNを使用する。公開されている[モデルm13](https://kaldi-asr.org/models/m13)はchain TDNN-Fであり、この経路の交換可能なモデルとしては扱えない。
- recipeではstressと語内位置の印を除いたphone mappingを使用する。raw GOPは対数事後確率に基づく指標であり、百分率へ変換しない。
- [Kaldiソース](https://github.com/kaldi-asr/kaldi/blob/master/COPYING)はApache 2.0。[SpeechOcean762配布ページ](https://www.openslr.org/101/)はCC BY 4.0と記載。モデル重み・辞書の利用条件は別途確認が必要。

## 現在の不足

KaldiのCPU用実行ファイル、M13 chainモデル、辞書をローカルで使った単一録音の試験は下記の通り実施した。ただし、この組合せのアラインメントは正常読みの対照として失敗した。公式recipeに適合するnon-chainモデル一式、または検証済みの別のアラインメント方式は未確定である。採用可能な重みと利用条件も確定していない。manifestの版・hashはnullのまま保持する。

実話者を分離したdev/test録音、固定した品質閾値、無音10・別文20・正常20・途中切断10の対照結果はない。warm推論時間、RAM/VRAMも未測定。単一録音のGOP値を発音評価の成功結果としない。

## 次の実作業

1. 公式recipeに適合するnon-chainモデル一式と辞書の入手条件を確定するか、M13 chainモデル用のアラインメント方式を検証する。
2. 正常読みの音素区間が録音と整合することを確認し、コマンド・モデル版・SHA-256・所要時間を記録する。APIリクエスト内でdownload/buildしない。
3. 辞書・phone inventory・stress扱い・要求sample rate・raw式を選定版から記録する。
4. devで品質条件を固定し、話者を分離したtestでDESIGN §7の棄却ゲートを実測する。

当面のCLI/APIは同じunavailable結果を返す。Kaldiの実処理はアプリへ未接続であり、この接続境界を実音響統合やP0合格と呼ばない。

## 2026-09-29: 公開モデル経路の再調査

[Kaldi公式recipe](https://github.com/kaldi-asr/kaldi/blob/master/egs/gop_speechocean762/s5/run.sh)はLibrispeechのnon-chain TDNNと対応するi-vector extractor、langを前提にする。[KaldiのM13配布](https://kaldi-asr.org/models/m13)にはchain TDNN-Fとi-vector extractorがある。[GOPT著者の手順](https://github.com/YuanGongND/gopt/blob/master/steps_of_inference.md)はM13を使い、recipeのmodel/lang参照を変更してGOP特徴を取り出す経路を示している。この経路を候補として取得し、下記の単一録音試験で確認した。

Kaldi公式ソースのcommit `e02e35f0254bb033fab73d1df99fc34123e31d56`を無視対象の`.cache/kaldi`へ取得し、CMakeでCPU用`compute-gop`をビルドした。実行ファイルのSHA-256は`468da21751632044c9f7a448c47c3aee657f10dd9bbcafef17eb28172054b75b`。この環境では生成CMakeが`-lcblas`を要求する一方、システムはOpenBLASのみを提供するため、無視対象の`.cache/kaldi-libs/libcblas.so`を既存`libopenblas.so`へリンクしてビルドした。`compute-gop --help`と`ldd`は成功。M13 extractorの配布アーカイブのSHA-256は`431e44aa6c5efdd76566199ffb96e14a3052e025a8c6ee495eb66703663850a7`。

M13主モデルと[SpeechOcean762](https://github.com/jimbozhang/speechocean762)の辞書・実話者WAVも取得し、MFCC→i-vector→M13出力→参照音素グラフ→強制アラインメント→`compute-gop`を1件通した。正規化後の参照文とオフラインASRの内容は一致した。GOP値は21音素分出たが、非無声音素21件すべての区間が1フレームに潰れた。通常フレーム設定では333フレーム中312、3倍間引きでは111中90フレームが無音に割り当てられた。録音長は3.36秒で、`silencedetect`は冒頭約0.59秒と末尾約0.54秒の無音を検出したため、この区間配分は正常読みの有効区間として採用できない。測定値・重み/辞書hashは[検証JSON](validation/kaldi-p0-smoke-2026-09-29.json)に保存した。M13 chainモデルをこのまま時間境界や発音スコアの根拠にできない。non-chainの適合モデルまたはアラインメント方式の再設計と、dev/testの別話者ゲートが必要。providerは引き続き`unavailable`とする。
