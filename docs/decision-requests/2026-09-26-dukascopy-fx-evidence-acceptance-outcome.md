# Dukascopy UTC日足FXの実確認結果

2026-09-26、[承認された移行方針](2026-09-26-dukascopy-fx-migration-approval.md)に基づき、
共有Coordinator経由でUSDJPY Bid日足を取得した。

## 結果

- 2026年を最初に取得・検証・raw公開し、2023〜2025年を追加した。合計4 GET、再送なし。
- 論理要求`dukascopy-c5cbd0e4d19d4295ab9a13e2231ca6ed`は`succeeded`。
- APIの足timestampはUTC 00:00、`shift=86400000`ミリ秒。対象区間は翌日00:00まで。
  これは最終quote時刻や公表時刻を示すものではない。
- 年別原応答に実在する日足は合計1169行。要求期間外の足も原応答・正規化証拠に保持する。
- 2026-09-24のBid終値は158.837円/USD、2026-09-25は157.227円/USD。
  両方とも取得時点でUTC日足区間が終了していた。
- 価格731日の換算は**731/731**、`complete_with_limitations`。欠損は0日。
  同一日付ラベルによる参考換算であり、株価とFXの同時刻観測ではない。
- FX証拠はv2、`accepted_with_limitations`。全成果物で`analysis_ready=false`を維持する。
  旧BOJ証拠・729/731日の結合結果は上書きしていない。

## 来歴

| 応答年 | 取得完了時刻（UTC） |
| --- | --- |
| 2026 | `2026-09-26T13:55:39.414082+00:00` |
| 2023 | `2026-09-26T13:55:41.841022+00:00` |
| 2024 | `2026-09-26T13:55:44.257338+00:00` |
| 2025 | `2026-09-26T13:55:46.706948+00:00` |

原応答SHA-256：

- 2026年：
  `af2521c234f79f9d749d28eed696cf3ea680d4bbf482068a61ddd95d1d9ae26b`
- 2023年：
  `f76a4897bf8a777e14604e1ac0b46adfbcc42140697c4880bb7462d03a19337c`
- 2024年：
  `0b449eb9b76e871d54e55e9810a25b20c9dd8d915b7b71c0de985ec9198afe63`
- 2025年：
  `80b4bbd929008a325d56683a5b10f8cd123b0fd52a2ebd3eed20ae394cc36ea8`

保存先はGit追跡しないローカルdirectoryである。

- FX証拠：`runs/dukascopy-evidence/dukascopy-fx-7203-20260926`
- 結合結果：`runs/price-fx-dukascopy-7203-20260926`
- 共有runtime：`runs/dukascopy-shared-runtime/`
- FX索引SHA-256：`d5969ff8bef7ce6231e106c8f22e381c959ed25819623923428857e0a52fa93d`
- 結合索引SHA-256：`ae7c7bb70ff236d8f4263de7a947cc3b004503f4d792ac4d6fb76cfc0b1af1b2`

取得には`dukascopy-node 1.50.0`、Node `v22.23.3`、bridge version 1を使用した。
NodeはURL生成と保存bytesの解釈のみを行い、全物理送信をPython側で監査した。
Bid終値を採用し、ティック取得、Ask取得、中間値計算、別日値による補完は行っていない。

## 検証と残る範囲

- 新旧FX証拠・価格結合・設定の重点テスト57件が成功した。
- Dukascopy個別テスト23件が成功。許可外要求の送信前拒否と途中年の失敗も検証した。
- Node組込みテスト4件、原応答の再解釈、Decimal換算、改変拒否を検証した。
- `./scripts/pre-commit/checks.sh`でRuff・Pytest・Mypy・Shell検査がすべて成功した。
- Dukascopy v2と旧BOJ v1の実成果物を追加通信なしで再検証した。
- Dockerはこの環境に存在しないため、Docker image buildとコンテナ内動作確認は未実施。
  DockerfileにはNode/npmとlock固定依存の配置を追加した。
- 詳細解析runtimeの証拠凍結・通常実行の最新終端日判定は後続作業。
- 利用条件に関する利用者判断と提供者の許諾確認は承認記録のとおり区別する。
