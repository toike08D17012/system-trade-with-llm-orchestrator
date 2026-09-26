# 価格・FX証拠を通常実行の準備工程へ接続する

## 概要と今回の到達点

実行時点に必要な価格・FXの期間を決め、保存済み証拠を再検証して、
指定銘柄・実行taskに結び付いた準備成果物を原子的に公開する。
固定期間の受入成功を、別の実行でも最新・完全とみなす問題を防ぐ。

今回の完成範囲は内部API・module CLIによる通常実行の価格・FX準備である。
公開の詳細解析開始command、財務・開示取得、Agent実行、全証拠集合の凍結は後続とする。
全出力で`analysis_ready=false`を維持する。
実装計画の承認後に実装・検証・文書同期・コミットまで行う。

## 確認した現状

基準commitは`64c8b0e`。直前のrebuild検証では1,018テストが成功している。
本計画のためのテスト・市場データ取得は実施していない。

| 現在の実装・契約 | 接続時の注意 |
| --- | --- |
| `preparation/task_input.py` | `create_human_selected_task`は銘柄・市場・受付時刻・task IDを構築済み |
| `preparation/market_evidence_cli.py` | 終端を東京の当日日付・exclusiveで決めており、取引終了時刻による判定はない |
| `sources/yfinance/normalization.py` | `TradingDates`と`three_year_start`を再利用できる |
| `preparation/price_acceptance.py` | 固定期間受入とbytes再検証済み。当日の価格を拒否し、実行時鮮度は保証しない |
| `preparation/dukascopy_fx.py` | UTC Bid日足の取得・保存・再検証済み。未確定足を区別する |
| `preparation/price_fx.py`、`price_fx_v2.py` | 価格とFXの期間一致が必要。旧BOJ v1・Dukascopy v2の再検証互換がある |
| `preparation/storage.py` | 排他的に所有するrootへのstaging・検証・rename公開がある |
| `contracts/detailed_analysis/v1/evidence.py` | 層別証拠・鮮度契約がある。`EvidenceSetV1`は凍結済み集合を表す |
| `contracts/detailed_analysis/v1/runtime.py` | `ExecutionManifestV1`は証拠集合参照・版を必須とする |
| `config/market-profiles/xtks/v1.yaml` | カレンダー未取得、`enabled_for_runtime_date_resolution=false` |

既存のDukascopy実成果物は2023-09-26〜2026-09-25の価格731日を換算済み。
これだけでは、後日の実行に必要な終端日まで揃ったことを証明できない。

## 採用する方針

### 1. 実行要件を先に固定する

入力はtask、timezone付き`checked_at`、出典・hash付き取引カレンダー、
市場profile、価格証拠、FX証拠、出力先とする。
初回評価時刻と再検証時刻を区別し、同じ保存入力の再生では保存時刻を使用する。
新しい現在時刻での鮮度確認は新しい準備成果物として保存する。

- `required_price_end`：確認時刻までに通常立会が終了した最新のXTKS取引日。
  取引終了予定だけでデータ到着済みとは扱わない。
- `required_fx_end`：上記までのXTKS取引日のうち、同日UTC日足区間が終了した最新日。
- `available_price_end`、`available_conversion_end`：検証済み入力から実際に利用可能な終端。
  必要日が欠けた場合に必要終端を過去へ繰り下げて成功させない。
- 共通の保持期間は価格の必要終端翌日をexclusiveとして`three_year_start`から計算する。
  FXには同じ期間を割り当て、換算対象だけを確定した日足の範囲に限定する。
  日次価格取得のexclusive終端とFXのinclusive終端を変換箇所で明示する。

東京の取引終了後〜翌朝09:00には、価格だけ当日分が必要になり得る。
当日のUTC FX日足が未確定でも、JPY価格の受入を取り消さない。
FXが確定する予定時刻を過ぎても応答が欠ければ、取得済みとは推測せず欠損を記録する。
週末・祝日を曜日だけから推測しない。カレンダーは要求開始から確認日の東京日付までの
収録範囲を必要とし、不足時は日付判定を`pending`にする。

