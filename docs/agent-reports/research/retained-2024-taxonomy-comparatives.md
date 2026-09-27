# 保存済み2024年報告のtaxonomyと2023年比較値

## 結論

2024年報告S100TR7Iには、2024年・2023年3月期の6項目候補が揃う。既存採用ルールに対応するlinkbaseの関係も保存原本内で確認できた。ただし、標準taxonomy 2023-12-01版の定義が未確認のため、採用policyの作成・数値採用はまだ行わない。

2023年比較値は「2024年報告に掲載された2023年比較値」として扱う必要がある。2023年の原報告・訂正報告を照合済み、または訂正による差異なしとは判断できない。追加通信0回、既存の完全期間2/5期とanalysis_ready=falseは維持した。

## 対象証拠

[限定続行の結果](../../decision-requests/2026-09-27-edinet-prior-annual-continuation-outcome.md)を引き継いだ。

- financial bundle: `runs/edinet-evidence/edinet-prepared-c29a217d36c24e27b95972fdb52900ec`
- archive SHA-256: `fa53ae6f2b86ed9aff183c95466df69e6f4bef4253cf044cc48ae28cc35cdfb1`
- 以下のmemberはすべて `XBRL/PublicDoc/jpcrp030000-asr-001_E02144-000_2024-03-31_01_2024-06-25` を接頭辞とする。

| suffix | SHA-256 |
| --- | --- |
| `.xsd` | `3514af5bd3d5455ef5077abd8459fe9b18223df682143faffd6d530644e60cd2` |
| `_cal.xml` | `34d7ee301df301c8938562432f9feab043e02d2808fc5278a481ca72be3b32d9` |
| `_def.xml` | `54978d476bc3a71898f38fd02fa5699e0d0b8eb7d4c2eab7625ea624cbbc6b73` |
| `_pre.xml` | `f8b80807441ab440015c05a125c54a8d79ae6662092caa744a21f5796023fc18` |

## 確認できた内容

発行体XSDのTotalNetRevenuesIFRSは、monetaryItemType、duration、credit、非abstract、xbrli:itemとして宣言されていた。namespaceは2024年報告固有のもの。既存2026年報告の同名項目と、宣言の主要属性が対応する。

保存linkbaseの構造確認として、既存policyの年度・ファイル名・namespaceを2024年報告に置き換え、現在のmember hashで `read_taxonomy_members` と `verify_taxonomy` を実行した。変更はメモリ内の診断用データだけで、承認configへ保存していない。

| 項目 | 既存ルールが要求する関係数 | 診断結果 |
| --- | --- | --- |
| 売上収益 | 12 | 対応する関係あり |
| 営業利益 | 7 | 対応する関係あり |
| 親会社帰属利益 | 8 | 対応する関係あり |
| 資産合計 | 8 | 対応する関係あり |
| 資本合計 | 8 | 対応する関係あり |
| 営業CF | 8 | 対応する関係あり |

上記51は各項目ルールの関係数の合計であり、共通関係の重複を除いた件数ではない。診断では各ruleのverifiedがtrueとなったが、これは外部標準定義を既存policyから仮置きした条件付きの結果。正式なtaxonomy証明・新年度policy承認・採用成功とは扱わない。

`xbrl_taxonomy.py::_declaration` は外部XSDを取得せず、policyの承認済み宣言と発行体XSDのimportの一致を検査する。したがって、仮置きの宣言が正しいことをこの関数自体は保証しない。今回の重要な未確認点はここにある。

## 不足する標準定義

発行体XSDはjpigp、jppfs、jpcrpの2023-12-01版をimportしている。ZIP内にjpigp_cor_2023-12-01.xsdとjppfs_cor_2023-12-01.xsdは含まれず、確認したローカル資料にも当該版の定義は見つからなかった。

次の確認対象は、importで指定された公式schemaの必要な宣言だけに限定できる。

- `http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2023-12-01/jpigp_cor_2023-12-01.xsd`
- `http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2023-12-01/jppfs_cor_2023-12-01.xsd`

