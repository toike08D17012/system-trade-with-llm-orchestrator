# 最新重要開示候補9資料のruntime取得

## 対象と取得方式

2026-09-28に確認した最新候補6件に、6月18日の制度変更、FY2027 1Qの説明付き資料・Q&Aを加えた9PDFを固定した。
[株式情報一覧](https://global.toyota/jp/ir/stock/share/)と
[決算一覧](https://global.toyota/en/ir/financial-results/)から対象URLを確認した。
既存の8PDFとは対象が重複しない。

所有者の必要取得・公開資料内部処理許可に基づき、`toyota-ir`の承認・profile v2を追加した。
v1を変更せず、provider・origin・egressの同じ共有gate、同時1、開始間隔60秒以上、retry・redirectなしで取得する。
providerの数値rate limitや個別許諾は主張しない。原本の外部公開・Agentへの自動転送は行わない。

初回取得であり、過去採用済みcontent hashとの一致を証明するものではない。
固定URL・公表日・task・policy・approval/profile hashを要求fingerprintへ束縛し、受信bytesのhashを正本DBへ保存する。
PDF署名・サイズと隔離parserによる可読性を確認してからraw publicationをcommitする。

## 来歴と採用の境界

manifest kindは`toyota-disclosure-runtime-candidates`、operationは`disclosure-pdf`、承認・profile版は2。
`toyota-latest-disclosure-nine-pdf-20260928`のcampaign directory作成時に9枠すべてを消費する。
中断・失敗でも再開・retry、marker削除、別runtimeによる回避は行わない。

検証は正本DBのlogical request・physical attempt・publication・logical successと、campaignの全slot、
保存body・request・response・receiptのbytes・hashに照合する。PDFの可読性も再確認する。
候補kindを既存財務統合の`interim_runtime`へ渡すと拒否する。本文採用やEvidenceSet完成を主張せず、
`analysis_ready=false`を維持する。

## 検証状況

対象テスト43件は当初41件成功。空白PDF fixtureと、送信前gate拒否時にlogical resultが未確定である期待値を修正し、失敗2件の再検証が成功した。
財務統合での候補kind拒否も別途成功した。Mypy・Ruff・formatの対象チェックが成功した。
旧8PDF取得bundleと旧統合bundleのオフラインvalidateが成功し、旧統合manifest SHA-256は
`585d3e1dda3a5d7bccd8fddb854c9dcbff13a835722522e60bf5f2c5f9a9b536`で不変だった。
全体Pytestは1,228件すべて成功し、Mypy・Ruff・format・Shell format/checkも成功した。
最初の全体pre-commitは実行中の文書更新を検出して終了したため、文書確定後は成功済みPytestを重複実行せず、`SKIP=pytest ./scripts/pre-commit/checks.sh`で再確認し、残りの全hookが成功した。
コミットhookでも成功済みPytestの重複実行だけを省略する。
独立レビューは取得scope、kind/version束縛、共有gate、正本publication、内容採用との境界に阻害事項なしと判断した。

## 内容確認で残る事項

- 9月3日の取得実績は8月末基準。9月末の株数として使用しない。
- 8月4日の取得枠・消却予定と実行済み実績を区別する。
- 8月7日の処分価額決定を8月4日の処分決定へ関連付ける。処分日経過のみで実行済みと扱わない。
- 6月18日の制度変更が参照する2025年8月7日の原開示は今回の取得対象外。
- 6月15日の個別確定・連結見込の売却益と、後続決算での計上を照合する。既に反映された予想へ重複加算しない。
- 最新決算の予想改定・一時要因・為替等の前提とQ&Aを採用レビューする。

これらの本文レビュー、直近3年の開示網羅性、EDINET照合、必要指標の計算、source横断の最終鮮度、証拠凍結は残る。

## 実取得結果

取得は2026-09-29 JSTに実施した。policy名とcampaign名の日付は9月28日に固定した候補範囲を表し、取得日時ではない。

- 共有runtime: `runs/dukascopy-shared-runtime`
- 主task: `edinet-acceptance-7203-20260927`
- 候補bundle: `runs/toyota-disclosure-runtime-evidence/toyota-disclosure-candidates-20260928`
- 実送信9件、retry0件、最短開始間隔61.476974秒。全9件committed・成功。
- 最初の開始: `2026-09-28T15:02:38.870752+00:00`。最後の開始: `2026-09-28T15:10:52.067553+00:00`。
- 取得manifest SHA-256: `0539f55522a0df0839999874d7fa449a1efc619615a9633077567309cf428b23`。
- オフラインvalidate成功。既存8資料のcampaignを再実行していない。

本文抽出と初期文脈レビューを`runs/toyota-disclosure-runtime-evidence/context-review-20260929/`に保存した。
9件それぞれに原本hash・committed reference・抽出hash・該当ページと観察事項を記録した。
Web閲覧のページ位置を流用せず、保存原本から抽出した本文内で参照語句の所在を確認した。
これは文脈整理であり、採用契約・財務統合・EvidenceSet凍結への接続は未完了である。
レビューSHA-256: `18b69c7ca64b71830d0b790bc18b5f5aa961cbb3aff2883ccbc7a22841a156ca`。
