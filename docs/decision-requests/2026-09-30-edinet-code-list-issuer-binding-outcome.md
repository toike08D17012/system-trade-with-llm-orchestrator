# EDINET公開コードリストの正式取得と発行体binding再検証

## 正式取得と原本

[承認範囲](2026-09-30-edinet-code-list-source-approval-request.md)に従い、9月30日22:03 JSTに
[金融庁の公式固定ZIP](https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip)へ
認証なしのGETを共有Coordinator経由で1回送信した。自動再試行は行っていない。
公開コードリスト専用の[source approval](../../config/source-approvals/edinet-code-list/v1.yaml)と
[profile](../../config/source-profiles/edinet-code-list/v1.yaml)を使用し、既存EDINET API承認の対象は変更していない。

原ZIPは私的`runs/edinet-code-list-20260930`に保存した。受信ZIP SHA-256は
`1a70628c19e73557924bbddeb8b09a90acfe38454f7fdebcb088e19540d1ff86`。
CSVは「2026年09月30日現在」の11,399件で、E02144と72030はどちらも一意に
トヨタ自動車株式会社へ対応した。調査時の9月29日現在11,394件とは別の原本である。
正式受信時刻は2026-09-30T13:03:16.896491+00:00。receipt、要求・応答metadata、
原ZIPを共有runtimeの成功したphysical attemptとコミット済みpublicationに再照合した。
元原本はGitへ追加しない。対応表は金融庁の公開データを内部照合用に加工したものである。

## 財務再生

元の6月10日書類一覧・XBRL原本・固定proposal・採用policyを保持し、正式コードリストの
receiptとrawを新しい財務入力に含めた。発行体bindingの適用終了日を9月30日に限定して別版再生した結果、
`issuer_binding_unresolved`は解消し、元候補bytesは不変だった。
mappingと採用結果を再生成し、6指標すべてが受入となった。各指標の値と出典参照は
9月27日の元採用結果と一致した。私的保存先は
`runs/market-recovery-20260930/financial-code-list-prepared`、
`financial-code-list-review`、`financial-code-list-adoption`。

今回の財務別版は市場runをまだ接続していないため`price_fx_not_connected`が残る。
9月30日22:10 JSTの要件再計算では価格終端が9月30日、確定FX終端が9月29日で、
既存市場runの9月29日価格・9月28日FXでは足りない。コードリストは9月30日より後の
発行体対応や全開示網羅性を証明しない。比較結果、財務統合、P1組立は新市場runに
同じ確認時刻で財務を結び直した後に進める。現段階は`analysis_ready=false`。

## 検証

模擬応答による1送信・raw改変検出、コードリストの重複・異符号・未来日付拒否、
旧財務入力の互換性と新bindingの原本改変拒否を対象テストで確認した。
全体`./scripts/pre-commit/checks.sh`はRuff lint・format、pytest 1,344件、Mypy、
shell検査を通過した。正式receiptは取得後にCLIでオフライン再検証した。
