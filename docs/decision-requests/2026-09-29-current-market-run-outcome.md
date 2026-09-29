# 当日立会終了後の価格取得と市場run更新結果

## 修正した境界

通常runの価格必要終端は立会終了後の当日を含むが、
`prepare_market_evidence`は排他的終了日が東京当日を越える入力を拒否していた。
9月29日23時台の終了日9月30日も通信前に拒否され、必要な当日価格を取得できなかった。

承認済み`run_requirements.session_end`を使い、profile期間内の15:30以降だけ、
排他的終了日として東京当日の翌日を許可するよう修正した。
立会終了前・当日より先の価格・profile期間外は通信前に拒否する。
取得を許可することを、providerの当日データ到着や品質の受入とは扱わない。
既存の過去期間、CLIの終了日default、native取得制御は変更しない。

## 検証

新しい6境界ケースは、15:30前・ちょうど・後、先の日付、profile開始前・期限後を確認する。
拒否時provider呼出0回、許可時1回、排他的終了日・保存artifact・offline validateを確認した。
市場証拠19テストと既存市場run33テストが成功し、Mypy240ファイルと対象Ruff・formatも通過した。
独立レビューでブロッカーはなかった。
最初のテスト指定に存在しないファイルを含めたため収集できず、実在する対象へ訂正した。
新テストのrequested_periodフィールド指定も修正し、対象19件を再検証した。
repository wrapperの全体Pytest・Mypy・Ruff・format・Shell検査がすべて成功した。
コミットhookでは成功済み全体Pytestの重複だけを省略し、他の適用hookを実行する。

## 実取得と保持

所有者の継続指示と既存online承認の範囲で、主財務task向けに別root
`runs/market-refresh-20260929`を作成した。元run・P1組立は変更しない。
最初の価格処理は通信前ガードで失敗したため、その記録を保持して修正後に初回の実取得を行った。
価格はyfinance 1.7.0のnative historyを1回呼び、必要3年windowを取得した。
通貨・銘柄・品質が成功し、CSV原本と元取得metadataを保存・validateした。
これはlibrary-call単位の記録で、HTTP送信数は主張しない。

固定期間用の価格受入APIは当日を含む入力を拒否した。
既存市場run APIには当日の立会終了と取得時点を照合するraw preparation入力経路があるため、
価格を再取得せずこの経路で受入・品質検証を行う。固定期間APIの仕様は変更しない。

FXは共有runtimeで2023〜2026年bucketの取得を開始したが、
2026年bucketは成功・committed、次の2023年bucketでprovider_errorとなった。
2024・2025年は未送信で、自動再試行は行わなかった。
正本の失敗状態とlogical/physical identityを私的summaryへ保存する。
成功した取得や新たなFX受入とは扱わず、保存済みFXのreceipt・取得時刻をそのまま再利用する。

## 残件

新価格と保存済みFXから`runs/market-refresh-20260929/pending-run`を作成・validateした。
価格729行は9月29日までready_with_limitationsで欠損なし。
FXは9月25日まで、必要な9月28日の1日が欠損でpending。
正常換算727行を保持し、9月28日はFX欠損、9月29日はUTC日足未完了として確定しない。
元市場run全ファイルbytes不変を確認した。
manifest SHA-256: `934e58025d871f6b09d72bff500471609e7242b32003ca0812ab4ee2e2bf9fdb`。
新価格index SHA-256: `f32744161d53e8bdf6e3227715c8f6b8b5ed818c62ec181f5bec4245ca46aaaf`。

成功年と旧履歴の合成はモデル上可能だが、今回の途中失敗では2026年の元HTTP受信時刻が
メモリ上にだけ存在し、完全bundleのmetadataが未保存だった。
保存rawのpublication作成/commit時刻をreceived_atへ置き換えず、合成は保留した。
今後は年別成功時の受信receiptを永続化する実装が必要である。
当初の「最初の2026年で失敗」という説明は正本DBの照合後に訂正した。
年別途中成果物のないことから、未送信または原本失敗と推測しない。
新FX取得の失敗は未解消で、再試行せず保存する。
開示・ニュースcoverage、必要評価入力・計算、初期EvidenceSet凍結、
独立分析と完了直前の鮮度確認は未完了。`analysis_ready=false`を維持する。
