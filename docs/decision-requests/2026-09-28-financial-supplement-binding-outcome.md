# 訂正本文の補足レビュー接続結果

## 対象と結果

2026-09-28、承認済みの進め方と継続指示に基づき、固定S100RAR0の本文レビューを
既存の年次・IR統合runへ参照付けする内部API・CLIを実装した。
`--correction-review` に保存済み確認directoryを指定し、prepare/validateの双方で同じ依存を渡す。
原報告・訂正の取得pairと採用bundleの指定が必須となる。

レビュー自体の固定hash、採用文書IDとarchive hash、原ZIPと指定HTML memberのhash、
12組の数値比較、取得時刻とレビュー時刻を照合する。
主taskへの参照を追加するが、当時の定性判断を再実行したとは扱わない。
旧制約は履歴として保持し、補足確認を `correction_review` に区別して保存する。
原文・財務値は統合出力に転記しない。

## 保存した成果物

- 新しい統合bundle: `runs/toyota-financial-ir-correction-run-20260928/`
- manifest SHA-256: `7331d80b75fe88a6d930bd6541b830b747a301b8f93b4b030d78f9463359cec4`
- 固定レビューSHA-256: `80d14b5fb119026da1f66a55f2f0c1b569fc5702258e8e90c1cab580f9c997a3`
- 年次30項目・5/5期、IR48項目・8/8期間を維持する。
- 旧 `runs/toyota-financial-ir-run-20260928/` の再検証と、manifest・inputsのbyte不変を確認した。

新bundleのprepare/validateは、旧入力索引のhashと一致する13依存に固定レビューdirectoryを加えて実行した。
外部EDINET通信は0件で、既存原本を再利用した。

## 最新開示調査と残件

公式決算資料、IRトップ、有価証券・半期報告一覧、株式情報一覧を確認した。
株式情報一覧にはIRトップに表示されていない9月3日の自己株式取得状況がある。
最新性確認をIRトップだけで完了させない。
[候補調査](../agent-reports/research/2026-09-28-latest-disclosure-inventory.md)に確認範囲を記録した。
このローカル調査文書はGit追跡対象外で、公式出典は
[株式情報一覧](https://global.toyota/jp/ir/stock/share/)と
[決算資料](https://global.toyota/en/ir/financial-results/)を参照する。

IRはoperator取得で、共有runtimeのtask・request・raw publicationとの束縛は未完成。
開示候補の受入・訂正網羅性・指標計算・統合鮮度・EvidenceSet凍結・公開manifest接続も残る。
`analysis_ready=false`を維持する。

## 検証

対象Pytest14件、対象Mypy、Ruff、formatが成功した。
保存済み実証拠で旧bundle validate、新bundle prepare/validateが成功した。
全体品質確認ではPytest1204件、Mypy、Ruff、format、Shell format/checkが成功した。
ただし初回 `./scripts/pre-commit/checks.sh` は実行中の結果文書更新を検出し、
Pytest hookが「files were modified」で失敗扱いとなった（テスト自体の失敗は0件）。
文書更新後、成功済みテストの重複実行を避けた `SKIP=pytest ./scripts/pre-commit/checks.sh` が成功した。
追加EDINET取得・IR runtime取得は今回の実装範囲外で実行していない。
