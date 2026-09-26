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
一覧定義・出力例に基づき、項目の型を明示し、対象書類の必須情報は引き続き検証する。
未知の項目を無視する変更や、nullからの値の推定は行っていない。
取得選別には縦覧区分・不開示区分の確認も追加した。
旧fixtureには追加2項目がないため、欠落は互換性のため許すが、live選別では縦覧区分不明を採用しない。

[利用規約](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html)も再確認した。
既存のローカル保存・外部Agent転送禁止を維持し、本文やbulk XBRLをGitに保存していない。
実credential値の表示は行わず、非保存の回帰検証には人工canaryを使用した。

次は保存済み一覧を再検証済み入力として再開する経路を用意し、対象書類1回の取得を行う。
現CLIの再実行は一覧を再送するため、今回の続行にそのまま使用しない。
実XBRL未取得のためtaxonomy対応表・訂正実例の検証は未実施のままである。
