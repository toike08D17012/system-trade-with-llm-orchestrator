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

9月30日22:10 JSTの要件再計算では価格終端が9月30日、確定FX終端が9月29日で、
既存市場runの9月29日価格・9月28日FXでは足りなかった。

## 同時刻の市場・財務統合

承認済みyfinance v3の`Ticker.history`を1回呼び、9月30日までの価格730営業日を
`prices-20260930`へ保存した。最初の試行はカレンダーmetadataの型検証で通信前に止まり、
正しい保存済みカレンダー来歴を渡した後に取得した。価格品質は通過し、JPXスナップショットの
鮮度未確認を残す。承認済みDukascopy経路で9月29日までの2026年bucketを取得し、
年別receiptを共有runtime・rawと照合した。2023～2025年の保存済み証拠と組み合わせた
`composite-fx-20260929`はオフライン再生で有効だった。

同じ確認時刻の`run-20260930-evening`は価格・FXの必要日付欠損がともにゼロ、
換算729行で`ready_with_limitations`となった。そこへ正式コードリストを含む財務入力を
結び直し、`financial-code-list-market-prepared`、`financial-code-list-market-review`、
`financial-code-list-market-adoption`、`financial-code-list-market-comparative`を再生成した。
発行体bindingは有効で、採用6指標の値・出典参照は旧版と同じだった。
比較結果では12件を受入とした。

元の17依存を新しい市場・主財務5依存へ差し替えて
`integrated-20260930-evening`を再生成した。年間5期、中間8期、受入指標30件、
組立草稿2,348レコード。manifest SHA-256は
`0807786e9bf8f7009e60ff604853ec4dcfdcafaacf93a4420826d9329f8494da`。
元成果物の保存済みinventoryでは欠落・hash変更がともにゼロだった。

コードリストは9月30日より後の発行体対応や全開示網羅性を証明しない。
市場・統合は`ready_with_limitations` / `pending`を保持し、`analysis_ready=false`。
開示・ニュースcoverage、最新性確認、EvidenceSet凍結は引き続き必要。

## 検証

模擬応答による1送信・raw改変検出、コードリストの重複・異符号・未来日付拒否、
旧財務入力の互換性と新bindingの原本改変拒否を対象テストで確認した。
全体`./scripts/pre-commit/checks.sh`はRuff lint・format、pytest 1,344件、Mypy、
shell検査を通過した。正式receiptは取得後にCLIでオフライン再検証した。
市場run、FX合成、財務採用、比較結果、17依存統合は各prepare時のvalidatorで再生した。
