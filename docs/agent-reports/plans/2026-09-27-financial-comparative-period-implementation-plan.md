# 実装計画: 前期比較値の受入と期間別充足判定

## 概要

保存済みの2026年3月期有価証券報告書から、2025年3月期の前期比較値6項目を追加受入する。年次資料の本数と、実際に6項目が揃う年次期間数を区別し、内部run preparationに期間別の不足を表示する。追加通信は行わず、全体の `analysis_ready=false` を維持する。

本計画の承認範囲は、下記の同一archive・同一QNameによる2025年3月期と2026年3月期に限定する。サマリー項目、2024年3月期の期首資本、他のarchive、複数提出書類間の自動優先選択は対象外。

## 確認した現状

- [過年度候補調査](../research/retained-xbrl-prior-period-coverage.md): 2025年3月期は6項目とも候補あり。2022～2024年3月期は不足し、四半期・半期のcontextはない。
- `financial_mapping.py` の `review_metric` はfactの期間・発行体・dimension・単位・重複を判定する。一方、`evaluate_mapping` は提出対象期間とproposal期間の一致を必須とする。
- `financial_acceptance.py` は単一期間の承認policyのSHA-256を固定し、`validate_mapping`、taxonomy検証、`adopt_metric` を通して採用する。既存manifestはversion 1。
- `financial_disclosure.py` の年次期間数は提出書類の期間に基づく。比較値が存在してもその期間を加算しない。
- `financial_run.py` は単一受入bundleの値を期間付き・数値なしの要約に変換する。全依存を再検証し、元の理由を `historical_reasons` に保存する。

## 実装方針

### 既存契約を維持した追加経路

既存のreview・adoption・run preparationのversion 1出力と再検証を変更しない。新しい比較期間受入sidecarと、その入力が指定された場合だけ生成するrun manifest version 2を追加する。旧経路への任意フィールド追加で既存成果物のバイト列を変えない。

新しいsidecarは元financial bundle・当期review・当期adoptionを明示入力とし、最初に既存の `validate_acceptance` で再検証する。当期提出書類の適格性を通過した同一archiveの比較factのみを調べる。提出期間の不一致判定を既存経路から削除したり、全体の除外理由を無視したりしない。

比較期間policyは新しい固定hashとして追加し、発行体、document ID `S100Y8NY`、archive hash、元policy hash、当期proposal hash、報告期間、許可する比較期間、6項目QNameとtaxonomy根拠を束縛する。元policyファイルと承認hashは変更しない。新policyの `status=approved` だけでは受入しない。

許可する期間は当期2025-04-01～2026-03-31と前期2024-04-01～2025-03-31。比較期間は明示列挙し、「当期より前なら許可」のような推定はしない。

### 期間別採用と充足

既存taxonomy証明を再計算した上で、許可する各期間について `review_metric` と `adopt_metric` を利用する。同じconceptの証明を使用できることを確認し、候補のentity・dimension・unit・period検査を省略しない。同じ値の重複は全参照を保持し、競合・nil・証明不成立は未採用にする。

sidecarには `policy.json`、期間別候補診断、taxonomy証明、ローカル専用 `values.json`、hash付きmanifestを保存する。キーは期間種別・開始日・終了日・metricとし、重複キーは拒否する。資産・資本のfact自体はinstantであることを維持し、年次期間への所属は外側の期間レコードで表す。当期の6項目は既存adoptionとの一致を検証し、二重加算しない。

run version 2には次を出力する。

- 期間別の6項目採用状態、欠落項目、出典参照数。財務値は出力しない。
- 年次の必要期間集合（今回の固定評価では2022～2026年3月期）、完全期間数、部分期間数、不足期間。
- 提出書類に基づく従来の期間情報と、採用済みfactに基づく期間充足情報を区別する。

完全期間は6項目すべてがacceptedの場合のみ。期末instantがあるだけで年次期間を充足させず、同じ期間の複数参照を複数期と数えない。5期の対象集合は今回の報告期から明示した固定集合であり、最新公表期を自動確認したという意味にはしない。将来の一般的な決算期変更への対応は対象外。

今回の期待結果は12項目採用・年次完全2/5期・不足2022～2024年3月期。`annual_periods_insufficient`、`interim_periods_insufficient`、IR・最新開示・その他開示の未確認理由は残す。過去理由を保存し、新しい期間別充足が全体の最新性確認を代替しない。価格・FXとのtask・確認時刻の一致検査は維持する。

