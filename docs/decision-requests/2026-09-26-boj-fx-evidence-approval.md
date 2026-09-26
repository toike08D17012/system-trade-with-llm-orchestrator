# 日銀FX証拠の送信制限・実装承認

2026-09-26、repository-ownerは提示された送信制限と日銀FX証拠の実装計画を承認した。
本記録はオンライン実確認の許可であり、実データの受入成功とは区別する。

## 承認範囲

| 項目 | 承認内容 |
| --- | --- |
| 系列・期間 | `FM08/FXERD04`、日次、要求月`202309`〜`202609` |
| 最初の実確認 | code APIへのGETを1回。失敗後の追加送信は自動実行しない |
| 用途 | ローカル内部分析。外部Agentへのraw転送・公開配布なし |
| 同時数・burst | 各1 |
| 内部送信制限 | 最小間隔60秒、rolling window 60秒につき1送信 |
| Retry-After | 有効な秒数またはHTTP日付を共有Coordinatorへ渡す |
| 未指定・不正なRetry-After | 既定cooldownを推測せず、当該実行を失敗として停止 |
| その他 | cache、自動retry、fallbackなし |
| HTTPX timeout | connect/write/pool各10秒、read30秒。処理全体の独自timeoutなし |
| 価格結合 | 同じ東京日付の調整前終値をUSDJPYで除算。Decimal精度28、ROUND_HALF_EVEN |
| 完了境界 | 実装・検証・単発実確認・結果記録・コミット。`analysis_ready=false`を維持 |

送信制限は日銀の公表上限ではなく、利用者が承認した内部運用値である。
設定の`boj-owner-rate-policy-20260926`は本記録を指す。
approval/profile v1を保持し、v2の実bytesと参照hashを固定して利用する。

## 公式資料の確認

2026-09-26に以下の公式資料を確認した。ここに記載する日付は閲覧日であり、
資料ファイルをローカル保存してhashを検証したとの主張ではない。

- [API利用マニュアル](https://www.stat-search.boj.or.jp/info/api_manual_en.pdf)：code指定GET、日次系列の月指定、JSON形式。
- [API利用上の注意](https://www.stat-search.boj.or.jp/info/api_notice_en.pdf)：過大なアクセスの制限、公開サービスの届出・出典表示。
- [FM08の日次系列一覧](https://www.stat-search.boj.or.jp/ssi/mtshtml/fm08_d_1.html)：`FXERD04`は東京市場の17時ドル円、円/ドル。
- [外国為替市況](https://www.boj.or.jp/en/statistics/market/forex/fxdaily/)：時系列検索の9時・17時ドル円系列はbid/offer中間値。

数値の公表rateを確認できないため、内部制限をproviderの数値仕様として記録しない。
価格の取引終了時点と17時のFXは同時刻ではない。本実装は日付の一致を検証するため、
終値を一律に15:30と固定せず、取引終了時点と17時の差として記録する。
