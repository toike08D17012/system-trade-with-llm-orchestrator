# EDINET限定取得受入の結果

確認日: 2026-09-27

[承認済み計画](../agent-reports/plans/2026-09-27-edinet-live-acceptance-implementation-plan.md)に従い、
既存credentialの権限0600を確認して共有Coordinator経由で一覧を取得した。

| 項目 | 結果 |
| --- | --- |
| API送信 | 2026-06-10の一覧1回。書類0回、retryなし |
| 初回結果 | HTTP応答後の一覧parserで失敗。論理処理を失敗として確定 |
| 保存先 | `runs/edinet-evidence/edinet-unaccepted-1a7ab767c1a645bba8a1b612bf843a0e-list` |
| raw bytes | 380,775 |
| raw SHA-256 | `6edeafe50054063da3dd4ed5fd4816e6d52c62bf242581ebf1c241bdd5f7493b` |
| オフライン再検証 | 修正後に成功。追加通信0回 |
| 一覧件数 | 全388件、既存対応書類種別85件 |
| 対象候補 | 7203、2025-04-01〜2026-03-31、年次・非取下げ・XBRLありで1件 |
| XBRL取得・財務候補 | 未実施 |
| source受入完了 | 未完了。初回失敗を成功に書き換えない |

原因は既存parserの公式項目 `csvFlag` / `legalStatus` 未対応と、
書類情報がnullの一覧行を一律に拒否していたことだった。
[API v2仕様書](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf)の
一覧定義・出力例に基づいて初回修正し、保存済み一覧の再parseに成功した。
その後、ユーザーの方針修正により、外部応答の追加項目を許容する形へ変更した（下記）。
取得選別には縦覧区分・不開示区分の確認も追加した。
一覧parserは使用しない項目の有無・型を制約しない。live選別では縦覧区分不明を採用しない。

[利用規約](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html)も再確認した。
既存のローカル保存・外部Agent転送禁止を維持し、本文やbulk XBRLをGitに保存していない。
実credential値の表示は行わず、非保存の回帰検証には人工canaryを使用した。

次は保存済み一覧を再検証済み入力として再開する経路を用意し、対象書類1回の取得を行う。
現CLIの再実行は一覧を再送するため、今回の続行にそのまま使用しない。
実XBRL未取得のためtaxonomy対応表・訂正実例の検証は未実施のままである。

## 外部応答の互換性方針（ユーザー指示による修正）

EDINET一覧の外部応答モデルは未知の追加項目を許容する。取り込んでも処理に使用しない項目は
schemaの必須項目から外し、型変更でも一覧のparseを止めない。元JSON bytesは全項目を保存する。
内部の正規化・保存契約は厳密なままとし、件数一致・書類ID重複・採用書類の必須情報・
実際の取得判断に用いる区分値は検証する。主要項目の欠損や型不正を推測で補わない。
追加項目があっても成功することと、必要な区分が不明なら書類送信をしないことを回帰検証した。

## 保存済み一覧からの再開結果

ユーザーの続行指示に基づき、一覧の再取得なしで対象XBRLを1回取得した。
実装した `--retained-list` 経路は元の失敗を保持し、hash・要求日・対象選別を再検証する。
今回までのAPI送信は一覧1回＋書類1回。自動retryは行っていない。

| 項目 | 結果 |
| --- | --- |
| 書類raw | 2,410,352 bytes |
| raw SHA-256 | `6b9e1d9cd955f630bc0cf62f68f4d790b09798568004151bbe988224ae450034` |
| XBRL解析 | 3 member、2,182 fact候補、274 context、4 unit |
| 財務準備保存先 | `runs/edinet-evidence/edinet-prepared-a27686c68a9f4a0c883105592f15eabc` |
| manifest SHA-256 | `8ba600927fa2e9f259c7bde6e0cc2781a7f06c1f1f93e30870ef438eeb2e29bf` |
| オフライン再生 | 成功 |
| 限定取得経路 | 書類取得・raw公開・論理終了・候補準備を確認 |
| 財務受入 | `pending` / `analysis_ready=false` |

実書類のentityと一覧のEDINETコードの対応は現判定では `xbrl_entity_unresolved` となる。
意味の異なるidentifierを文字列の加工で同一と推定せず、次のmapping設計で根拠を確認する。
年次5期・中間8期間・IR・最新性の確認も未完了。価格・FXは同一時刻での再準備を行っていないため未接続。

財務項目対応の根拠採取として、原context/unit付きの語彙検索候補をローカルの
`runs/edinet-evidence/edinet-mapping-candidates-20260927.json` に保存した。
売上45、営業利益16、親会社帰属利益7、資産12、営業CF7候補を検出したが、
これはタグ名の検索結果であり、採用済み財務値や指標対応表ではない。
資本の今回の検索は0件で、値を推測・補完していない。訂正実例は未取得。
本文・fact値・bulk候補は外部Agentへ転送していない。
