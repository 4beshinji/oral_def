# 依存・ライセンスの記録

2026-09-15、実際に取得した配布物のmetadataとlockfileを確認。ソースの転載やモデル重みの同梱は行っていない。プロジェクト全体のライセンスは所有者がまだ指定していない。

## アプリと開発ツール

| 依存 | 確認した版 | 配布物のライセンス表記 |
|---|---|---|
| FastAPI | 0.141.1 | MIT |
| Uvicorn | 0.53.0 | BSD-3-Clause |
| HTTPX | 0.28.1 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| imageio-ffmpeg（Pythonラッパー） | 0.6.0 | BSD-2-Clause |
| pytest | 9.1.1 | MIT |
| Ruff | 0.16.7 | MIT |
| React / React DOM | 19.3.0 | MIT |
| Vite | 6.4.3 | MIT |
| TypeScript | 5.9.3 | Apache-2.0 |
| @vitejs/plugin-react | 4.7.0 | MIT |
| Playwright | 1.63.0 | Apache-2.0 |
| Prettier | 3.9.6 | MIT |

Pythonの推移的依存を含む版は`uv.lock`、ライセンス原文はインストール先の`*.dist-info/licenses/`などにある。確認した例外的な表記はcertifiのMPL-2.0、typing_extensionsのPSF-2.0、packagingのApache-2.0 OR BSD-2-Clause。その他は各配布物のMIT、BSD、Apache表記を確認した。

JavaScriptの推移的依存は`frontend/package-lock.json`に版・integrity・licenseを記録。開発依存を含めMIT、Apache-2.0、ISC、BSD-3-Clause、caniuse-liteのCC-BY-4.0の表記がある。各`node_modules/<package>/LICENSE*`も参照する。既存のチャットUIや他プロジェクトのコード移植はない。

## ffmpegの実行ファイルは別ライセンス

imageio-ffmpeg wheelに同梱されたLinux x86_64バイナリは`ffmpeg 7.0.2-static`。実行ファイルの`-L`出力で **GPL version 3 or later** と確認した（`--enable-gpl --enable-version3`）。PythonラッパーのBSDライセンスと混同しない。

現在はローカルで依存パッケージとして使用する。バイナリの再配布を含むパッケージを今後作る場合は、そのバイナリのライセンス・対応ソース・NOTICEを別途扱う。PATHのffmpegへ差し替えた場合は版とビルド条件を再確認する。

## モデル・辞書・データ

Kaldiソース、モデル重み、辞書、SpeechOcean762は取得していない。Kaldiソース・データの確認先、モデル互換性と未確認点は[P0_REPORT.md](P0_REPORT.md)に分離した。manifestのモデルhash・辞書hash・ライセンスは未選定のためnull。

自動テストのWAVは無音、Chromeの録音は仮想マイクの合成音であり、コーパスの転載や本人の録音ではない。発音性能の評価用データとして扱わない。
