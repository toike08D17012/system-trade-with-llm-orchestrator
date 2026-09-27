# 財務6項目の限定採用結果

## 承認と到達点

2026-09-27、ユーザーが [実装計画](../agent-reports/plans/2026-09-27-financial-mapping-adoption-implementation-plan.md)
と対応案を承認した。保存済み7203の2026年3月期について、taxonomy根拠を検証し、
売上・営業利益・親会社帰属利益・資産合計・資本合計・営業CFの6項目をローカル採用した。
追加のEDINET API取得・credential使用は0回。

採用は今回の書類・期間・原archiveに限定する。年次5期・中間8期間、最新性、IR、価格FX接続は
未完了のため、全体の `status=pending`、`analysis_ready=false` を維持する。
数値はローカル保存のみ。coding agentやscreening Agentへの財務数値・原文転送は許可していない。

## 採用規則と根拠

[採用policy](../../config/financial-mapping/7203-2026-approved.json)のSHA-256:
`7874467ef5032e5dcfa58a7403259f08c93222b7fb91ac73d95856c944c4d46f`

実装はpolicyの正確なhashを照合し、draftのstatus変更だけでは採用しない。
policyはsecurity、EDINETコード、書類ID `S100Y8NY`、原archive、期間、完全QName、
schema・linkbaseのhashと必要な51関係、25概念の宣言対応を保持する。
公式標準schemaの限定宣言一覧を承認policyに固定し、実書類のschema importと照合する。
外部schemaを自動取得する汎用DTS処理は行わない。

売上は連結損益計算書の `TotalNetRevenuesIFRS` を採用した。
製品売上・金融事業収益を含む合計を示すschema・presentation・definition・calculationの関係を検証する。
子項目の値から合算し直さず、開示された合計factを使う。
資本合計は `EquityIFRS` であり、親会社所有者帰属持分とは区別する。

連結範囲は当該roleのheadingからconceptへの経路、table・axis・defaultの関係を検証する。
context IDやdimensionなしだけでは決めない。根拠欠落や未対応の上書き関係は未採用になる。
未知の無関係なconcept・roleの追加は必要な関係の判定に使わない。
原archive hashの限定は書類単位の採用権限であり、外部APIの未知項目を拒否する変更ではない。

金額はJPY単位のDecimalから文字列化し、decimalsを倍率と扱わない。
同値重複は全参照を残し、nil/ゼロ、異値競合、別通貨、未対応scale、訂正未確定を区別する。

## 保存結果

原bundle:
`runs/edinet-evidence/edinet-prepared-a27686c68a9f4a0c883105592f15eabc`

候補レビュー:
`runs/edinet-evidence/edinet-mapping-review-20260927`

新規採用bundle:
`runs/edinet-evidence/edinet-financial-accepted-20260927`

新manifest SHA-256:
`e21238c24d0b727c63c3c8168aa2c1a2a557d3c22c21aa6d6095f7218cabde6c`

| 項目 | 採用結果 | 保持したfact参照数 |
| --- | --- | --- |
| 売上 | accepted | 1 |
| 営業利益 | accepted | 2 |
| 親会社帰属利益 | accepted | 1 |
| 資産合計 | accepted | 2 |
| 資本合計 | accepted | 2 |
| 営業CF | accepted | 1 |

新bundleにはpolicy、taxonomy根拠、ローカル値、値を含まない診断、manifestを保存する。
出力directoryは `0700`。元bundleとv1レビューは変更しない。
再検証には元bundleとv1レビューが必要で、原bytesからの再生で値・根拠・診断の改変を検出する。
旧manifestの不足理由は履歴として `inherited_reasons` に保持し、旧結果を後付けで書き換えない。
現在の個別項目の採用状態は新しい `values.json` と `diagnostic.json` で確認する。

## 検証

- taxonomy、限定採用、既存財務準備・候補レビュー・fact parserの関連83テストが成功。
- Ruff・Mypyが成功。
- 合成原ZIPから6項目採用・保存・再検証を実施。値・根拠・policy・原証拠の改変、再公開、
  出力重複、危険なXML/ZIP、未解決locator、誤namespace、連結defaultの競合を検証。
- 保存済み実証拠のprepare/validateに成功。標準出力は採用件数と状態のみで財務数値を含まない。

## 次に残る作業

採用済み値を通常実行の財務準備へ参照として接続し、個別の採用状態と証拠集合全体の不足を
同時に表示・再検証できるようにする。期間拡張・最新性・IRの取得は別途範囲を決める。
このpolicyを他年度・他社へ自動展開したり、今回の6項目だけで証拠集合を凍結したりしない。
