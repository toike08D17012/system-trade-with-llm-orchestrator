# 過年度年次の限定続行結果

## 結果

ユーザー承認に基づきv4限定続行を実装・実行した。2024年3月期のXBRL取得・オフライン再検証は成功。続く2023-06-30の一覧は文書ID重複によりparserが拒否し、停止した。

| 項目 | 実績 |
| --- | --- |
| 今回の新規要求 | 2（2024年書類、2023年一覧） |
| 元campaignからの通算 | 3 / 最大4 |
| 未送信 | 2023年書類1要求 |
| 最初の一覧再送 | 0 |
| 2024年書類 | S100TR7I、E02144／72030、2023-04-01～2024-03-31 |
| 新しい財務値採用 | 0。候補保存のみ |

slot-0は2026-09-27T11:38:44.069389+00:00、slot-1は11:54:20.870794+00:00、slot-2は11:55:28.159081+00:00。要求間隔は60秒以上。元の失敗・送信記録と続行markerを維持し、slot-3は未消費。失敗後の自動再開・retry・別runtimeへの切替なし。

## 保存した証拠

- 2024年financial bundle: `runs/edinet-evidence/edinet-prepared-c29a217d36c24e27b95972fdb52900ec`
- manifest SHA-256: `30f3a7aa965fb0f63583f50ff2d188ae7857fe220298e19418c7d4b870e6ade7`
- archive SHA-256: `fa53ae6f2b86ed9aff183c95466df69e6f4bef4253cf044cc48ae28cc35cdfb1`
- XBRL候補: 2,285 facts、307 contexts。原本からのfinancial再検証成功。
- 2023年失敗一覧: `runs/edinet-evidence/edinet-unaccepted-76c1cc64feeb4113b2171e8e4bb3a3cf-list`
- 一覧SHA-256: `94fef761bca1f9b3c222156c526918ee7f41e3ffa097dfea73545927534c3519`
- 一覧受信時刻: 2026-09-27T11:55:28.332215+00:00。

最初の失敗一覧とapproval v3は新financial入力version 2へ保持し、書類取得はapproval v4に束縛した。旧version 1は維持。raw・財務値・credentialはGitやCLI出力へ転送していない。

## 2023年一覧の停止原因と追加発見

全1,978行中、別発行体E01330の文書ID S100R9BJが2行あった。両行はseqNumberだけでなく、opeDateTime、docDescription、docInfoEditStatusも異なるため、単純に片方を捨てる変更は行わない。全一覧は有効な採用証拠として再検証できた状態ではない。

保存rawの対象発行体のメタデータには次の関係があった。これは局所的な診断結果であり、一覧全体の受入成功とは区別する。

| 文書 | 種別 | 提出時刻 | 関係 |
| --- | --- | --- | --- |
| S100QZHY | 年次120 | 2023-06-30 11:30 | 2022-04-01～2023-03-31 |
| S100RAR0 | 訂正130 | 2023-06-30 14:13 | parentDocID=S100QZHY、期間欄null |

対象期間だけで訂正を検出すると、期間欄nullの親文書関係を見落とすため、固定過年度の選別を補強した。親文書IDが採用候補を指す場合も停止する。重複行を処理できても、今回の2023年原報告を無条件に取得・採用してよいとはしない。

## 取得済み2024年報告の候補棚卸し

既存6項目と同じlocal nameを持つ実在QNameについて、entity・期間・dimension・単純JPY単位・nil・重複一致の既存候補判定を行った。意味・連結範囲のtaxonomy承認は未実施。

| 項目 | 2023年3月期ordinal | 2024年3月期ordinal |
| --- | --- | --- |
| TotalNetRevenuesIFRS | 929 | 930 |
| OperatingProfitLossIFRS | 939, 1275 | 940, 1320 |
| ProfitLossAttributableToOwnersOfParentIFRS | 957 | 958 |
| AssetsIFRS | 875, 1280 | 876, 1325 |
| EquityIFRS | 921, 1080, 1088 | 922, 1168 |
| NetCashProvidedByUsedInOperatingActivitiesIFRS | 1205 | 1206 |

複数ordinalは値が一致する候補。税引前利益や親会社帰属持分による代用はしていない。売上のnamespaceは `http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E02144-000/2024-03-31/01/2024-06-25`、残りは `http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2023-12-01/jpigp_cor`。

2022年3月期はEquityIFRSのordinal 1000だけが候補で、他5項目は揃わない。この資料から2023・2024年の2期をローカル採用できる可能性はあるが、2023年の訂正前後・比較値の位置付けを含め、採用前の検証が必要。既存の完全期間2/5期はまだ増やしていない。

## 検証と次の作業

実取得前に関連54テスト、全体Pytest・Mypy・Ruffが通過した。親文書IDによる訂正停止ケースを追加し、campaign関連13テストが通過。最終変更も全体Pytest・MypyおよびRuffで検証した。整形hook適用後の同じコードに対して全体テストが通過し、再コミットでは成功済みPytest・Mypyを重複実行しない。Shell変更なし。

次は追加通信なしで2024年報告のtaxonomy・2023年比較値を確認し、2期間の受入計画を作る。2023年の一覧重複を一律に無視して取得再開する方法は取らない。2022年分の資料選択は別途必要で、今回残った1要求を別日付・別書類へ転用しない。