### 2. カレンダーの利用条件を明文化する

旧市場profile v1と固定期間受入の意味は保持する。
通常実行向けprofile v2（新設案）と、出典付きローカルカレンダーの検証条件を追加する。
版・出典・取得/確認時刻・内容hash・対象範囲を証拠に同梱する。
profileの有効化だけでは日付判定を許可せず、適合するカレンダーbytesを毎回検証する。

この計画承認は、検証可能なローカルカレンダーを日付判定に利用する方式の承認とする。
未確認の休日一覧が正しいとの承認にはしない。実装時は合成fixtureで検証し、
実証拠が確認日をカバーしなければその不足を結果に残す。
過去の取引終了時刻に現在の15:30を遡及適用しない。
初版の通常実行評価はprofile v2の有効期間内に限定し、旧期間の証拠再生は従来経路を使う。

### 3. 再利用と当日価格の受入

- 元証拠のbytesを再検証してからtaskの銘柄・市場・通貨・期間を照合する。
  元taskが違っても同銘柄なら、再利用元taskと再検証結果を保持して再利用できる。
  FXは通貨ペア・side・日付基準を照合する。
- 旧受入索引を書き換えず、通常実行向けの新しい内部受入モデル・評価関数を追加する。
  当日価格は、承認profileによる立会終了判定と価格品質の確認後にのみ受け入れる。
  元v1再検証関数を変更して過去のhash・判定を変えない。
- 入力の収録期間が要求期間より広い場合も原bytesは保持する。
  原証拠の再検証後に、要求期間の投影を派生データとして生成し、
  除外日・入力hash・変換版を記録する。要求期間の欠損を投影で隠さない。
- JPY価格、FX、換算の状態と不足理由を分離する。
  通常実行の採用FXはDukascopy UTC Bidとし、BOJへの自動切替を行わない。
  旧BOJの読取・既存結合コマンドは引き続き利用可能にする。
- source設定は取得時の有効性と今回の再利用可否を区別して確認する。
  失効・停止・版の不一致を無視せず、再利用判定に理由を残す。
  新しい確認時刻より未来の取得・検証時刻を持つ入力は拒否する。

### 4. 準備manifestを作る

内部の`PriceFxPreparationManifest`（新設案）に次を保持する。

- task、評価時刻、必要/利用可能な終端、期間、policy・profile・カレンダー参照。
- 原入力の保存先・hash・schema/内部形式版、元task、再利用判定。
- 原データ・出典・正規化・換算の対応と、一意な内部証拠ID・依存参照。
- 層別の状態、欠損日、制約、公開generation、計算版、`analysis_ready=false`。

価格・FX限定の準備manifestを、公開`ExecutionManifestV1`や凍結済み
`EvidenceSetV1`として出力しない。yfinanceのCSVをYahoo原HTTPと表記しない。
raw取得時のgenerationは来歴として保持し、新しい準備公開generationと混同しない。
全保存参照・hash・依存を再検証して、同一filesystem上の新規directoryへ原子的に公開する。
既存宛先、symlink、path逸脱、改変、同一rootへの競合公開を拒否する。
初版は排他的に所有するrootを前提とし、汎用の並行runtimeを追加しない。

### 5. CLIと送信範囲

新しい内部module CLI（`preparation.price_fx_run_cli`案）に、
必要期間を保存する`plan`、既存証拠を評価・公開する`prepare`、
保存bytesを再検証する`validate`を設ける。いずれもネットワーク通信を行わない。
`prepare`の出力は`ready_with_limitations`または`pending`とし、保存成功は0、
壊れた入力・不正な設定・公開失敗は1。`ready`は価格・FX準備の範囲だけを表す。

不足時は必要な取得期間と理由を出力し、既存の明示的な取得経路へ渡せるようにする。
自動取得・retry・バックフィル、source送信上限の変更は今回追加しない。
これにより、通常実行の判断を保存証拠だけで検証してから取得連携を拡張できる。

