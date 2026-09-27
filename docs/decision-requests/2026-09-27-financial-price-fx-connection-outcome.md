# 財務・価格・FXの実証拠接続結果

## 結果

2026-09-27、保存済み価格・Dukascopy FXを財務と同じtask・確認時刻で再prepareし、
財務6項目の限定採用結果と接続した。既存CLIだけで実施し、コード変更・追加API取得・credential使用は0回。

- task: `edinet-acceptance-7203-20260927`
- 確認時刻: `2026-09-26T18:10:24.796869+00:00`（JST 2026-09-27 03:10:24.796869）
- 財務: 2026年3月期の当期連結6項目を採用済み
- 価格/FX必要終端: ともに `2026-09-25`
- 価格/FX欠損日: ともに0日
- USD参考換算: 731行
- 価格・FX・換算状態: `ready_with_limitations`
- 全体: `pending`、`analysis_ready=false`

確認時刻は原財務bundleから引き継いだ固定時刻である。
現在時刻の最新性を新たに確認したものではない。
予定カレンダーの限界、株価とFXの終値時刻の違い、独立provider比較未実施、
原bytesの外部転送禁止等、既存の利用制約を保持している。

## 使用した証拠

- 原財務: `runs/edinet-evidence/edinet-prepared-a27686c68a9f4a0c883105592f15eabc`
- mappingレビュー: `runs/edinet-evidence/edinet-mapping-review-20260927`
- 財務採用: `runs/edinet-evidence/edinet-financial-accepted-20260927`
- 価格: `runs/price-acceptance-7203-20260926`
- FX: `runs/dukascopy-evidence/dukascopy-fx-7203-20260926`
- カレンダー・metadata・調査記録: `runs/calendar-local-20260927/`

原財務・候補レビュー・採用・旧実行準備は変更せず、新しい出力を作成した。

| 出力 | 保存先 | manifest SHA-256 |
| --- | --- | --- |
| 同task価格FX準備 | `runs/edinet-evidence/edinet-price-fx-run-20260927` | `b341b1b39fdae5383ce3111063223743d2fa8fdafc7c9cc385dcfff19d16f97b` |
| 財務・価格FX接続 | `runs/edinet-evidence/edinet-financial-market-run-20260927` | `78822cc9453aa8420c6ef802574fb230c3c2e5b82bb1aa2c570d163eab1e4ab2` |

`price_fx_not_connected` は現在の不足理由から除かれ、旧状態の `historical_reasons` に残る。
財務数値・原文は診断や本記録に含めていない。

## 実施した検証

既存の `price_fx_run_cli prepare` / `validate` と
`financial_disclosure_cli prepare-run` / `validate-run` がすべて成功した。
後者に `--price-fx` を明示し、task全体・確認時刻一致と依存artifactの原bytesからの再生を確認した。

コード変更はないため、Pytest・Ruff・Mypyの再実行は行っていない。
今回の検証対象は保存済み実証拠の再準備・接続・再生である。

## 次の作業

財務期間と開示の不足が残る。
まず保存済みXBRLの比較期・過年度候補について、項目・期間・連結範囲の充足を確認する。
再利用できる候補と追加取得が必要な期間を分けてから、複数期間の採用規則と取得範囲を決める。
中間8期間、最新開示の確認、発行体IRの接続は未完了である。

今回の結果は内部実行準備であり、公開分析runtime・証拠集合凍結・Agent分析開始には進めていない。
