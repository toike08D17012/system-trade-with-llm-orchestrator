# 2026年10月1日のJPX会社別開示再確認

[JPXの東証上場会社情報サービス](https://www.jpx.co.jp/listing/co-search/01.html?method=init)で
証券コード7203を検索し、72030・トヨタ自動車の会社詳細を再取得した。
原本HTMLは私的調査ファイル
`runs/toyota-ir-catalog-research-20260930/jpx-toyota-detail-20261001.html`に保存した。
SHA-256は`8aba4cdfff1ce949c95e85b47f9dfe1c5eb2e767f10873eb1884abed72777d66`。

2023年10月1日～2026年10月1日で再解析した私的索引は、適時開示111分類行・
固有107文書、発行体自身のEDINET法定提出101件だった。抽出対象の全行は
9月30日の索引と一致し、適時開示の最新日付は2026年9月3日、法定提出の最新日付は
2026年9月11日である。会社基本情報の発行済株式数は8月末時点で
14,594,987,460株と表示された。[JPXの説明](https://www.jpx.co.jp/listing/co-search/01.html?method=init)では
この欄は前々月末時点の値であり、時点が異なる自己株式数と単純に差し引かない。

再解析した原本・索引はそれぞれ
`runs/jpx-toyota-disclosure-catalog-20261001`と
`runs/jpx-toyota-statutory-catalog-20261001`に置いた。
この取得は手動調査であり正式なCoordinator receiptではない。
また、JPX会社別ページの法定開示欄は当該発行体自身の提出に限るため、
第三者の大量保有報告書等を含まない。最新性を受け入れるには、
JPXの更新時刻とTDnet直近表示を照合し、重要なPDF本文・訂正の関係を確認する。
`analysis_ready=false`を維持する。
