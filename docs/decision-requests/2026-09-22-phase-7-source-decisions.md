# Phase 7 source実装 一括意思決定シート

## 1. 目的

Phase 7以降のsource固有実装を、実装途中で追加判断のために止めず、一括して進められる状態にする。
本書はEDINET、日本銀行、`yfinance`、JPX/TSEについて、repository ownerが決める事項、推奨案、背景、
未承認時の安全側動作をまとめた承認依頼である。

回答は次のどちらかでよい。

- 一括承認: `全推奨案を承認します。`
- 例外付き承認: `全推奨案を承認します。ただし DEC-XX は ... に変更します。`

承認は実装着手の許可であり、live接続の自動有効化ではない。offline fixture、contract、parser、Coordinator接続、
raw publicationを先に完成させ、live smokeは通常CIから分離した明示的opt-inでのみ実行する。

## 2. 現在地

- production Coordinator、lease、queue、gate、single-flight、controlled transportは実装済み。
- exact raw staging、atomic publication、reconciliationは実装済み。
- credential-free source intentとbounded response parser protocolは実装済み。
- `yfinance`だけ既存のapproval/profileがある。EDINET、日本銀行、JPX/TSEにはversioned approval/profileがない。
- source profileにはprovider固有rateはあるが、globalからroleまでの共有gate値、response上限、operation mappingはない。
- source固有adapter、production HTTP stack、canonical normalizationは未実装である。

## 3. 一括回答欄

| ID | 決めること | 推奨案 | 回答 |
| --- | --- | --- | --- |
| `DEC-01` | 実装順 | EDINET → 日本銀行 → `yfinance` → JPX/TSE | 承認 |
| `DEC-02` | source有効化 | offline実装は進めるが、source別live acceptance完了までonline disabled | 承認 |
| `DEC-03` | HTTP stack | EDINET・日銀・JPX用に`httpx`をcompatibility確認後に固定。条件不適合なら追加せず停止 | 承認 |
| `DEC-04` | 共有gate | 初期MVPは全physical sendを直列化。source profileのprovider limitより緩めない | 承認 |
| `DEC-05` | cache/retry/fallback | cache無効、自動retryなし、source fallbackなし。人間による再実行だけ許可 | 承認 |
| `DEC-06` | raw・fixture | rawはlocal artifactだけ。Gitには人工fixtureのみ。実provider rawをcommitしない | 承認 |
| `DEC-07` | source-native model | Phase 7ではinternal model。public contract化はPhase 8でfield identity確定後 | 承認 |
| `DEC-08` | EDINET対象 | 書類一覧と書類取得を分離し、有価証券報告書・半期/四半期報告書・訂正報告書を対象 | 承認 |
| `DEC-09` | EDINET形式 | 一覧JSONとXBRL ZIPを採用。PDFは本文解析の正本にしない | 承認 |
| `DEC-10` | EDINET credential | 既存のowner-only fileとsend直前読込みを維持。値はartifact・DB・logへ保存しない | 承認 |
| `DEC-11` | 日本銀行系列 | `FM08` / `FXERD04`、東京17時、1 USD当たりJPYのBid・Ask中間値だけを採用 | 承認 |
| `DEC-12` | 日本銀行欠損・訂正 | 前後日補完なし。欠損を明示し、訂正は別raw版として保持 | 承認 |
| `DEC-13` | `yfinance`採用条件 | Python 3.14と全send捕捉をspikeで証明できた最新安定版だけ採用。失敗時はdisabled | 承認 |
| `DEC-14` | `yfinance`取得関数 | `download`、`threads=False`、`auto_adjust=False`、`actions=True`、`repair=False` | 承認 |
| `DEC-15` | Yahooデータ用途 | 個人の内部分析限定。bulk rawをAgent・Git・公開成果物へ渡さない | 承認 |
| `DEC-16` | JPX/TSE正本 | 当面はJPX公式「東証上場銘柄一覧」をcurrent eligibilityの正本とする | 承認 |
| `DEC-17` | 対象銘柄 | 東証Prime/Standard/Growthの内国普通株。ETF、REIT、優先株、外国株等は除外 | 承認 |
| `DEC-18` | suspension/delisting | 一覧から推測しない。公式status sourceが不足する場合はunknownとして採用停止 | 承認 |
| `DEC-19` | EDINETとYahooの優先順位 | 財務値はEDINET/発行体一次資料を優先。Yahoo値は補助で、不一致を平均化しない | 承認 |
| `DEC-20` | response上限 | source・operation別の保守的上限を採用し、超過時は保存・parseせず失敗 | 承認 |

