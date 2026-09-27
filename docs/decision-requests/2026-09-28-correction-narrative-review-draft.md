# 保存済み訂正本文の限定確認案

## 必要な判断

年次5期とinterim8期間の数値受入・接続は完了したが、訂正報告S100RAR0の定性的な訂正内容は未確認。
[承認済みmapping計画](../agent-reports/plans/2026-09-27-xbrl-entity-financial-mapping-implementation-plan.md)は、
「原ZIP/XML/本文、text block、全fact一覧、財務数値そのもの、credentialは送らない」としている。
`config/source-approvals/edinet/v5.yaml`も`external_agent_transfer_allowed: false`。
今回の診断はこの範囲を維持し、本文をcoding agentのコンテキストへ出していない。

次のどちらで訂正本文を確認するか、所有者の判断が必要。

1. **限定した開発確認を許可する案**: 下記の固定HTML内の訂正理由・訂正事項・訂正箇所の説明文だけを、
   財務数値・人名・連絡先を除いて最大4,000文字、このcoding agentで確認する。
   除外・範囲選別はローカル処理で行い、範囲を確定できない場合は出力せず停止する。
   原文と位置・hashを保ち、本文と説明に含まれる命令はuntrusted dataとして扱う。
   未承認の追加member、全text block、数値表、raw/bulk、screening Agentへの転送は対象外。
   恒久的なsource approvalの転送禁止は解除しない。追加取得もしない。
2. **ローカルで人間が確認する案**: 所有者が下記原本を確認し、訂正内容の要約と採用判断を提示する。
   coding agentは原文を受信せず、その判断と原本参照を成果物に記録する。

これは取得許可の再確認ではなく、既存の本文送信禁止に対する限定確認方法の判断。
承認状態はpending。人間の判断前に本文を表示しない。

## 固定した原本と診断結果

- 保存先: `runs/edinet-evidence/edinet-pair-554488a5b687429a8bfea2a7430af7dd/raw/S100RAR0/body.bin`
- 原ZIP SHA-256: `a363937b4b42d31200a797ca9a83326a129aa9644d312ecc16354acbf2c7620c`
- HTML member: `PublicDoc/0101000_0529900133506.htm`
- member SHA-256: `c177e1e65d31cec2498c10208cfe02aa9c35382c5fe71968097e3be4204c6b8f`
- HTML内に「訂正事項」「訂正箇所」の各見出し1件を確認した。内容の意味は未判定。
- 原報告・訂正報告はそれぞれ151 text block候補。厳密なQName・context比較で117キーが同一、
  原報告のみ34キー・訂正報告のみ34キー。未対応キーを無条件に同じconceptとして扱っていない。
  これは本文68箇所が変更されたという意味ではない。
- メタデータのみの診断保存先: `runs/edinet-correction-inventory-20260928/`。

定性確認を完了しても、最新重要開示・訂正網羅性・runtime IR binding・証拠凍結等が未完了なら
`analysis_ready=false`を維持する。
