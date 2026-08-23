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
実装は、承認済み要件、ADR、実装計画の順に整合を確認してから開始する。

## ADR一覧

| ADR | 状態 | 決定 |
| --- | --- | --- |
| [ADR-0001](0001-centralize-external-request-coordination.md) | `Accepted` | 外部リクエストを共有Coordinatorで調整する |