## 変更内容

以下のPythonパスは `src/stock_research_llm_orchestrator/preparation/` 配下。

| ファイル | 予定変更 |
| --- | --- |
| `financial_comparative.py`（新規） | 固定policy、期間別候補検証・採用・充足、sidecar prepare/validate |
| `financial_acceptance_cli.py` | 比較期間用prepare/validateサブコマンドを追加。既存コマンドは維持 |
| `financial_run.py` | 比較sidecar指定時のversion 2生成・再検証、期間別メタデータ |
| `financial_disclosure_cli.py` | prepare-run/validate-runに明示的な比較sidecar入力を追加 |
| `config/financial-mapping/7203-2026-comparative-approved.json`（新規） | 承認後に作成する限定policy。既存policyは不変 |
| `tests/preparation/test_financial_comparative.py`（新規） | 比較値の採用・充足・改ざん・出力境界 |
| `tests/preparation/test_financial_run.py`、`test_financial_disclosure.py` | 新経路の統合と旧経路の再現性 |
| `README.md`、`docs/TODO.md`、新しい受入結果文書 | CLI・実績・残る不足期間を更新 |

`financial_mapping.py`、`financial_acceptance.py` の既存候補判定・採用関数は再利用し、既存version 1の判定条件は緩めない。

## 実装手順

1. 限定policyとsidecarモデルを追加し、元資料の再検証・入力hash束縛・期間の一意性を実装する。
2. 期間別候補診断とtaxonomy検証を接続し、当期一致検証、前期6項目採用、完全期間の集計を実装する。
3. 比較期間CLIとrun version 2を接続する。依存は明示指定し、manifest内のパスをたどらない。既存の安全なパス検査・private directory・atomic publish・上書き禁止を踏襲する。
4. 対象テストと品質チェックを実行し、保存証拠から新しい別ディレクトリへprepare/validateする。既存の価格・FX bundleも再利用し、追加通信なしで統合確認する。
5. 実績と残課題を文書化し、実装・検証結果をコミットする。

## 検証計画（実装時）

- 2025/2026年3月期が各6項目acceptedとなり、12項目・完全2期として集計される。2024年3月期の資本だけを勝手に追加しない。
- 提出報告期間と比較期間の混同、許可外期間・QName・entity・dimension・unit、nil、重複不一致、taxonomy不成立を拒否または未採用として保持する。
- 同一期間の重複を加算しない。1項目欠けた期間は完全期間に数えない。当期値が旧adoptionと不一致なら受入しない。
- policy・原本・候補診断・値・proof・依存hashの改ざんを再検証で検出する。未承認policy、symlink、依存とのパス重複、既存出力への上書きを拒否する。
- CLI成功・失敗、診断、run要約に数値本文や外部資料本文を含めない。既存のネットワーク禁止fixture下で実行する。
- 旧version 1のprepare/validateと保存済みbundleの再検証を維持する。比較sidecar指定時も価格・FXのtask/時刻不一致を拒否する。
- `./scripts/pre-commit/pytest.sh` で新規比較期間テストと既存 `test_financial_mapping.py`、`test_financial_acceptance.py`、`test_financial_run.py`、`test_financial_disclosure.py`、`test_price_fx_run.py` を対象実行する。Ruff・Mypyは各skillとrepository wrapperに従い、必要な全体チェックはコミットhook等の既存経路で実施する。

計画作成段階ではテスト未実行。過年度候補調査のオフライン検証結果を再利用した。

## 互換性とロールバック

既存のbundle、config、固定hash、version 1の公開関数呼出しは維持する。新しい比較sidecar引数を省略すれば旧経路となる。ロールバックは新経路の利用を止め、既存の当期のみのrun bundleを使う。旧成果物の書換えやデータ移行は不要。

## 未解決事項・承認範囲

実装方針上の未解決事項はなし。本計画の承認は、同一保存archiveから前期比較値6項目をローカル採用する新policyの作成を含む。追加API取得、外部エージェントへの数値送信、最新性確認の省略、全体の分析可能判定への昇格は含めない。

`src/AGENTS.md` §6 とimplementation-plan skillの最終手順に従い、計画レビュー後に実装へ進む。
