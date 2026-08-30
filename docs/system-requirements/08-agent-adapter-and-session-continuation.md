# AgentAdapterとsession継続の要件

| 項目 | 内容 |
| --- | --- |
| 文書状態 | Approved |
| 文書オーナー | リポジトリ所有者 |
| 承認者 | リポジトリ所有者 |
| 版 | 1.0 |
| 作成日 | 2026-08-31 |
| 承認日 | 2026-08-31 |
| 発効日 | 2026-08-31 |

## 1. 目的

Codex、Claude Code、Antigravityの非対話実行を共通オーケストレーションから扱うため、
`AgentAdapter`の公開操作、process管理との責務分離、session継続PolicyおよびMVP境界を定める。

## 2. 責務境界

| 主体 | 責務 |
| --- | --- |
| `SessionRunner` | subprocess起動、標準出力・標準エラー、終了code、時刻、OS process状態、取消、資格情報注入 |
| `AgentAdapter` | command構築、capability、preflight、provider session、response・error・usageの共通形式への変換 |
| オーケストレーター | task checkpoint、session mode、独立性、証拠・Policy・schema検証、状態遷移、成果物採否 |

Adapterはprovider固有responseから共通のraw JSONを取り出し、共通検証wrapperへ渡す。自然文からの
推測修復、暗黙の型変換、証拠参照の推測または業務上の採否判断をAdapter内で行わない。

## 3. 公開操作

MVPの`CodexAdapter`、`ClaudeCodeAdapter`および`AntigravityCliAdapter`は次を必須とする。

| 操作 | 要件 |
| --- | --- |
| `capabilities` | 対応CLI版、非対話、構造化出力、resume、status、cancel、usageの取得可否を返す |
| `preflight` | 選択CLI、版、認証、model、非対話、構造化出力、native resumeを検証する |
| `start` | 新しいprovider sessionと`agent_run`を開始し、handleを返す |
| `resume` | 明示したprovider sessionを継続し、新しい`agent_run`を開始する |
| `get_status` | 1つの`agent_run`について、共通process状態と時刻を返す |
| `cancel` | 1つの`agent_run`へ冪等な取消要求を送り、要求受理と終了確認を区別する |
| `collect` | 終端runのprocess結果、raw JSON、error、usageおよび成果物参照を返す |

`start`と`resume`は完了結果ではなく、systemが発行した`agent_run_id`を含むhandleを返す。
`get_status`は少なくとも`queued`、`running`、`cancelling`、`succeeded`、`failed`、`cancelled`および
`unknown`を区別する。このprocess状態をtask全体の実行状態と混同しない。

native forkは任意操作とし、`capabilities`で対応可否を返す。オーケストレーターは、対応時の
`native_fork`、検証済み成果物を新規sessionへ再入力する`reconstructed_fork`および履歴を引き継がない
`new`を区別する。

## 4. 識別子と記録

- `logical_session_id`: 同一task・役割・論理Agent・処理目的を継続するシステム内の単位
- `provider_session_id`: providerが発行する不透明なsession識別子
- `agent_run_id`: start、resumeまたはforkによるCLI起動ごとの識別子
- `parent_agent_run_id`: 継続または派生元のrun
- `parent_logical_session_id`: fork時の派生元logical session

同じprovider sessionをresumeする場合も、新しい`agent_run_id`を発行する。各runについて、入力版、
session mode、選択理由、開始・終了時刻、process結果、出力、検証結果、成果物参照および取得可能な
使用量を`manifest`から追跡可能にする。provider sessionをtask IDまたは実行履歴の正本にしない。

## 5. 直列実行

1つのlogical sessionではactive runを最大1件とする。レビュー指摘は指摘ごとに並行resumeせず、
指摘IDを保持した1つのfeedback packetへまとめて差し戻す。

active runが存在するlogical sessionへのstart、resumeまたはfork要求は、`session_busy`として拒否する。
重複する操作IDまたはfeedback IDを検出し、同じ処理を二重に起動しない。MVPではsession専用の
永続queueを設けない。再起動後に前runの終了を確認できない場合は、新しいresumeを開始せず安全停止する。

## 6. session更新Policy

### 6.1 `resume`を既定とする処理

