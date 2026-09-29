# 価格・FXのP1証拠ドラフト接続結果

## 実装範囲

継続実装・検証済みコミットの所有者指示に基づき、保存済み入力をオフラインで再検証した。
新たな通信・取得・価格/FX再計算は行っていない。
財務統合CLIのprepare / validateに任意の`--price-fx-evidence-draft`を追加し、年次・IRとは別の私的ドラフトを出力する。
未指定時の従来出力は維持する。

価格の保存rawはyfinanceが返したDataFrameのCSV出力であり、HTTP本文ではない。
artifact_kind・http_body_retained=false・native policy・元のlibrary metadataをsource IDとpayloadへ束縛する。
保存取得承認の版をhistory metadataと照合し、現在の使用承認や再利用承認と区別する。
対応外の取得形態は任意出力を拒否し、古い内部出力を変更しない。

FX原本は年bucketの保存bytesと元のreceipt・取得日時・URL・hashを参照する。
FXのsource対象期間は対象sliceとyear bucketの交差で、year・requested期間をpayloadへ記録する。
今回追加のruntime DB照合を行ったとは主張しない。
公表・初回利用可能・更新時刻が未知のsourceではnullを維持する。

## 値・欠損・時点

既存conversion.jsonの行からclose_jpy、usd_jpy、close_usdをそのまま転記する。
日付ごとにnormalized価格・FXとcalculated換算を独立レコードにし、入力ID・処理版・content hashを束縛する。
単位はJPY_per_share、JPY_per_USD、USD_per_share。
換算式unadjusted-close-jpy-divided-by-usdjpy-v1、精度28・ROUND_HALF_EVEN・追加quantizeなしを保持する。
欠損換算はnullと元の全reasonsを保持し、値を補完しない。

normalized値が存在する場合も価格/FXのrun layer status・reasons、原row reasonsと確認時点を保持する。
数値の存在は品質・承認・鮮度の受入完了を意味しない。
FXの観測日・UTC区間と確認状態を保存し、株価終値との同時性を主張しない。
JPX/calendar/品質/現在使用承認は再検証済みmanifest・requirements・入力hashに束縛し、新たなP1化を主張しない。

## 保存・検証

値・原本・ドラフトはGit追跡対象外で、manifestはhash・層別件数のみ。
元原本と取得承認のartifact locatorをdependency price_fxの固定path/hashに限定し、原本を複製しない。
取得来歴、canonical payload、共通確認contextをhashで束縛する。

対象40テストと全体Mypy236ファイルが成功した。
実再生fixtureで正常換算、元の値・単位・input層/hash、native CSVの区別、欠損・品質状態、取得形態/版/FX原本/日付/欠損理由不一致を確認した。
独立の計画・実装レビューで重大なブロッカーは認められなかった。
対象Ruff・formatとrepository wrapperによる全体Pytest・Mypy・Ruff・format・Shell検査もすべて成功した。
コミットhookでは成功済み全体Pytestの重複だけを省略し、他の適用hookを実行する。

旧IR P1 run validate、新run prepare / validate、旧run全ファイルbytes不変を確認した。
新run: `runs/toyota-financial-market-p1-draft-run-20260929`。
2,203レコード（source_metadata 5、raw 5、normalized 1,462、calculated 731）を生成した。
731換算行と1,462入力枠すべてで、元の値・日付・参照行の維持を確認した。
新manifest SHA-256: `8c573b06d096a6cc85cdfded855cfb44f4a1c0d84693416a25867969aee13e36`。
価格/FXドラフトSHA-256: `7aef92c2b89577749a1c57ddda158467ac6520bfb917547dd82e28f6a7ca4094`。
年次・IRドラフトと年次計算、元price_fxのconversion.jsonは変更しなかった。

## 残件

定性レビューのP1接続、開示網羅性・最終鮮度、全P1証拠の組立、EvidenceSet凍結、Agent実行・レビュー・最終レポートは未完了。
全source制約・restricted usesと`analysis_ready=false`を維持する。
