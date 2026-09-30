# トヨタ公式決算資料・JPX適時開示一覧の取得結果

## 取得した索引

2026年9月30日、トヨタの[現行決算資料](https://global.toyota/en/ir/financial-results/)と
[過去資料](https://global.toyota/en/ir/financial-results/archives/03.html)のHTML、
JPXの[東証上場会社情報サービス](https://www.jpx.co.jp/listing/co-search/index.html)から
7203を検索した会社詳細HTMLを私的領域に保存した。取得原本と抽出結果をそれぞれ
`runs/toyota-ir-catalog-20260930`、`runs/jpx-toyota-disclosure-catalog-20260930`に
オフライン再検証可能な形で保持する。両カタログの`analysis_ready`は`false`。

| 索引 | 2023-10-01～2026-09-30の抽出結果 | 原本SHA-256 |
| --- | --- | --- |
| トヨタ現行決算資料 | 2026-08-04公表のリンク4件 | `135a2d791a61a9609edba84ae1d37a61cfba342f2a5fc874f559570ec06d42f8` |
| トヨタFY2021～FY2026アーカイブ | 2023-11-01～2026-05-08のリンク51件 | `3ff98691f69bcff0f174042d010ce2ad5f1ced0d9d0c2055ece3bcdb2ca10d18` |
| JPXトヨタ会社詳細 | 2023-10-04～2026-09-03の111分類行、固有PDF 107件 | `534d0868dcff9068a0af6ce8dd4b6db0523df8b147599dfa13d9e9f9e13b7b05` |
| JPXに掲載された法定提出 | 2023-11-10～2026-09-11の101文書ID、提出日69日分 | 同じ会社詳細HTMLを再解析 |

JPXの111行は決算情報27、決定・発生事実81、その他3。4つの同一PDFが複数分類に
載るため固有文書数は107である。題名に「訂正」または「修正」を含む分類行は5。
これは題名だけの機械的な候補抽出で、訂正関係の確定ではない。
法定提出の索引は`runs/jpx-toyota-statutory-catalog-20260930`に保持し、
題名に「訂正」を含む候補が20件ある。JPXは[利用案内](https://www.jpx.co.jp/listing/co-search/01.html?method=init)で、
当該会社がEDINETに提出した書類を載せ、他者提出の大量保有報告書などは対象外と説明する。
したがって101件はトヨタ自身の提出候補であり、EDINETの全書類・全文受入や
訂正前後の対応関係を証明しない。

## 網羅性と残る確認

JPXは[会社別情報の説明](https://www.jpx.co.jp/listing/co-search/01.html?method=init)で
適時開示を121か月分掲載すると説明している。上記の会社詳細ページは検索で表示した
HTMLであり、現時点では取得時刻・検索要求・JPX応答を本番Coordinatorのreceiptに
束縛していない。原本の再解析は可能だが、正式な本番取得証拠ではない。

[JPXの案内](https://www.jpx.co.jp/listing/co-search/index.html)によれば会社別サービスは
毎日午前1時頃更新で、最新の開示は別の適時開示情報閲覧サービスで確認する必要がある。
本索引の最終行は9月3日であり、9月30日まで新規開示がなかったとまだ断定できない。
保存済みの[トヨタ投資家ニュース](https://global.toyota/en/ir/)は直近20件だけで、
過去3年のIR全件一覧ではない。

次はJPX原本の正式な取得記録と最新31日間の突合、EDINET全提出日・訂正関係の確認、
重要PDF本文の受入、ニュース12か月と評価指標の収集を進める。

評価指標の一次情報候補として、トヨタの[株式概要](https://global.toyota/en/ir/stock/outline/)、
[配当情報](https://global.toyota/en/ir/stock/dividend/)、
[財務ハイライト](https://global.toyota/en/ir/finance/)も確認した。
株式概要は発行済株式数と自己株式控除の注記、配当情報は配当政策と過去実績、
財務ハイライトは収益性を載せる。いずれも今の私的カタログには未収録で、
時価総額・企業価値・比較会社・将来予想の受入値としてはまだ扱わない。
