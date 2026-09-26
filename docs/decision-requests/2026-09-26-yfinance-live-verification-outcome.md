# yfinance補助通信の実確認結果

> 追記: 標準取得は[ネイティブhistory方針](2026-09-26-yfinance-native-history-policy.md)へ移行した。
> 以下の補助HTTP制御・観測記録は旧経路のものであり、新経路の取得可否とは区別する。


## 標準history経路の確認（09:17 UTC）

ユーザー承認の要件緩和後、`NativeHistoryClient`と共有guardを通して
`Ticker("7203.T").history(period="1y", interval="1d", auto_adjust=False, ...)`を実行し、成功した。
独自Session・補助URLの直接取得・HTTP単位の監査を使用していない。

- 取得日時: 2026-09-26T09:17:38.350435+00:00
- 行数: 243
- 返却期間: 2025-09-25〜2026-09-25
- timezone: Asia/Tokyo
- 株価: `runs/yfinance-native-check-20260926/prices.csv`
- 出典情報: `runs/yfinance-native-check-20260926/metadata.json`
- CSV SHA-256: `08f92099dc8addde67bcf3cb50448a6634f235703817150e6bbef478ef6069bc`

上記はyfinanceの戻り値を保存したものであり、Yahooの原HTTP応答ではない。
JPX適格性を検証した診断ではなく、分析全体のacceptance・欠損検証とは区別する。
内部通信を記録していないため、この実行中のcookie404の有無・HTTP回数は不明である。
旧probeとの差のうちどれが429に影響したかは、この成功だけでは特定できない。

新経路の関連12テストが成功し、`./scripts/pre-commit/checks.sh`の全体テスト、
Ruff、format、Mypy、shell checkも成功した。ソース承認v3の追加に伴う設定件数の期待値を更新した。
Dockerがない環境のため、既存wrapperの環境内実行を使用した。

## 旧補助probeの結論

2026-09-26の修正後の再検証では、cookie取得のHTTP 404を記録して継続し、crumb取得へ進んだ。
crumb取得先はHTTP 429、`text/html`、19 bytesを返した。利用制限の応答を受信し、
許可形式`text/plain`とも一致しないため停止した。自動再試行は行っていない。
実行は09:03:54.342073 UTCに開始し、09:03:57.376041 UTCに終了した。
監査記録は`/tmp/yfinance-live-verification-20260926-04/summary.json`にある。
cookie値・crumb本文は保存・表示していない。株価取得とlive acceptanceは未完了である。

以下は修正前の観測記録である。YahooへのHTTPS接続と応答受信を確認した。
`cookie_basic`の応答はHTTP 404、`text/html`、UTF-8、4,572 bytesだった。
承認済み形式の検証には合格したが、HTTPエラーとして停止した。
crumb・consent・株価取得は未確認であり、source全体のlive acceptanceは未完了である。

## 実行条件

- ユーザーが実ネットワーク確認を許可し、実装計画の作成・承認は不要と指定した。
- source approval v2、source profile v3、yfinance 1.7.0を使用した。
- 検証対象は銘柄に依存しないcookie取得と、継続可能な場合のcrumb取得とした。
  試行4では形式検証に合格したcookie取得の404も継続対象とした。
  実在銘柄を確認したJPX証拠がまだないため、fixtureを実在確認済み銘柄として扱っていない。
- 永続queue、lease、8 scopeのgate、単発permit、実curl backend、応答形式検証を通した。
- 補助本文・cookie・token・そのhashは保存しない。監査メタデータのみ保存した。
- redirect追跡、自動retry、想定外形式の自動許容を行わなかった。
- 通常の本番取得停止は維持し、明示的opt-inの検証コマンドだけを実行した。

## 観測記録

| 試行 | UTC取得・完了時刻 | 結果 |
| --- | --- | --- |
| 1 | 08:53:32 | sandbox内でDNS解決に失敗。HTTP応答なし |
| 2 | 08:53:58 | ネットワーク利用可能な実行経路でHTTP 404を受信。URL末尾スラッシュの比較不具合で形式検証が停止 |
| 3 | 08:55:44.545918 | 不具合修正後の明示的再検証。形式検証は合格、HTTP 404により`provider_error`で停止 |
| 4 | 09:03:54–09:03:57 | cookieの404を記録して継続。crumbの429・HTML応答により停止 |

試行3は08:55:44.620273 UTCに終了した。通信先は承認済みの`fc.yahoo.com`である。
制限外実行はツールの自動承認審査を通過した。2・3は自動retryではなく、環境制限と
コード不具合を切り分けた後に実行した個別の検証である。

## 追加・修正した実装

- cookie・consentのHTML／text、crumbのtext、UTF-8という承認済み応答形式を実装した。
- 補助通信ではraw本文の代わりに`yfinance-auxiliary-audit`のJSONを公開し、
  `body_retained=false`と監査情報を記録する。分析用rawとは別の参照として扱う。
- 独立した検証プロセスで、永続cookie／timezone cache、既存singletonの再利用、
  Pythonログ・標準出力・標準エラーへのライブラリ出力を抑止する。
- 同時実行とnested実行を拒否し、後処理で例外が発生してもglobal状態を復元する。
- 同一originの空pathと`/`を同じroot pathとして検証する。
- 通常のbackend構成拒否を維持し、`live_verification=True`とprivate runtimeの併用だけを許可する。

## 再現用コマンド

出力先には存在しないdirectoryを指定する。Dockerがある環境では標準wrapperで実行する。
今回の環境にはDockerがなかったため、環境内のPythonと標準検証wrapperを使用した。

```bash
python -m stock_research_llm_orchestrator.sources.yfinance.live_probe \
  --allow-network --output /tmp/yfinance-live-verification-new
```

今回の試行3の監査記録は`/tmp/yfinance-live-verification-20260926-03/summary.json`と
同directory内の`.runtime/`にある。`/tmp`の保持は保証しないため、主要な観測値は本文へ転記した。

## 残事項

実通信前後に人工応答テストを実施した。対象83件、Ruff、format、Mypy（151ファイル）、
および`./scripts/pre-commit/checks.sh`の全体検証が成功した。
本文・cookie・token hashの非保存、HTTPエラー停止、opt-inなしの拒否、global状態の復元、
同時実行拒否を含む。Docker経由の検証は環境にDockerがないため未実施である。

cookieの404継続修正後も全体検証が成功した。追加した本番portのテストでは、
物理試行の404記録を残しながら論理取得を成功できることを確認した。

- cookie取得先の404は、上流挙動を説明した後にユーザーが継続を承認し、実装・再検証済み。
  cookieがある場合とない場合の継続、物理試行の404記録と論理取得の成功の両立をテストした。
- HTTP 429によりcrumb成功は未確認。制限解除後の明示的な再検証で、crumbと
  実在確認済み銘柄のchart取得を確認する。今回の応答だけでは利用制限の詳細な原因は特定できない。
- consent・status fallback・永続cookie防止を含む全経路のlive acceptanceを完了する。

今回の結果は「接続できたが取得処理は停止した」であり、株価取得成功や本番運用可能を意味しない。