## 4. 共通判断の背景

### `DEC-01`: 実装順

推奨順はEDINET、日本銀行、`yfinance`、JPX/TSEとする。

- EDINETは既存のcredential file、preflight、send-time loaderを再利用でき、法定開示の一次資料境界を先に確立できる。
- 日本銀行はAPI key不要で、対象系列と結合規則がsystem requirementsで既に狭く定義されている。
- `yfinance`はsession injection APIを公開しているが、依存関係が多く、Python 3.14対応と内部secondary requestの
  捕捉を実測する必要がある。
- JPX/TSEはcurrent一覧、過去as-of、上場廃止、売買停止を単一資料で満たせないため、authority hierarchyの確認が必要である。

未承認時はsource固有実装へ進まない。

### `DEC-02`: offline実装とonline有効化の分離

adapter、parser、人工fixture、raw publication接続は実credentialやnetworkなしで実装する。一方、onlineは次をすべて満たす
sourceだけ有効にする。

- currentなapproval/profile
- dependency compatibility
- source固有fixtureとnegative test
- credentialまたはanonymous preflight
- 明示的なnetwork opt-in
- 1件のlive smokeとredaction確認

条件不足は「別経路へfallback」ではなくdisabledとする。

### `DEC-03`: production HTTP stack

推奨はEDINET、日本銀行、JPXの直接HTTP取得に`httpx`を使用することである。ただし、採用versionは次をPython 3.14環境で
確認してから固定する。

- response streaming中のbyte上限
- redirect無効化と許可origin照合
- timeoutの明示指定
- compression展開後sizeの検証
- custom headerへcredentialをsend直前だけ注入
- exception、request representation、debug logへのsecret非露出

条件を満たさない場合はdependencyを追加せず、そのsourceをdisabledにする。`yfinance`はライブラリが要求するsession実装を
使用し、公式source用HTTP clientと混同しない。

### `DEC-04`: 初期gate方針

安全側の初期値として、全sourceをprocess全体で直列化する。

| scope | 初期方針 |
| --- | --- |
| global / egress / task / role | `max_concurrency=1`、追加sleepなし、1分60件を上限 |
| provider / origin / credential / operation | source profileの値を適用し、より緩い値へ拡張しない |
| unknown / missing | send前にfail-closed |

performance tuningはlive acceptance後に別承認とする。

### `DEC-05`: cache、retry、fallback

初期MVPではcacheを無効にし、provider error、rate limit、parse errorに対する自動retryを行わない。`Retry-After`はcooldownに
記録するが、その場で再送しない。別source、別account、直接接続へのfallbackも行わない。

### `DEC-06`: rawとfixture

- 実response rawはowner-onlyなlocal run artifactへ保存する。
- Gitへ置くfixtureは仕様から手作業で作った最小の人工データだけにする。
- credential、cookie、private URL、実providerのbulk responseをfixtureへ含めない。
- live smokeのbodyはテスト失敗時にもstdout/stderrへ出さない。

### `DEC-07`: model公開範囲

Phase 7のsource-native modelはinternalとする。source間のfield identity、missing reason、correction、freshness、provenanceを
Phase 8で確定するまでpublic schemaを増やさない。これによりprovider shapeをpublic contractとして固定する誤りを避ける。

## 5. EDINET

### `DEC-08`〜`DEC-10`: 対象、形式、credential

推奨operationは次の2つである。

| operation | 入力 | raw | source-native出力 |
| --- | --- | --- | --- |
| `document-list` | 日付、承認済みdocument type | 一覧JSON | document ID、公表日時、書類種別、訂正関係 |
| `document-retrieval` | document ID | XBRL ZIP | filing metadataとparse済みfact候補 |

