# 財務採用結果の実行準備への接続結果

## 実装結果

2026-09-27にユーザーが [実装計画](../agent-reports/plans/2026-09-27-financial-adoption-run-connection-implementation-plan.md)
を承認し、既存財務準備CLIへ `prepare-run` / `validate-run` を追加した。
原財務bundle・v1 mappingレビュー・限定採用結果を再検証し、値を含まない実行準備summaryを保存する。
旧bundle、旧CLI、採用policy、転送許可は変更していない。

現在の採用状態・不足と旧結果の理由を区別する。
一件でも採用値がある場合は `financial_mapping_partial`、0件なら `financial_mapping_unaccepted` とし、
旧 `financial_mapping_unimplemented` は履歴に保持する。
6項目の限定採用だけで年次5期・中間8期間・IR・最新性の不足を解消した扱いにはしない。

price/FXは任意入力または原bundleの同梱分を利用できる。
既存validatorで再生し、task全体と確認時刻の一致を要求する。
同梱分との競合を拒否し、接続先のpending・不足日・利用制約を保持する。
公開CLIの分析runtimeやAgentへの転送は今回の対象外。

## 保存済み証拠の確認

使用した依存bundle:

- 原財務: `runs/edinet-evidence/edinet-prepared-a27686c68a9f4a0c883105592f15eabc`
- レビュー: `runs/edinet-evidence/edinet-mapping-review-20260927`
- 採用: `runs/edinet-evidence/edinet-financial-accepted-20260927`

新規出力: `runs/edinet-evidence/edinet-financial-run-20260927`

manifest SHA-256: `19ac8cd84415c99677092a1ab5d8006ebd6def1106017f8f753380467999e79e`

実証拠のprepare-run / validate-runは成功。taskと確認時刻を原財務bundleから引き継ぎ、
6項目の採用状態・当期対象期間を保持した。price/FXは今回の実証拠では未接続。
年次・中間期不足、IR未確認、最新性未確認、その他開示未確認、元一覧取得失敗後の再検証履歴を保持する。
全体は `status=pending`、`analysis_ready=false`。
追加API取得・credential使用は0回で、財務値・原文は出力していない。

出力は `inputs.json` と `manifest.json` の2ファイルのみ。
依存ファイルのhashを記録し、manifest内の任意パスを自動で開かない。
再検証には明示的な依存bundleの指定が必要で、原bytesから採用値・根拠まで再生する。

## 検証

- 対象46テスト（財務準備17、財務run表示3、価格FX26）が成功。
- Ruff・Mypyが成功。
- 合成原ZIPから財務採用・実行準備・CLI再検証まで、validatorを置き換えずに統合検証した。
- 一部/全項目の未採用表示は表示層の単体テストで検証。
- price/FX側は実bundleの再生を利用して、同梱/別指定、一致/競合、task/銘柄/時刻違い、
  ready/pending状態・不足・利用制約の保持を検証した。
- 依存artifactと出力の改変、依存不足、symlink、入力への出力重複、再公開を拒否した。
- 元証拠の不変、値・本文を含まない出力、ネットワーク未使用を確認した。

## 残る作業

同一task・同一確認時刻のprice/FX準備を実証拠に接続する。
その後、複数期間の財務とIR・最新開示の不足を埋める範囲を決める。
今回の内部summary接続だけでは証拠集合を凍結せず、公開分析runtimeの完成とも扱わない。
