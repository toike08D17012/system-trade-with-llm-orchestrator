# 現時点FX不足の解消と市場run再検証

## 結果

2026年のDukascopy USDJPY Bid日足bucketを共有Coordinator経由で1 GET取得した。取得は成功し、実際のHTTP受信時刻を含む年別receiptを保存・再検証できた。2023年のprovider_errorを自動再試行せず、旧論理失敗とphysical記録を保持した。

9月30日00:22 JSTの別市場runは`ready_with_limitations`。必要終端は価格9月29日・FX9月28日で、必要価格729日・確定FX728日に欠損がなく、728日を換算した。9月29日のFXはUTC日足が未確定で必要対象外。全体は`analysis_ready=false`で、証拠集合は未凍結のまま。

価格は保存済み9月29日取得を再利用し、新規価格通信は行っていない。予定calendarは保存祝日CSVのhashを確認して9月30日まで延長した。元出典確認時刻は9月26日のままで、新しい出典観測や臨時休場の確認とは扱わない。

## 構成時刻の修正

初期計画では古い年と新しい年を既存v2へそのまま構成できると考えたが、実装確認で単一requested_atの制約が判明した。古い時刻では新しい終端が未確定となり、新しい時刻では古いreceiptが要求前受信となる。このため実装計画を追加し、任意の`year_requested_at`で各年の元要求時刻を明示した。

構成証拠では年キーの完全一致、offset付き時刻、全体requested_atと元要求時刻の最小値の一致、各年の要求と受信の順序を検証する。各年の元要求時点と対象区間で年別URLを再確認し、確定UTC日制約を維持する。通常取得は任意項目を出力せず、旧v2証拠の検証経路と完全bundle inventoryを保持する。

2023〜2025年の元receiptと新2026年receiptを変更せず、別FX bundleへ構成した。構成元index・metadataのhash、元要求期間、年別receiptと元要求時刻の対応を別provenanceへ保存した。全体requested_atは最も早い構成元要求時刻で、拡張期間の単一取得を意味しない。元receiptに記録済みのHTTP受信時刻を使い、publication時刻への置換は行っていない。

## 保存と照合

私的出力は`runs/market-recovery-20260930`。原応答・receipt・数値はGitへ追加しない。

- 新logical: `dukascopy-dd4d19f5854148f99253c19143b94356`。physical 1件成功、raw commit済み、logical成功。
- 市場manifest SHA-256: `1e1de474ba01d45b0f61e82f60964dd8d144d211fba80e494a7c116799407487`。
- 構成FX index SHA-256: `2d24b2cf43c7dc1b766690925bd588bd1fcb3a7123f41c74e29ebb417c28fa38`。
- 構成provenance index SHA-256: `371c972f4441c1037f3a19a5c7a589773ec41cff3c5d2de4eaa9d584d5b52d25`。

構成元の全bundleを再生し、元要求fingerprintとruntime DBのlogical/physical identity、raw commit、保存request/response/body/receiptへ各年を照合した。古い年の検証には元の完全metadataを使い、遡及的な年別receiptを保存していない。9月29日に受信時刻を失ったpartial原本は今回の構成に使っていない。

旧市場run、9月29日の価格・pending run、旧P1組立は全ファイルのhashとinventoryが不変。旧失敗logical・logical result・physical attemptsも不変を確認した。

## 検証と残課題

- 対象pytest: `test_dukascopy_fx.py`と`test_price_fx_run.py`の78件成功。
- Ruff lint・format: 変更したPython 2ファイルで成功。
- Mypy: 240ファイルで成功。
- 計画と実装の独立レビュー: PASS。
- 実保存証拠: 元FX・新FX・年別receipt・構成FX・構成provenance・市場runの再生と照合が成功。
- 全体チェック: `./scripts/pre-commit/checks.sh`でRuff lint・format、全pytest、Mypy、Shell検査がすべて成功。コミットhookでは成功済みpytestの重複のみ省略する。

次はこの市場runを入力に私的P1草稿と組立を更新する。三年開示・十二か月ニュースのcoverage、証拠集合の受入・凍結と最終鮮度確認は未完了。今回は財務・開示の通信を行っていない。
