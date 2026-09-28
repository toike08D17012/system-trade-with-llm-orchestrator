# 保存済み訂正報告の本文確認結果

## 利用方針と承認

2026-09-28、所有者は公開資料をこのcoding agentで確認し、必要に応じて編集・ローカル保存し、
所有者へ説明することを許可した。先の限定確認案の4,000文字・数値除外等は必須条件としない。
公開資料の内部利用と外部への再公開を区別し、前者について同じ確認を繰り返さない。
外部への再公開は今回の許可に含まれない。credential等の非公開情報は従来どおり保護する。

過去のmapping計画の本文送信禁止は、この開発確認について今回の明示的な指示が優先する。
`external_agent_transfer_allowed: false`等の固定runtime承認や、既存成果物のhashは変更していない。
通常screeningの自動bulk転送機能を有効化したという意味ではない。

## 確認内容

S100RAR0の「有価証券報告書の訂正報告書の提出理由」「訂正事項」「訂正箇所」を確認した。
訂正理由は第119期有価証券報告書の添付書類の添付漏れであり、訂正事項は次の3件の追加。

- 第119回定時株主総会招集ご通知
- 第119回定時株主総会 その他の電子提供措置事項（交付書面省略事項）
- 第119回定時株主総会決議ご通知

これらの節には、受入対象の財務6指標の数値訂正は記載されていない。
既存の原報告S100QZHY・訂正報告S100RAR0の2022/2023年12組の数値一致と整合する。
この固定報告について、本文を未確認としていた問題を解消した。
添付書類3件の内容・実在をすべて検証した、他の訂正が存在しない、全text blockが同じ、という判断ではない。

## 出典と保存

- 原ZIP: `runs/edinet-evidence/edinet-pair-554488a5b687429a8bfea2a7430af7dd/raw/S100RAR0/body.bin`
- 原ZIP SHA-256: `a363937b4b42d31200a797ca9a83326a129aa9644d312ecc16354acbf2c7620c`
- member: `PublicDoc/0101000_0529900133506.htm`
- member SHA-256: `c177e1e65d31cec2498c10208cfe02aa9c35382c5fe71968097e3be4204c6b8f`
- ローカル確認記録: `runs/edinet-correction-inventory-20260928/narrative-review.json`
- ローカル本文: `runs/edinet-correction-inventory-20260928/correction-explanation.html`

既存取得分だけで確認し、追加通信は0件。原ZIP・memberの固定hashを照合した。
原文はGitへ追加せず、文書には出典と要約のみ記録した。
既存の年次・IR受入bundleは不変で、過去の「本文未確認」という制約は当時の状態を表す。
今回の補足確認を自動的にruntimeの採用判定へ反映したとは扱わない。

## 検証と次の作業

保存済み原本のhash、説明3節、訂正事項3件と既存の数値比較結果を確認した。
変更はMarkdownとローカル確認記録のみで、`git diff --check`と関連リンクを確認する。
Python・設定・既存bundleを変更していないため、成功済みのPytest・Mypy等は繰り返さない。

次は最新重要開示の対象確認、IRのtask-bound runtime接続、補足レビューのrun参照と証拠凍結が残る。
年次5/5期・interim8/8期間の充足だけでは完了とせず、`analysis_ready=false`を維持する。

## 実行準備への補足参照

同日の[補足レビュー接続](2026-09-28-financial-supplement-binding-outcome.md)で、
原ZIP・HTML member・採用済みpairとの照合を内部API・CLIへ追加した。
新しい統合bundleから固定レビューを参照できる。既存bundleは不変で、
最新開示・IR runtime接続・証拠凍結の完了とは扱わない。
