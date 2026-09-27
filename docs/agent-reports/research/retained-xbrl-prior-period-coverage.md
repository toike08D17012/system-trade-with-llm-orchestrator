# 保存済みXBRLの過年度候補と追加取得範囲

## 結論

2026-09-27時点の保存資料では、2025年3月期の6項目を前期比較値から再利用できる候補が揃っている。追加API取得前に、この比較期間の受入対応を進める価値がある。2022～2024年3月期は同じ6項目が揃わず、追加資料の優先対象となる。四半期・半期の期間は保存XBRLに存在しない。

候補の存在は採用済みを意味しない。今回、受入policy、既存成果物、実装、`analysis_ready=false` は変更していない。外部API送信は0回。

## 対象と検証方法

対象は `runs/edinet-evidence/edinet-prepared-a27686c68a9f4a0c883105592f15eabc` の `candidates.json` と保存原本。文書IDは `S100Y8NY`、発行体は `E02144`、archive SHA-256 は `6b9e1d9cd955f630bc0cf62f68f4d790b09798568004151bbe988224ae450034`。

既存CLIの `financial_disclosure_cli validate --input` によるオフライン再検証は成功した。既存 `financial_mapping.review_metric` にdraftの6項目QNameを渡し、開始日・終了日だけを各年次期間に差し替えて候補を調べた。発行体一致、dimensionなし、単純JPY単位、nil、数値構文、重複一致の既存判定を利用した。財務値の本文は調査出力・本報告へ掲載していない。

これはfact単位の候補調査であり、過年度の提出書類としての受入やtaxonomy証明の追加承認ではない。現在の `evaluate_mapping` は提出書類の対象期間とproposal期間の一致を求めるため、既存CLIへ過年度proposalを渡すだけでは採用できない。

## 同一QNameの候補

数字は保存factのordinal。複数記載は既存判定で値が一致した重複候補。各期間は4月1日～翌年3月31日、資産・資本は期末時点。

| 項目 | 2022年3月期 | 2023年3月期 | 2024年3月期 | 2025年3月期 | 2026年3月期 |
| --- | --- | --- | --- | --- | --- |
| 売上収益 | なし | なし | なし | 807 | 808 |
| 営業利益 | なし | なし | なし | 817, 1192 | 818, 1237 |
| 親会社帰属利益 | なし | なし | なし | 835 | 836 |
| 資産合計 | なし | なし | なし | 743, 1197 | 744, 1242 |
| 資本合計 | なし | なし | 878 | 799, 966, 975 | 800, 1083 |
| 営業CF | なし | なし | なし | 1120 | 1121 |

QNameは [既存draft](../../../config/financial-mapping/7203-2026-draft.json) と同一。2025年3月期の6項目と2024年3月期の資本合計は、期間を変更した候補判定でnil・数値競合がなかった。ただし資本変動表の期首残高等の役割とtaxonomyの対応は、過年度採用時に別途確認する。

## サマリー項目と限界

dimensionなしの以下の項目は2022～2026年3月期の5期間に存在し、nilではなく、単位参照は `JPY`。名称が似ているだけで現行項目に統合しない。

| local name | ordinal（古い期間順） | 未解決事項 |
| --- | --- | --- |
| OperatingRevenuesIFRSKeyFinancialData | 2–6 | 発行体拡張。TotalNetRevenuesIFRSとの意味・集計範囲の対応 |
| ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults | 12–16 | 本表項目との定義・連結範囲・比較値整合 |
| TotalAssetsIFRSSummaryOfBusinessResults | 27–31 | 同上 |
| CashFlowsFromUsedInOperatingActivitiesIFRSSummaryOfBusinessResults | 62–66 | 同上 |
| EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults | 22–26 | 親会社所有者帰属持分であり、資本合計の代用不可 |

先頭は既存draftと同じ発行体namespace、残りは `http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2025-11-01/jpcrp_cor`。営業利益に相当するサマリー候補は今回の名称検索では確認できなかった。税引前利益は営業利益の代用にしない。

全274 contextのduration期間は上記5つの年次期間のみ。四半期・半期を示す短いdurationはなく、年次値の分割・推計で補完しない。

## 追加取得の優先範囲

1. **まず追加取得なしで2025年3月期の比較値受入を計画する。** 文書の報告期間とfactの比較期間を分け、期間別のtaxonomy証明、出典、重複・訂正の扱いを定義する。現行の承認policyは2026年3月期に固定されており、自動拡張しない。
2. **年次の不足対象は2022～2024年3月期。** 6項目を揃える目的では、この期間を含む過年度年次報告の本文・比較値が優先候補。2024年3月期の報告で2023年3月期も確認できる可能性はあるが、取得前に収録を保証しない。必要な文書数は現時点で確定できない。2025年3月期の別報告も訂正・最新性確認が必要なら取得対象になり得る。
3. **四半期・半期は別途探索が必要。** 要件は証拠凍結時点で公表済みの直近8期間。保存資料だけでは最新公表期間、対象文書、8期間の並びを確定できない。発行体IR・開示一覧の確認と制度変更に伴う資料種別の扱いを整理してから、送信先・日付範囲・上限回数を提案する。8期間を一律に8四半期または8半期と仮定しない。

これは最低限の6項目候補を揃えるための絞り込みであり、IR・訂正・最新開示など全体の証拠要件を免除するものではない。追加EDINET取得の送信範囲はまだ承認・実行していない。

## 次の実装計画への引継ぎ

- `preparation/financial_mapping.py`: 比較期間と提出対象期間の区別。
- `preparation/financial_acceptance.py` / approved policy: 複数期間の受入根拠と固定範囲。
- `preparation/financial_disclosure.py`: 現在の年次数は提出書類の期間から数えるため、比較factが見つかっても自動で充足しない。元の成果物の再現性を維持し、期間別の項目充足をどう評価するか検討する。
- `preparation/financial_run.py`: 期間別要約と不足理由の整合。
- 検証候補: 比較期間のみの採用、期間取り違え、重複一致・不一致、訂正前後の混在、nil、部分項目しかない期間、旧bundle再現性、数値を含まない診断出力。

根拠: [証拠方針 §5](../../system-requirements/02-data-source-and-evidence-policy.md#5-mvpで必要な鮮度と対象期間)、[現在の接続結果](../../decision-requests/2026-09-27-financial-price-fx-connection-outcome.md)。実装変更がないためテストスイートは再実行していない。
