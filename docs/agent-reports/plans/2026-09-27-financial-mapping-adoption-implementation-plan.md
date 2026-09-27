# 実装計画: taxonomy根拠に基づく財務6項目の限定採用

## 概要・承認対象

保存済み7203の2026年3月期について、売上の意味と連結範囲を支えるtaxonomy構造を確認した。
この構造を再現可能な根拠として保存・検証し、当期連結6項目をローカルの正規化財務値として採用する。
前回の候補レビューから、数値の限定採用へ進むための計画である。

承認対象は次の範囲。

- 下記の売上・連結範囲の対応案と、6項目の限定採用ロジックの実装。
- 当該書類・原archive hash・対象期間・QNameに対応付けた採用policyの追加。
- 正規化値はローカル保存のみ。coding agentには識別情報、構造、判定理由、件数だけを出力する。
- 追加EDINET送信は行わず、実装・検証・実証拠の再評価・コミットまで実施する。

個別項目の採用と証拠集合全体の準備完了を分ける。
年次5期・中間8期間、最新性、IR、価格FX接続等の不足は残り、全体の `analysis_ready=false` を維持する。
screening Agentへの転送、他社・別期間への自動適用、訂正の自動採用は対象外。

## 確認した実装

- `preparation/financial_mapping.py` はdraft専用の `MappingProposal`、`MetricReview`、
  `evaluate_mapping`、`validate_mapping` を持つ。正確なQName・entity・期間・単位で比較するが、
  `mapping_not_approved` と `consolidation_scope_unconfirmed` は全項目に固定で残す。
- `preparation/financial_disclosure.py::validate_financial` は原証拠から旧bundleを再生する。
- `sources/edinet/document_retrieval.py` はZIP memberの安全な一覧を検証する。
- `sources/edinet/xbrl_facts.py` はfact・context・unitを扱い、taxonomyのschema・linkbase解析は未実装。
- `config/financial-mapping/7203-2026-draft.json` は前回レビューの対応案であり、採用権限ではない。
- 同値重複を含む6項目の候補と原証拠hashは
  [前回レビュー](../../decision-requests/2026-09-27-financial-mapping-review.md) に記録済み。

前回の検証・採用候補を再利用し、今回テストを再実行していない。
今回の調査は原ZIPを展開せず、必要なschema属性・QName・role/arc識別情報とhashだけを抽出した。
財務値、本文、ラベル全文は出力していない。

## 追加調査で確認できた根拠

原archive SHA-256:
`6b9e1d9cd955f630bc0cf62f68f4d790b09798568004151bbe988224ae450034`

以下のmemberは `XBRL/PublicDoc/` 配下で、共通のstemは
`jpcrp030000-asr-001_E02144-000_2026-03-31_01_2026-06-10`。

| suffix | SHA-256 |
| --- | --- |
| `.xsd` | `09c767607130cf932a429939b47e876e19ee2f29f2be9d4500047c24b086c10a` |
| `_cal.xml` | `80682849a144a24cd01a6b1740836595fcb7ff62a3bbabe87847b6a1ea2e555b` |
| `_def.xml` | `31bf4a64e7f372fd87de4efcc71866b18601cec00d38bc3889e32ad98b159092` |
| `_pre.xml` | `bbcf8498b2090d683a9e37d31ed4449b3e3fde990280cffb1120fc43da36484a` |

### 売上の採用案

提出者独自の `TotalNetRevenuesIFRS` はschemaでmonetary型・duration・credit・非abstractと定義される。
連結損益計算書roleのcalculation linkbaseに次の関係がある。

| 計算上の親 | 子 | weight |
| --- | --- | --- |
| `TotalNetRevenuesIFRS` | `SalesOfProductsIFRS` | +1 |
| `TotalNetRevenuesIFRS` | `FinancingOperationsIFRS` | +1 |
| `OperatingProfitLossIFRS` | `TotalNetRevenuesIFRS` | +1 |
| `OperatingProfitLossIFRS` | `TotalCostsAndExpensesIFRS` | -1 |

同じ連結損益計算書のpresentation/definitionでは、売上のabstract項目配下に配置される。
これらから、今回の売上指標には製品売上と金融事業収益を含む合計を採用する案が妥当と判断する。
これは構造からの解釈であり、計算された数値の一致や全社に通用する同義関係を確認したものではない。
数値はこの合計factを採り、子項目から独自に合算し直さない。
`SalesRevenuesIFRS` など類似名の別conceptを同義扱いしない。

### 連結範囲の採用案

次の3つのroleのdefinition linkbaseで、headingから対象conceptへのdomain-member経路が存在する。
role URIの共通部分は `http://disclosure.edinet-fsa.go.jp/role/jpigp/`。

| role末尾 | 対象項目 |
| --- | --- |
| `rol_ConsolidatedStatementOfFinancialPositionIFRS` | 資産合計・資本合計 |
| `rol_ConsolidatedStatementOfProfitOrLossIFRS` | 売上・営業利益・親会社帰属利益 |
| `rol_ConsolidatedStatementOfCashFlowsIFRS` | 営業CF |

各roleにはheadingからtableへの `all`、tableから
`jppfs_cor:ConsolidatedOrNonConsolidatedAxis` への `hypercube-dimension`、
同axisから `jppfs_cor:ConsolidatedMember` への `dimension-domain` と `dimension-default` がある。
`all` は `contextElement=scenario`、`closed=true`。
対象role内ではprohibited、非0 priority、targetRoleを持つarcは今回の診断で見つからなかった。
namespaceは実書類が参照する2025-11-01版に限定する。

