# 最新開示9資料の選択節レビュー接続

## 採用した範囲

取得済み9PDFの選択ページを確認し、定性事実と更新関係を別の固定レビューへ記録した。
原本の新規取得は行っていない。レビュー担当はcoding-agentであり、投資判断や数値採用を行う承認ではない。
旧文脈レビューを変更せず、`runs/toyota-disclosure-runtime-evidence/narrative-adoption-20260929/review.json`へ保存した。

| 対象 | 選択ページ | 定性採用と留保 |
| --- | --- | --- |
| 9月3日取得実績 | 1 | 8月末基準の実績。現在株数の証明ではない |
| 8月4日取得・消却決定 | 1 | 取得枠と消却予定。消却実行の証明ではない |
| 8月7日処分価額決定 | 1 | 8月4日処分決定の後続価格決定。処分実行は未確認 |
| 8月4日株式処分 | 1–2 | 決定・予定期日と臨時報告書への参照。臨時報告書は未受入 |
| 8月4日株式制度 | 1、3 | 幹部職制度の継続・特定人材制度の導入 |
| 6月18日制度変更 | 1–2 | 受益権確定日・居住国による取扱いの変更。2025年原開示は未受入 |
| 6月15日売却益 | 1 | 売却完了の公表、個別確定・連結見込、5月予想へ反映済みの区別 |
| 最新決算説明資料 | 3、13 | 営業利益予想上方改定、為替・物流前提変更、地震影響未反映 |
| 最新決算Q&A | 1–3、6 | 編集済み要約、一時要因と返還・売却益の説明。各項目の詳細額は未開示 |

Q&Aの売却益説明を6月15日の対象取引へ関連付けたが、見込額とQ&Aの概数の厳密な一致や計上数値の採用は主張しない。
取得実績と決議、価格決定と処分決定、処分と制度、Q&Aと説明資料を関係として保持する。
2025年の制度原開示、処分に関する臨時報告書、3月30日の取引原開示は未受入参照先として残す。

## 接続時の検証

- 固定レビューSHA-256: `031ce940c45f89287162af9d25a71f513b3f08525201e6486bf710a1578cbd7d`
- 取得manifest SHA-256: `0539f55522a0df0839999874d7fa449a1efc619615a9633077567309cf428b23`
- 主task: `edinet-acceptance-7203-20260927`

`bind_disclosure_review`はレビューのexact bytes、取得manifest、task・9key順序、公表日のmetadata、原本hashを照合する。
`verify_runtime`で正本DB・attempt・publication・slot・rawを検証し、原本から本文を再抽出して抽出hash・選択ページ範囲を確認する。
保存済みpages JSONだけを根拠にせず、取得後のレビュー時刻も確認する。
公表日のmetadataを原本hashだけで独立検証したとは主張しない。

runの`latest_disclosures`へ参照を保存する。原文・財務数値は含めず、意味の判定を再実行しない。
`selected_narrative_adopted=true`は選択節の定性事実だけを表し、`financial_values_adopted=false`・
`complete_disclosure_coverage=false`と未受入参照先・制約を同じレビュー内に保持する。
既存財務checked_atは変更しない。年次・IRの数値採用数と`analysis_ready=false`を維持する。

## 検証と保存

対象Pytest29件、対象Mypy・Ruffが成功した。
新規15テストはレビューhash・inventory・manifest・raw・kind・正本拒否の伝播、task・key・公表日・時刻・抽出hash・ページ範囲の不一致を検証する。
既存財務統合テストは片入力・raw root省略拒否、制約・数値件数保持と旧bytes不変を確認する。
独立レビューで実装・選択事実と関係の境界に阻害事項なし。
旧統合bundleのvalidate、新統合bundleのprepare/validateが成功した。
旧manifest・inputs bytesは不変だった。新bundleは`runs/toyota-financial-disclosure-reviewed-run-20260929/`。
manifest SHA-256は`256dc62780216590e34e9eb200ade8b5ee2914256351383b610abfccf8a35be3`。
年次30項目・IR48項目、定性選択9資料、`analysis_ready=false`である。
`./scripts/pre-commit/checks.sh`の全体Pytest・Mypy・Ruff・format・Shell format/checkがすべて成功した。
新規ファイルのformatも対象指定で確認済み。コミットhookでは成功済みPytestの重複実行だけを省略する。
次工程の計算入力不足は`docs/agent-reports/research/calculated-evidence-input-gaps.md`へ記録した。

## 残件

全本文と直近3年の開示網羅性、EDINET照合、未受入参照先、売却益の数値照合、処分・消却完了確認、
必要指標計算、source横断の最終鮮度、EvidenceSetと公開実行manifestの凍結は未完了である。
取得・選択節レビュー・数値採用・網羅性・鮮度を区別し、`analysis_ready=false`を維持する。
