# 実装計画: EDINET実取得受入と財務対応表の根拠採取

## 概要

既存EDINET transport・Coordinator・raw公開を内部CLIへ接続し、7203の年次報告1件で
取得・保存・再生・財務候補準備までを確認する。初回の通信は一覧1回と条件付き書類1回とし、
財務対応表を決めるための実資料をローカルに確保する。
前段のオフライン準備は実装済みだが、実EDINETの受入は未実施である。
本計画は承認待ち。コード・設定・認証情報は今回変更・読取りしていない。

## 現状と調査根拠

Pythonパスは `src/stock_research_llm_orchestrator/` 基点。

- `sources/edinet/httpx_transport.py::build_httpx_edinet_transport` は既存の送信時credential loaderを使用し、
  redirectなし・trust_env=false・HTTP依存ログ無効化・秘密値を除いた例外を実装済み。
- `credentials/edinet.py::use_edinet_api_key_for_send` はowner-only fileを送信直前に開く。
- `requests/transport.py`、`requests/raw_artifacts.py` とEDINETの既存offline E2Eは再利用対象。
- `preparation/dukascopy_fx.py` に共有Coordinatorのexchange・raw公開後の論理終了処理がある。
  provider固有処理は流用せず、この終了連携を接続時の参考にする。
- `preparation/financial_disclosure.py` は手動組立てした取得情報を受け取る。
  取得時刻・source intent・承認snapshotをruntime経路から出力する機能は未実装。
- `config/source-approvals/edinet/v1.yaml` はonline=false・外部Agent転送=false。
  `config/source-profiles/edinet/v1.yaml` は並列1・60秒間隔・retryなし。
- [既存source決定](../../decision-requests/2026-09-22-phase-7-source-decisions.md)は
  offline承認とsource別live受入を区別する。今回の新規承認対象は以下の限定live経路である。

2026-09-27に確認した公式資料:

- [EDINET操作ガイド](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html):
  2026年6月更新のAPI v2仕様書と、版別のXBRL資料への入口。
- [API v2仕様書](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf):
  一覧のdate/typeとSubscription-Keyによる要求形式を確認した。
  60秒間隔は既存のローカル制限であり、公式の最大許容量という意味ではない。
