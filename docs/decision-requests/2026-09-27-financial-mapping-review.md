# XBRL entity対応・財務項目mappingレビュー

## 状態

2026-09-27、ユーザーが実装計画と限定的な開発診断情報の確認を承認した。
追加のEDINET API送信なしで、保存済み7203年次書類を再評価した。
entity対応と候補レビューの実装・再生は完了。以下の対応表は**採用前の案**であり、
財務数値の正規化・受入を完了したものではない。

診断許可は計画に記載された識別情報に限定する。財務数値、原文・text block、raw/bulk、
credentialをcoding agentへ出力しない。EDINET source approvalの一般転送禁止は変更しない。

## 証拠と再生

- 元bundle: `runs/edinet-evidence/edinet-prepared-a27686c68a9f4a0c883105592f15eabc`
- 元manifest SHA-256: `8ba600927fa2e9f259c7bde6e0cc2781a7f06c1f1f93e30870ef438eeb2e29bf`
- 新規レビュー: `runs/edinet-evidence/edinet-mapping-review-20260927`
- review SHA-256: `006663dcbcb8150d9505bf25c50895bff369d122eb574d11c9d981da89ce14fe`
- [機械可読な対応案](../../config/financial-mapping/7203-2026-draft.json)
- 対象期間: 2025-04-01〜2026-03-31。最新公表分と確認したものではない。

元bundleを原bytesから再検証してから候補を参照する。新出力は `proposal.json` と
`review.json` のみで、原証拠を複製せず、元manifestのhashを参照する。
再検証には元bundleも必要となる。旧manifestと旧判定は変更していない。

## entity

公式scheme `http://disclosure.edinet-fsa.go.jp`、identifier `E02144-000` を、
一覧に基づくEDINETコード `E02144` と追番 `000` に対応付けた。
全274 contextは同じ組で、今回の新レビューでは `matched` となった。
旧manifestの `xbrl_entity_unresolved` は履歴として残し、新しい候補判定ではcontextごとに再照合する。
他scheme・別コード・非000追番を同一視せず、混在は診断に残す。
未使用contextの追加だけで別の候補を拒否しない。

根拠は金融庁[報告書インスタンス作成ガイドライン](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140112.pdf)
2025年11月版 §4-2、§5-4。2026-09-27に確認した。

## 6項目の対応案と実証拠の結果

標準QNameのnamespaceは
`http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2025-11-01/jpigp_cor`。
売上だけは提出者namespace
`http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E02144-000/2026-03-31/01/2026-06-10`。
版・namespaceを完全一致で扱い、他社や将来版へ自動展開しない。

| 項目 | QName local name | 期間種別 | 全候補数 | 条件一致ordinal | 結果 |
| --- | --- | --- | --- | --- | --- |
| 売上 | `TotalNetRevenuesIFRS` | duration | 2 | 808 | 候補1件。提出者独自概念の意味は未確定 |
| 営業利益 | `OperatingProfitLossIFRS` | duration | 12 | 818, 1237 | 同値重複 |
| 親会社帰属利益 | `ProfitLossAttributableToOwnersOfParentIFRS` | duration | 2 | 836 | 候補1件 |
| 資産合計 | `AssetsIFRS` | instant | 12 | 744, 1242 | 同値重複 |
| 資本合計 | `EquityIFRS` | instant | 36 | 800, 1083 | 同値重複 |
| 営業CF | `NetCashProvidedByUsedInOperatingActivitiesIFRS` | duration | 2 | 1121 | 候補1件 |

標準5項目の概念名・monetary型・期間種別は、実書類と同じ2026年版の金融庁
[国際会計基準タクソノミ要素リスト](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140184.xlsx)
で照合した（2026-09-27取得）。`sheet4.xml` の48、113、206、217、384行等に該当定義がある。
取得ファイルのSHA-256は `4dda7e3c79a3e30b7d1c29ab2f03514359c6c3e129736d83e599e797f549e8fc`。
資本合計は親会社所有者帰属持分と別概念であり、後者を代用しない。

売上の `TotalNetRevenuesIFRS` はQNameによる発見候補に留まる。
同じ提出者には `SalesRevenuesIFRS` 等もあり、文字列の類似だけでは同義と扱わない。
正式採用前に提出者taxonomyの定義・関係を確認する必要がある。

## 判定の範囲と未確定事項

durationは期首・期末完全一致、instantは期末一致、公式entity、単純JPY unit、
dimensionなしの候補を比較対象とした。比較対象への分類は財務値としての採用ではない。
比較期、追加dimension、nil等は理由付きで保持する。無関係な追加conceptは停止原因にしない。
数値比較はローカルDecimalで行い、decimalsを倍率として扱わない。
scale付き表現は未対応。nilとゼロを分け、nilと数値の混在や異値は競合にする。

dimensionがないことだけでは連結と確定しない。前記作成ガイドライン§5-4-5-1には
連結・個別dimensionの省略規則と例外があるため、実書類での適用関係の確認が必要である。
全6項目に `mapping_not_approved` と `consolidation_scope_unconfirmed` を残した。

次の作業は、提出者taxonomyの必要な概念定義・関係と連結範囲を確認し、対応表の採用案を確定すること。
今回の診断許可に本文やラベル全文の外部表示は含めていない。
正式採用前にこの未確定事項を解消し、採用ルールをレビューする。
期間不足、IR未確認、最新性未確認、価格FX未接続等は元manifestから保持し、
常に `status=pending`、`analysis_ready=false` とする。

## 検証

- entity、期間、dimension、通貨、scale、nil/ゼロ、重複/競合、未知追加項目を合成データで検証。
- 旧単体identifierのbundle再生、元証拠不変、改変拒否、再公開拒否、出力の値・本文除外を検証。
- 実証拠のprepare/validateに成功。EDINETへの追加通信・credential使用は0回。
