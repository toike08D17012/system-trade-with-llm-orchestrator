# 定性レビューのP1証拠ドラフト接続結果

## 実装範囲

継続実装と検証済みコミットの所有者指示に基づき、固定済みレビューをオフライン再検証した。
財務統合CLIのprepare / validateへ任意の`--narrative-evidence-draft`を追加した。
年次計算・年次ドラフト、訂正pairと補足レビュー、最新開示runtimeと固定レビューが必要になる。
新規通信・取得・意味の再判定は行っていない。

最新9資料では固定レビューのfactsだけを資料ごとのnormalized値に転記する。
選択ページ、抽出hash、レビューhash・担当・時刻・方式を保持し、未解決参照はcontextに残す。
訂正1資料では訂正分類と選択された財務再表示の有無だけを転記する。
年次ドラフトからarchive hashと訂正文書IDの両方で原本を選び、曖昧な候補を拒否する。
source/rawのID・payload・取得承認と原本locatorは変更せず再利用する。

## 来歴と制約

原本取得時刻とレビュー時刻を区別する。
最新資料の公表日は日単位のpayload根拠として保持し、正確な公表時刻はnullとする。
適用期間は公表日の点であり、記述内容全体の効力期間を主張しない。
保存PDF bytes・取得承認hash・固定レビュー・訂正member hashを照合し、レコード参照の閉包と層を検証する。
定性値・レビュー原文とドラフトはGit対象外で、manifestにはhashと層別件数のみを追加する。
全制約、選択レビューの非網羅性、意味の再判定なし、`analysis_ready=false`を維持する。

## 検証と保存

対象37テストが成功し、結合ケース追加後も対象7テストが成功した。
原本・承認・補足レビューhashの改変、文書ID不一致、曖昧な原本、task・資料集合不一致を拒否するテストを含む。
対象Ruffと全体Mypy238ファイルが成功し、独立した実装レビューでブロッカーはなかった。
repository wrapperの全体Pytest・Mypy・Ruff・format・Shell検査もすべて成功した。
コミットhookでは成功済み全体Pytestの重複だけを省略し、他の適用hookを実行する。

旧価格/FX run validate、新run prepare / validateが成功した。
新runは`runs/toyota-financial-narrative-p1-draft-run-20260929`。
30レコード（source_metadata 10、raw 10、normalized 10）を生成した。
manifest SHA-256: `1b2dd0953c6a0718232eac2e3916f9dfc9c51d1c02f2e4f60942a7a347e72446`。
定性ドラフトSHA-256: `7837054eac3a30f27453117cc2e02ba17c60ab64ea06105c7a5e6b27201e33d0`。
旧runの全検証対象ファイルと新runの年次・IR・価格/FXドラフト、年次計算のbytes不変を確認した。
実再生後の比較スクリプトのpath指定誤りを修正し、保存済み成果物で比較を完了した。

## 残件

全P1ドラフトの組立、開示網羅性・最終鮮度、EvidenceSet凍結、分析Agent実行と最終レポートは未完了。
訂正原本は年次ドラフトとIDが共通になるため、組立時には同一payloadの重複を整理する。
