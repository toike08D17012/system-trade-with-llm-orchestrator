# ADR-0001: 外部リクエストを共有Coordinatorで調整する

| 項目 | 値 |
| --- | --- |
| 状態 | Accepted |
| 決定日 | 2026-08-23 |
| 要件との整合確認日 | 2026-08-28 |
| 適用範囲 | 詳細解析MVPと将来の一次スクリーニングにおける外部データ取得・Web検索 |
| 実装状態 | offline fake Coordinatorあり。production Coordinatorは未実装 |

## コンテキスト

複数のAgentとデータソースアダプターが同じprovider、認証情報または送信元IPを共有する。
個別に外部送信すると、同時実行、rate制約、`Retry-After`、cache、重複要求および利用状況を
一貫して扱えない。分析上の独立性は必要だが、通信制御状態までAgentごとに分離する必要はない。

## 決定

外部へ直接送信する決定論的データ取得は、外部接続の直前に置く共有
`RequestCoordinator`を経由する。MVPでは単一processがCoordinatorを所有し、SQLiteのruntime
leaseによって同じ実行環境で複数のオンラインCoordinatorが動作することを防ぐ。

```mermaid
flowchart LR
    O[オーケストレーター] --> A[source adapter]
    A --> C[RequestCoordinator]
    C --> K[cache・single-flight]
    K --> Q[provider別queue]
    Q --> G[rate gate・cooldown]
    G --> P[外部provider]
```

Coordinatorは次を担当する。

- source承認とrequest分類の検証
- logical request ID、fingerprintおよびphysical attemptの記録
- cache判定と`single-flight`
- provider別queue、同時実行数、送信間隔、rate、burstおよびcooldown
- cancellation、runtime leaseおよび再起動時に必要な状態の永続化
- response分類、エラーおよび取得可能な利用量の記録

MVPではシステム独自の固定request上限、timeoutまたは自動再試行を設けない。providerが
`Retry-After`またはcooldownを要求した場合は共有状態へ反映し、同じtask内で自動再送せず
`Failed`として終了する。必要な再実行は人間が新しいtaskとして明示的に開始する。

### source profile

provider固有値をコードへ分散させず、版付きsource profileに次を持たせる。

- `rate_domain`、非秘密の`credential_scope`、`egress_scope`
- `max_concurrency`、`min_interval`、`rate`、`window`、`burst`
- `cache_policy`と`batch_policy`
- providerのcooldown、エラー記録および`auto_retry = false`
- `policy_version`と対応するsource承認記録

具体値を公式条件から確認できないsourceは、`max_concurrency = 1`、`burst = 1`とし、正の
`min_interval`を定めるまでオンライン利用しない。providerの制約は迂回しない。

### queueとcache

process内schedulerはprovider別queueを持ち、同じproviderではtaskごとのFIFOとactive task間の
round-robinを使う。異なるproviderはそれぞれのgateを満たす場合に並行できる。cacheと
`single-flight`はrate gateより前に適用し、利用条件、認証scope、鮮度、対象期間、source Policy版が
一致しない結果を再利用しない。

### Agent内蔵Web検索

Agent CLI内部の物理requestを観測できない場合は、Agent sessionの開始許可、同時実行数、
provider accountのcooldownを制御する。物理requestを観測できない通信をorigin単位で制御できた
ものとは記録しない。初回独立探索では検索語、検索結果、候補URLまたは検索結果cacheをAgent間で
共有せず、候補URLの検証取得だけをCoordinatorへ戻す。

### `yfinance`

固定した`yfinance`版を使い、物理送信前にpermitを取得する`CoordinatedSession`を注入する。
`threads=False`を明示し、全送信を捕捉できない版を採用しない。捕捉不能時に直接接続へ
fallbackしてはならない。

### 障害時

Coordinator、SQLite stateまたはsource profileの検証に失敗した場合は、外部接続を停止する。
adapterやAgentによる直接接続、別credentialまたは未承認sourceへの切替は行わない。

## 永続状態と監査

Git管理外のruntime stateへ、runtime lease、logical request、physical attempt、rate state、
cooldownおよびsingle-flight参照を保存する。取得本文はSQLiteへ格納せず、成果物の保存方針に従う。
URL query、cookie、認証header、API key、tokenその他の秘密値をログへ含めない。

運用上はprovider別request rate、in-flight、queue、429・503、cooldown、cache hit、stale拒否、
single-flight、task別待機時間および取得可能な利用量を観測する。固定上限の追加は、実測で問題が
明確になった場合に別の要件変更として判断する。

## 結果

複数Agent・taskによる集中送信を一か所で調整でき、決定論的データの共有と分析の独立性を
両立できる。一方、Coordinatorは単一障害点となり、source profile、永続状態、cache判定、
fairnessおよび各providerとの互換性を検証する必要がある。

## 検討した代替案

- Agent・adapterごとの`sleep`: 他Agent、task、processと調整できないため採用しない。
- 全provider共通の直列queue: 無関係なproviderまで待たせるため採用しない。
- 固定semaphoreだけ: 時間窓、burst、cooldown、再起動後状態を扱えないため採用しない。
- 分散queue: 単一実行環境のMVPには運用負荷が大きいため採用しない。

## 検証条件

1. 同一requestの同時投入を`single-flight`で1つの物理送信へまとめられる。
2. provider別rate、burst、同時数、cooldownを固定時計で検証できる。
3. 異なるproviderは並行でき、同一provider内でtaskをstarvationさせない。
4. `Retry-After`を共有cooldownへ反映し、自動再送しない。
5. 再起動後もcooldownとruntime leaseを保守的に復元する。
6. `yfinance`の全物理送信が`CoordinatedSession`を通る。
7. 初回独立探索で他Agentの検索内容・結果が見えない。
8. ログへ秘密情報を保存せず、Coordinator障害時に直接接続へfallbackしない。

## P1で定義する事項

- providerごとのrate、window、burst、`min_interval`、`max_concurrency`
- sourceごとのcache TTL、batch方法およびcooldown処理
- runtime stateの正確な保存先と破損時の照合方式
- 採用する固定`yfinance`版とsession互換性
- Agent CLIごとの検索利用量取得方法

runtime ownership、SQLite、lease・fencing・回復、request cardinality、queue bound、gate順序、
raw artifact publicationは[ADR-0005](0005-own-production-request-runtime-and-artifact-publication.md)と
対応する要件04版1.1・要件05版1.3で確定した。

## 参照

- [CLIと実行運用の要件](../system-requirements/04-cli-and-runtime-operations.md)
- [データソースと証拠の方針](../system-requirements/02-data-source-and-evidence-policy.md)
- [Agentレビューと追加監査の方針](../system-requirements/03-agent-review-and-audit-policy.md)
- [成果物の保持とセキュリティ](../system-requirements/05-artifact-retention-and-security.md)
