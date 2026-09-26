# Architecture Decision Records

`docs/decisions/`には、システムの構造、実行境界、技術選択に関するArchitecture Decision
Record（ADR）を保存する。

ADRは次の状態を使用する。

| 状態 | 意味 |
| --- | --- |
| `Proposed` | 提案中であり、実装判断には使用しない |
| `Accepted` | 採用した決定。実装済みであることは意味しない |
| `Superseded` | 後続ADRに置き換えられた |
| `Rejected` | 検討したが採用しなかった |

要件文書が`Draft`の場合、ADRを`Accepted`にしても要件承認や運用承認を意味しない。
実装は、対応する承認済み要件とADRが一致し、実装計画がその内容に従っていることを
確認してから開始する。

## 要件との関係

Accepted ADRと、そこで採用した決定を規定するsystem requirementsは、1対1に対応させる。
system requirementsは「何を満たすか」、ADRは「なぜその方式を採用したか」、検討した代替案、
検証条件を記録する。同じ決定を異なる目的で記述する文書であり、どちらか一方が他方より
優先されるという関係ではない。

横断的な決定を複数の要件文書へ分けて記述する場合も、ADR一覧から対応する一組の仕様を
一意に追跡できるようにする。ADRと対応仕様に差異がある場合は、一方を優先して解釈せず、
未反映または不整合として記録する。実装へ進む前に両者を同期し、一方を変更する場合は
対応する他方も同じ変更単位で更新する。

## ADR一覧

<!-- markdownlint-disable MD013 -->

| ADR | 状態 | 決定 | 対応仕様 |
| --- | --- | --- | --- |
| [ADR-0001](0001-centralize-external-request-coordination.md) | `Accepted` | 外部リクエストを共有Coordinatorで調整する | [データソースと証拠](../system-requirements/02-data-source-and-evidence-policy.md)、[Agent・レビュー・監査](../system-requirements/03-agent-review-and-audit-policy.md)、[CLI・実行運用](../system-requirements/04-cli-and-runtime-operations.md)、[成果物・保持・セキュリティ](../system-requirements/05-artifact-retention-and-security.md) |
| [ADR-0002](0002-use-pydantic-contracts-and-json-interchange.md) | `Accepted` | Pydanticを契約定義の正本としJSONで交換する | [機械可読契約と人間向け成果物](../system-requirements/07-machine-readable-contracts.md) |
| [ADR-0003](0003-standardize-agent-adapters-and-session-continuation.md) | `Accepted` | AgentAdapterとsession継続境界を標準化する | [AgentAdapterとsession継続](../system-requirements/08-agent-adapter-and-session-continuation.md) |
| [ADR-0004](0004-structure-disputes-and-single-issue-audits.md) | `Accepted` | 争点を構造化し1セッション1争点で追加監査する | [Agent・レビュー・監査](../system-requirements/03-agent-review-and-audit-policy.md) |
| [ADR-0005](0005-own-production-request-runtime-and-artifact-publication.md) | `Accepted` | 本番外部request runtimeとraw artifact公開境界を確定する | [CLI・実行運用 1.1](../system-requirements/04-cli-and-runtime-operations.md)、[成果物・保持・セキュリティ 1.3](../system-requirements/05-artifact-retention-and-security.md) |

| [ADR-0006](0006-remove-source-response-capacity-limits.md) | `Accepted` | 全データソースの取得・解析に容量上限を設けない | [データソースと証拠 1.2、6.1節](../system-requirements/02-data-source-and-evidence-policy.md#61-取得展開解析の容量上限) |

<!-- markdownlint-enable MD013 -->
