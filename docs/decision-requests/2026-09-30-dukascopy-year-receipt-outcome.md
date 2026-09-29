# Dukascopy年別receiptの保存と検証結果

## 変更

成功した各年の原応答をcommitした直後、後続年へ送信する前に、年別receiptを別ディレクトリへatomicに保存する。callbackで得た実際のHTTP受信時刻、取得時刻、保存時刻、元の期間、取得identityを保持する。既存の全期間v2証拠のinventoryは変更しない。

`fx_evidence_cli validate-year-receipt`は通信せず、設定bytesとindex、元の要求範囲とfingerprint、共有runtime DBのlogical/physical identityと成功状態、原応答のcommit状態と実bytesを照合する。HTTP受信時刻はreceiptに保存したcallback値を使い、DBの保存時刻から推測しない。runtime DBがない場合は新規作成せず拒否する。

後続年が失敗しても成功年のreceiptを検証でき、元の論理取得の失敗状態は変更しない。receipt公開に失敗した場合は後続送信を停止し、有効なfenceの下で論理取得を失敗として確定する。自動retryは追加しない。

## 検証

- 対象pytest: 35件成功。後続年失敗、保存失敗による送信停止、改変したprovenanceと原応答、runtime DB欠落を含む。
- Ruff lintとformat: 変更したPython 3ファイルで成功。
- Mypy: 240ファイルで成功。
- 独立レビュー: 実装と計画の照合でPASS。
- 全体チェック: `./scripts/pre-commit/checks.sh`でlint・format・全pytest・Mypyすべて成功。取得保存境界への変更のため、対象検証に加えて全体検証を行った。実ネットワーク取得は今回の保存動作の確認には不要なため実施していない。

## 残課題

9月29日の実取得では2026年の原応答commit後に2023年が失敗した。今回の実装前に失われたHTTP受信時刻は、DBの作成・保存時刻で補わない。この過去の成功年にreceiptを遡及作成しない。

新しい明示的な取得で必要なFX証拠を確保し、実行時刻に必要終端を解決し直して市場runを再検証する必要がある。現在の9月28日FX不足とP1の未凍結状態は残り、`analysis_ready=false`を維持する。今回は実ネットワーク取得を行っていない。
