# P1証拠ドラフトの私的組立結果

## 実装範囲

財務統合CLIのprepare / validateへ任意の`--assembled-evidence-draft`を追加した。
年次計算と年次・IR・価格/FX・定性の4ドラフト生成フラグを必須とする。
既存依存入力をオフライン再検証して4ドラフトを再生成し、そのbytesから私的組立を作る。
保存済みの任意ファイルを外部入力として読み込む機能は追加していない。

## 同一性と来歴

P1モデルでレコードを検証し、同一IDのrecordと対応payloadが一致する場合だけ重複を整理する。
元のID・層・値・欠損・丸め・hash対象payloadを保持する。
rawはsource_metadata、normalizedはraw、calculatedはnormalizedへの参照を確認し、欠落・層違いを拒否する。
各ドラフトのtask・kind・version・pending状態・analysis_ready=falseを確認する。
取得承認と原本locator、元のcontext/hash・制約・構成レコードIDをカテゴリごとに保持し、
元ドラフトbytesのhashに束縛する。sourceのcontent hashは原本hashでありmetadata payloadのhashに置き換えない。

## 検証

対象18テストと全体Mypy240ファイル、対象Ruffが成功した。
正常な重複整理と決定性、task・state・kind不一致、record/payload衝突、
同一ドラフト内の重複、参照欠落・層違い、未知のpayload ID、フラグ不足と出力改変拒否を確認した。
独立した計画・実装レビューでブロッカーはなかった。
実データの旧run validate、新run prepare / validateが成功した。
新run: `runs/toyota-financial-assembled-p1-draft-run-20260929`。
2,351件（source_metadata 25、raw 25、normalized 1,550、calculated 751）を保存した。
旧run全ファイルと元の4ドラフト・年次計算・inputsのbytes不変を確認した。
manifest SHA-256: `7ad1d642737b8ca997f03c41765fb7f178097bf4bbd1661014ab2c9eef1586af`。
組立SHA-256: `519163b6da8ecb2982b26fd06d4ec244ceefebe15760c07b82283e94362d48e9`。

初回全体checksも成功した後、真偽値と数値の比較境界を追加確認した。
Pythonの等値判定でtrueと1、falseと0が同じと扱われるため、
重複record/payloadの比較をcanonical JSON bytesの一致に変更した。
型の違いを拒否する4ケースを追加し、修正後の組立15テストが成功した。
最終状態の全体Pytest・Mypy・Ruff・format・Shell検査もすべて成功した。
修正後の実データvalidateで同じ出力hashと旧bytes不変を再確認した。
コミットhookでは成功済み全体Pytestの重複のみ省略し、他の適用hookを実行する。

## 残件

組立は`internal-assembled-p1-evidence-draft`のpending成果物で、EvidenceSetV1への凍結ではない。
網羅性・最終鮮度・凍結日時・分析完了を付加せず、`analysis_ready=false`を維持する。
新規通信・取得・値の再計算は行っていない。原本・値・ドラフトはGit対象外に保持する。
開示網羅性とtask単位の最終鮮度判定、EvidenceSet凍結、分析Agent実行・最終レポートが残る。
