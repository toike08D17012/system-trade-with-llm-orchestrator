# EDINET公開コードリストによる発行体確認の調査結果

## 取得と照合

金融庁の[公式ダウンロードページ](https://disclosure2.edinet-fsa.go.jp/weee0010.aspx)に掲載され、
[EDINET API仕様書](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf)にも
明記された[固定リンク](https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip)を確認した。
9月30日07:48 JSTごろに公開ZIPを認証なしで調査取得した。HEAD 1回とGET 1回。
APIキー、共有runtime、Production Coordinatorは使用していない。

ZIPにはShift-JISの`EdinetcodeDlInfo.csv`が1ファイル含まれ、一覧には
「2026年09月29日現在」、11,394件と記載されている。E02144の行は1件、証券コード72030の行も1件で、
どちらもトヨタ自動車株式会社に対応する。ZIP SHA-256は
`e697d4ace93da2054dfeb04e6f1cace67d2f0a1e6c51603a4b989df899b4cd0d`、
CSV SHA-256は`1c2123866b6eb578becd49db6b03a4877321e8d130a528421c6128a63d9369e0`。
9月29日14:59:02 UTCのLast-Modifiedを受け取った。私的ZIPと調査metadataは
`runs/edinet-code-list-research-20260930`に保存し、Git追跡しない。
これは**発行体対応の調査結果**であり、9月30日以降の全開示網羅性、
財務値の受入、本番source approval、正式な取得receiptを意味しない。

[金融庁の利用規約](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html)は
公開コンテンツの出典表示を求め、APIで取得できないコンテンツの機械取得を禁止の例外としている。
公式API仕様書はコードリストの固定配布リンクを別途案内する。
本調査で加工した対応表には金融庁が作成した未加工の表とは異なることを明示する。

## 財務接続の状態

現行の[EDINET API source approval v2](../../config/source-approvals/edinet/v2.yaml)は、
2026-06-10の固定書類一覧と年次書類1件に限定される。今回のコードリストは承認済み財務証拠に加えず、
9月27日で期限切れのissuer bindingも変更していない。

コードリストの確認時刻は既存市場runの確認時刻より後であり、既存runに遡及して使えない。
新しい承認と正規取得後、その時点に合わせて市場・財務を別runで再評価する必要がある。
[新source承認依頼](2026-09-30-edinet-code-list-source-approval-request.md)に対象を固定した。
`analysis_ready=false`、旧受入6指標と旧P1組立は不変。
