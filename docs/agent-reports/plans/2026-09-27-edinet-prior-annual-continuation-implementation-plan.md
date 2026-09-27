# 実装計画: 保存一覧を使う過年度取得の限定続行

## 目的

[取得結果](../../decision-requests/2026-09-27-edinet-prior-annual-acquisition-outcome.md)の失敗・消費済みslotを保持し、未送信の3要求だけを続行する。2024-06-25の一覧を再取得しない。元の4要求上限を増やさず、一般的なretry・再開機能は作らない。

## 固定する入力と送信

- 入力原本hash: `5ceafc9ee61ee61ef3de8cf6911d7af26d3f8fa1cea875cb3865f1c770a3eca5`。
- 元campaign: `edinet-prior-annual-20260927`。slot-0の2024年一覧送信と失敗記録を要求する。slot-1以降、成功結果、別の続行開始記録があれば拒否。
- 原本・failure記録・source intentの日付・receipt時刻・元approvalを確認し、修正parserで再parseして、E02144／72030の2024年3月期年次120を一意に再選別する。候補IDは `S100TR7I` と一致する必要がある。
- 残り送信: 当該書類1回、2023-06-30の一覧1回、そこから選別した2023年3月期XBRL1回。計3要求。60秒間隔、retryなし。

## 変更方針

1. approval/profileの次版に、元一覧再送禁止・固定原本・残り3slot・一度限り続行を記録する。v2/v3を改変しない。
2. `edinet_campaign.py` に固定続行markerをatomicに作る経路を追加。slot-0は変更せず、元のslot-1～3を使用する。送信数を0へ戻す初期化や記録削除を行わない。再度の失敗・中断後はオンライン続行不可。
3. `edinet_revalidation.py` で固定原本の再検証を行い、`edinet_evidence.py` の書類から始める既存処理を再利用する。v2のretained-list形式・固定日検証と混同しない。過年度継続用の明示CLIオプションを追加する。
4. `financial_disclosure.py` のretained-list判定は現在v2の再検証へ結び付くため、新入力の版を明示して区別する。元の失敗一覧と取得済み書類の出典・approval版を保持し、異なる取得時刻を捏造して揃えない。旧bundleの再検証を維持する。
5. 模擬通信と保存証拠で検証し、承認後は実行、オフライン再検証、文書更新、コミットまで進める。

## 検証

`tests/preparation/test_edinet_campaign.py` と `test_edinet_evidence.py` に、初回一覧失敗からの限定続行で合計4要求となり最初の一覧が送信されない統合ケースを追加。原本・失敗記録・slot改ざん、対象ID変更、slot-1以降既存、2重起動、再失敗・再続行、旧v2再検証を検証する。実ネットワークはテストでは禁止し、wrapperによる関連Pytest・Mypy・Ruff、共有取得境界の全体検査を行う。

実取得後は3要求以下・原本hash・期間・文書ID・保存参照を確認する。taxonomy採用・財務充足・四半期資料取得は対象外。

## ブロッキング事項

元計画の「途中失敗後はオンライン再開しない」制約に対し、この固定3要求だけの続行を許可する判断が必要。承認前に追加送信しない。承認後は実装・検証・実取得まで個別の形式的確認を挟まない。

## ロールバック

続行経路を使用しなければ元campaignは停止したまま。コードを戻しても消費済みslot・失敗記録・続行markerは削除しない。

## Implementation Notes

- ユーザーが本限定続行を承認。approval/profile v4を追加し、旧v2/v3は保持した。
- 原本・failure・元approvalのhashに加え、元campaignのstarted.jsonとslot-0.jsonを固定hashで確認する。続行markerは排他的に作成し、slot-1〜3を使用する。
- financial入力version 2だけがこの固定保存一覧を受け入れ、元の失敗・取得日時・approval v3を保存する。旧version 1出力を変更しない。
