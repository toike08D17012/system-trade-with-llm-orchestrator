# 2024年報告の当期・比較値の限定採用結果

ユーザーの継続指示を[実装計画](../agent-reports/plans/2026-09-27-retained-2024-financial-adoption-implementation-plan.md)の限定採用への承認として実施した。追加通信は行っていない。

## 採用範囲

S100TR7I（7203／E02144）の2024年3月期6項目と2023年3月期比較値6項目を採用し、オフライン再検証に成功した。出典は2024年報告であり、2023年原報告・訂正報告の本文照合を完了した扱いにはしない。数値はローカルbundleに保持し、検証出力には表示していない。

- source archive SHA-256: `fa53ae6f2b86ed9aff183c95466df69e6f4bef4253cf044cc48ae28cc35cdfb1`
- 当期manifest SHA-256: `839dde42f14a400865b9cde6c8a2d10646d92ed142cf23d4109fdfca74df107e`
- 比較manifest SHA-256: `ee9a8fa25c37da09acf82770634380c90866dbe4ca7f26f1a2b578fb39fe8406`
- 保存先: `runs/edinet-evidence/edinet-2024-financial-accepted-20260927`、`edinet-2024-financial-comparative-20260927`

## 制約と互換性

`provenance.json`にsource document、reporting period、公式schema hash、報告書掲載値という採用基準を保存し、比較bundleにはfact期間とローカル窓の区別も記録した。manifestにも訂正内容未照合・最新性未確認・全体coverageと異なる制約を残し、改ざんを再検証で拒否する。

ローカル年次窓2020～2024年では2/5期。既存2026年runの2022～2026年窓は2/5期のままで、4/5期を達成したとは扱わない。`analysis_ready=false`を維持する。新bundleのrun接続は、出典固有制約を落とさないため明示的に拒否する。

既存2026年の当期・比較bundleとrun version 1・2を保存済み入力から再検証し、既存manifest hashが変わらないことを確認した。対象テスト28件、型検査、Ruffが成功した。

## 次の作業

複数報告の財務証拠を統合する実装計画を作る。異なるtask・確認時刻の扱い、出典別制約の継承、期間重複の一致確認、全体coverageの算定を定義する。2023年訂正内容の照合と2022年等の不足期間は別の未解決事項として残す。
