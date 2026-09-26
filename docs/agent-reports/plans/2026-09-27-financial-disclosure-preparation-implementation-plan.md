# 実装計画: 財務・開示証拠の通常実行準備への接続

## 概要

価格・FXに続き、保存済みEDINET一覧・書類を対象銘柄と確認時刻に結び付け、
法定開示索引、XBRL候補、必要期間の不足を再現可能に保存する。
初回実装はオフライン準備と既存価格・FX準備への参照接続までとする。
財務項目の意味付けが未確定な候補を分析可能な財務値へ昇格させず、
発行体IR・適時開示の未確認も残す。常に `analysis_ready=false` とする。

計画の深さはMedium。新規取得・外部送信・公開schema変更を含めず、既存の原証拠を保つ。
本計画は未実装であり、承認後に着手する。

## 確認した現状

以下のPythonパスは `src/stock_research_llm_orchestrator/` 基点。

| 対象 | 確認した内容 |
| --- | --- |
| `sources/edinet/document_list.py` | `EdinetDocumentListAdapter` が年次・四半期・半期と各訂正（120〜170）を抽出。EDINETコード、証券コード、提出日時、対象期間、親書類、取下げ、XBRL有無を保持 |
| `sources/edinet/retrieval_plan.py` | `plan_edinet_retrievals` が公開済み一覧から取得intentを作成。取下げ・XBRLなしは明示的に除外するが、対象銘柄による選別は行わない |
| `sources/edinet/xbrl_facts.py` | `EdinetXbrlFactExtractor` がfact候補・context・unit・dimension・原archive hashを抽出。財務指標の標準化や会計基準をまたぐ意味の照合は行わない |
| `sources/edinet/xbrl_document.py`、`sources/publication.py` | archiveの検証・候補抽出と原bytes公開を接続済み |
| `requests/raw_artifacts.py` | `RawArtifactPublisher` に原子公開、hash・metadata照合がある。保存bundleの再利用ではこの保存形式を検証する必要がある |
| `tests/sources/test_edinet_retrieval_offline_e2e.py` | 合成XBRLとMockTransportでCoordinator・書類取得・原証拠公開を接続。実EDINET書類での受入成功を示すものではない |
| `config/source-approvals/edinet/v1.yaml` | オフライン実装承認。`online_use_allowed=false`、`external_agent_transfer_allowed=false`。再確認期限は2026-12-22 |
| `config/source-profiles/edinet/v1.yaml` | ローカル制限は並列1、60秒間隔、retryなし。この計画では変更しない |
| `preparation/price_fx_run.py` | task・評価時刻・入力hashを保持する内部準備manifestと再検証を実装済み |
| `contracts/detailed_analysis/v1/evidence.py` | 公開証拠集合は凍結状態を前提とする。未完成な財務・開示準備の格納先にはしない |

要件02 §5は年次直近5期、四半期・半期直近8期間、開示直近3年と最新重要開示を要求する。
要件01は損益・貸借・キャッシュフロー・主要KPIを求めるが、指標ごとのtaxonomy対応表は未定義。
要件02 §9の財務照合規則も未確定で、確認したyfinance実装には財務取得の接続がない。
発行体IR・適時開示の個別source approval/profileも現configにはない。

旧取得計画の「EDINET adapter未実装」は履歴であり、現状として再利用しない。
価格・FXの最新実確認は `2026-09-27-price-fx-run-preparation-outcome.md` を参照する。

## 実装方針

### 1. 原証拠から銘柄別の法定開示索引を作る

入力はtask、タイムゾーン付き `checked_at`、保存済み一覧・書類bundle、
根拠付き銘柄対応表、任意の価格・FX準備bundleとする。
銘柄対応表にはtaskの証券コード、EDINETコード、一覧上の証券コード、適用範囲、
根拠となる一覧の参照・hashを保持する。桁の切捨てや社名一致だけで同一発行体にしない。
対応が不明・複数なら `pending` とし、別銘柄の書類を混入させない。

保存一覧のrawとmetadataを照合して再parseし、対象外・取下げ・XBRLなしの書類も
除外理由付き索引に残す。既存のsource adapterが対象外にする書類種別を、
「開示なし」と解釈しない。発行体IR・適時開示とその他の法定開示は未確認として分離する。

書類ID、一覧参照、親書類ID、提出時刻、取得時刻、対象期間、原archive hashを保持する。
提出時刻は既存parserのローカル時刻を東京時刻として扱い、日付精度を勝手に補わない。
確認時刻より後の情報は当時の証拠に採用しない。技術的に壊れた入力はエラーとする。

### 2. 訂正・期間・XBRL候補を保持する

訂正書類は親子関係を保存し、単純な「最後の書類で全部置換」は行わない。
親不在、循環、異発行体の親、訂正同士の競合を検出し、採用未確定の理由を残す。
同一書類IDの異なるsnapshotは取得時刻・hashで区別し、取下げの観測を失わない。

XBRL候補のidentityはarchive・member・conceptのnamespace/local name・context・unit・ordinalで保持する。
連結/単体、instant/duration、累計/単独、通貨・単位、nil、dimensionを潰さない。
未対応contextや拡張taxonomyから値を推定せず、候補のまま保存する。
この段階では会計期間をまたぐ合計、四半期差分、財務値のFX換算、yfinanceとの照合はしない。

期間充足は「収録済み書類の期間」と「必要な最新期間の確認」を分ける。
一覧の要求日・処理時刻・取得時刻と調査範囲を保存し、調査範囲の穴は不足として残す。
年次5件を5期と数えず、訂正と比較期間の重複を識別する。
四半期・半期は実際の開始・終了日と書類種別を保持し、8期間を任意の8書類で満たさない。
最新公表分を確認する根拠がない場合、保存された最大期を必要終端と決めない。
財務数値の網羅性と発行体IR確認が未完成な初版では、書類件数だけで財務・開示全体をreadyにしない。

