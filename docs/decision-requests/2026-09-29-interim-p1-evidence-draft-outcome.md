# IRのP1証拠ドラフト接続結果

## 接続範囲

所有者の継続実装・検証済みコミット指示に基づき、取得済み資料のオフライン再検証だけで実施した。
新たな通信・取得は行っていない。
財務統合CLIのprepare / validateへ`--interim-evidence-draft`を追加した。
interim/interim_source/interim_runtimeと明示runtime・ir_raw_rootを必須にする。
年次ドラフトとは別に、source_metadata・raw・normalizedのIRドラフトを保存する。
追加指標計算、quarter差分、P1 EvidenceSet凍結は行わない。

## 時刻・単位・来歴

runtime原本とoperator原本が固定policyのbody hashに一致することを確認する。
runtime取得のsource_metadataへ元のreceived_at・保存取得時承認を参照付けし、receiptの全identity・root alias・task・path・hashをIDへ束縛する。
原本と承認のlocatorは依存束または呼出元の明示raw rootへ限定する。

公表日は日単位でしか確認できないため、payloadにpublished_onを保持し、published_at・first_available_at・updated_atはnullとする。
時点指標はstart=end、フロー指標は年度累計の元start/endを使用する。
採用済みJPY値を追加変換・丸めせず保存し、元単位JPY_millionsと倍率1000000を処理根拠として保持する。
page/table/column根拠、欠損と元の理由、values・policy hashをcanonical payloadへ保存する。

P1 raw参照はruntime取得のPDFである。operator採用の元receipt・取得日時・body/receipt locatorは別の履歴として残す。
同じbody hashという関係は、operator取得のDB検証やruntime取得時承認の遡及適用を意味しない。
既存operator制約と全source制約を引き継ぐ。
値・原本・ドラフトはGit追跡対象外、manifestはhash・層別件数のみでCLIへ値を表示しない。

## 検証

実P1モデルを使う対象テストで、期間・元単位・値の維持・原本locator・欠損・別attempt・同一再生とraw/承認/資料集合/metric/期間/値不一致の拒否を確認した。
統合テストは必須入力、prepare/validateへの対称な受渡し、改変拒否、既存出力不変を確認する。
独立計画・実装レビューで重大なブロッカーは認められなかった。
IR単体12件と既存統合7件が成功した。統合テストの追加部分で、既存の拒否テストが変更したfixtureのkindを正規kindに戻す修正を行い、該当テストを再確認した。
対象Ruff・format、全体Mypy235ファイルに加え、repository wrapperの全体Pytest・Mypy・Ruff・format・Shell検査はすべて成功した。
コミットhookでは成功済み全体Pytestのみを省略し、他の適用hookを実行する。

旧年次P1 run validate、新run prepare / validateと旧全ファイルbytes不変を確認した。
新runは`runs/toyota-financial-interim-p1-draft-run-20260929`。
IR64レコード（source_metadata 8、raw 8、normalized 48）を生成した。
実データの全48枠で元の値・原参照・単位・時点/累計期間を保持し、全sourceの公表timestampはnullであることを確認した。
新manifest SHA-256: `1dd18c409be9a2658a75020f8ba44f59a86390b68d600dddf2096d2d8c1a9eb3`。
IRドラフトSHA-256: `341bf7a9235638cb391794c580c9c5bcfe9c70b689eec1646a2fd76c53a39874`。
年次ドラフトhashは`825a36e9088a72edaa0f141c3fd46ab56b2e3db92b65c8856d8b01510d5d1b03`のまま。
年次計算成果物もbytes不変である。

## 残件

価格/FX・定性レビューのP1接続、開示網羅性、全体の最終鮮度、EvidenceSet凍結、Agent実行・レビュー・最終レポートは未完了。
年次30項目・IR48項目の採用件数と`analysis_ready=false`を維持する。
