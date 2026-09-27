# 複数報告の財務証拠統合計画

## 目的と根拠

2026年報告の2025・2026年と2024年報告の2023・2024年を、新しいオフライン実行準備bundleにまとめる。旧runは上書きしない。`financial_run.py`の既存run検証、`financial_comparative.py`の承認済み期間・数値再検証を再利用する。

設計03の時刻要件ではtask受付を情報打切りにしない。一方、既存`resolve_price_fx`は同一task・checked_atを要求する。この条件は維持し、財務の追加報告のみ異なるtask・確認時刻を出典単位で保持する。統合時刻を鮮度確認時刻に読み替えない。

## 実装

1. 新規`preparation/financial_multi_report.py`で主報告の既存version 2 runを再検証し、追加報告のfinancial/review/adoption/comparativeを再検証する。両taskのsecurity・market・horizons・policy・constraintsとevaluation-policyの実体、発行体を照合する。task IDと確認時刻の相違は保存する。
2. 主報告policyの5年窓を統合窓とする。追加報告のローカル窓を足し合わせない。期間・metricで重複排除し、重複した採用値は通貨・scope・Decimal値が一致する場合のみ統合する。不一致は数値を出さず拒否する。窓外factは拒否する。
3. 新manifestに出典別task・確認時刻・document/archive・制約・metric参照数、統合coverage、全依存ファイルhashを保持する。数値は元bundleのみに残す。最新性・訂正未照合を保持し、pending/analysis_ready=falseを固定する。元のrunと価格FXの時刻は変更しない。
4. 新規`financial_multi_report_cli.py`で明示パスによるprepare/validateを提供する。manifest内のパスは追跡しない。既存CLIは変更しない。
5. 対象テスト、保存済み実データのprepare/replay、README・TODO・結果を更新しコミットする。

## 検証と互換性

既存統合fixtureを使い、正常統合、重複一致、競合、発行体・task scope不一致、制約・依存改ざんを確認する。対象Pytest、Mypy、Ruffとコミットhookを実施する。実データは数値を出さず4/5期・24項目・2022年不足を検証する。旧version 1/2は変更しない。新出力は別ディレクトリであり、ロールバックは使用停止のみ。

## Blocking issues

なし。採用済み値の集約のみで、訂正照合の完了や最新性確認の代替にはしない。追加通信・外部転送・新しい財務値の採用は行わない。

## Implementation Notes

実装・保存済みデータのprepare/replayを完了し、24項目・4/5期を確認した。出典別のcandidate・adoption・comparative制約をすべて保持し、旧runは変更していない。新規7テストと既存comparativeテスト、Mypy・Ruffで検証した。[結果](../../decision-requests/2026-09-27-financial-multi-report-outcome.md)参照。