## 変更対象と実装順

Pythonパスは`src/stock_research_llm_orchestrator/`基点。新規名は配置案。

| 順序 | 対象 | 内容 |
| --- | --- | --- |
| 1 | 要件01/02・要件README、移行の意思決定記録 | 終端の分離、当日価格、カレンダー、準備と凍結の境界を同期 |
| 2 | `config/market-profiles/xtks/v2.yaml`、`preparation/run_requirements.py`新設 | profile検証・時刻/期間判定・カレンダー不足判定 |
| 3 | `preparation/price_acceptance.py`と新規通常実行評価module | v1互換を保ち、品質判定の共通部分のみ再利用 |
| 4 | `preparation/price_fx_v2.py`と新規`price_fx_run.py` | 期間投影・確定足の結合・task照合・準備manifest・原子公開 |
| 5 | 新規`price_fx_run_cli.py` | plan/prepare/validate、状態と不足理由の表示 |
| 6 | `tests/preparation/`、`tests/test_configuration.py` | 境界・再利用・保存再生・新profileの検証 |
| 7 | `README.md`、`docs/TODO.md`、設計03 | 実装済み範囲・コマンド・後続作業を同期 |

既存公開契約のschema変更は行わない。準備manifestは内部形式として版を固定する。
既存価格受入・FX索引・旧コマンド・保存成果物を上書きしない。

## 実装時の検証

- 注入時計と合成カレンダーで平日終了前後、UTC日境界、週末・祝日、閏年、
  カレンダー範囲不足、profile未承認/適用期間外を検証する。
- 同日JPYあり/FX未確定、確定後もFX欠損、古い終端、途中の欠損、異銘柄、
  広い入力期間からの投影、元task再利用を検証する。
- callerのDecimal contextによらない換算、v1受入再生、BOJ互換を維持する。
  既存`test_price_acceptance.py`、`test_price_fx.py`、`test_dukascopy_fx.py`を回帰対象にする。
- 保存後の全参照・hash・計算再生、改変・symlink・宛先重複・公開途中失敗を検証する。
  network禁止でCLIを通し、原証拠が不変であることを確認する。
- 既存731日証拠を追加通信なしで評価する。現在日付のカレンダーや鮮度根拠が不足すれば、
  `pending`が正しい結果であり、731/731の旧実績だけで成功させない。
- repository wrapperによるPytest・Ruff・Mypyと`./scripts/pre-commit/checks.sh`を実行し、
  schema生成物が不変であることを確認して通常hookでコミットする。

## 完了条件・後続作業

現在の実行要件に対する不足を再現可能に説明でき、価格・FX準備manifestを保存・再検証できること。
完全な証拠集合への昇格や解析開始は行わない。
次は財務・開示証拠の接続、全証拠集合の凍結と公開manifest、Agent実行の順に進める。

ブロッキングな未解決事項なし。上記の範囲・新しい判定方針を本計画の承認対象とする。
既存調査を再利用し、追加調査・計画作成は主agentが実施した。

## Implementation Notes

- 内部CLIと準備manifestを実装した。既存v1の受入再生は維持し、当日価格判定は新しい評価経路だけで有効にした。
- `MarketProfileV1` の配置検証を `profile_version` に合わせ、承認済みv2を追加した。
- yfinance再利用はnative取得方針とapproval v3を照合する。旧HTTP単位profileは再有効化しない。
- 元証拠全体の品質を確認した後に必要期間へ投影する。新taskの評価policyも保存・照合する。
- 公開時は親directoryの非待機排他lockで競合を拒否し、既存の原子公開処理を使用する。
- 既存の固定期間結合moduleと公開schemaは変更していない。新規取得・当日取得の自動連携は対象外とした。
- 実証拠の再検証結果は[確認記録](../../decision-requests/2026-09-27-price-fx-run-preparation-outcome.md)を参照する。
