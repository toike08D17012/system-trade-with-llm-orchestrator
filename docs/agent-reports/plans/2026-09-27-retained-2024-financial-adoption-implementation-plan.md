# 実装計画: 2024年報告の当期・比較値の限定採用

## 概要

保存済みS100TR7Iから2024年3月期の6項目と、同報告に掲載された2023年3月期比較値6項目をローカル採用する。追加の財務API取得は行わない。別task・確認時刻の既存2026年bundleへの統合は、この段階では実施しない。

## 根拠と現状

[調査報告](../research/retained-2024-taxonomy-comparatives.md)に、archive・member・公式schemaのhashと検証結果を記録済み。20標準宣言と、既存6項目ルールの計51関係が対応する。候補は両期間とも6項目、重複は一致している。

2023年一覧には原報告S100QZHYと訂正S100RAR0の関係があるが、本文未取得。新しい値の出典はS100TR7Iであり、2023年原報告・訂正報告を確認した値とは扱わない。

## 採用範囲の提案

- 発行体7203／E02144、archive `fa53ae6f2b86ed9aff183c95466df69e6f4bef4253cf044cc48ae28cc35cdfb1` に固定する。
- 2024年当期と2023年比較値のみ。2022年の資本候補、サマリー項目、他archiveは対象外。
- source document、報告期間、fact期間を別々に保持し、「2024年報告掲載の比較値」と明示する。
- 訂正内容未照合と最新性未確認を機械可読な制約として保存し、analysis_ready=falseを維持する。
- 既存2025・2026年の採用と合わせた全体runをまだ生成せず、完全期間4/5期の達成とは報告しない。

## 変更と手順

1. `config/financial-mapping/7203-2024-draft.json`、同当期approved policy、comparative policyを新規作成する。source、member、公式schema根拠をpinする。元2026年policyは不変。
2. `preparation/financial_acceptance.py` と `financial_comparative.py` の単一承認hashを、明示承認済みの2組だけに限定した照合へ拡張する。任意policyのstatus自己申告や日付だけの一致で許可しない。新policyが旧policy・旧reviewへすり替わる入力を拒否する。
3. `financial_mapping.py` の既存当期review経路で2024年当期を評価する。`financial_acceptance.py` で当期を採用し、`financial_comparative.py` で2023年比較値を採用する。既存の候補・taxonomy・一致検証を再利用する。
4. 訂正未照合と報告書内比較値の性質を新policyに束縛し、新しい診断・manifestに保持する。旧出力のバイト列は変更しない。新sidecarのローカル年次窓は2020～2024年であり、全体要件の2022～2026年窓とは明示的に区別する。
5. 新しい別ディレクトリへprepare/validateし、数値を出力せず12項目採用を確認する。README・TODO・受入結果を更新しコミットする。

Pythonパスは `src/stock_research_llm_orchestrator/` 配下。新規policy名・制約フィールドは提案であり、現在の実装として記述しない。runの複数archive統合と時刻整合は後続計画とする。

## 検証

既存の `tests/preparation/test_financial_acceptance.py`、`test_financial_comparative.py`、`test_financial_disclosure.py` を拡張する。新旧policy混在、archive・proposal・公式定義の根拠不一致、未承認period、当期不一致、nil・競合、制約消失・改ざんを検出する。旧2026年bundleの再検証と出力の数値非表示を維持する。wrapper経由の対象Pytest・Mypy・Ruffと必要な全体hookを実施し、ネットワークなしで実データを再検証する。

## 未解決の採用判断

2023年比較値を「S100TR7Iに掲載された値」として限定採用し、訂正内容未照合を制約として残すことを提案する。これは訂正追跡要件の充足承認ではない。新しいfinancial mapping policyはこの採用判断の承認後に作成する。

## 互換性

既存policy・hash・保存成果物・runは不変。新経路の利用を停止すれば従来状態へ戻れる。原本と失敗履歴は削除しない。

## Implementation Notes

ユーザーの継続指示により上記限定採用を承認済みとして実装した。2組のpolicy hashのみを許可し、新bundleの`provenance.json`とmanifestに制約を保持する。公式schema hashは事前検証済みの根拠をpolicyに固定するもので、実行時の再取得は行わない。

既存run経路への誤投入で出典固有の制約が失われないよう、`financial_run.py`に新source-report受入の拒否を追加した。複数報告統合は後続作業とする。対象テスト28件、型検査、Ruffを通過。実データのprepareで当期6項目、比較込み12項目を採用した。詳細は[受入結果](../../decision-requests/2026-09-27-retained-2024-financial-adoption-outcome.md)を参照。