- [トヨタIR](https://global.toyota/jp/ir/): 2026-06-10に2026年3月期有価証券報告書を掲載した旨を確認した。
- [トヨタ報告書一覧](https://global.toyota/jp/ir/library/securities-report/): 対象年次報告の掲載を確認した。

IR掲載日からEDINET提出日を断定しない。書類ID・EDINETコードは一覧応答で確認する。
現時点では実XBRLのtaxonomy・context構造や抽出成功は未確認である。

## 承認対象と実行範囲

| 項目 | 提案 |
| --- | --- |
| 対象 | 7203、2025-04-01〜2026-03-31の年次報告 |
| 一覧 | `api.edinet-fsa.go.jp` の既存一覧経路、date=2026-06-10、type=2を1回 |
| 書類 | 上記一覧で発行体・証券コード対応・期間・種別120・非取下げ・XBRLありを一意に確認できた書類に限りtype=1を1回 |
| 送信制御 | 共有Coordinator、並列1、送信間隔60秒以上、既存共有gateの厳しい方に従う |
| 認証 | 既存file loaderのみ。キー値をCLI引数・環境値・ログ・DB・保存URLへ出さない |
| 停止条件 | 該当なし、複数候補、認証/HTTP/parse失敗、429、redirect、対応不明なら停止 |
| 再試行等 | 自動retry・日付走査・別source fallbackなし |
| 保存 | rawはローカルrunsのみ。Gitにはコード・人工fixture・非秘密の結果記録のみ |
| 外部処理 | raw・bulk本文の外部Agent転送禁止を維持。候補の自動転送も追加しない |
| 完了の意味 | 1書類の取得経路と再生の受入。年次5期・中間8期間・開示3年の充足ではない |

2回はこの受入ケースの限定範囲であり、恒久的な再調査回数制限や容量制限を新設しない。
一覧日付が不一致なら、公開資料から次の候補日を確認した結果を記録し、別の実行範囲として扱う。
書類IDをモデル知識から補わない。

## 変更内容と順序

新規パスは配置案。

1. 新規 `config/source-approvals/edinet/v2.yaml` と `config/source-profiles/edinet/v2.yaml` に、
   本計画で承認された受入範囲・日付・参照を反映する。v1は不変とする。
   v2は受入専用CLIでのみ選択し、通常運用への自動有効化をしない。
   live開始前に既存の利用条件から重大な変更がないか確認し、根拠版・確認日を保存する。
2. 新規 `preparation/edinet_evidence.py` に単一一覧と選別後の単一archive取得を追加する。
   既存transport、production Coordinator、raw publication、論理終了連携を接続する。
   lease・gate・承認の照合を飛ばす直HTTP経路を作らない。
3. 原bytes公開とcommit確定後に、要求intent、実取得時刻、承認bytes/hash、原公開参照を
   `FinancialInput` 用の入力一式へexportする。staging・reconciliation_requiredを完了として渡さない。
   未確定rawが残る場合は通信を増やさず、既存のreconcile経路で状態を解決する。
4. 新規 `preparation/edinet_evidence_cli.py` に受入専用commandを設ける。
   `--allow-network`、task、config、共有runtime、出力先と既存credential file pathを要求する。
   source/keyのopt-inがなければ送信しない。秘密ファイル内容の事前表示は行わない。
5. 保存済みrawをオフラインで再parseし、`financial_disclosure_cli` のprepare/validateへ渡す。
   現在の価格・FX準備とは評価時刻が変わるため、そのままreadyを継承しない。
   初回は財務単独の準備結果を保存し、必要なら同一時刻で価格・FXを再prepareして接続する。
6. 新規 `docs/decision-requests/2026-09-27-edinet-live-acceptance-outcome.md` に
   送信件数・選別結果・原hash・保存先・再生結果・不足を記録する。
   README、TODO、財務準備計画の実装記録を同期する。

## 財務対応・訂正ルールの根拠採取

初回liveと同時に未知のtaxonomyへ汎用mappingを実装しない。
取得後、ローカルの検証コードで次を一覧化し、次のmapping実装計画へ渡す。

- namespaceと版、concept、context、entity scheme/identifier、dimension、unit、対象期間、nil/decimals。
- 売上・営業利益・親会社帰属利益・資産・資本・営業CFの候補と、対応する資料位置。
  タグが見つからない項目は未対応とし、名称の類似だけで同義と判定しない。
- 連結/単体・当期/比較期・duration/instantの区別。比較期値を別の提出書類や最新性の証明としない。
- 訂正は親子・版を保持する。部分訂正を全項目置換とせず、対象factごとに根拠が揃うまで未確定とする。
  初回対象に訂正実例がなければ実検証未実施と記録し、追加通信で探し回らない。

この候補一覧も既存ローカル利用条件に従う。外部Agentで内容を処理する範囲の拡張は本計画に含めない。
人がローカル資料を確認できる形で保存し、転送可能な情報の判断は別途明示する。

## 実装時の検証

- 新規 `tests/preparation/test_edinet_evidence.py` でnetwork/key opt-inなしの送信0、
  一意候補以外の書類送信0、正常時の一覧1＋書類1、60秒gateを注入時計で検証する。
- 既存 `tests/sources/test_edinet_httpx_production_integration.py`、
  `test_edinet_retrieval_offline_e2e.py` と新規ケースでcanary keyのlog/DB/artifact非混入を確認する。
- timeout/401/429/redirect、parse失敗、raw公開後の終了失敗で、再送なし・適切な停止状態となること。
- exportから既存財務準備の再生が成功し、発行体不一致、snapshot改変、未commit参照を拒否すること。
- 新規・関連テストをpytest wrapper、変更ファイルをRuff/Mypy wrapperで検証する。
  共有送信・終了連携への影響があるため実装後に `./scripts/pre-commit/checks.sh` も実行する。
- 上記成功後だけ限定liveを実行し、追加GETなしで再検証する。実書類のparse失敗は失敗として記録し、
  必要なparser修正の範囲を判断する。受入成功と書かない。

## 未解決事項・切戻し

credentialのmount・利用可否は未確認。実行前に既存手順でメタデータを確認し、値は表示しない。
未用意ならoffline実装・検証まで完了し、liveだけ未実施として報告する。
書類IDと実taxonomyは一覧・書類を取得して初めて確定するが、限定取得経路の実装を妨げない。

異常時は受入CLIの利用を停止し、v2を通常経路で選択しない。v1と保存証拠を保持する。
本計画の承認により、上記範囲の実装・検証と条件成立時の限定liveを一括して進める。

## Implementation Notes

- 承認後、受入専用v2設定・内部CLI・共有Coordinatorとraw公開・財務入力exportを実装した。
- 初回liveは一覧1回でparserが停止した。追加送信せず、未受入rawを保存した。
- 実応答と公式仕様に基づき、一覧のcsvFlag/legalStatusとnullメタデータ行への対応を追加した。
  保存済み一覧の再parseと対象候補1件の確認は追加通信なしで成功した。
- 初回失敗を保持し、XBRL取得以降は未実施。保存済み一覧からの再開は後続作業とする。
- [結果記録](../../decision-requests/2026-09-27-edinet-live-acceptance-outcome.md)を参照する。
