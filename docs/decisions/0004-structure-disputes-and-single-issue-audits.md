# ADR-0004: 争点を構造化し1セッション1争点で追加監査する

| 項目 | 値 |
| --- | --- |
| 状態 | Accepted |
| 決定日 | 2026-09-01 |
| 要件との整合確認日 | 2026-09-01 |
| 適用範囲 | 一次レビュー指摘、異議申立て、争点化、materiality、Antigravity追加監査 |
| 実装状態 | 未実装 |

## コンテキスト

分類タグだけでは、問題箇所、分類理由、根拠、最終結果への影響および必要な修正を人間やAgentが
確認できない。一方、説明を自由記述だけにすると、追加監査の起動、同一争点の監査回数制限、
状態遷移および集計を機械判定できない。

一次レビューワーに指摘と重要度の初期判定を委ねても、workerとオーケストレーターが理由付きで
異議を出せなければ、レビューワーの判断が実質的に無条件で採用される。Antigravityも第三票や
最終決定者ではなく、中立な材料から独立した監査意見を返す役割に限定する必要がある。

## 決定

### findingと分類

findingは、対象成果物、`claim_id`、JSON Pointer、対象期間、要約、詳細説明、主分類、副分類、
分類理由、重要度と理由、証拠・Policy・schema参照、影響、期待する解決および処理状態を持つ。
分類タグは検索と機械分岐の索引とし、説明の代替にしない。

MVPの主分類は次の8つとする。

- `factual_error`
- `insufficient_evidence`
- `schema_or_format_error`
- `policy_application_difference`
- `logical_gap`
- `future_hypothesis_difference`
- `risk_materiality_difference`
- `style_or_expression`

主分類は1つを必須とし、副分類は0個以上を許可する。分類変更だけの専用履歴は設けず、immutableな
finding、responseおよび再レビュー結果の系列から処理経緯を追跡する。

`unclassified`を終端分類にしない。既存分類へ確定できない場合は一時状態
`classification_pending`とし、理由、検討した候補分類、各候補を採用しなかった理由および次の
分類主体を必須にする。この状態では一次レビュー合格、追加監査起動および`AnalysisCompleted`への
遷移を禁止する。

### 重要度と異議申立て

一次レビューワーがfindingの初期重要度を決定する。対象workerとオーケストレーターは、対象ID、
証拠またはPolicy参照および理由を伴う異議を、オーケストレーターが管理するresponseとして提出できる。
一次レビューワーは異議を再確認し、受入れ、一部受入れ、拒否、追加証拠・修正要求またはfinding撤回を
理由付きで返す。

再確認後もレビューワーが重要度を維持したことだけでfindingの内容を正しいと確定しない。
`high`または`critical`、あるいは`material`な解釈差が残る場合はdisputeへ移行する。
`medium`以下かつ非`material`な未解決指摘は、オーケストレーターが採否と残置理由を記録した場合に限り
一次レビュー合格を妨げない。

### materiality

`severity`は放置した問題の大きさ、`materiality`は争点の解決方向によって人間へ提示する実質的な
判断材料が変わるか、`audit_eligible`は追加監査の全起動条件を満たすかを表し、混同しない。

materialityは、各解釈を採用した場合に変わる対象ID、期間、変更前後の扱いおよび理由を持つ
`impacts`で申告する。中期・長期の4段階評価、評価可否、主要投資仮説、重大リスクまたは
エントリー・撤退参考情報のいずれかに有効なimpactが1件以上あれば、コードが`is_material = true`を
算出する。レビューワーが初期impactを提示し、workerは理由付きで異議を出せる。オーケストレーターが
統合結果への影響を確認し、再レビュー後も判定が解消しない場合は、安全側に`material`として扱う。

### disputeと監査パケット

workerとレビューワー、またはオーケストレーターとレビューワーの間で、通常応答、Policy照合および
許可された限定再調査後も`material`な解釈差が残る場合にdisputeを作成する。事実照合やschema修正で
一意に解消できる指摘はdisputeにしない。

争点化時に不変の`dispute_id`を発行する。対象`claim_id`、期間、Policy論点および未解決質問が同じなら、
文面、分類または重要度が変わっても同一争点とする。分割・統合では親子関係を記録し、監査回数制限を
回避しない。同一taskの再開ではIDを引き継ぎ、新taskでは新IDから元争点を参照する。

