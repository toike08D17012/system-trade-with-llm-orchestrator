# 年次・interim財務証拠のローカル統合計画

## 目的と現状

年次5期の30項目と、公式IR PDFの四半期・半期8期間48項目を、内部runの別成果物へ接続する。
`preparation/financial_multi_report.py`は年次2報告と任意の訂正pairを再検証し、値を出力せずcoverageを集約する。
`preparation/interim_ir.py`は固定PDF・取得記録・policyを再検証する。既存の年次出力は変更しない。

## 変更と手順

1. 複数報告CLIに任意の`--interim`と`--interim-source`を追加する。両方指定を必須とし、保存先は入力と重複させない。
2. 統合時に`validate_interim`を実行し、年次とのsecurity_code・edinet_codeを照合する。主報告の確認日より後の公表資料は混入させない。
3. 年次countは既存の意味を維持し、interimのcountとcoverageを別項目へ保存する。出典の公表日・取得日・hash・累計/時点の区別と制約を引き継ぐ。取得日時を一括鮮度確認時刻とはしない。
4. 接続なしの既存bundleがbyte単位で同じこと、片側指定・発行体不一致・再検証失敗・将来公表資料を拒否することを対象テストで確認する。保存済み実証拠の統合とreplayを実施する。
5. README・TODO・結果文書を更新し、dependency追加を含む全体検査を行ってコミットする。

## 制約と完了条件

原文・財務値はローカルに置き、統合成果物へ値を転記しない。IR取得はoperator取得であり、runtime source bindingの完了とは扱わない。`analysis_ready=false`を維持する。新規送信は不要。

変更対象は上記2実装module・CLI、対応テスト、文書。旧年次成果物のreplayを保ち、年次5/5期とinterim8/8期間を別々に追跡できれば接続完了とする。Blocking issuesなし。

## Implementation Notes

実資料で年次30項目・5/5期とinterim48項目・8/8期間を接続し、全原証拠からreplayした。旧年次5期bundleもbyte単位で同一の再検証に成功した。統合出力は値を含まず、analysis_ready=falseのまま保存した。