### 3. 内部準備として保存・接続する

新しい内部manifestにsource metadata、raw参照、法定開示索引、XBRL候補、
対象銘柄・期間照合、不足理由、policy版、原取得generationと準備generationを保存する。
原bytesを自己完結した出力へ保持し、全参照・hashとparse結果を再検証する。
保存済みJSONの値を信頼して通すだけのvalidatorにはしない。

EDINETのオンライン無効はローカル再検証の禁止と混同しない。
元取得時の承認と現在のローカル利用条件を分けて記録し、外部Agent転送は許可しない。

任意の価格・FX準備は既存validatorで再検証し、task・銘柄・評価policy・評価時刻を照合する。
異なる評価時刻のreadyを現在のreadyへ引き継がず、同一時刻での再prepareを要求する。
既存manifestは変更せず、新しい統合準備索引から参照する。
公開 `EvidenceSetV1` / `ExecutionManifestV1` は作らない。

## 変更内容と実装順

新規ファイル名は配置案。Pythonは上記基点、tests/configはrepository基点。

| 順序 | ファイル | 予定変更 |
| --- | --- | --- |
| 1 | 新規 `preparation/financial_disclosure.py` | 内部入力・manifest、保存raw読込と再検証、task・発行体対応、一覧・書類索引、期間不足判定 |
| 2 | 新規 `preparation/financial_candidates.py` | 既存XBRL抽出器の再利用、context/unit付き候補の保存、訂正・重複の未確定理由 |
| 3 | 新規 `preparation/financial_disclosure_cli.py` | オフライン `prepare` / `validate`。入力欠損の保存は0、改変・parse・公開失敗は1。network引数なし |
| 4 | 新規 `tests/preparation/test_financial_disclosure.py` | bundleから準備・再生までの境界、銘柄・訂正・期間・秘密情報不混入を検証 |
| 5 | `README.md`、`docs/TODO.md`、設計03、要件02 | 実装範囲、候補と正規化済み財務の違い、残る充足条件、内部CLIを同期 |

保存公開は既存 `preparation/storage.py` と `price_fx_run.py::publish_run_preparation` を再利用する。
共有公開処理の移動・source adapterの意味変更は必要が生じた場合だけ行い、既存挙動を維持する。
初版の利用例は合成fixtureとし、実書類が存在すると仮定したコマンド例を載せない。

## 実装時の検証

- 保存rawの改変、参照欠落、異書類ID、異銘柄、未来時刻、重複・訂正・取下げを検証する。
  不足は理由付きpending、不正入力は技術エラーになること。
- 同一context IDの別member、連結/単体、異なる通貨・単位、nil、期間重複を保持すること。
  合成XBRL候補から未定義の財務指標を生成しないこと。
- 年次5書類でも同一期重複なら5期充足にならず、IR未確認・最新性未確認が残ること。
- 保存後の再parse・再計算が一致し、入力不変、既存宛先・symlink・競合公開を拒否すること。
- socket禁止・credential loader禁止でCLIを実行する。秘密値を必要としないこと。
- 価格・FX参照のtask・時刻不一致を拒否し、既存の731日再生互換を維持すること。
- 新規準備テストと `tests/sources/test_edinet_retrieval_plan.py`、
  `test_edinet_xbrl_facts.py`、`test_edinet_retrieval_offline_e2e.py`、
  `tests/preparation/test_price_fx_run.py` を `./scripts/pre-commit/pytest.sh` で実行する。
- 変更範囲のRuff・Mypyを既存wrapperで実行する。共有保存処理を変更した場合は全体チェックへ広げる。
  schemaを変更しないことを確認する。計画作成時にはテストを実行しない。

## 後続段階と承認境界

初版の完了条件は、銘柄・時点に結び付いた法定開示と財務候補が再生可能で、
不足と未対応部分を価格・FXと並べて説明できることである。財務分析開始の完了条件とは異なる。

次の段階では実資料を根拠に、会計基準別・taxonomy版別の指標対応表と連結優先規則、
訂正採用、最新8期間の扱い、一次資料との照合を具体化する。
EDINET live受入は既存オンライン無効設定を変更する別の承認対象とし、
送信対象・件数・期間・認証境界・失敗時動作を具体化してから実行する。
過去5期のための日次一覧の大量走査を、この計画で暗黙に許可しない。
発行体IR・適時開示の取得元と保存・転送条件も別途確認する。

本オフライン初版にブロッキングな未解決事項はない。
正規化済み財務値の受入とlive取得は、上記未決定事項を解消するまで初版の対象外とする。
原証拠と既存CLIを変更しないため、新しい内部CLIの利用停止で切り戻せる。

## Implementation Notes

- 既存raw bundleには取得時刻・source intent・承認bytesがないため、入力inventoryと取得時承認snapshotを明示的に添付する形式とした。DBのcommit確認を行ったとは表現しない。
- 新規候補moduleは訂正関係判定を担当し、fact抽出は既存adapterに委譲した。共有公開処理と既存source実装は変更していない。
- 候補準備は常にpending。期間件数は観測情報だけを示し、taxonomy・最新性・IRの完了を認定しない。
- 実証拠の新規取得は行わず、合成fixtureでCLI・原証拠再生・価格FX接続を確認する。

実装検証では新規15件を含む関連59テストが成功した。変更ファイルのRuff・Mypyと
公開schema生成物の `--check` も成功した。共有保存・source adapter・公開契約は変更していない。
実EDINETのlive受入は今回実行していない。