- 同一task、同一役割、同一論理Agentおよび同じ処理目的へのレビュー差し戻し
- 同じ指摘への応答後の再確認
- 同一争点に対する未完了の補足
- 検証済み共通証拠追加後に、同じ作業者が行う影響範囲の再分析

### 6.2 `new`を必須とする処理

- 新しい`task_id`または明示的な再実行
- 別銘柄、別市場または処理目的の変更
- 別役割、別の論理Agentまたは独立分析経路への切替
- model family、安全境界または権限主体の変更

同じ履歴から別案を派生させる場合は、`native_fork`または`reconstructed_fork`を使用する。
Policyが複数のsession modeを許可した場合だけ、オーケストレーターが選択し、理由codeと説明を記録する。

Codex作業者、Claude作業者、一次レビューワー、オーケストレーターおよびAntigravityは、互いの
provider sessionをresumeしない。相手の検索結果、暫定結論または非公開contextをsession経由で共有しない。

## 7. session設定

logical sessionの開始時に、YAML設定から次を確定する。

- providerとmodel
- effort相当値
- 論理的な役割と役割指示版
- permissionとworkspace
- Web検索および外部移送Policy
- 出力の`schema_id`、`schema_version`およびhash

同じlogical session内でこれらを変更しない。変更が必要な場合は、session更新Policyに従ってforkまたは
newを開始する。resume時には、現在のtask、役割、処理目的、feedback、証拠集合版、変更された証拠、
Policy版および出力契約版を明示し、providerの履歴より検証済みの現在入力を優先する。

## 8. 失敗と安全停止

- native resumeに失敗した場合は、error、未処理事項および取得済み中間成果物を保存して`Failed`とする。
- resume失敗時にnewまたはreconstructed forkへ自動fallbackしない。
- schema違反、JSON parse失敗、無効な証拠参照、security違反または自然文との矛盾を自動採用しない。
- 部分出力、終了code、秘密情報除去済みの標準出力・標準エラー参照およびprovider errorを保存する。
- 必要な再実行または新session開始は、人間が明示的に開始する。

## 9. Antigravity監査

同一`dispute_id`、同一`logical_audit_id`および同一監査目的の未完了runをresumeして完了させる処理は、
2回目の論理監査に数えない。完了した監査へ新しい問いまたは監査目的を追加する処理は、同じprovider
sessionを使用しても別監査として扱い、同一争点1回の制限を迂回しない。

## 10. MVPで追加制御しない事項

provider sessionのcontext圧縮、context欠落またはcontext window不足について、MVPでは専用検出、
予防的fork、自動要約または自動再構成を要求しない。実測で問題が発生した場合に要件を追加する。

## 11. 使用量

CLIから容易に取得できるtoken、費用、所要時間、turn数などを実験的にrun記録へ含める。項目ごとに
provider報告値、派生値、run値、session累積値、未取得、provider非対応または意味未確認を区別し、
取得不能な値を`0`としない。正確な取得項目と意味は、各CLIの固定fixtureまたは最小実測で確認する。

## 12. 検証

- 3つの候補CLIの固定版で、非対話startとsession ID指定resumeを検証する。
- 同じsessionのresumeごとに別の`agent_run_id`が発行され、親runを追跡できる。
- 新task、別役割または独立分析経路で過去sessionをresumeできない。
- active runがあるsessionへの重複要求を`session_busy`として拒否する。
- resume前後でsession設定が変化していないことを検証する。
- resume失敗時に自動fallbackせず、`Failed`と未処理事項を保存する。
- provider固有responseを共通raw JSONへ変換し、共通検証wrapperで検証する。

## 13. 実装時に確認する事項

- 各CLIの対応版、非対話start・resume引数およびsession IDの取得位置
- native forkの対応有無と正確な引数
- cancelとstatus確認のprovider固有方式
- 使用量項目、値の意味および取得不能時の表現
- 標準出力・標準エラーから秘密情報を除去して保持できる範囲

## 14. 参照資料

- [ADR-0003](../decisions/0003-standardize-agent-adapters-and-session-continuation.md)
- [機械可読契約と人間向け成果物の要件](07-machine-readable-contracts.md)
- [CLIと実行運用の要件](04-cli-and-runtime-operations.md)
- [Agent・レビュー・追加監査の方針](03-agent-review-and-audit-policy.md)
- [成果物・保持・セキュリティの要件](05-artifact-retention-and-security.md)
