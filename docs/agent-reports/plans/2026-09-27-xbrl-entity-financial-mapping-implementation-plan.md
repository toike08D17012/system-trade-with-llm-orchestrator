# 実装計画: XBRL entity対応と財務項目の採用候補整理

## 概要・承認対象

保存済み7203年次報告を追加のEDINET通信なしで処理し、公式仕様に基づくentity対応を実装する。
財務値は当期・連結の6項目に絞り、意味・期間・単位を確認できる候補表を作成する。
初版は候補の説明と採用ルールのレビューまでで、未確認のtaxonomy対応を自動採用しない。

本計画で承認を求める事項:

1. 公式schemeとEDINETコード・追番に基づくentity対応、および候補選別工程の実装。
2. 当期連結の売上、営業利益、親会社帰属利益、資産合計、資本合計、営業CFを初回対象にする。
   資本合計と親会社所有者帰属持分は別項目として扱う。
3. mappingレビューに必要な限定的な診断情報を、この開発作業のcoding agentで確認する。
   許可対象はentity scheme/identifier、taxonomy namespace・QName、member/context ID、期間、
   dimension・unit・decimals・nil、候補選別理由、原hash・書類ID。
   原ZIP/XML/本文、text block、全fact一覧、財務数値そのもの、credentialは送らない。
   未選別の候補ファイルをそのまま表示せず、必要な識別情報だけを抽出する。

これは選別された開発診断情報の確認許可であり、screening Agentへのraw/bulk転送の許可ではない。
現在のEDINET source approvalの転送禁止を一律に解除しない。
本計画の承認後、実装・検証・実証拠のローカル再評価・コミットまで進める。

## 確認した現状

Pythonは `src/stock_research_llm_orchestrator/` 基点。

- `preparation/financial_disclosure.py::evaluate_financial` は、archive内の全entity identifier集合が
  一覧のEDINETコード単体と一致することを要求し、schemeを照合に使用していない。
- 保存済み実証拠はentityのscheme/identifier組が1種類で、274 contextすべてが公式schemeと
  一覧コードに `-000` を付けたidentifierを使用する。完全一致0件のため現在は未確定扱い。
- `sources/edinet/xbrl_facts.py` はnamespace、member、context、unit、dimension、期間、
  value/nil/decimals等を保持している。615種類のconcept、2,182件のfact候補がある。
- `preparation/financial_candidates.py::correction_reasons` は訂正を未確定のまま保持する。
  訂正の自動採用や財務項目のmappingは実装していない。
- ローカルの語彙検索候補では資本合計が0件だった。これはタグ名検索の結果であり、
  原資料に資本合計がないことを示すものではない。
- 価格・FXは今回の財務準備と同一時刻で再prepareしておらず、未接続。

実証拠:
`runs/edinet-evidence/edinet-prepared-a27686c68a9f4a0c883105592f15eabc`

manifest SHA-256:
`8ba600927fa2e9f259c7bde6e0cc2781a7f06c1f1f93e30870ef438eeb2e29bf`

今回の調査では件数・構造の一致判定だけを出力し、財務値・bulk候補は表示していない。

## 公式根拠

2026-09-27確認の金融庁[報告書インスタンス作成ガイドライン](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140112.pdf)
（2025年11月版）§5-4のcontext定義では、schemeが `http://disclosure.edinet-fsa.go.jp`、
identifierが `{EDINETコード}-{追番}` とされている。
§4-2のファイル命名では追番が3桁で、000から開始することが記載されている。
[公式資料一覧](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html)には
版別taxonomy資料があり、財務mappingでは保存書類の実際の版に対応する資料を使う。
現在公開されている最新版を、過去の書類へ無条件に適用しない。

## 実装方針

### entity対応

- 元scheme/identifierを残し、照合結果にEDINETコード・追番・規則版・理由を記録する。
- 初回の通常企業年次報告では、公式scheme・一覧コード一致・追番000を明示的に照合する。
  他の追番は削除して同一視せず、別instanceとして保存し未対応理由を出す。
- 別scheme、別EDINETコード、混在entityは自動統合しない。
  書類全体の診断と、実際に採用候補が参照するcontextの判定を分ける。
- 未使用のcontextが増えただけで全archiveを拒否せず、未対応contextの影響を受ける候補を識別する。
- 現在の単体identifierを使う人工fixtureの読み込み互換は保持する。
  新しい本番entity判定は版付きの別出力とし、旧manifest再生結果を変更しない。

### 財務候補の選別

- 最新と推定せず、既に取得した対象期間2025-04-01〜2026-03-31の候補として評価する。
- duration項目は対象期間完全一致、instant項目は期末一致を基本とする。
  連結/単体とdimensionは実taxonomyの定義を根拠に確認する。
  context IDの名前だけ、dimensionなしという条件だけで連結と断定しない。