これは保存XSDから抽出した参照先であり、今回アクセスしたURLではない。取得時には公式配布元・応答同一性・必要な型／periodType／dimension宣言を検証し、外部DTDやリンク先を再帰的に取得しない。追加のschema参照が必要なら、その必要性を確認して範囲を決める。

## 2023年比較値と訂正の境界

保存された2023年一覧には、年次S100QZHYを親に持つ訂正S100RAR0がある。ただしこの一覧全体は別発行体の重複IDでparse未完了であり、2書類の本文も未取得。訂正内容や比較値との一致を判断できない。

今後の受入は、source_document_id=S100TR7I、報告期間=2024年3月期、fact期間=2023年3月期という別々の出典属性を保持する方針が適切。後年比較値であることを理由に、過去年次の訂正追跡要件や最新性確認を満たしたことにはしない。異なる報告から同じ期間の値が得られた場合の優先順位も自動で決めない。

## 次の実装への引継ぎ

1. 2023-12-01版の必要標準定義を公式資料で確認する。ローカル証拠だけではこの点が未解決。
2. 2024年報告の2期間を固定したpolicyと、後年比較値であることを保持する受入計画を作る。2026年policyの文字列置換だけで承認を作らない。
3. 既存2025・2026年の2期と集約する際は、提出報告期間・fact期間・訂正未確認を区別し、年次4/5期になっても全体のanalysis_readyを上げない。
4. 2022年は同じ6項目が揃わないため、別の資料選択が必要。未使用slotを無承認の別資料取得へ転用しない。

調査は既存parserとtaxonomy検証関数によるローカル診断のみ。コード・config・証拠bundleを変更していないため、テストスイートは実行していない。財務値・XBRL本文・credentialは出力していない。

## 2026-09-27 公式標準定義の追加確認

上記の「標準定義未確認」は以下の取得・照合で解消した。財務値の採用と訂正内容の照合は未実施のまま。

| 公式schema | bytes | SHA-256 |
| --- | --- | --- |
| [jpigp_cor_2023-12-01.xsd](https://disclosure2.edinet-fsa.go.jp/taxonomy/jpigp/2023-12-01/jpigp_cor_2023-12-01.xsd) | 689,047 | `6db3cbbd412097a9476f2a627d258910d779deae392bbd366088fcbcb52b62f3` |
| [jppfs_cor_2023-12-01.xsd](https://disclosure2.edinet-fsa.go.jp/taxonomy/jppfs/2023-12-01/jppfs_cor_2023-12-01.xsd) | 1,041,100 | `a509b5dfcf96d24743686b33506fd8ca5492580d97cce6859386ac0b711e331a` |

Web閲覧ツールではschemaを開けなかったため、ローカルHTTP取得で確認した。元の公式HTTPS URLから `disclosure2.edinet-fsa.go.jp` の同一schemaパスへ転送され、両方HTTP 200。取得時刻はそれぞれ2026-09-27T12:42:25.777849+00:00、12:42:26.034308+00:00。原本・要求URL・最終URL・hashは `runs/taxonomy-2023-12-01/` に保存した。schema内のimportを追跡する通信はしていない。EDINET認証API送信は0回で、財務取得campaignの残りslotは未使用。

採用ルールで必要な外部宣言20件（jpigp 18、jppfs 2）について、ID一意性、name、targetNamespaceを確認した。営業利益・親会社帰属利益・営業CFはmonetaryItemType/duration、資産・資本はmonetaryItemType/instant。いずれも非abstractのxbrli:item。表はhypercubeItem、連結／非連結軸はdimensionItem、連結memberはdomainItemTypeであることを確認した。

これにより、前回の構造照合で仮置きした標準宣言の必要属性は、当該2023-12-01版で裏付けられた。発行体ローカル宣言は保存XSDの検証結果を使用する。全taxonomyや会計上の意味の完全検証、訂正前後の数値比較を完了したという意味ではない。