対象は有価証券報告書、半期報告書、四半期報告書、これらの訂正報告書とする。大量保有報告、公開買付、PDFだけの表示資料は
初期対象外とする。訂正報告書は元書類を上書きせず、関係を保持する。

credentialは既存契約を維持する。

- host: `${HOME}/.config/system-trade-with-llm-orchestrator/edinet-api-key`または明示した外部path
- container: `/run/secrets/edinet_api_key`
- mode: owner-only file、read-only mount
- 値の読込み: physical send直前だけ
- 保存禁止: logical request、URL、artifact metadata、SQLite、log、exception、Agent input

公式仕様・規約・取得文書の権利をversioned approvalへ記録できるまでonlineはdisabledとする。

## 6. 日本銀行

### `DEC-11`〜`DEC-12`: 系列、日付、欠損、訂正

system requirementsで既に定めた次の規則をsource approval/profileにも固定する。

- database: `FM08`
- series: `FXERD04`
- frequency: 日次
- meaning: 東京市場17時、1米ドル当たり円、Bid・Ask中間値
- timezone: `Asia/Tokyo`
- join: 同じ東京日付の東証終値だけ
- missing: 前日・翌日・月平均で補完しない
- correction: content hashと取得日時が異なる別raw版として保存する

日銀APIはコードAPI、階層API、メタデータAPIを分け、固定endpointとparameterを使用する。API keyは使用しない。
公開サービス化は今回のMVP範囲外とし、ローカル内部分析だけを承認対象にする。

## 7. `yfinance`

### `DEC-13`: version採用条件

version番号を先に固定せず、隔離したcompatibility spikeで次を満たす最新安定版を採用する。

- Python 3.14でinstall/import/fixture testが成功する。
- `download(..., session=...)`または同等の公開APIでsessionを注入できる。
- cookie/crumb取得を含む全physical sendをcontrolled sessionで捕捉できる。
- `threads=False`で追加threadを生成しない。
- internal retryを無効化または捕捉できる。できない場合は不採用とする。
- redirect、response size、origin、statusをCoordinator境界で検証できる。

公式ドキュメント上、`download`には`threads`と`session`引数がある。一方、upstreamは変更が活発で、現行metadataが
Python 3.14を明示していない可能性があるため、実測を採用gateとする。

### `DEC-14`: 取得関数と引数

初期market data取得は次へ固定する。

```python
yfinance.download(
    tickers=[symbol],
    start=start_date,
    end=end_date_exclusive,
    interval="1d",
    actions=True,
    auto_adjust=False,
    repair=False,
    keepna=True,
    threads=False,
    progress=False,
    session=coordinated_session,
)
```

timezone、currency、OHLC、Adj Close、volume、dividend、splitを別fieldとして保持する。`auto_adjust`や`repair`による暗黙変換を
避け、必要な調整はversioned normalizationで行う。

### `DEC-15`: 利用範囲

既存approvalどおり、個人の内部分析に限定する。実Yahoo rawのGit保存、公開、再配布、外部Agentへのbulk series送信は行わない。
外部Agentへ渡せるのは、承認済み用途に必要な最小限の検証済み証拠と派生値だけである。

## 8. JPX/TSE

### `DEC-16`: current eligibilityの正本

初期MVPではJPX公式「東証上場銘柄一覧」を、取得時点でのcurrent eligibilityの正本とする。同ページは直近月末一覧を掲載し、
ファイルを順次差し替えるため、取得日時とcontent hashを必ず保存する。

この一覧だけで過去任意日のpoint-in-time再構成は行わない。過去as-ofが必要な場合は、保存済みsnapshotまたは別途承認した
履歴sourceがない限り`not_evaluable`とする。JPX Data PortalのCSVは、利用条件と自動取得方法を確認するspike後に補助sourceとして
追加できる。

### `DEC-17`: eligibility集合

初期集合は次の全条件を満たす銘柄とする。

- marketが東京証券取引所
- segmentがPrime、Standard、Growthのいずれか
- domestic issuer
- ordinary common equity
- snapshot時点でlisted