- 売上・利益・資産・資本・営業CFのQName対応は、限定診断と同じ版の公式資料から
  根拠付き対応案を作成する。部分一致のタグ名検索は候補発見にのみ使う。
- 通貨unitを確認し、初版はJPYの単純通貨値に限定する。
  decimalsは精度情報として扱い、表示単位の倍率として使わない。
  scale等が必要な未対応表現は未対応とし、推定換算しない。
- 同じ意味・期間・単位・連結範囲の重複候補は全参照を保持する。
  同値なら重複として束ね、異値ならconflictingとし平均しない。
- nilとmissingを区別し、ゼロで補完しない。訂正が絡む候補は従来どおり未確定を維持する。
- 新規・未使用項目は候補収録を妨げない。重要な意味・identity・単位・期間の確認に限定して判定する。

具体的なQName対応表は現在未承認である。この工程では根拠付き対応案を作り、
採用前にレビューできる状態にする。財務数値としての受入を完了したとは表現しない。

## 変更ファイルと手順

新規ファイル名は配置案。

| 順序 | 対象 | 内容 |
| --- | --- | --- |
| 1 | 新規 `preparation/financial_mapping.py` | entity対応結果、6項目の候補参照・理由・mapping規則版を表す内部モデルと純粋な判定関数 |
| 2 | 新規 `preparation/financial_mapping_cli.py` | 既存財務bundleを検証し、ローカル候補表を新規directoryへ保存するprepare/validate。ネットワーク・credential不要 |
| 3 | 新規 `tests/preparation/test_financial_mapping.py` | entity・context・単位・候補重複・旧証拠再生・保存改変の検証 |
| 4 | 実装時に新規mappingレビュー記録 | 6項目のQName対応案、根拠資料、候補の絞込み、未解決事項を記載 |
| 5 | README、TODO、結果記録 | 実装済み候補整理と、未完了の財務受入・全期間充足を区別する |

旧 `financial_disclosure.py` の判定を上書きするより、新しい版付きmapping結果から
元manifestを参照する構成を採用する。元のpending・取得失敗履歴・原bytesは不変とする。
将来の採用済み財務との統合は、対応表承認後の別段階とする。

## 実装時の検証

- 公式scheme＋同一コード-000で対応成立。異scheme・異コード・非000追番・混在を理由付きで区別する。
- 当期/比較期、instant/duration、連結/単体、追加dimension、異通貨を混同しない。
- 未知の追加項目で停止しない。採用に必要な項目不備は理由を記録する。
- nil/ゼロ、同値重複/異値競合、訂正未確定を区別し、値を合算・平均・補完しない。
- 新規テストと `tests/preparation/test_financial_disclosure.py`、
  `tests/sources/test_edinet_xbrl_facts.py` をpytest wrapperで検証する。
  変更ファイルのRuff/Mypy、原子公開・改変拒否、保存済み実証拠のオフライン再生を確認する。
- 外部に出す開発診断にはvalue/text blockがなく、指定した識別情報だけであることを検証する。
- 新しい候補表の検証が成功しても、全体の `analysis_ready=false` と期間・IR不足を維持する。

## 未解決事項・承認後の到達点

entity仕様と実書類の形式は確認済みであり、対応実装は着手可能。
各指標の正確なQName・会計範囲・資本の分類は未確認で、限定診断と資料照合で確定する。
この段階の成果物は「根拠付きmapping案と再現可能な候補表」である。
年次5期・中間8期間、実訂正の採用、比率計算、価格FX統合、IR取得は今回の対象外。

現在の承認範囲はraw/bulkの外部Agent転送を禁止しているため、開発診断の限定確認範囲を
本計画で明示して承認を受ける。承認前に実候補の詳細をcoding agentへ出力しない。
承認後も追加のEDINET取得は不要。公式仕様資料の参照とローカル再処理で進める。

## Implementation Notes

2026-09-27に計画・限定診断範囲の承認を受け、実装と実証拠のオフライン再評価を実施した。
新しいレビューは元bundleを参照するsidecarとし、旧manifestの判定を変更していない。
機械可読な6項目案を `config/financial-mapping/7203-2026-draft.json` に追加した。
既存fixtureを重複させないため、統合テストは `test_financial_disclosure.py` に追加した。

標準5項目のQName・型・期間種別は同版の公式要素リストで照合できた。
売上は提出者独自概念であり、意味の確認を未完了として記録した。
連結範囲をdimensionなしだけで断定せず、全項目を採用前のレビュー対象に留める。
詳細は [mappingレビュー](../../decision-requests/2026-09-27-financial-mapping-review.md) を参照。
