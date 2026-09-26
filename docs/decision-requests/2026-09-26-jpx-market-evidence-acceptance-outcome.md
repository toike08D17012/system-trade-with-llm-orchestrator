# JPX検証済み価格証拠の受入確認結果

## 現在の結果

2026-09-26、承認済み価格受入方針を保存済み`7203`データへ適用し、
2023-09-26〜2026-09-25の731行についてPA-01〜PA-09すべてが成立した。
固定期間の価格証拠の受入は`accepted_with_limitations`として完了した。
現在の上場適格性等の制約を保持し、詳細解析全体の開始可否は`analysis_ready=false`である。
判定・保存・検証の詳細は[受入完了の記録](#承認済み方針による価格証拠の受入完了)を参照する。

以下は初回取得から受入完了までの経過であり、途中の未完了状態や旧成果物は当時の記録として保持する。

## 初回取得・正規化時点の結果

2026-09-26、JPX一覧の実bytes照合、銘柄検証、標準history取得、価格正規化、
内部証拠保存を接続した。固定fixtureによる検証とリポジトリの品質チェックは成功した。
続行指示後、JPXパーサーの5桁コード対応を修正し、検証済み`7203`について
3年分731行の取得・正規化・証拠保存に成功した。JPX鮮度・取引日網羅性が未確認のため、
この時点では固定期間の価格証拠の受入は未完了だった。

初回はJPX公式XLSXをHTTP 200で受信したが、既存の`JpxCurrentListAdapter.parse`が
`JpxCurrentListParseError`を返した。銘柄検証が完了していないためYahooの取得へ進めていない。
後述の明示的な診断取得と修正で解消した。自動retry、別URLへのfallback、
5桁コードから普通株への推定分類は行っていない。

## 初回の実通信記録

| 項目 | 観測値 |
| --- | --- |
| 開始時刻 | `2026-09-26T09:52:13.664385+00:00` |
| 受信完了時刻 | `2026-09-26T09:52:14.048359+00:00` |
| 検証終了時刻 | `2026-09-26T09:52:14.264102+00:00` |
| source | `jpx`、source approval/profileともに版1 |
| 送信回数 | JPX 1回、Yahoo 0回 |
| HTTP status | `200` |
| Content-Type | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| 本文サイズ | `228486` bytes |
| 本文SHA-256 | `fff94dd14057c8bfa36a3fbabd8e228bbed63751c7ecd10c1a8281f5385fcb78` |
| raw publication | `failed`。検証済み証拠としての公開なし |

承認済みの公式URLに対し、既存`JpxPhysicalTransport`を
`ProductionTransportCoordinator.execute_exchange_with_callback`から1回実行した。
source設定を検証し、永続lease・queue・全scopeのgate・単発permitを使用した。
実取得時刻はHTTP応答を受け取った時点で記録し、保存完了時刻から代用していない。

実行サマリーは`runs/jpx-market-acceptance-20260926/summary.json`、監査状態は
`runs/.runtime/jpx-production/request-coordinator.sqlite3`にローカル保存した。
publication IDは`publication-dc7f68b06e7d4295bd2aa96b1fe229ff`である。
これらの実データ関連成果物はGit追跡対象外である。

既存publisherはsource検証失敗時に未公開のstagingを除去するため、初回の応答本文は残っていない。
初回時点では拒否条件を特定できず、次の診断で本文を隔離保存した。

## 続行後の診断と修正

ユーザーの続行指示後、`2026-09-26T09:57:39.255646+00:00`に同じ公式URLから
HTTP 200で取得し、`runs/jpx-market-diagnostic-20260926-03/unvalidated/`へ診断本文と
取得記録を隔離保存した。本文hash・サイズは初回と同一だった。
JPXへの実送信は初回と診断の計2回である。

ヘッダー・コードの一意性・並び順は正常だったが、4,441行のうち7行が5桁の数字コードであり、
`JpxListedIssue.code`の4文字制約に違反していた。source-nativeモデルだけを4文字または
5桁の数字へ対応させ、5桁の場合は`security_class`と`eligibility`を`unknown`に限定した。
4文字の入力・Yahoo mappingは維持し、コードを切り詰めない。

修正後、同じ保存本文を再送なしで再解析し、全4,441行の読込と`7203`の適格性確認に成功した。
再検証結果は`runs/jpx-validated-20260926/`へ一括保存した。
元のCoordinator raw publicationの`failed`記録は書き換えず、ローカル再検証として区別した。

診断補助コードには、前回の失敗後にsingle-flightの終了が未連携という問題もあった。
その状態を参照した診断起動2回は送信前に停止し、未送信要求を取消した。
以後の明示的な単発診断は既存の`enqueue_logical_request`を使い、同じ永続queue・lease・
全scope gateと共有rate stateを維持した。single-flightの正常・失敗終了の連携は
Coordinator統合の残件として保持する。

## 価格証拠の実確認

```bash
python -m stock_research_llm_orchestrator.preparation.market_evidence_cli \
  --allow-network --code 7203 --evaluation-policy-version 1 \
  --jpx-body runs/jpx-validated-20260926/body.bin \
  --jpx-metadata runs/jpx-validated-20260926/provenance.json \
  --output runs/market-evidence-7203-20260926
```

上記はDockerコマンドのない現環境で実行した。通常のDocker環境では
`./docker/run-docker.sh`を前置する。

| 項目 | 観測値 |
| --- | --- |
| 取得完了時刻 | `2026-09-26T10:00:23.586838+00:00` |
| JPX snapshot | `2026-08-31` |
| 検証銘柄 | `7203`、`verified_eligible` |
| history呼び出し | 1回。内部HTTP送信数とは区別 |
| 要求・観測期間 | `2023-09-26`〜`2026-09-25`（包含的） |
| 行数 | `731` |
| 通貨・timezone | `JPY`、`Asia/Tokyo` |
| provider銘柄照合 | 符号・市場timezone・instrument typeの整合を確認 |
| CSV SHA-256 | `55e6239ba45d72e5e045f48018f1007fb0d20667d62ab59fd18fd16ea33c5cff` |
| 保存物 | `runs/market-evidence-7203-20260926/`の7ファイル |
| 保存後の検証 | 全参照・実bytesのhash整合を再確認 |
| 結果 | `prepared_with_gaps`、CLI終了コード2、`analysis_ready=false` |

残る品質issueは`jpx_snapshot_freshness_unconfirmed`と`trading_dates_unconfirmed`である。
配当ごとの原通貨を独立検証していないという制約も内部索引に保持する。

## 検証済みの範囲

- 3暦年の合成データを使ったJPX検証からnative取得・正規化・一括保存までの処理。
- 不適格、改変されたJPX証拠・取引日資料、既存出力先では取得前に拒否すること。
- 実取得メタデータの保持、追加通信なしの通貨抽出、数値・日付・欠損・actionの検証。
- source不一致・欠損・取引日未確認を合格としないこと、hash参照整合、保存失敗時の非公開。
- 内部CLIの明示的ネットワーク許可と、`prepared_with_gaps`の終了コード2。
- 既存互換経路テストをprivate runtimeで隔離し、端末のcookie cacheへ依存しないこと。
- 合成5桁コードを保持しても普通株として取得せず、不正なコード形式は引き続き拒否すること。

JPX修正後の対象38件が成功し、続いて全体品質チェックも再度成功した。

`./scripts/pre-commit/checks.sh`でPytest、Ruff、format、Mypy、Shell検証が成功した。
新規ファイルにも個別にRuffとformatを適用した。Dockerコマンドがない環境のため、
既存wrapperの環境内実行分岐を使用した。Docker経由での再検証は実施していない。

## オフライン再検証の完了

続く[公式情報調査と方針案](2026-09-26-jpx-freshness-and-calendar-policy.md)では、
JPXの最新掲載月が保存済みsnapshotと一致し、予定取引日731日と保存済み日付が完全に
一致することを確認した。価格の再取得と既存artifactの書換えは行っていない。

承認済み計画に沿って`preparation.market_revalidation`と専用CLIを追加し、
`2026-09-26T10:39:13.089234+00:00`にネットワーク禁止下で実データを再検証した。

| 項目 | 結果 |
| --- | --- |
| 保存先 | `runs/market-revalidation-7203-20260926/` |
| ファイル数 | 12（元の7ファイル、追加調査資料4ファイル、新索引） |
| 日付照合 | 期待731日・観測731日、欠落0日・余剰0日 |
| 掲載月照合 | 2026-09-26の手動観察に対して2026-08が一致 |
| 現在の上場適格性 | `unconfirmed` |
| 全体状態 | `revalidated_with_gaps`、`analysis_ready=false` |
| 元indexのSHA-256 | `eb21d26868e16df448c2803d4fea5b4390c704597c3bea3f55357efea30a9514` |
| 新indexのSHA-256 | `d9f70d007569353fb77735b37a4a830de46b24e8dbb3214188af2fb8cb029adb` |

入力用の調査メモsnapshot・出典メタデータ・掲載月観察記録は
`runs/market-revalidation-inputs-20260926/`に保存した。
元の準備結果と調査ディレクトリ内の通常ファイル計12個のhashが実行前後で不変であることを確認した。
新記録は元ファイルのコピーと補足資料を同梱し、保存後に全参照hashと判定結果を再検証した。
元の品質issueと`price_quality_passed=false`は過去の結果として保持する。

掲載月の確認はHTML原bytesのない手動調査で、確認日は日単位で記録した。
`recorded_at`は観察記録の作成時刻であり、Web取得時刻を表すものではない。
カレンダーの由来は調査メモと出典メタデータとして保持するが、本番source承認は付与していない。

新規23件を含む対象36件のテスト、Mypy（162ファイル）、全体の
`./scripts/pre-commit/checks.sh`が成功した。新規ファイルのRuff・formatも成功した。
Dockerがないため、既存ラッパーの現在環境で実行した。

## 承認済み方針による価格証拠の受入完了

カレンダーと上場変更の扱いを委任された後、
[価格証拠受入方針](2026-09-26-price-evidence-acceptance-draft.md)と実装計画が承認された。
固定期間を対象とする内部受入API・CLIを実装し、保存済みデータへ適用した。

| 項目 | 結果 |
| --- | --- |
| 判定日時 | `2026-09-26T11:35:10.412863+00:00` |
| 受入方針版 | `price-evidence-acceptance-v1` |
| 保存先 | `runs/price-acceptance-7203-20260926/` |
| 対象 | 2023-09-26〜2026-09-25の731行 |
| 条件別判定 | PA-01〜PA-09すべて成立 |
| 状態 | `accepted_with_limitations` |
| 解析全体の開始可否 | `analysis_ready=false` |
| 保存数 | 13ファイル（元7ファイル、調査資料3ファイル、承認記録2ファイル、新索引） |
| 新索引のSHA-256 | `7ac44dac5871963ff64ae2372df4b0ddacbdbe00b28092f6cc94e749cf653ade` |

外部通信を禁止してCLIを実行し、終了コード0を確認した。元CSVの数値を文字列から
Decimalへ変換して正規化値と照合し、数値品質を再評価した。
JPX v1・yfinance v3の承認記録も同梱し、取得条件・対象・利用範囲・発効日・再確認期限を検証した。
保存後に同梱資料から判定全体を再現した。元準備結果・カレンダー調査・再検証入力・
再検証記録の計27ファイルは実行前後でhashが不変だった。

現在適格性と最新掲載月の再確認、臨時休場の網羅調査、配当原通貨、独立provider照合等は
制約として残す。JPYと東京時間そのものは確認済みである。
原通貨確認が必要な配当再投資等の計算、未提供の調整済みOHLCを用いる計算は許可しない。
旧品質issueを消すことなく、この固定期間の価格証拠だけを制約付きで受け入れた。

新規28件を含む対象83件が成功し、Mypy（165ファイル）、新規ファイルのRuff・format、
全体の`./scripts/pre-commit/checks.sh`も成功した。
Docker不在のため、既存ラッパーの現在環境で実行した。

## 次の作業

通常実行への接続時には、必要な終端日と他の必須証拠を確認する。
固定期間の価格証拠の受入は完了したが、P3全体、完全な証拠集合の凍結・実行manifest確定、
詳細解析の開始判定は後続作業である。
