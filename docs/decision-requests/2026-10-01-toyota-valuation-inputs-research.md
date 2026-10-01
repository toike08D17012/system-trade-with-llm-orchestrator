# トヨタ評価指標の一次資料と時点の確認

2026年10月1日時点で、評価指標に必要な利益・配当・株数の出典を確認した。
当日終値は承認済みのYahoo Finance取得経路で候補を得たが、
現在の上場状態と取引所価格との独立照合が未受入であり、
当日までの自己株式変動も同じ時点で確定できていない。
したがってPER・PBR・予想配当利回りを購入判断用の確定値としては扱わない。

| 指標 | 確認した値・時点 | 一次資料 |
| --- | --- | --- |
| 2027年3月期第1四半期売上収益 | 13兆5,254億円、前年同期比10.4%増 | [決算短信](https://global.toyota/pages/global_toyota/ir/financial-results/2027_1q_summary_en.pdf) |
| 同営業利益 | 1兆634億円、前年同期比8.8%減。自動車事業の営業利益は前年同期比21.0%減 | [決算短信](https://global.toyota/pages/global_toyota/ir/financial-results/2027_1q_summary_en.pdf) |
| 2027年3月期会社予想 | 売上収益54兆円、営業利益3.4兆円、親会社帰属利益3.25兆円、基本EPS272.17円。営業利益は従来予想3.0兆円から上方修正 | [決算短信](https://global.toyota/pages/global_toyota/ir/financial-results/2027_1q_summary_en.pdf)、[説明資料](https://global.toyota/pages/global_toyota/ir/financial-results/2027_1q_presentation_2_en.pdf) |
| 2027年3月期配当予想 | 年100円（中間50円、期末50円）。2026年3月期実績は年95円 | [決算短信](https://global.toyota/pages/global_toyota/ir/financial-results/2027_1q_summary_en.pdf)、[配当方針](https://global.toyota/en/ir/stock/dividend/) |
| 2026年6月末発行済株式・自己株式 | 発行済14,594,987,460株、自己株式2,753,153,800株。差額11,841,833,660株はESOP保有株を自己株式に含む決算短信の定義による | [決算短信](https://global.toyota/pages/global_toyota/ir/financial-results/2027_1q_summary_en.pdf) |
| 2026年8月の自己株式取得 | 28,211,700株、864億9,417万5,900円。8月31日までの実績であり、10月1日現在の累計ではない | [取得状況](https://global.toyota/pages/global_toyota/ir/stock/share/commonstock_20260903_en.pdf) |
| 新しい自己株式取得枠 | 2026年8月5日～2027年8月4日、上限5億株・1兆円。上限は実行済み株数ではない | [取締役会決議](https://global.toyota/pages/global_toyota/ir/stock/share/commonstock_20260804_en.pdf) |
| 発行済株式数 | JPX会社詳細は2026年8月末時点14,594,987,460株と表示。自己株式控除後株数はこの欄だけでは得られない | [JPXの会社別情報説明](https://www.jpx.co.jp/listing/co-search/01.html?method=init)、[株式概要](https://global.toyota/en/ir/stock/outline/) |

承認済みの`yfinance`経路で2026年10月1日の取引終了後に取得した日足は、
始値2,900円、高値2,942.5円、安値2,894.5円、終値2,925円、
出来高26,937,700株だった。私的準備物は
`runs/market-recovery-20261001/price-through-today`に保存した。
会社予想EPS272.17円と配当予想100円を用いた単純な参考計算は、
予想PER約10.75倍、予想配当利回り約3.42%である。
元の準備物の判定は`prepared_with_gaps`で、
`jpx_snapshot_freshness_unconfirmed`と`trading_dates_unconfirmed`が残る。
翌10月2日に保存済み日足を研究用の取引日カレンダーと再照合し、
欠落・余分な日付はいずれもゼロだった。
`runs/market-recovery-20261001/price-acceptance`は
`accepted_with_limitations`（全9条件合格）として再現可能で、
対象は2023年10月1日～2026年10月1日の固定期間・私的分析に限る。
現時点の上場適格性、株価の独立検証、将来時点への鮮度保証は含まれず、
`analysis_ready=false`のままである。
JPX上場銘柄一覧を10月1日に再取得しても内容は8月31日版と同じであり、
この欠損を新しい取得時刻だけで解消しない。

利益の見方にも注意が要る。第1四半期の親会社帰属利益は前年同期比75.6%増だが、
営業利益は減少している。[決算短信](https://global.toyota/pages/global_toyota/ir/financial-results/2027_1q_summary_en.pdf)では
為替損益が前年同期の損失から利益へ転じ、持分法投資利益も増加している。
日野自動車など70社が連結範囲から外れた点も比較時に考慮する。
親会社帰属利益の増加をそのまま継続的な自動車事業の成長とみなさない。会社の
[予想修正説明](https://global.toyota/pages/global_toyota/ir/financial-results/2027_1q_presentation_2_en.pdf)では、
営業利益予想の上方修正に為替影響などが含まれる。

JPX掲載の[2026年8月7日訂正資料](https://www2.jpx.co.jp/disc/72030/140120260807513909.pdf)も確認した。
訂正対象は第1四半期決算短信の補足資料にある、親会社帰属利益の増減要因のうち
法人所得税費用・非支配持分に帰属する利益の表示である。
上表の売上・営業利益・通期予想・EPSは訂正資料に再掲された数値と一致する。
この増減要因を詳しく分析する際は8月4日版ではなく8月7日訂正版を使う。

次は10月1日の株価と出来高をJPX日報等で独立照合し、自己株式とESOPの最新残高、
予想EPSと発行済株式数の定義を同じ日付で照合する。
10月2日午前0時台に[JPX日報](https://www.jpx.co.jp/markets/statistics-equities/daily/)の
公開JSONを確認した時点では、月別一覧の最新は2026年9月で、10月分は未掲載だった。
金融事業の負債を含む連結企業価値指標は、自動車事業の評価と混同しない。
比較企業・シナリオ・反証条件も未確定で、`analysis_ready=false`を維持する。
