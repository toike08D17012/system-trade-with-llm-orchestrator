# yfinance補助応答ポリシーの具体値

> 追記: 標準取得は[ネイティブhistory方針](2026-09-26-yfinance-native-history-policy.md)へ移行した。
> 以下の補助HTTP制御・観測記録は旧経路のものであり、新経路の取得可否とは区別する。


## 状態

2026-09-26、リポジトリ所有者が下記の形式・保存方針を承認した。
サイズ上限は先行する決定で全sourceとも不要としている。OOMの可能性を受容し、
応答サイズを理由に取得・保存を拒否しない。補助応答形式・本文非保存と検証用の隔離処理は実装した。
live acceptanceは未完了である。

## サイズに関する決定

判断理由・代替案・リスクを含む正式な記録は[ADR-0006](../decisions/0006-remove-source-response-capacity-limits.md)を参照する。

- cookie・crumb・consentの操作別上限案を撤回する。
- chartの既存32 MiB上限と、全応答共通32 MiB上限案も採用しない。
- streaming時・応答検証時のアプリケーション側bytes上限は設けない。
- OSや実行環境のメモリ設定は変更しない。OOM時の自動再試行も追加しない。
- 対象は`yfinance`、EDINET、日本銀行、JPXを含む全sourceと共通transportである。
- アーカイブの展開bytes・圧縮率・member数、XBRLのfact件数・値の最大長など、資源消費量だけを理由とする制限も撤廃する。形式、path安全性、hash、記録長との一致、識別子などの意味的な検証は維持する。

この決定は従来の`DEC-20`および実装計画のうち、全sourceの応答・展開サイズ上限に関する部分を置き換える。

## 形式に関する決定

| 操作 | 許可media type | encoding |
| --- | --- | --- |
| `cookie_basic` | `text/html`、`text/plain` | UTF-8 |
| `crumb_basic`、`crumb_csrf` | `text/plain` | UTF-8 |
| `consent_form`、`consent_collect`、`consent_copy` | `text/html`、`text/plain` | UTF-8 |

共通条件:

- `Content-Type`を必須とし、上表以外を自動推測しない。
- charset未指定時はUTF-8として厳密にdecodeし、指定時はUTF-8だけを許す。不正bytesを置換しない。
- redirect追跡、一般retry、制限の自動緩和を許さない。
- 形式不一致、Content-Type欠落、不正な文字コードを検出した場合は停止する。
  実通信との適合は未確認であり、不一致時は根拠を確認して方針を見直す。
- 後続のユーザー承認により、`cookie_basic`のHTTP 404は記録したうえで後続へ進む。
  cookieがないことだけを停止理由にせず、取得できたcookieはメモリ内で利用する。
  この例外は404全般へ適用せず、429、未承認の通信先、形式不一致は引き続き停止する。

## 保存に関する決定

- cookie・crumb・consentの補助応答本文、cookie jar、一時トークンはメモリ内だけで扱う。
  rawファイル、一時ファイル、ログ、Agent入力、ライブラリの永続cookieキャッシュへ保存しない。
- 操作種別、取得時刻、HTTPステータス、応答形式、bytes数、検証成否、論理要求・物理試行の
  識別子、秘密値を含まない失敗理由コードだけを監査記録として保存する。
- cookie値、トークン、全ヘッダー、クエリ付きURL、トークン本文のhashは保存しない。
- 株価などの分析用原データは従来どおり保存する。補助通信だけをraw本文保存の例外とし、
  本文を保存していないことを明示する。本文を再現できないという制約を受容する。
- デバッグ用途の本文保存は今回の承認に含めない。

判断理由: 補助通信の内容は分析の証拠ではなく、主にデバッグ用途である。
全本文保存よりも、一時トークンを残さず通信成否を追跡できる方針を採用する。
承認済み要件は[データソースと証拠の方針6.2節](../system-requirements/02-data-source-and-evidence-policy.md#62-yfinance補助応答の形式と保存)へ反映する。

## 承認とlive有効化の区別

この表の承認だけではlive有効化しない。少なくとも、実ライブラリの補助通信・status処理、cookie cacheの永続化防止、秘密値の非露出、検証済みintent／session envelopeとの厳密な対応、永続consumer roleの照合、明示的opt-in、source別live acceptanceが必要である。

このうち、完全なintentと単一Sessionの一回限りのenvelopeの照合、および永続consumerの
active leader照合は実装済みである。consumer roleはsingle-flightの`leader`／`follower`を指す。
Agent種別はintent・taskとともにfingerprintへ含め、永続要求との一致を確認する。
補助応答の形式・保存方針の承認と、その実装・検証は別の段階として扱う。

通常の本番送信は停止を維持する。明示的opt-inとprivate runtimeを伴う検証経路には実backendを
許可し、補助応答は本文の代わりに監査情報だけを保存する。
[実通信確認結果](2026-09-26-yfinance-live-verification-outcome.md)では、修正後にcookie取得先の
HTTP 404を越えてcrumb取得へ進んだが、HTTP 429と応答形式不一致により停止した。
株価取得は未確認である。
全sourceの容量上限は撤廃済みであり、互換用の`max_response_bytes`は`None`だけを許可する。

## 回答欄

- サイズ: 上限不要。OOMの発生を受容する（ユーザー決定）。再承認は不要。
- media type・encoding: 上表を承認。想定外の形式では停止する。
- 保存: 補助応答本文・一時トークンはメモリ内限定。秘密値を含まない監査情報だけを永続化する。
