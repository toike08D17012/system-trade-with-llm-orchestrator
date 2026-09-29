# 新市場runのP1接続と発行体binding境界

## 保存結果

9月30日00:22 JSTの市場runから、独立した価格・FX P1草稿を私的`runs/market-recovery-20260930/market-p1-draft`へ保存し、元市場runとのhash束縛を再生した。2,197レコード（source_metadata 5、raw 5、normalized 1,458、calculated 729）。草稿SHA-256は`6e1c8dfbf4bf6909570860ac37b2ee3e20bc1694c45a693f3560301abd3a978e`。必要価格729日と確定FX728日の別市場runを入力とし、換算値は再計算していない。pending、`analysis_ready=false`。

## 財務統合を止めた理由

既存財務と新市場runを直接つなぐと、財務の確認時刻は9月27日、市場の確認時刻は9月30日で一致せず、`financial_run_price_fx_task_or_time_mismatch`で拒否された。これは同一確認時点の要件であり、旧manifestの日付を変更して回避しない。

既存APIで保持財務原本を新市場と同じ時刻に別再生した。元原本・候補bytes・固定proposal・承認済みpolicy・調査期間を保持し、財務、mapping、採用結果まで各validatorで再生した。ただし元の発行体bindingの適用終了日は9月27日。9月30日時点では`issuer_binding_unresolved`となり、以前採用された6指標がすべて未受入へ変化した。値不変条件を満たさないので、比較結果と財務統合・P1組立は進めていない。再生時に発行体bindingの期間を自動延長せず、元値への置換も行っていない。

失敗境界と再生済み診断は私的`runs/market-recovery-20260930/financial-replay-boundary.json`に保存した。元市場・旧財務依存・旧P1組立の全ファイルinventory/hashは不変。新規EDINET通信は行っていない。

## 次に必要な判断

9月30日を含む発行体bindingを、現在のEDINETリスト等を確認した新しい根拠で明示的に採用するか、保持証拠に基づく適用期間の延長をownerが承認する必要がある。取得の有無と根拠が決まれば、正規財務再生で6指標の採用不変を検証し、比較結果、財務統合、P1組立を再生成する。旧調査期間は9月30日までの開示網羅性を示さないため、その確認も別途必要。

今回はsource・tests・設定の変更がなく、既存APIによる実保存証拠のprepare / validate、候補・元原本のbyte照合と、未受入時に統合を停止する確認を行った。成功済み全体pytest・Mypy・lintの再実行はしていない。
