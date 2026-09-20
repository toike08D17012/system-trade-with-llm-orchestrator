# Agent CLI使用量観測fixture

## 目的

このディレクトリは、Agent CLIの新規sessionとnative resumeで取得できる使用量fieldを、
固定したCLI版の観測として保存する。P4のAgent adapter実装前に、provider固有出力と
`AgentUsageMetricV1`への安全なマッピングを確認するためのfixtureである。

観測結果は記載したCLI版にだけ適用する。将来版の動作を保証しない。

## 測定条件

2026-09-20にDocker開発環境内の空の一時workspaceで測定した。固定した無害なpromptを使用し、
Codexでは`read-only` sandbox、Antigravityでは`plan` modeを指定した。Antigravityの
`--sandbox`はDocker環境でinvocation errorになったため使用していない。

生の標準出力と標準エラーは権限を制限した一時directoryにだけ保存し、session IDの一致確認後、
allowlistしたfieldだけをfixtureへ転記した。prompt、応答本文、session ID、認証情報、
アカウント情報、絶対pathは保存していない。生transcriptは保持していない。

## 結果

<!-- markdownlint-disable MD013 -->

| Provider | CLI版 | 新規・resume | Provider出力で観測した項目 | 共通契約へ数値採用 |
| --- | --- | --- | --- | --- |
| Codex | `codex-cli 0.155.1` | 成功 | input、cached input、cache write、output、reasoning output token | local wall timeのみ |
| Antigravity | `1.2.7` | 成功 | input、output、thinking、cache read、total token、duration、turn数 | local wall timeのみ |
| Claude Code | 未測定 | 契約前のため保留 | 未確認 | なし |

<!-- markdownlint-enable MD013 -->

CodexとAntigravityではresume時にprovider報告値が増加した。ただし、増加だけでは各fieldが
run単位かsession累積かを確定できない。CLI helpにも集計範囲の定義がないため、provider観測値は
`provider_observation`へ保存し、対応する共通usageは`availability: unknown`、
`scope: unknown`、`meaning_confirmed: false`、`value: null`としている。

プロセス起動直前から終了直後まで測定した`local_wall_time`は、`origin: derived`、
`scope: run`として数値を採用する。費用は出力に存在しなかったため`not_retrieved`であり、
provider非対応とは断定しない。取得不能を`0`で補完しない一方、provider観測に含まれる
正当なゼロ値はそのまま保持する。

## Fixture形式

各観測は次の3層を分離する。

1. CLI版、取得時刻、実行形、session一致などの観測メタデータ
2. allowlistで抽出した`provider_observation`
3. `AgentUsageMetricV1`で検証する`expected_usage`

`pair_id`はfixture内で新規・resumeを関連付ける識別子であり、provider session IDから
生成した値ではない。元のsession IDはfixtureへ保存しない。

## Claude Codeの残件

Claude Codeは契約と利用可能な認証環境が整った後、次を実施する。

1. 採用するCLI版を記録する。
2. 空の一時workspaceで非対話の新規sessionを1回実行する。
3. 同じprovider sessionをID指定で1回resumeする。
4. token、費用、所要時間、turn数のfieldと集計範囲を確認する。
5. 同じfixture形式と検証を追加する。

アカウント作成、契約、credit購入、対話loginはこの確認作業に含めない。
