# 固定IR 8資料の共有runtime取得・接続結果

## 採用した実行方式

所有者の必要取得許可・公開資料内部利用許可と、検証済み変更をコミットして次へ進む指示に基づき実施した。
旧operator記録に要求IDを後付けせず、固定済み7203の8PDFを各1回だけ再取得した。
同時1、開始間隔60秒以上、retry・redirect・任意URL・環境proxyなしとした。
providerの公表rate limitを主張するものではない。
[公式利用条件](https://global.toyota/en/terms-of-use/)と
[決算資料一覧](https://global.toyota/en/ir/financial-results/)を確認し、私的内部処理に限定したsource承認・profileを追加した。
providerの個別許諾取得や、外部公開・自動bulk転送の許可を主張しない。

## 取得と保存

- 共有runtime: `runs/dukascopy-shared-runtime`
- 一度限りcampaign: `toyota-ir-eight-pdf-20260928`
- 主task: `edinet-acceptance-7203-20260927`
- 原証拠root: `runs/toyota-ir-runtime-evidence`
- 取得bundle: `runs/toyota-ir-runtime-evidence/toyota-ir-runtime-20260928`
- 実送信8件、retry0件。最短開始間隔は60.939659秒。
- 固定8PDFすべてが既存の採用済み原本hashと一致した。
- 取得manifest SHA-256: `2da9e7732c756b3884146d080555a4bc037a2ea2f827620caf2f032b3b75d258`
- 統合bundle: `runs/toyota-financial-ir-runtime-run-20260928`
- 統合manifest SHA-256: `585d3e1dda3a5d7bccd8fddb854c9dcbff13a835722522e60bf5f2c5f9a9b536`

campaign directoryはlease保有中に排他的・永続的に作成し、作成時に全campaignを消費する。
失敗・中断・未完了でも再開始しない。別task・出力先、marker削除・別runtimeへの切替で再送しない。
原本と財務値はGit追跡対象外に保持した。

## 来歴の検証と財務接続

各receiptに対し、正本DBのlogical request、URL・content・task・approval/profile hashを束縛するfingerprint、
physical attemptの親要求・sequence・generation・成功状態、raw publicationとcommitted reference、
logical successを照合した。保存されたbody・request・response・receiptも検証した。
DBに存在しない自己申告のreceiptを採用しない。

新しい取得参照は同じbytesの取得を証明する。採用済み財務値のoperator由来は変更せず、
旧制約は履歴として保持し、`interim_runtime` に新しいtask-bound来歴を区別して保存する。
新runは年次30項目・5/5期、IR48項目・8/8期間を維持する。
公開APIを破壊せず、runtime入力を省略した既存bundleは同じbytesで再現できる。

## 検証

新規17テストは本物のCoordinator・SQLite・RawArtifactPublisherとMockTransportで、
固定8送信、永続間隔、再送拒否、HTTP429/redirect/失敗、原本変更、受信中lease失効、
markerのみを残す中断、publication/finalization中断、DB・raw・task不一致、任意対象拒否、network opt-inを確認した。
既存財務統合、設定検証、production storageの回帰検証も行った。
repository wrapperによる全体Pytest、Mypy、Ruff、format、Shell format/checkはすべて成功した。
新規ファイルのRuff・formatも対象指定で確認した。

実通信後の取得bundle validate、旧訂正レビュー付きrun validate、新run prepare/validateは成功した。
旧runのmanifest・inputsはbyte不変であることを確認済み。
成功済み全体テストを重複実行しないため、コミットhookではPytestのみを省略し、他のhookを実行する。

## 残件

今回の8資料だけでは最新重要開示・訂正の網羅性・全体鮮度を証明できない。
必要指標の計算、EvidenceSet凍結、公開実行manifest、Agent実行・レビュー・最終レポートへの接続は残る。
`analysis_ready=false`を維持する。次は最新重要開示の正式受入を進める。
