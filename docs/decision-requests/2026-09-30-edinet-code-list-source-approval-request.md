# EDINET公開コードリストの新source承認依頼

## 判断してほしい範囲

[調査結果](2026-09-30-edinet-code-list-issuer-research-outcome.md)で、金融庁公開ZIPの
2026年9月29日現在の一覧にE02144・72030・トヨタ自動車株式会社の一意対応を確認した。
このZIPを**内部の発行体binding再検証だけに使う独立source**として承認してほしい。
既存のEDINET API承認v2の固定日付・書類を変更する依頼ではない。

| 項目 | 提案する承認条件 |
| --- | --- |
| provider・URL | 金融庁EDINET閲覧サイトの[公式固定リンク](https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip)のみ |
| 用途 | 7203/E02144の発行体対応を9月30日確認時点の財務再生へ束縛する内部用途のみ |
| 送信 | 承認後、共有Coordinatorとgateによる明示的な1 GET。調査時の1 GETとは別に記録する |
| 認証・制限 | APIキー不要。並列1、最小間隔60秒、再試行・cache・fallback・日付総当たりなし |
| 保存 | 原ZIP、要求・応答・受信時刻・hash・承認版を私的rawへ保存。Git追跡と外部Agent転送なし |
| 受入 | ZIP単一CSV、日付・件数・hash、一意のE02144/72030、旧書類一覧bindingとの一致をオフライン検証 |
| 期限 | 9月30日を含む発行体確認に限定。以後の最新性は別途再確認 |
| 条件 | [金融庁公式仕様書](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf)の固定リンクと[利用規約](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html)の出典・加工表示を守る |

## 承認後の受入条件

新source approval/profileと固定URL取得・offline validatorを実装し、対象・設定hashと原本をテストする。
取得時刻以降の市場runと財務runを同じ確認時点で別版として再生成し、issuer bindingをコードリストに照合する。
承認済みの旧proposalと採用policyを変更せず、6指標の値・参照が不変である場合だけ
比較結果、財務統合、P1組立へ進む。候補や値が変われば停止して差分を報告する。

9月29日現在のコードリストは、9月30日の提出書類全件の網羅性を示さない。
旧調査期間、ニュースcoverage、最終鮮度確認、証拠集合の凍結も未完了のまま残す。
[実装計画](../agent-reports/plans/2026-09-30-edinet-code-list-issuer-binding-implementation-plan.md)に
ファイル責務と検証を記録した。

**承認対象:** 上表の固定URL・用途・1回の本番GET・私的保存・照合条件。
条件の変更が必要なら、その範囲を指定してほしい。

## 判断結果

所有者の継続指示により、上記の固定範囲を承認済みとして9月30日に1回取得した。
[実取得・照合結果](2026-09-30-edinet-code-list-issuer-binding-outcome.md)に記録した。
