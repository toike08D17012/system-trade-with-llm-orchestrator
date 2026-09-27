# 複数報告の財務統合結果

保存済み2026年・2024年報告を再検証し、新しいローカル実行準備bundleに統合した。追加通信なし。

- 2022～2026年の年次窓で24項目、完全期間4/5期。2023～2026年を採用し、2022年は不足。
- 出典別task・確認時刻・document/archive・制約・参照数を保存。値は依存bundleのみに残した。
- 2023年訂正内容未照合、最新性未確認、interim・IR等の不足を保持し、pending/analysis_ready=false。
- 旧run・価格FXの時刻条件は不変。新しい統合時刻を最終鮮度確認として作成しない。
- 全依存を再検証し、出力改ざんを拒否することを実データで確認。対象テストでは重複一致・競合・窓外期間・task scope・評価policy・発行体不一致を検証。

最終出力は`runs/edinet-evidence/edinet-multi-report-financial-run-20260927-v2`。末尾v2はローカル検証成果物名であり、manifest schemaはversion 1。初回検証出力も削除せず保持している。

manifest SHA-256: `2909262b3d98cbb96ed0ad2f19b969cb5bfacc4c770b61e87de92a32b737b5bb`。

次は[2023年原報告・訂正報告の取得承認](2026-09-27-edinet-2023-pair-acquisition-draft.md)。既存承認v4は「no retry or further continuation」であり、旧campaignの未消費slotを再利用しない。