ETF、ETN、REIT、インフラファンド、優先株、種類株、外国株、預託証券、投資証券は除外する。コードだけからsecurity classを
推測しない。

### `DEC-18`: suspension、delisting、correction

月末一覧に存在しないstatusを推測しない。売買停止、整理銘柄、上場廃止日、訂正履歴を判定するには、別のJPX公式資料を
authority hierarchyへ追加する。必要fieldが取得できない銘柄はeligibleへ暗黙採用せず`unknown`とする。

## 9. source間の優先順位

### `DEC-19`: EDINETとYahoo

- 日本企業の重要財務値はEDINET XBRLまたは発行体一次資料を正本とする。
- `yfinance`の財務値は候補発見・補助比較に限定する。
- period、consolidation、currency、unit、amendmentが一致しない値を比較対象にしない。
- 一致しない値を平均化せず、両方の値とprovenanceを保持する。
- materialな不一致を一次資料で解消できなければ、その値に依存する評価を確定しない。

## 10. transport上限

### `DEC-20`: 初期値

| source / operation | 最大body | media type | encoding | redirect |
| --- | ---: | --- | --- | --- |
| EDINET document list | 32 MiB | JSON | UTF-8 | 不許可 |
| EDINET document retrieval | 256 MiB | ZIP | binary | 不許可 |
| 日本銀行 code/metadata | 16 MiB | JSONまたはCSV | UTF-8 | 不許可 |
| `yfinance` chart/actions | 32 MiB/physical response | JSON | UTF-8 | 許可originをspikeで固定 |
| JPX/TSE current list | 64 MiB | XLSXまたは承認済みCSV | binaryまたは明示encoding | 不許可 |

上限は許容値であって期待値ではない。streaming中に超えた時点で停止し、部分bodyをcommitted rawとして扱わない。
provider固有shape検証はtransportではなくsource parserで行う。

## 11. 実装中に再承認が必要になる条件

次の場合は本書の承認範囲を超えるため、実装を止めて追加承認を求める。

- 実providerの利用条件が既存approvalと矛盾する。
- sourceが有償契約、公開サービス届出、再配布許諾を要求する。
- `yfinance`の全physical sendを捕捉できない。
- EDINET credentialをsend直前以外に読む必要が生じる。
- JPX current listだけでは承認済みeligibility fieldを取得できず、別の有償sourceが必要になる。
- response上限の引上げ、並列化、cache、自動retry、fallbackが必要になる。
- 実rawをGit fixtureまたは外部Agentへ送る必要が生じる。
- public contractの破壊的変更が必要になる。

## 12. 公式資料と既存根拠

- [EDINET](https://disclosure2.edinet-fsa.go.jp/WEEK0010.aspx)
- [日本銀行 時系列統計データ検索サイト API機能利用マニュアル](https://www.stat-search.boj.or.jp/info/api_manual.pdf)
- [日本銀行 USD/JPY日次時系列](https://www.stat-search.boj.or.jp/ssi/mtshtml/fm08_d_1.html)
- [`yfinance.download` API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
- [`yfinance` upstream package metadata](https://github.com/ranaroussi/yfinance/blob/main/pyproject.toml)
- [JPX 東証上場銘柄一覧](https://www.jpx.co.jp/markets/statistics-equities/misc/01.html)
- [JPxData Portal](https://www.jpx.co.jp/markets/data-catalog/)
- [データソースと証拠の方針](../system-requirements/02-data-source-and-evidence-policy.md)
- [本番取得再評価計画](../agent-reports/plans/2026-09-21-production-data-acquisition-reassessment-implementation-plan.md)
- [P3市場データ正規化計画](../agent-reports/plans/2026-09-21-p3-offline-market-data-normalization-implementation-plan.md)

## 13. 承認記録

承認後に次を記録する。

| 項目 | 値 |
| --- | --- |
| reviewer | repository owner |
| reviewed on | 2026-09-22 |
| approved decision IDs | `DEC-01`〜`DEC-20` |
| exceptions | なし |
| next recheck | source approvalごとに設定 |