監査パケットには、中立な監査質問、合意済み事実、未解決事項、対象Policy、検証済み共通証拠、
2つの解釈と各根拠、反証条件、対象期間および主張を含める。作成主体名、レビューワーが支持する側、
オーケストレーターの暫定結論、推奨案または優勢案を含めない。MVPでは1つの`audit_id`を1つの
`dispute_id`だけに対応させ、1セッション1争点とする。

### 起動、結果および新規資料

既定の6条件をすべて満たす場合だけ追加監査を起動し、結果を`start_audit`、`do_not_audit`または
`human_decision_required`として機械算出する。

有効な監査結果の列挙値は次とする。

- `support_interpretation_1`
- `support_interpretation_2`
- `support_neither`
- `indeterminate_more_evidence_possible`
- `indeterminate_no_more_evidence`
- `human_judgment_required`

`invalid_output`、`execution_failed`および`cancelled`は監査意見ではなく実行結果として分離する。
監査結果を直接最終判定にせず、支持または両案修正は成果物へ反映して一次レビューへ戻し、追加証拠を
取得可能なら限定再調査、取得不能または人間判断が必要なら人間判断待ちへ進める。

Antigravityが候補資料を発見した場合は、候補URLと支持・反証対象だけを返す。通常の取得・検証工程を
通過した資料だけを共通証拠へ追加し、`evidence_set_version`を更新して影響workerの再分析、統合および
一次レビューを行う。同じ争点への2回目のAntigravity監査は行わない。

同一`dispute_id`の論理監査は、schema、参照および争点範囲の検証に成功した監査結果を受領した時点で
初めて1回と数える。起動失敗、途中失敗、空出力または無効な出力は論理監査回数に数えない。
MVPでは物理試行回数の専用契約、高度な復旧または自動再試行を設けず、失敗を記録して安全停止し、
必要な再実行は人間が明示的に開始する。有効な`indeterminate`または`human_judgment_required`も
論理監査1回として数える。

## 結果

分類と説明、重要度とmateriality、監査意見と実行結果を分離できる。workerの異議申立てを許しながら、
未解決の重大指摘を単独で棄却できない。監査には結論誘導を含まない材料だけを渡し、争点単位の独立性と
1回制限を検証できる。一方、P1ではfinding、response、dispute、audit、materialityおよび状態遷移の
具体的なPydantic契約とfixtureが必要になる。

## 検討した代替案

- `unclassified`を通常分類にする: Agentが分類判断を回避できるため採用しない。
- レビューワーの再判定を常に最終決定とする: workerの異議と独立監査の意味を失うため採用しない。
- Antigravityへ暫定結論を渡す: 監査判断を誘導するため採用しない。
- 複数争点を1セッションへまとめる: MVPでは監査範囲と結果の対応を複雑にするため採用しない。
- 失敗した物理実行を論理監査1回と数える: 有効な監査意見を得ていないため採用しない。

## 検証条件

1. findingの分類タグだけでは保存できず、対象、説明、理由、根拠、影響および期待する解決を検証できる。
2. `classification_pending`のままレビュー合格、追加監査または解析完了へ進めない。
3. workerとオーケストレーターの異議およびレビューワーの再確認を構造化して追跡できる。
4. materialityのimpactから`is_material`を決定論的に算出できる。
5. 同一争点の改名、分類変更、分割または統合で追加監査回数を回避できない。
6. 監査パケットに主体名、暫定結論、推奨案または争点外情報が含まれない。
7. 1つの`audit_id`が1つの`dispute_id`だけを参照する。
8. 監査結果を決定表へ適用し、再レビュー、限定再調査、人間判断待ちまたは失敗へ遷移できる。
9. 有効な監査結果だけが論理監査1回として記録され、失敗時に自動再試行しない。

## 参照

- [Agent・レビュー・追加監査の方針](../system-requirements/03-agent-review-and-audit-policy.md)
- [機械可読契約と人間向け成果物の要件](../system-requirements/07-machine-readable-contracts.md)
- [AgentAdapterとsession継続の要件](../system-requirements/08-agent-adapter-and-session-continuation.md)
