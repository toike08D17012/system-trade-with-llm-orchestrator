# 年次P1証拠ドラフトの接続結果

## 接続範囲

所有者の継続実装・検証済みコミット指示に基づき、採用済み年次値と内部計算をP1モデルへ接続した。
新たな通信・取得は行わず、既存の原本採用をオフラインで再検証した。
prepare / validateの`--annual-evidence-draft`は`--calculate-annual`併用を必須とする。
未指定時の従来出力は変更しない。

ドラフトはsource_metadata・raw・normalized・calculatedの4層を持つ。
正規化は年度×指標、計算は年度×計算指標の独立した枠にする。
原ZIPのbytes hash、取得時承認と元の取得・提出時刻を参照付けし、sourceの報告期間を比較期間で上書きしない。
訂正文書のlist期間がnullの場合は、再検証済みamended policyの報告期間を使用する。
first_available_at・updated_atは未知のままnullにする。

## 保存と来歴

`annual-evidence-draft.json`にP1レコード、canonical hash対象payload、artifact locatorを保存する。
値・原本・ドラフトはGit追跡対象外で、manifestとCLIには値を表示しない。
原本は依存bundleのdependency/path/hashで参照し、追加複製しない。
source IDへ元のFilingInput全体を束縛し、同じbytes・日時・承認でも別attemptを区別する。
同一取得identityの重複は共有し、正規化payloadには全観測・concept・期間・member/context/unit/ordinal・values hashを保持する。

JPY採用値は末尾ゼロだけを正規化し、精度contextで丸めない。
派生値のcontent hashは指標・期間・入力ID・根拠・処理版を含むcanonical payloadを対象とする。
欠損正規化枠もnullと理由を持ち、計算枠の入力参照へ残す。
最古年の前年比は未知の前期IDを作らず、`prior_period_outside_window`を維持する。
全レコードを実P1モデルで検証し、rawはsource_metadata、normalizedはraw、calculatedはnormalizedのみを参照する。

## 検証

取得時刻・提出時刻・原本/承認hash、等値重複・別attempt、採用値の桁保持、欠損・前期外、input層・payload hash・同一再生、改変拒否を対象テストで確認した。
既存統合テストはフラグ前提、再検証への対称な受渡し、成果物改変拒否、従来bytesの不変を確認する。
対象17件、全体Mypy233ファイル、新規ファイルを含む対象Ruff・formatが成功した。
repository wrapperによる全体Pytest・Mypy・Ruff・format・Shell検査もすべて成功した。
独立レビューで指摘された、同一metadataの別attemptを区別する取得identityの束縛を反映し、負例テストで確認した。

旧計算run validate、新run prepare / validateが成功し、旧manifest・inputs・計算成果物のbytesは不変だった。
新run: `runs/toyota-financial-p1-draft-run-20260929`。
56レコード（source_metadata 3、raw 3、normalized 30、calculated 20）を生成した。
実データの全正規化枠がconceptと原fact参照を持ち、全計算枠の指標・期間の入力selectorが対応することを確認した。
計算20枠の19値・1欠損、年次30項目・IR48項目は維持した。
manifest SHA-256: `3835e2936466b2452864c53d80d8b3b626f0bea64a5598aadb820380c5ef71a3`。
ドラフトSHA-256: `825a36e9088a72edaa0f141c3fd46ab56b2e3db92b65c8856d8b01510d5d1b03`。
従来計算成果物hashは`3f31c8c31550235592e494e4cfbb61e4848669257cbf200c1a3e7fff51cde7ef`のまま。
コミットhookでは成功済み全体Pytestの重複実行のみ省略し、他の適用hookを実行する。

## 残件

これは凍結済みEvidenceSetV1ではなく、私的なP1レコードの内部下書きである。
operator取得metadataがruntime DB未照合である既存制約と全source制約を保持する。
既存の`not_p1_calculated_evidence`は従来の内部計算ファイルに対する制約として残す。
今回別途生成したP1レコードのドラフトも、全証拠の準備完了や凍結を示さない。
IR・価格/FX・定性資料のP1接続、開示網羅性、最終鮮度、証拠凍結、Agent実行は別工程であり、`analysis_ready=false`を維持する。
