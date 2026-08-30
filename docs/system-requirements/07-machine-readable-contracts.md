# 機械可読契約と人間向け成果物の要件

| 項目 | 内容 |
| --- | --- |
| 文書状態 | Approved |
| 文書オーナー | リポジトリ所有者 |
| 承認者 | リポジトリ所有者 |
| 版 | 1.0 |
| 作成日 | 2026-08-31 |
| 承認日 | 2026-08-31 |
| 発効日 | 2026-08-31 |

## 1. 目的

詳細解析MVPで使用する設定、Agent間契約、機械可読成果物、検証および人間向け最終成果物の
共通形式と責務を定める。個別のtask、証拠、分析、レビュー、争点、監査および状態契約は、
本要件を共通基盤としてP1で定義する。

## 2. 形式と正本

| 対象 | 形式 | 要件 |
| --- | --- | --- |
| 人間が編集する設定 | YAML | 安全なJSON互換データへ読み込み、適用契約で検証する |
| 契約定義 | Pydanticモデル | フィールド、型および制約の正本とする |
| Agentへ提示する契約 | JSON Schema | Pydanticから生成し、手作業で編集しない |
| Agent間通信・機械可読成果物 | JSONテキスト | 交換形式および実行データの正本とする |
| 人間向け最終成果物 | Markdown | 版付きテンプレートに従い、検証済みJSONから生成する |

JSON SchemaはDraft 2020-12互換とし、生成に使用したPydanticおよび生成処理の版を固定する。
生成JSON SchemaをGit管理し、Pydantic契約からの再生成結果との一致をCIで検証する。

YAMLのcustom tag、pickleその他のPython固有バイナリ形式を、設定値、Agent間通信、契約配布または
永続成果物に使用しない。PydanticオブジェクトはPython process内の検証済み表現に限定する。

## 3. スキーマの識別と版

- 各契約は安定した`schema_id`と正の整数の`schema_version`を持つ。
- 公開済みの契約と生成JSON Schemaは不変とし、構造または検証規則を変更する場合は新しい版を作る。
- `schema_version`は、アプリケーション版、Policy版、データ版および`evidence_set_version`から分離する。
- `manifest`は各成果物へ適用した`schema_id`、`schema_version`および生成物のhashを記録する。
- 検証処理は`schema_id`と`schema_version`の完全一致でモデルを選び、未対応版を安全側に拒否する。
- MVPでは暗黙の型変換、暗黙の版変換および自動migrationを行わない。
- 未知フィールドは原則として拒否し、拡張を許可する場合は契約内に明示したフィールドへ限定する。

旧版の読み込みが必要になった場合は、元成果物を上書きしない明示的で決定論的なmigrationとして
要件、入力版、出力版および検証方法を別途承認する。

## 4. 共通検証wrapper

すべての設定と機械可読成果物は、後続処理または保存確定の前に共通検証wrapperを通す。
各consumerがPydantic、YAML parserまたはJSON parserを直接呼び出して検証を迂回してはならない。

共通検証wrapperは次を行う。

1. 入力を安全にparseする。
2. `schema_id`と`schema_version`からPydanticモデルを選ぶ。
3. 必須フィールド、型、列挙値、値域、未知フィールドおよび成果物内条件を検証する。
4. 証拠参照、成果物間整合、状態遷移、Policyおよびセキュリティの専用validatorを実行する。
5. 検証済みデータをJSONテキストとして保存し、後続処理へ渡す。
6. 検証失敗を共通エラー契約へ変換し、無効な成果物を自動採用しない。

parseまたは検証で入力値を暗黙に補完、推測または別の型へ強制変換しない。システムが生成する
受付時刻などの値は、契約で生成主体を明示し、入力値の暗黙補完と区別する。

## 5. 検証エラー契約

検証エラーは版付きの機械可読成果物として保存し、少なくとも次を持つ。

- `error_contract_version`
- `category`
- 安定した`code`
- 対象の`artifact_type`と、存在する場合は`artifact_id`
- 適用した`schema_id`と`schema_version`
- JSON Pointer形式の`instance_path`と、取得可能な場合は`schema_path`
- 人間向け`message`
- 秘密情報を含まない構造化`context`

`category`は少なくとも、schema、semantic、reference、state transition、policyおよびsecurityを
区別する。consumerは`message`の文面ではなく`category`と`code`で分岐する。CLIは簡潔な要約を
表示してよいが、秘密情報、入力全体または未マスクの値をmessage、contextまたはログへ複製しない。

## 6. 人間向け最終成果物

人間向け最終成果物は、既定では日本語のMarkdownとする。文書種別ごとの版付きテンプレートを
使用し、検証済み機械可読JSONと共通証拠から生成する。テンプレート版、入力成果物、入力成果物の
版とhashおよび生成結果のhashを`manifest`から追跡可能にする。

Markdown生成時に新しい事実、評価、証拠または推奨を追加しない。保存前に、評価、評価可否、
数値、確信度、欠損、反証および証拠参照が機械可読成果物と一致することを検証する。Markdownを
Agent間の正本にせず、Markdownから機械可読結果を再構成しない。

人間はMarkdownを最終閲覧・検収対象とする。機械可読JSONは、再処理、監査、障害調査および
Markdownとの対応確認に使用する実行データの正本として`runs/<task-id>/`へ保持する。

## 7. 契約生成と検証

各契約について、少なくとも次を固定fixtureで検証する。

- 正常なYAML入力またはJSON成果物を受理する。
- 必須項目の欠損、型違反、範囲外、未知フィールドおよび未対応版を拒否する。
- 無効な証拠参照、成果物間矛盾および不正な状態遷移を拒否する。
- Pydanticから再生成したJSON SchemaがGit管理された生成物と一致する。
- library固有エラーを共通検証エラー契約へ変換し、秘密情報を含めない。
- 同じ検証済みJSONとテンプレート版から再現可能なMarkdownを生成する。
- Markdownと機械可読成果物の重要項目の不一致を保存前に検出する。

## 8. P1・P5で定義する事項

- Pydanticモデル、生成JSON SchemaおよびMarkdownテンプレートの正確な配置と命名規則
- 共通検証wrapperとvalidator registryの公開インターフェース
- 検証エラーのcategory、codeおよび安全なcontextの完全な一覧
- 各契約の具体的なフィールド、条件付き制約、識別子の一意性範囲およびconsumer
- 各人間向け文書のテンプレート、必須sectionおよび意味的一致の具体的な検証方法

## 9. 参照資料

- [ADR-0002](../decisions/0002-use-pydantic-contracts-and-json-interchange.md)
- [言語と対象読者の方針](06-language-and-audience-policy.md)
- [成果物・保持・セキュリティの要件](05-artifact-retention-and-security.md)
- [個別株調査・スクリーニングシステム構成](../design/03-stock-research-system-architecture.md)
