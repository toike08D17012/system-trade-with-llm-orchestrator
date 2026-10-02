# トヨタの品質リスクに関する追加調査（2026年10月2日）

成長期待・購入判断の反証材料として、トヨタ米国法人が公表した安全リコールを確認した。下表の台数は各発表時点の概数であり、販売減少台数や費用見積額ではない。米国向け発表と北米向け発表を混ぜて合計しない。

| 公表日 | 対象と公表規模 | 内容と確認先 |
| --- | --- | --- |
| 2026-09-02 | 2026年式C-HR、北米で約1万台 | 回生ブレーキに関するソフトウェアの問題で駆動力を失うおそれ。制御ソフトを更新。[トヨタ米国法人](https://pressroom.toyota.com/toyota-recalls-certain-model-year-2026-c-hr-vehicles/) |
| 2026-08-06 | 2024～2025年式Tacoma TRD Offroad、米国で約4.8万台 | ショックアブソーバーの部品が腐食により脱落するおそれ。点検し、対象部品を交換。[トヨタ米国法人](https://pressroom.toyota.com/toyota-recalls-certain-model-year-2024-2025-tacoma-vehicles/) |
| 2026-08-06 | 2025～2026年式Camry、米国で約50.8万台 | 計器表示が消え、方向指示灯等も作動しないおそれ。制御ソフトを更新。[トヨタ米国法人](https://pressroom.toyota.com/toyota-recalls-certain-model-year-2025-2026-camry-vehicles/) |
| 2026-07-29 | カーテンエアバッグ補修部品、米国で約4,100個 | 対象部品が予定どおり展開しないおそれ。対象車両を点検し、該当部品を交換。[トヨタ米国法人](https://pressroom.toyota.com/toyota-recalls-certain-curtain-shield-airbag-csa-service-parts/) |

補修部品4,100個は車両台数ではない。各発表は対象範囲が後日変わり得るとしている。これらの公表だけから、費用負担、販売台数への影響、連結業績予想の修正を推定しない。財務上の重要性を評価するには、米運輸当局の届出・改訂履歴、修理実施率、保証引当金と品質関連費用の開示、後続の業績予想を照合する必要がある。

## 米運輸当局の届出との照合（2026年10月3日）

[NHTSAの公式API説明](https://www.nhtsa.gov/nhtsa-datasets-and-apis)に従い、キャンペーン番号から次の届出を取得した。APIの`Count`は対象の車種・年式の行数であり、キャンペーン数ではない。各行に繰り返される対象台数を加算しない。

| トヨタ発表 | NHTSAキャンペーン | 米国内の対象台数 | 照合結果 |
| --- | --- | ---: | --- |
| Camry約50.8万台 | [26V511000](https://api.nhtsa.gov/recalls/campaignNumber?campaignNumber=26V511000) | 508,354 | 2025～2026年式Camry Hybridの計器表示。ソフトウェア更新を予定。 |
| Tacoma約4.8万台 | [26V514000](https://api.nhtsa.gov/recalls/campaignNumber?campaignNumber=26V514000) | 48,280 | 2024～2025年式Tacomaと2024年式Tacoma Hybridのショックアブソーバー。点検し、必要な場合に交換。 |
| C-HR北米約1万台 | [26V570000](https://api.nhtsa.gov/recalls/campaignNumber?campaignNumber=26V570000) | 8,521 | 2026年式C-HRの米国内対象数。北米概数と米国内届出台数の地理的範囲が異なる。ソフトウェア更新を予定。 |
| 補修用エアバッグ部品約4,100個 | [26E050000](https://api.nhtsa.gov/recalls/campaignNumber?campaignNumber=26E050000)、[26E051000](https://api.nhtsa.gov/recalls/campaignNumber?campaignNumber=26E051000) | 2,381個、1,737個 | Tacoma用とTundra用の装備品を別々に登録。合計4,118個は公表概数と整合する。車両台数ではない。 |

APIの`ReportReceivedDate`は`06/08/2026`、`02/09/2026`のように表示され、トヨタの公表日と照らすと日/月/年の順である。月/日/年として引用しない。補修用エアバッグ部品の届出では、Tacoma向けは9月28日、Tundra向けは9月25日に安全上の危険を知らせる暫定通知を送付済みと記載されている。最終的な修理方法が利用可能になった後に追加通知を送る予定で、Tacoma向けは2026年第4四半期を見込む。したがって、暫定通知の送付を修理完了とみなさない。上表の届出は修理完了率や実費を示さず、業績影響は未判定である。

本調査は個別事案の原本確認であり、世界各地の安全リコールを網羅する索引ではない。Toyota Global Newsroomの既存索引だけでは米国法人の安全リコールを捕捉できないため、反証材料の取得対象に地域別公式サイトと規制当局を加える。正式なCoordinator receiptには未登録で、`analysis_ready=false`を維持する。