この構造と対象contextのentity・期間・dimensionを合わせ、当該書類の候補を連結として扱う案とする。
role名やcontext ID、dimensionがないことだけでは確定しない。
公式の省略規則・例外は金融庁
[報告書インスタンス作成ガイドライン](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140112.pdf)
2025年11月版 §5-4-5、§5-4-5-1（2026-09-27確認）を参照した。

## 実装手順とファイル

新規ファイル名は配置案。

1. `sources/edinet/xbrl_taxonomy.py` を追加し、既存のarchive検証とサイズ制限を再利用して、
   採用policyが指定するschema・presentation/definition/calculationの必要な構造だけを抽出する。
   member hash、完全QName、role、arcrole、元member内の参照を根拠に含める。
   locatorのfragment名だけで照合せず、参照先schema・namespace・宣言IDとの対応を確認する。
   標準schemaは前回確認済みの同版公式定義に対応付けたpolicyの限定一覧で解決し、
   未解決の外部参照をネットワークで自動取得しない。
2. `config/financial-mapping/7203-2026-approved.json` と内部の採用policyモデルを追加する。
   security、EDINETコード、書類ID、原archive hash、期間、6つの完全QName、
   上記member/関係、根拠と承認記録を対応付ける。
   draftの `status` を書き換えるだけで採用できないよう、承認policyの正確なhashを実装側で照合する。
3. `preparation/financial_acceptance.py` と内部CLI `financial_acceptance_cli.py` を追加する。
   原bundleとv1候補レビューを再検証し、採用policyとtaxonomy根拠が一致する項目について、
   JPY単位のDecimal値を文字列としてローカルに保存する。
   同値重複は値1つと全fact参照を保持し、nil・異値競合・訂正未確定は未採用とする。
   schema型・periodTypeとfactのunit/期間も照合する。
   `accepted` は個別項目に限定し、全体の `pending` と不足理由は維持する。
   出力は新しい版付きbundleとし、元manifest・draft・v1レビューを変更しない。
4. 出力はpolicy、taxonomy根拠、正規化値、値を含まない診断、原証拠参照をhashで対応付ける。
   prepare/validateは既存のprivate directory・原子公開・再生検証を再利用する。
   CLIの標準出力・例外には財務値・原文を出さない。
5. `tests/sources/test_edinet_xbrl_taxonomy.py`、`tests/preparation/test_financial_acceptance.py` を追加し、
   必要な統合検証を `test_financial_disclosure.py` の既存fixtureで行う。
   実装時に前回レビュー・README・TODOと今回の承認/結果記録を更新する。

## 互換性と判定の限界

- 外部APIの未知項目を拒否する変更はしない。未使用taxonomy項目・別roleの追加も無視できる構造にする。
- 原archiveのhash限定は、この一件への採用承認を他書類へ流用しないための境界である。
  他書類も取得・候補化できるが、このpolicyによる正式採用は行わない。
- 採用に関係する未解決locator、競合する定義、prohibited/priority/targetRole等の未対応構造は、
  影響する項目を理由付き未採用にする。完全な汎用DTS処理系は今回実装しない。
- dimensionなしを一律に連結扱いしない。指定roleの対象conceptまでの経路と
  defaultを確認できた場合だけscopeを確定する。
- ローカル採用値をscreening Agentへ送信可能になったとは扱わない。
- 旧v1の再生ロジックと結果を残し、新出力を利用しなければ元の候補レビューへ戻れる。

## 検証・完了条件

- 合成taxonomyで上記の正しい関係を再現し、6項目が指定scopeで採用できることを確認する。
- role名だけ一致、default欠落/変更、concept経路欠落、偽namespace、locator参照違い、
  未対応arc構造、XMLの危険な宣言/ZIP traversalを採用根拠に使わないことを確認する。
- 無関係なconcept・roleの追加では候補処理を停止しないことを確認する。
- draftや別policy、別archive・期間で採用できず、値・根拠・原証拠の改変をvalidateが拒否することを確認する。
- nil/ゼロ、同値重複/異値競合、別通貨・scale・訂正の既存境界を保ち、値や本文が診断へ出ないことを確認する。
- 元bundleとv1レビューの再生互換、出力の再公開拒否・原子公開、offline/credential未使用を検証する。
- 新規テスト、既存financial mapping/disclosure・XBRL factテストをpytest wrapperで実行する。
  変更ファイルのRuff・Mypyとコミットhookを実行する。
- 保存済み実証拠でprepare/validateを行い、6項目の採用件数と根拠だけを報告する。
  財務値はローカルに残し、全体の `analysis_ready=false` を確認する。

## 承認前の状態

上記の構造調査と本計画の作成まで完了した。新しい採用ロジック・policyは未実装。
承認を受けた後に実装・検証・コミットへ進む。

## Implementation Notes

2026-09-27にユーザーが承認し、実装・保存済み実証拠のprepare/validateを完了した。
6項目がローカル採用され、全体の `analysis_ready=false` を維持した。
標準schemaは承認policy内の限定宣言一覧と実書類のimportを照合し、追加通信は行わない。
選択XMLは1 member 8 MiB、合計32 MiBに制限する。

原ZIPからの合成統合テストは既存 `test_financial_disclosure.py` のfixtureを再利用した。
関連83テスト・Ruff・Mypyと実証拠の再生が成功。
原証拠と旧レビューを変更せず、新しい採用bundleに値・根拠・診断を保存した。
詳細は [限定採用結果](../../decision-requests/2026-09-27-financial-mapping-adoption-outcome.md) を参照。
