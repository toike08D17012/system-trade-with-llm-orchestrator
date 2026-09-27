# 2022年比較値の限定採用結果

進行に必要な取得と明確な次タスクの自動続行指示に基づき、2023年訂正報告S100RAR0掲載の2022年比較値6指標を採用した。原報告S100QZHYとの2022・2023年12組の一致を毎回再検証する。一般的な訂正選択を自動化したものではない。

## 検証根拠

2022-11-01版の公式jpigp/jppfs schemaを必要な2件に限定し、63秒以上の間隔・retryなしで取得した。標準20宣言のID・QName・期間型・金額型を確認し、原報告・訂正の両taxonomy関係を再検証した。

- jpigp SHA-256: `ee13a48304a249f716b834cc08aa653bba9d0ecc4d09fb2b71275d94fd83bedf`
- jppfs SHA-256: `54abd73d1336269efd19c769eb04128c7be3123a4118b6e8d8fb090a1de6ab29`
- 公式schema URL・取得時刻・サイズは`runs/taxonomy-2022-11-01/*-receipt.json`に保存。

財務値はローカルのみ。受入policy・raw・schema member・出力hashを保持し、期間混在・不一致・nil・欠落は拒否する。2023年6指標は既存2024年報告比較値とも一致した。

## 成果物と残る不足

- 6項目受入: `runs/edinet-evidence/edinet-2022-pair-accepted-20260927`
- 30項目統合: `runs/edinet-evidence/edinet-five-year-financial-run-20260927`
- 年次窓2022～2026年は5/5期。旧4/5期・旧2/5期bundleは変更していない。
- 訂正本文の定性的内容未確認、最新性未確認、四半期・半期、IR・その他開示などは未解決。
- status=pending、analysis_ready=false。期間充足を分析可能性に読み替えない。

実データprepare/replayと改ざん拒否を確認した。合成テストでtaxonomyを含む受入、policy/archive/output改ざん、欠落・nil・値競合、任意pair接続と従来出力維持を検証した。

受入manifest SHA-256: `59c7d0b0ba8a735a4ad314bb9673e90cce9a36ec369340e1e18e294e89863782`。統合manifest SHA-256: `a0f8a4010c59253417c80c34a893c80937f2923656846b8a5edef17625e55376`。コミット時の全体Pytest・Mypy・Ruffが成功した。

次の[四半期・半期資料の候補調査](../agent-reports/research/2026-09-27-interim-source-inventory.md)も実施した。PDFの期間・比較欄・抽出方式は受入前に確認する。
