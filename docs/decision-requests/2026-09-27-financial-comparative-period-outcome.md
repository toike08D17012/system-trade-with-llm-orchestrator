# 前期比較値の受入結果

## 結果

承認済みの[実装計画](../agent-reports/plans/2026-09-27-financial-comparative-period-implementation-plan.md)に沿って、保存済み7203年次報告の前期比較値をローカル採用した。2025年3月期・2026年3月期が各6項目、計12項目accepted。年次完全期間は2/5期、部分期間は0期。追加API送信は0回。

| 年次期間 | 6項目の充足 |
| --- | --- |
| 2021-04-01～2022-03-31 | missing |
| 2022-04-01～2023-03-31 | missing |
| 2023-04-01～2024-03-31 | missing |
| 2024-04-01～2025-03-31 | complete |
| 2025-04-01～2026-03-31 | complete |

6項目は売上収益、営業利益、親会社帰属利益、資産合計、資本合計、営業CF。未承認のサマリー項目と2024年3月期の資本候補は採用していない。数値本文は本書・CLI・診断・run要約に掲載しない。

## 保存先と再検証

共通ディレクトリは `runs/edinet-evidence/`。

| 成果物 | ディレクトリ | manifest SHA-256 |
| --- | --- | --- |
| 比較期間の受入 | `edinet-financial-comparative-20260927` | `87d7d29a75471a24af66a86fd8535889451d45960181b94f41cd42866c2ac2a3` |
| 財務・価格・FXのrun version 2 | `edinet-financial-comparative-market-run-20260927` | `040f3e6fa3cd65d28d110d3474da98c1ef516867271a881a0e4e56f9f7efba90` |

比較policy: `config/financial-mapping/7203-2026-comparative-approved.json`、SHA-256 `eaf1740c4c717eefcc82921ce6ffb1b98bcbb0804aafb775fa26c34ecfe5790e`。

元archive、当期review、当期adoptionを再検証した後、明示した2期間だけを評価した。taxonomy証明は既存adoptionの検証で原本から再計算されたものを再利用し、証明を無検証で信用しない。当期6項目の全参照を含む結果が従来adoptionと一致することも検証した。

比較sidecarのCLI prepare/validate、新しいrunのprepare/validateは成功。以前の `edinet-financial-market-run-20260927` のversion 1再検証も成功した。task・確認時刻は既存の `edinet-acceptance-7203-20260927` / `2026-09-26T18:10:24.796869+00:00` を維持し、現在時刻の最新性を確認したとは扱わない。

## 残る不足

全体は `pending` / `analysis_ready=false`。年次3期間、四半期・半期、発行体IR、最新開示、その他開示が不足している。`financial_mapping_partial` と元資料の失敗後再検証の履歴を保持する。価格・FXは既存の同一task・同一時刻bundleを接続し、その利用制限も維持した。

次は2022～2024年3月期を含む年次報告の取得範囲と、公表済み直近8期間の四半期・半期資料を特定するための調査・送信範囲を計画する。今回の承認を追加EDINET送信の承認へ拡張しない。

## 検証

関連6ファイルの82テスト、対象PythonのMypy、実データのオフライン再検証を実施した。承認policyの束縛違反とCLI失敗時の出力確認を追加後、統合テスト1件の再実行も成功した。コミット時のrepository全体Pytest・Mypy、および対象ファイルのRuff check/format hookも成功した。Shell変更がないためShell検査は対象外。
