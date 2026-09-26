# yfinance標準取得への移行

## 決定

2026-09-26、ユーザーはHTTP単位の制御・ログが複雑性を増す場合の要件緩和と、
yfinanceへ通信を任せる構成を承認した。過剰アクセス防止は維持する。
実装計画の作成・再承認は不要という既存の指示に従い実装した。

標準経路は`yf.Ticker(symbol).history(...)`とし、独自Sessionを注入しない。
cookie・crumb・consent・redirect・認証方式の切替をyfinance 1.7.0へ委ねる。
補助応答の形式検査、個別permit、HTTP監査ログ、HTTP本文保存は必須にしない。
旧profile v3と`CoordinatedSession`は明示的な互換経路に限る。
通常運用では新旧取得経路を混在させない。旧経路は標準経路のエラー時にも自動選択しない。

## 過剰アクセス防止

- 単位は1銘柄の`history()`呼び出し。HTTP送信回数とは区別する。
- 同じprovider状態を共有するLinuxプロセス間で同時取得は1件。
- 取得完了後、次回の取得まで最低10秒を空ける。制限中は待機・自動再試行せず拒否する。
- `YFRateLimitError`を含む取得失敗時は15分のcooldownを保存する。
- 通信前にも15分の予約を保存する。プロセス異常終了後の即時再送を防ぐ。
  実行中は時間経過にかかわらずファイルロックを維持する。
- レート状態破損・未対応版・時計の巻き戻り時は送信しない。
- yfinanceの一般ネットワークretryは0。認証方式の切替に伴う内部の限定的な再送は許容する。
- 状態は`runs/.runtime/yfinance-native/`に保存し、task・銘柄・出力先ごとに分割しない。
  別マシン・別checkout・このクライアントを使わないノートブックとは制限を共有しない。

10秒・15分は保守的なローカル運用値であり、Yahooの公表レート上限ではない。
HTTP単位の間隔制御・送信数計測・`Retry-After`の読取りは保証しない。
内部の最初の429を即時捕捉する保証もなく、ライブラリからの例外・戻り値で取得成否を判定する。
必要な場合は次の手動実行までさらに時間を空ける。

## データと出典

補助本文・cookie・tokenは保存しない。既存の隔離runtimeを利用し、cookie cacheの永続化と
ライブラリログを抑止する。取得workerは専用の単一threadで使用する。

保存する証拠は、yfinanceが返したDataFrameのCSVと、提供元、ライブラリ版、関数、引数、
取得日時、期間、timezone、CSVのSHA-256である。Yahooの原HTTP JSONではないことを明記する。
空データ・必要列不足・不正indexを成功として扱わない。欠損の補完や財務分析用の品質判定は別工程である。
ソース利用承認はv3で維持し、期限切れ・利用不可なら送信しない。

`YfinanceDailyAdapter.download(intent)`はJPX検証済みintentと開始・終了日を使用する。
`NativeHistoryClient.history(symbol, period="1y")`と以下のCLIは取得診断にも利用できるが、
銘柄のJPX適格性を検証したことにはならない。

```bash
python -m stock_research_llm_orchestrator.sources.yfinance.history_cli \
  --symbol 7203.T --period 1y --output runs/yfinance-check
```

出力先は新しいdirectoryを指定する。過去の結果を上書きしない。

## 関連資料

- [データソースと証拠の方針](../system-requirements/02-data-source-and-evidence-policy.md)
- [CLIと実行運用](../system-requirements/04-cli-and-runtime-operations.md)
- [従来の補助応答方針](2026-09-26-yfinance-auxiliary-response-policy.md)
- [通信確認結果](2026-09-26-yfinance-live-verification-outcome.md)

## 調査と検証

`impact-scope-researcher`へ既存の契約・呼び出し元・要件への影響調査を委任し、結果を使用した。
共通HTTP gateはphysical attemptを作成するため、ライブラリ呼び出しをHTTP1件と偽って流用せず、
小さな独立した共有guardを実装した。
標準経路・永続間隔・別processの排他・異常終了・429のcooldown・不正データをテストする。
実通信の観測値は通信確認結果へ追記する。
