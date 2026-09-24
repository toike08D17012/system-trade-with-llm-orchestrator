# JPX/TSE current-list 利用承認リクエスト
## 解決済み（2026-09-24）

repository ownerの説明により、利用範囲を次へ確定した。本書の「人による判断待ち」とA〜Cの選択要求は撤回する。

- repository owner本人による私的・非商用の内部分析に限定する。
- raw XLSX、一覧全体、抽出データを第三者へ再配信せず、公開サービスやOSSデータセットとして提供しない。
- 取得は低頻度かつ直列で行い、並列取得、自動retry、direct fallbackを行わない。
- 同一snapshotを保存・再利用し、不要な再取得を避ける。
- 公開、第三者提供、商用化へscopeが変わる場合だけsource approvalを再審査する。

JPX自身が公式ページでファイルのdownloadと保存を案内していること、およびこの利用が本人限定の閲覧・保存・解析で
あることから、個別許諾の回答待ちを実装開始条件にしない。Phase 7Dをこの境界で継続する。


- 作成日: 2026-09-24
- 対象: Phase 7D 公式JPX/TSE銘柄source
- 状態: 人による判断待ち
- 推奨: A

## 結論

公式「東証上場銘柄一覧」をsourceとして実装する前に、利用許諾または代替sourceを確定する必要がある。

`DEC-16`〜`DEC-18`では同一覧をcurrent eligibilityの正本とすることを承認済みだが、調査した現行サイト規約は、
JPXの許諾がない二次利用・再配信を用途を問わず禁止している。また、高頻度・高負荷につながる可能性がある自動取得を
控えるよう明記している。したがって、技術的に取得可能であることだけを根拠にproduction adapterを有効化しない。

## 確認できた技術条件

- 公式ページ: `https://www.jpx.co.jp/markets/statistics-equities/misc/01.html`
- 現行ファイル: `/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx`
- 2026-09-24確認時の掲載snapshot: 2026年8月末
- 更新: 毎月第3営業日の午前9時以降。XLSXは同じ公開先で順次差し替えられる。
- 実測サイズ: 約224 KiB。承認済み64 MiB上限内。
- 形式: OOXML XLSX。主要列は日付、コード、銘柄名、市場・商品区分、33業種、17業種、規模区分。
- 認証: なし。
- この一覧だけでは売買停止、整理銘柄、上場廃止日、訂正履歴を確定できない。`DEC-18`どおり未知として扱う必要がある。

## 規約上の論点

2026-09-24に確認したJPX「サイトのご利用上の注意と免責事項」には、次が記載されている。

- 掲載情報の権利はJPXに帰属する。
- JPXの許諾がない場合、商用データ収集だけでなく、用途を問わず二次利用・再配信はできない。
- 生成AIによる学習・解析・生成でも、権利・利益の侵害やJPXに不利益となる行為を禁止する。
- 高頻度・高負荷につながる可能性がある自動取得等を控えるよう求める。

参照:

- https://www.jpx.co.jp/term-of-use/index.html
- https://www.jpx.co.jp/markets/statistics-equities/misc/01.html

本システムは個人の内部分析用だが、XLSXを自動取得・保存・解析してcanonical identityへ変換するため、
「閲覧」に留まらず二次利用に該当しないと断定できない。repository内にJPXの個別許諾記録も存在しない。

## 決めること

### A. JPXへ利用可否を確認し、回答までsourceをdisabledにする（推奨）

JPXの問い合わせ窓口へ、次の限定利用が許容されるか確認する。

- 個人による非商用・内部分析
- 月1回以下の低頻度取得
- raw XLSXはローカル限定で、Git・外部Agent・公開成果物へ渡さない
- 銘柄コード、市場区分、内国株式区分の抽出
- 派生結果も公開・再配信しない
- User-Agent、取得間隔、問い合わせ先が指定する追加条件を遵守

回答をsource approvalへ記録できるまで、JPX profileはdisabledのままにする。

影響: 法的・契約上の不確実性を最小化できるが、P3の公式eligibility joinは保留になる。

### B. 手動取得ファイルだけを入力にし、自動取得を実装しない

利用者がブラウザで取得したXLSXをローカル入力として渡し、offline parserとeligibility判定だけ実装する。

影響: 自動取得への懸念は減るが、二次利用の論点は残る。許諾なしで安全と断定できないため、Bを選ぶ場合も
利用者自身が利用条件への適合を確認し、その責任と利用範囲をsource approvalへ明記する必要がある。

### C. JPX一覧を不採用とし、別の許諾済みsourceを選ぶ

公式または契約済みの別sourceについて、料金、認証、自動取得、保存、解析、派生利用、外部Agent転送の条件を審査する。

影響: source選定とauthority hierarchyを再承認するため、Phase 7DとPhase 8のidentity joinを再計画する必要がある。
有償sourceになる場合は、契約とcredential管理も追加で必要になる。

## 回答方法

次のいずれかを回答する。

- `Aを承認`
- `Bを承認。確認済みの利用条件と許容範囲: ...`
- `Cを承認。候補source: ...`

Aの場合、JPXからの回答内容を受領後に、source approval/profile、credential-free intent、bounded XLSX parser、
production Coordinator transport、raw publication、offline E2Eをまとめて実装する。
