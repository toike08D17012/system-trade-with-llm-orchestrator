# 複数 Coding Agent による個別株スクリーニングシステム

Codex、Claude Code、Antigravity CLI を組み合わせ、個別株の調査・比較・反証・レビューを支援するシステムの設計・実装リポジトリです。多数の銘柄から、中長期的な値上がり候補を人間が確認できる件数まで絞り込み、根拠と不確実性を追跡できるレポートとして出力することを目指します。

> [!IMPORTANT]
> 現在は詳細解析MVPのデータ・証拠準備を実装している段階です。契約検証基盤、offline設定検証CLIに加え、
> JPX銘柄確認、価格取得・正規化・保存、オフライン再検証、固定期間の価格証拠受入を内部API・CLIで実装済みです。
> 財務・為替・開示等を含む証拠集合の確定と、Agent分析・レビュー・最終レポートを接続するruntimeは未完成です。

## 目的

- 定量条件による一次スクリーニングで調査対象を絞り込む
- 財務、事業、バリュエーション、テクニカル、需給、ニュースを整理する
- Codex系とClaude系が、共通データを使って独立に分析する
- 強気材料だけでなく、リスク、反証条件、データ不足を明示する
- モデル間の不一致と判断理由を消さずに保存する
- 最終候補を、人間が比較・確認できるMarkdownおよび構造化データへまとめる

本システムは調査と意思決定を支援するものであり、投資判断や売買を自動化するものではありません。

## 対象範囲

### 対象

- 調査対象銘柄の受け付けと一次スクリーニング
- 財務・事業・バリュエーション・テクニカル・リスクの調査
- Codex系とClaude系による独立分析と、オーケストレーターとは異なるモデル系統による一次レビュー
- 重要な未解決争点に限定したAntigravity系の第三者監査
- 根拠、異論、監査結果、未確認事項を含むレポート生成

### 対象外

- 株式の自動購入・自動売却
- 証券会社や注文APIとの連携
- 人間の承認を伴わない投資判断
- 利益または株価上昇の保証
- 複数モデルの単純多数決による判定

## 想定アーキテクチャ

決定論的に処理できるデータ取得・正規化・指標計算・一次スクリーニングと、LLMが担当する定性分析・反証・レビューを分離します。

```mermaid
flowchart TD
    H["人間が調査条件を指定"] --> C["CLI・オーケストレーター"]
    C --> D["データ取得・正規化・一次選別"]
    D --> W["Codex・Claudeによる独立分析"]
    W --> S["分析結果の統合"]
    S --> R["オーケストレーターと異なる<br/>モデル系統による一次レビュー"]
    R --> J{"重要な争点が未解決か"}
    J -->|いいえ| F["最終レポート"]
    J -->|はい| A["Antigravityによる追加監査"]
    A --> P{"争点を処理できるか"}
    P -->|はい| F
    P -->|いいえ| E["人間へ判断を依頼"]
```

Antigravity系は通常の作業者や「第三票」ではなく、Codex系とClaude系だけでは解消できない重要争点を監査する役割です。証拠不足や機械的に確認できる誤りは、追加監査ではなく再調査または検証処理へ戻します。

## 初期MVP

初期実装では、次の範囲から開始します。

- 人間が指定した1銘柄を対象とし、自動一次スクリーニングは実行しない
- Codex系とClaude系の作業者を1つずつ利用する
- 各実行で、オーケストレーターと異なるモデル系統の一次レビューワーを1つ利用する
- システム独自の時間、token、費用、Web検索、再調査、合議往復、再レビュー、
  異なる争点数の固定上限を設けない
- 同一争点に対する有効なAntigravity論理監査は1回に限定する
- providerまたは契約プランの上限到達時は、中間成果物と未解決事項を保存して安全停止する
- MarkdownとJSONで結果を保存する
- 売買執行、証券口座連携、自動通知は実装しない

## 現在の状態

| 項目 | 状態 |
| --- | --- |
| 構想・アーキテクチャ | 設計資料あり |
| 詳細解析MVP要件 | P0要件6文書とP1契約基盤文書を承認済み |
| Pythonパッケージ・アプリケーション | 契約基盤、task入力準備、credential preflight、原子的保存、共有Coordinatorの永続制御・raw公開・single-flight終了連携を実装済み。詳細解析runtimeの統合は未完了 |
| 実行用CLI | 公開CLIは`config validate`。市場証拠準備・オフライン再検証・価格受入・Dukascopy為替取得と価格結合は内部module CLIで実装済み。詳細解析の調査・運用commandは未実装 |
| データソース・評価指標・閾値 | JPX銘柄検証、標準`yfinance`取得、価格正規化・保存・再検証・固定期間の受入を実装済み。通常実行の価格・FX必要期間判定を実装済み。財務・開示等を含む証拠集合の確定は未完了 |
| Pythonバージョン・依存関係 | Python 3.14、runtime・開発依存関係、品質ツール、ロックファイルを定義済み |
| テスト・lint・型チェック | 契約、設定、CLI、logging、task準備、credential、request coordinator、市場証拠準備・再検証・価格受入のテストあり |
| CI・Docker・devcontainer | Docker標準開発環境、GitHub Actions CI、VS Code devcontainerあり |
| ライセンス | MIT License |

2026-09-26の[実確認](docs/decision-requests/2026-09-26-jpx-market-evidence-acceptance-outcome.md)では、
`7203`の2023-09-26〜2026-09-25の731行がPA-01〜PA-09を満たし、
`accepted_with_limitations`となりました。現在の上場適格性等の制約を保持し、
詳細解析全体の開始可否は`analysis_ready=false`です。

共有Coordinatorは、leaderの論理結果を待機中のfollowerへ原子的に反映し、終了後の同じ要求を
新規leaderとして受け付けます。取消済みfollowerは復活させず、完了したfollowerの証拠参照は
元の取得taskに固定します。物理通信の終了だけでは論理結果を確定しません。
旧版で残った「終了済みleaderを参照するactive flight」は、lease取得・復旧・同じ要求の受付時に
保存済み結果と整合を確認して修復します。不整合は安全停止し、自動再送しません。
内部SQLiteはschema v8へ移行します。旧版へ戻す場合は移行前DBの退避が必要です。

## セットアップ

標準開発環境にはDockerを使用します。Dockerfile、Compose、`docker/run-docker.sh`、
ロックファイルを用意しており、次のコマンドで開発コンテナを起動できます。

```bash
./docker/run-docker.sh
```

コンテナ起動時に開発依存関係を同期します。offline設定検証以外の
個別株調査ランタイムと実行commandはまだ実装されていません。

VS Codeではリポジトリをdevcontainerで開けます。初期化時に`docker/.env`と
共有するCoding Agent状態のpathを準備し、作成後に`uv sync --frozen`とGit hookの
登録を実行します。workspaceはホスト側リポジトリのbasenameを使って配置します。

現在のCompose構成は、複数のCoding Agent CLI、認証状態、リポジトリを共有する
開発環境です。Codex、Claude Code、Antigravityの状態は、
ホスト側の既定pathまたは明示したpathからbind mountします。EDINET credentialは
`$HOME/.config/system-trade-with-llm-orchestrator/edinet-api-key`から単一fileとして
読み取り専用で既定mountします。`HOST_EDINET_API_KEY_FILE`でホストpathを変更できます。
このfileが存在しない場合、Docker wrapperとdevcontainerは起動しません。将来の株式調査ランタイムで
求める役割別の資格情報分離、読み取り専用化、
ネットワーク制限を満たす実行サンドボックスではありません。

`docker/run-docker.sh` は最初に `ghcr.io` の `:latest` imageをpullし、
pullに失敗した場合だけ、ホストのUID/GIDを反映したimageをローカルでbuildします。
開発用serviceは`app`です。`USER_NAME`の既定値は`kujira`で、`GROUP_NAME`を
省略した場合は同じ名前を使います。変更する場合は、build時と起動時に同じ値を指定し、
対応するimageを再buildしてください。entrypointはimageに記録したユーザーを使います。
Coding Agent CLIのinstallerを明示的に再実行して更新する場合は、次を実行します。

```bash
./docker/build-docker.sh --no-cache --progress=plain
```

base imageも含めて広く更新する場合は、`--pull` も指定します。

```bash
./docker/build-docker.sh --no-cache --pull --progress=plain
```

将来GHCRへのimage公開を開始した場合、起動時のpullによってローカルで更新した
`:latest` imageが置き換わる可能性があります。公開時にはpullの優先順位と、
image内のUID/GIDを改めて確認してください。

Pythonは3.14を使用します。`pyproject.toml` には契約基盤のruntime依存関係、
開発用のRuff、Mypy、Pytest、pre-commitと、VS Code上でNotebookを実行するための
`ipykernel` を定義しています。実行可能な個別株調査アプリケーションはまだありません。

### Coding Agent CLIのインストール補助

`scripts/` には、各提供元のインストーラーを取得して実行する補助スクリプトがあります。

```bash
./scripts/install-codex.sh
./scripts/install-claude-code.sh
./scripts/install-antigravity-cli.sh
```

これらは本プロジェクトをインストールまたは起動するスクリプトではありません。ネットワークから取得したスクリプトをシェルへ直接渡すため、実行前に内容と提供元を確認してください。各CLIの認証と利用設定も別途必要です。

## 使い方

版付き設定をnetwork、credential、Agent CLIへ接続せず検証できます。

```bash
stock-research config validate
stock-research config validate --config-dir ./config
python -m stock_research_llm_orchestrator config validate
```

`--config-dir`の既定値は、呼出元のcurrent working directoryにある`./config`です。
成功時は検証済みartifact数をstdoutへ出力します。失敗時はsecret-safeなmessageを
stderrへ出力し、設定内容の不正は終了code 3、pathまたはfileの問題は終了code 4を返します。

個別株の調査・運用commandは未実装です。現段階では、最初に
[システム要件文書の管理方針](docs/system-requirements/README.md)で承認済み要件と
変更手順を確認し、次の設計資料をレビューします。設計資料に記載された将来の構成は、
対応する実装と検証が完了するまで実装済みとして扱いません。

1. [個別株調査・スクリーニングシステム全体像](docs/design/01-stock-research-system-overview.md)
2. [個別株調査・スクリーニングシステム構想](docs/design/02-stock-research-system-concept.md)
3. [個別株調査・スクリーニングシステム構成](docs/design/03-stock-research-system-architecture.md)
4. [一次スクリーニングシステム構成](docs/design/04-primary-screening-architecture.md)
5. [一次スクリーニング判定部 要求事項](docs/design/05-screening-rule-requirements.md)

## リポジトリ構成とファイル配置

| パス | 配置する内容 | 現在の状態 |
| --- | --- | --- |
| `.agents/instructions/` | Python、Markdown、Shellなど、開発時に適用する言語別ルール | 定義済み |
| `.agents/skills/` | 調査、計画、検証など、Codexが開発時に使用するリポジトリ固有スキル | 定義済み |
| `.codex/agents/` | リポジトリ調査・設計・品質確認を担当するCodexサブエージェント定義 | 定義済み |
| `agent-sources/` | 実行Agent向けの役割別指示と入出力契約の原本 | P1 v1の共通指示、5役割別指示、合成定義あり |
| `config/` | 版付きPolicy・profile | 内部Policyと承認済み`yfinance` profileあり |
| `docs/design/` | システム全体像、構想、アーキテクチャ、一次スクリーニング設計、判定ルール要求 | 5文書あり |
| `docs/system-requirements/` | 詳細解析MVPの承認済みシステム要件 | P0要件6文書とP1契約基盤文書を承認済み |
| `docs/agent-reports/` | Coding Agentが生成する開発用の調査、計画、構成確認レポート | 追跡対象外のローカル生成物 |
| `reports/agent-reports/` | 銘柄調査・レビュー・監査の中間レポート | 空。形式は今後確定 |
| `reports/finalized-reports/` | 人間向けに確定した個別株調査・スクリーニングレポート | 空。形式は今後確定 |
| `docker/` | 標準開発環境のDockerfile、Compose設定、実行ラッパー | 構成あり |
| `scripts/` | Coding Agent CLIの導入補助、pre-commit・CI検証ラッパー | 構成あり |
| `schemas/` | 生成JSON Schema | P1フェーズ3までの28公開Schemaあり |
| `src/` | PythonアプリケーションとPython開発指示 | 契約基盤、offline設定検証CLI、task準備、credential preflight、保存・request coordinator基盤あり |
| `tests/` | Python実装の追跡対象テスト | 契約、生成Schema、設定、CLI、logging、task準備、credential、request coordinatorのテストあり |
| `AGENTS.md` | スクリーニング実行時の共通原則、安全境界、成果物配置、最小限のリポジトリ構成 | 定義済み |
| `pyproject.toml` / `uv.lock` | Python、依存関係、品質ツール | 設定、ロックファイル、CLI entrypointあり |
| `LICENSE` | リポジトリと将来の配布物に適用するライセンス | MIT License |

開発支援用の `docs/agent-reports/` と、スクリーニング結果用の
`reports/agent-reports/` は用途が異なります。前者はCoding Agentが必要に応じて
生成する追跡対象外のローカル成果物であり、後者には銘柄調査の実行結果を
保存します。継続的に管理する人間向け文書は、`docs/agent-reports/` 以外の
`docs/` 配下へ保存します。

`runs/` と `reports/` の2つの成果物ディレクトリは、機密情報やライセンス対象データの
誤コミットを防ぐため既定でGit追跡対象外です。最終化済み成果物を追跡する場合は、
公開範囲とデータ利用条件を個別に承認してから明示的に追加します。

設計上は、1回の実行に関する入力、元データ、分析、レビュー、争点、最終成果物を
`runs/<task-id>/` 配下へまとめる構成も想定しています。正本、移送、保持のP0案は
[成果物・保持・セキュリティの要件](docs/system-requirements/05-artifact-retention-and-security.md)
で承認済みです。検証済みbytesを内部準備directoryへ原子的にpublishするprimitiveはありますが、
manifest確定と完全な証拠一式を含む実行ディレクトリへの保存は未実装です。

## 価格証拠の準備

`preparation.market_evidence`は、保存済みJPX一覧の実bytesとhashを照合し、
東証上場内国普通株として検証した銘柄だけを標準`Ticker.history()`へ渡します。
取得メタデータ、library-returned CSV、正規化JSON、内部索引を新規directoryへ一括保存します。
既存成果物は上書きしません。Yahooの原HTTP応答を保存したものではありません。

次の内部検証用CLIは、東京日付の前日までの3暦年を取得します。出力先の親directoryは
事前に作成した信頼できる場所を使用し、同じ場所への並行実行は行わないでください。

```bash
./docker/run-docker.sh python -m stock_research_llm_orchestrator.preparation.market_evidence_cli \
  --allow-network --code 7203 --evaluation-policy-version 1 \
  --jpx-body runs/jpx-snapshot/body.bin \
  --jpx-metadata runs/jpx-snapshot/provenance.json \
  --output runs/market-evidence-001
```

JPX入力は既存の承認済みCoordinator取得経路で保存した一覧と取得記録を使用します。
`provenance.json`は`JpxSnapshotProvenance`形式で、`physical_attempt_id`、実取得時刻の
`retrieved_at`、本文の`sha256`を必須とします。sourceは`jpx`、承認版は1、参照先は
承認済み公式XLSXに固定しています。取得時刻をファイル更新時刻や保存完了時刻から推測しません。
このCLI自身はJPX一覧をダウンロードしません。

正規化では価格・出来高・配当・分割の欠損を補完せず、値を丸めません。調整済みとして
保持する値はproviderの`Adj Close`だけです。通貨は同じhistory応答のキャッシュから読み、
追加通信やティッカーからの推測は行いません。配当ごとの原通貨は独立に検証できていません。

取引日検証には、出典を確認したローカルの`TradingDates` JSONを`--calendar`で、
`CalendarProvenance` JSONを`--calendar-metadata`で指定できます。前者には包含的な`period`と
日付順・重複なしの`dates`、後者には`source_reference`、`retrieved_at`、本文の`sha256`を持たせます。
取引日資料がなければ網羅性は未確認です。テストの合成平日カレンダーを実市場の検証には使いません。

終了コード1は入力・取得・保存の失敗、2は不足を明示した準備結果の保存を表します。
現時点ではJPX一覧の鮮度確認が残るため、保存成功でも`prepared_with_gaps`を返します。
`index.json`の`price_quality_passed`と`issues`で価格の品質を確認できますが、
`analysis_ready`は常にfalseです。これは完全な証拠集合の凍結や詳細解析の完了を意味しません。
取得はprovider共有の間隔・cooldownを守り、自動待機・再試行はしません。

[2026-09-26の実確認](docs/decision-requests/2026-09-26-jpx-market-evidence-acceptance-outcome.md)では、
JPX一覧の5桁コードによる解析失敗を修正し、JPX検証済み`7203`で3年分731行の
取得・正規化・保存とhash照合に成功しました。続くオフライン再検証で予定取引日731日と
保存済み日付の一致、調査時点の最新掲載月との一致を確認しました。
承認済み方針では、出典付きローカルカレンダーとsnapshot時点の適格性を採用します。
現在の上場適格性の未確認を含めた制約は、新しい価格証拠受入記録で明示します。

### 保存済み証拠のオフライン再検証

`preparation.market_revalidation_cli`は外部通信せず、元の全ファイルと追加の調査資料を
新規directoryへ保存します。元の価格値・索引・品質issueは変更しません。
新しい索引には入力hash、検証方式の版、元taskの評価方針版、日付差分と月次観察の比較結果を記録します。
保存済み結果は`validate_market_revalidation`で元入力の場所に依存せず再検証できます。

```bash
./docker/run-docker.sh python -m stock_research_llm_orchestrator.preparation.market_revalidation_cli \
  --preparation runs/market-evidence-7203-20260926 \
  --calendar runs/calendar-research-20260926/calendar.candidate.json \
  --calendar-metadata runs/market-revalidation-inputs-20260926/calendar-metadata.json \
  --research-note runs/market-revalidation-inputs-20260926/research-note.md \
  --publication-observation runs/market-revalidation-inputs-20260926/publication-observation.json \
  --output runs/market-revalidation-7203-002
```

この例の入力はローカル調査成果物です。新しい入力は以下のモデルに従って用意します。

- `--calendar`: 全対象期間を含む既存`TradingDates`形式。
- `--calendar-metadata`: `ResearchCalendarMetadata`形式。
  `evidence_id`、`checked_at`、`calendar_sha256`、`research_note_sha256`、
  空でない`source_references`・`rules`を必須とし、`scope`は`research_only`です。
- `--research-note`: 出典と算出規則・確認限界を記した調査メモの保存時点のbytes。
- `--publication-observation`: 任意の`PublicationObservation`形式。
  `evidence_id`、公式一覧ページの`source_reference`、確認日`observed_on`、
  記録作成時刻`recorded_at`、`latest_published_month`（`YYYY-MM`）、
  `research_note_sha256`を持ちます。省略すると掲載月は未確認になります。
  確認時刻が不明な過去のWeb調査に、推測した時刻を付けません。

入力と出力は同じ利用者が管理する通常ファイル・directoryを使用し、同時変更を避けます。
symlink、入力内の出力先、既存出力先、hash不一致、カレンダーの期間不足を拒否します。
終了コード1は入力・保存失敗、2は残る制約を含む記録の保存成功です。
日付や掲載月の不一致も差分として保存されるため、2だけで一致を判断せず索引を確認してください。

日付照合は提示された予定取引日との比較です。臨時休場や個別停止、価格値の正しさは
独立検証していません。掲載月の一致は手動観察日に限った結果で、同月の差替え版や
再検証日現在の上場状況を保証しません。新記録も`revalidated_with_gaps`、
`analysis_ready=false`を維持し、本番source承認や証拠集合の凍結を代替しません。

### 承認済み方針による価格証拠の受入

`preparation.price_acceptance_cli`は元CSVと正規化値をDecimal精度で照合し、
[承認済み受入条件](docs/decision-requests/2026-09-26-price-evidence-acceptance-draft.md)の
PA-01〜PA-09を再評価します。価格を再取得せず、入力一式と条件別の結果を別directoryへ保存します。

```bash
./docker/run-docker.sh python -m stock_research_llm_orchestrator.preparation.price_acceptance_cli \
  --preparation runs/market-evidence-7203-20260926 \
  --calendar runs/calendar-research-20260926/calendar.candidate.json \
  --calendar-metadata runs/market-revalidation-inputs-20260926/calendar-metadata.json \
  --research-note runs/market-revalidation-inputs-20260926/research-note.md \
  --output runs/price-acceptance-7203-002
```

カレンダーとメタデータ・調査メモは前節と同じ形式です。元メタデータの`research_only`は
取得当時の区分として保持し、今回承認されたローカル照合用途で使用します。
`--approval-root`の既定値は`config/source-approvals`です。JPX v1とyfinance v3の承認記録を
同梱し、状態、対象source・版、内部利用の範囲、発効日と再確認期限を検証します。
同梱した承認は判定時点の記録であり、後日の利用許可を保証するものではありません。

既知の重大な矛盾を伝える場合は、`--known-conflict`と`--conflict-evidence`を対で指定します。
前者は`KnownPriceConflict`形式で、`evidence_id`、`source_reference`、`checked_on`、
`security_code`、`category`、後者の`evidence_sha256`を持ちます。
区分は`delisted`、`outside_target_market`、`identity`、`currency`、`dates`、`values`、
`provenance`です。入力がない場合も、現在の上場適格性を確認済みとはしません。

終了コード0は価格証拠の受入、2は`pending`、1は技術エラーです。
標準出力と新しい`index.json`に状態、条件別結果、制約、用途制限を記録します。
現在のnative取得表には原HTTP未保存等の制約があるため、正常時は
`accepted_with_limitations`です。JPYやタイムゾーンが不明な場合は受け入れません。
配当ごとの原通貨の未確認は制約として保持し、それを必要とする計算を保留します。

判定は明示した固定期間に限ります。新しい解析への利用時は必要な終端日を再確認します。
旧索引の未確認issueは書き換えず、価格証拠を受け入れても`analysis_ready=false`を維持します。
`validate_price_acceptance`で、同梱した入力から条件別結果と索引全体を再現・検証できます。

## 為替日足証拠と価格の日付ラベル結合

内部CLI `preparation.fx_evidence_cli acquire`は、共有Coordinator経由でDukascopyの
USDJPY Bid日足を年単位で取得し、`dukascopy-node 1.50.0`でオフライン解釈します。
UTC日足終値を使い、東京市場の終了時刻への整合やtick取得は行いません。
内部制限は同時数1・burst 1・最小間隔2秒・rolling 60秒に30送信です。
cache・自動retry・fallbackはなく、失敗時は後続年の取得を止めます。
[承認と利用条件の判断](docs/decision-requests/2026-09-26-dukascopy-fx-migration-approval.md)を保持します。

Docker imageにはNode/npmと固定依存を`/opt/dukascopy/`へ配置します。
ホスト実行ではNode 22と`npm ci --prefix node/dukascopy --ignore-scripts`が必要です。
Node bridgeは通信を禁止し、送信・permit・原応答公開・論理結果はPython側が管理します。
共有runtime directoryを継続使用してください。以下は明示的なオンライン取得の例です。

```bash
./docker/run-docker.sh python -m stock_research_llm_orchestrator.preparation.fx_evidence_cli acquire \
  --allow-network --config config --runtime runs/dukascopy-shared-runtime --runs runs/dukascopy-evidence \
  --destination dukascopy-fx-7203-20260926 --task-id dukascopy-acquisition-7203-20260926 \
  --start 2023-09-26 --end 2026-09-25
```

保存済み価格とFXの結合には通信しません。

```bash
./docker/run-docker.sh python -m stock_research_llm_orchestrator.preparation.fx_evidence_cli join \
  --prices runs/price-acceptance-7203-20260926 \
  --fx runs/dukascopy-evidence/dukascopy-fx-7203-20260926 \
  --output runs/price-fx-dukascopy-7203-20260926
```

株価の日付ラベル`D`にUTC日付ラベル`D`の確定したBid終値を対応させ、
調整前JPY終値を除算します。Decimal精度28・ROUND_HALF_EVENを固定した参考値です。
最終quote・公表時刻は推測せず、足のtimestamp・対象区間・受信時刻を保存します。
欠損を別日やBOJで補完せず、原応答にない補完足と未確定足を採用しません。
v2証拠にはraw・設定bytes・正規化結果とhashを保持し、保存後に再検証します。

旧BOJ v1証拠と価格FX索引の再検証・結合互換は維持します。
`revalidate`は旧BOJ未受入診断の互換コマンドであり、Dukascopy取得には使用しません。
[Dukascopy初回確認](docs/decision-requests/2026-09-26-dukascopy-fx-evidence-acceptance-outcome.md)では
4 GETで731/731日を換算した。旧BOJの[729/731日という結果](docs/decision-requests/2026-09-26-boj-fx-evidence-acceptance-outcome.md)は
歴史的記録として保持します。

終了コード0は保存成功を示し、`incomplete`なら欠損が残っています。
技術エラーは1で停止し、既存出力先・入力との重複・symlink・改変を拒否します。
実rawはGit追跡しないローカル`runs/`に保存します。
財務・開示との接続、証拠集合の凍結は未実装で、
`analysis_ready=false`を維持します。

## 通常実行向けの価格・FX準備

内部module CLI `preparation.price_fx_run_cli` の `plan` / `prepare` / `validate` は、
保存済み証拠だけを使用します。`plan` は必要期間を保存し、`prepare` は価格・FXを
その期間で評価して内部準備manifestを保存します。新規取得・自動バックフィルは行いません。

```bash
./docker/run-docker.sh python -m stock_research_llm_orchestrator.preparation.price_fx_run_cli prepare \
  --task runs/current-task.json --config config \
  --calendar runs/current-calendar.json \
  --calendar-metadata runs/current-calendar-metadata.json \
  --research-note runs/current-calendar-note.md \
  --prices runs/price-acceptance-7203-20260926 \
  --fx runs/dukascopy-evidence/dukascopy-fx-7203-20260926 \
  --output runs/current-price-fx-preparation

./docker/run-docker.sh python -m stock_research_llm_orchestrator.preparation.price_fx_run_cli validate \
  --input runs/current-price-fx-preparation
```

`current-*` 入力は、その実行のtaskと根拠・hash付きカレンダーを事前に用意します。
`plan` は同じ共通引数を取り、`--prices` / `--fx` は不要です。
`--checked-at` でタイムゾーン付き評価時刻を指定できます。省略時は現在時刻です。
価格入力には受入済みv1または保存済み市場証拠bundleを使用できます。

価格終端は終了済みXTKS営業日、FX終端は同じ日付ラベルのUTC日足が終了した営業日です。
当日のJPY価格を利用できてもFX日足が未確定なら、その日のUSD換算を保留します。
必要終端を欠損日に合わせて下げず、利用可能終端と欠損日を別に記録します。
カレンダーが評価日まで届かない場合は `pending` です。
承認済みローカル市場profile v2の適用期間は2026-09-27以上・2026-12-27未満です。

終了コード0は保存成功で、状態は `ready_with_limitations` または `pending` です。
技術エラーは1です。既存宛先・改変・symlink・同一rootへの競合公開を拒否します。
入力と公開先は同じ利用者が排他的に管理し、入力の同時変更を避けてください。
元証拠・元taskの来歴と新しい準備generationを保持します。
公開 `ExecutionManifestV1` / 凍結済み `EvidenceSetV1` への昇格は行わず、
常に `analysis_ready=false` です。取得工程の当日対応と自動接続は後続作業です。

## 財務・法定開示のオフライン準備

内部CLI `preparation.financial_disclosure_cli` は、保存済みEDINET一覧・XBRL書類を
再parseし、銘柄別の書類索引、訂正・取下げ、不足期間、context/unit付き財務候補を保存します。
初版は財務指標の正規化・最新公表分の確認・発行体IR接続が未完成のため、常に
`pending` / `analysis_ready=false` です。オンライン取得や外部Agent転送は行いません。

入力directoryは以下の形式で事前に組み立てます。実行DBからの自動exportは未実装です。

| 入力 | 内容 |
| --- | --- |
| `task.json` | 既存 `DetailedAnalysisTaskV1` |
| `inputs.json` | `FinancialInput`。version=1、checked_at、survey_period、issuer、acquisitions |
| `evaluation-policy.yaml` | taskの評価policyに対応する設定bytes |
| `approval.yaml` | 現在のEDINETローカル利用承認 |
| `acquisition-approvals/<key>.yaml` | 各取得時の承認bytes |
| `raw/<key>/` | 既存raw bundleの `body.bin` / `request.json` / `response.json` / `receipt.json` |
| `price-fx/`（任意） | 同じtask・評価時刻の価格・FX準備bundle一式 |

`acquisitions` の各項目はkey、publication（`RawPublicationIntent`）、source_intent
（秘密値を含まない `CredentialFreeSourceIntent`）、retrieved_at、acquisition_approval_sha256です。
これらはoperatorによる取得記録のexportであり、DBへのcommit確認を証明するものではありません。
`issuer` はsecurity_code、edinet_code、provider_security_code、applicable_period、
list_key、list_sha256を持つ根拠付き対応表です。不明ならnullとし、対応未確定を保存します。
キー値そのものや認証ファイルは入力に含めません。

合成入力を構築する例は `tests/preparation/test_financial_disclosure.py` の `inputs` fixtureです。
用意した入力には次のように実行します。

```bash
python -m stock_research_llm_orchestrator.preparation.financial_disclosure_cli prepare \
  --input runs/financial-input --output runs/financial-prepared
python -m stock_research_llm_orchestrator.preparation.financial_disclosure_cli validate \
  --input runs/financial-prepared
```

上記はPython環境のあるdevcontainer内の例です。終了コード0は保存・再生成功、1は技術エラーです。
候補はraw/member/concept/context/unit/ordinalのidentityを保ち、単位変換や四半期差分計算をしません。
書類の期間件数は観測値であり、年次5期・四半期半期8期間・開示3年の充足認定ではありません。
元証拠は変更せず、既存宛先・symlink・競合公開・改変を拒否します。

## 開発への参加

Pythonの開発前に `src/AGENTS.md` と `.agents/instructions/python.md` を
確認してください。MarkdownまたはShellを変更する場合は、対象ファイルに対応する
`.agents/instructions/` も確認します。Pythonコード、設定、スクリプト、実行動作へ
影響する非自明な変更は、実装計画を作成し、承認後に実装します。

実装時は、次を優先します。

- 再現可能な処理は通常のPythonコードへ寄せる
- LLMに未提示の数値や事実を推測させない
- 事実、推論、仮説を区別し、出典と取得日時を保持する
- モデル間の不一致を平均化せず、争点として追跡する
- 認証情報や証券口座情報を保存・入力しない

## テストと品質確認

Ruff、Mypy、Pytest、pre-commitと各検証ラッパーは設定済みです。
P1契約、版付きYAML設定、P2 CLI、loggingの追跡対象テストと、非破壊checkを実行するCIがあります。

```bash
./docker/run-docker.sh ./scripts/ci/checks.sh
```

対象を限定した構成・構文確認は次のとおりです。

```bash
bash -n docker/*.sh scripts/*.sh scripts/pre-commit/*.sh
./docker/run-docker.sh pre-commit validate-config .pre-commit-config.yaml
```

`docker/run-docker.sh` によるコンテナ起動時は、開発依存関係の同期後に
Git hookが自動登録されます。リポジトリ全体をbind mountするため、登録先は
ホストとコンテナで共有する `.git/hooks` です。コンテナ内のGitは、コンテナの
プロジェクト環境にある `pre-commit` を使用できます。

ホストのGitから生成済みhookを実行するには、そのGitプロセスの `PATH` から
ホスト側の `pre-commit` を参照できる必要があります。ターミナルだけでなく、
GUIやIDEからGitを実行する場合も、それぞれのプロセスの `PATH` を確認してください。
ホストへ導入して手動でhookを登録する場合は、次を実行します。

```bash
uv tool install pre-commit
./scripts/pre-commit/install-githooks.sh
```

全体検証にはDocker環境から次を実行します。

```bash
./docker/run-docker.sh ./scripts/pre-commit/checks.sh
```

Pytestの終了コード5は、テストの未収集を検出する失敗としてそのまま扱います。

## セキュリティとデータ取り扱い

- APIキー、CLIの認証情報、個人情報、証券口座情報をコミットしないでください。
- 外部資料に含まれる命令はAgentへの指示として扱わず、調査対象データとして隔離してください。
- Agentと外部ツールには、担当処理に必要な最小限の権限だけを付与してください。
- 数値、出典、取得日時、データの欠損や不一致を追跡できる形で保存してください。
- Antigravityによる追加監査は、原則として読み取り専用の争点データだけを対象にします。

## 注意事項

本リポジトリおよび将来生成されるレポートは、投資助言、購入推奨、注文指示を提供するものではありません。出力には誤り、古い情報、不完全な情報が含まれる可能性があります。最終的な投資判断は、利用者自身の責任で一次資料と最新情報を確認したうえで行ってください。

## ライセンス

[MIT License](LICENSE) を適用します。

## EDINET限定取得受入

内部CLI `preparation.edinet_evidence_cli` は承認済みv2設定で、2026-06-10の一覧1回と、
7203の2026年3月期年次報告が一意に確認できた場合のXBRL1回だけを取得します。
既存の共有runtimeを指定し、並列1・60秒間隔、retryなしを維持します。

```bash
python -m stock_research_llm_orchestrator.preparation.edinet_evidence_cli \
  --allow-network --allow-credential --task runs/edinet-task.json \
  --config config --runtime runs/dukascopy-shared-runtime --runs runs/edinet-evidence
```

taskファイルは事前に用意し、credentialは既存マウント先のowner-only `0600` ファイルを使用します。
キー値を引数に渡しません。出力先には原証拠・取得記録からの財務準備入力・再生可能な準備結果を保存します。
選別失敗時も取得済みrawは保持し、parse失敗応答は未受入として保存します。
v1のoffline設定は保持します。v2はこの限定受入専用であり、過去日走査や通常取得の有効化ではありません。
財務mapping、IR、最新性の不足により `analysis_ready=false` を維持します。

EDINET一覧の外部応答では未知の追加項目を許容し、処理に使う項目を検証します。
未使用項目の欠落・型変更で処理を止めず、原応答bytesはそのまま保持します。
書類IDの重複・件数不一致・採用書類の必須情報不備や、取得可否の不明な区分は引き続き検出します。
内部の保存契約の厳密性は維持します。

一覧取得後にparse失敗した保存結果は、`edinet_evidence_cli` の `--retained-list` に指定して再開できます。
`body.bin` と元の `failure.json` をhash・要求日・取得時刻で照合し、再parse・対象選別後、
書類だけを1回取得します。一覧を再送せず、元の失敗記録は `retained-list/` に保持します。
この来歴を通常の成功した一覧取得へ置き換えません。

### XBRL entityと財務mapping候補のレビュー

内部CLI `preparation.financial_mapping_cli` は、検証済み財務bundleと6項目のQName対応案から、
entity照合、期間・単位・dimensionによる除外、同値重複・異値競合のレビュー結果を保存します。
旧bundleは変更せず、原manifestのhashを参照する別の出力を作成します。
追加通信・認証は不要です。出力には財務値・本文を含めず、候補参照と診断情報だけを保存します。

```bash
python -m stock_research_llm_orchestrator.preparation.financial_mapping_cli prepare \
  --source runs/financial-prepared \
  --proposal config/financial-mapping/7203-2026-draft.json \
  --output runs/financial-mapping-review
python -m stock_research_llm_orchestrator.preparation.financial_mapping_cli validate \
  --source runs/financial-prepared --input runs/financial-mapping-review
```

同梱draftは候補レビュー専用です。7203の2026年3月期については、別の承認済みpolicyで
連結範囲と提出者独自の売上概念を検証する限定採用処理を追加しました。
候補レビュー自体は従来どおり `analysis_ready=false` を維持します。
[対応案・検証結果](docs/decision-requests/2026-09-27-financial-mapping-review.md)を参照してください。


### 承認済み財務6項目のローカル採用

内部CLI `preparation.financial_acceptance_cli` は原bundle・候補レビュー・承認済みpolicyを照合し、
必要なtaxonomy関係を検証して、6項目の正規化値と全fact参照を新しいローカルbundleに保存します。
原証拠・v1候補レビューは変更しません。再検証には両方が必要です。
出力の `values.json` はローカル専用で、標準出力や `diagnostic.json` に財務値は含めません。

```bash
python -m stock_research_llm_orchestrator.preparation.financial_acceptance_cli prepare \
  --source runs/financial-prepared --review runs/financial-mapping-review \
  --policy config/financial-mapping/7203-2026-approved.json \
  --output runs/financial-accepted
python -m stock_research_llm_orchestrator.preparation.financial_acceptance_cli validate \
  --source runs/financial-prepared --review runs/financial-mapping-review \
  --input runs/financial-accepted
```

同梱policyは承認された7203の書類・原archive・期間への限定適用です。
他の書類やdraftのstatus変更では採用できません。追加通信や認証は不要です。
個別項目が `accepted` でも、期間不足・IR等が未解決のため、全体は `pending` / `analysis_ready=false` です。
[採用結果と残る作業](docs/decision-requests/2026-09-27-financial-mapping-adoption-outcome.md)を参照してください。

### 財務採用結果を実行準備へ接続する

`financial_disclosure_cli prepare-run` は、原財務bundle・mappingレビュー・採用結果を再検証し、
採用項目、対象期間、未解決事項を一つの内部実行準備結果にまとめます。
出力は値を含まないsummaryと依存ファイルのhash参照のみです。
再検証時にも依存bundleを明示的に指定します。旧 `prepare` / `validate` は従来どおり利用できます。

```bash
python -m stock_research_llm_orchestrator.preparation.financial_disclosure_cli prepare-run \
  --financial runs/financial-prepared --mapping-review runs/financial-mapping-review \
  --adoption runs/financial-accepted --output runs/financial-run
python -m stock_research_llm_orchestrator.preparation.financial_disclosure_cli validate-run \
  --financial runs/financial-prepared --mapping-review runs/financial-mapping-review \
  --adoption runs/financial-accepted --input runs/financial-run
```

価格・FXは任意の `--price-fx` で接続できます。原財務bundleに同梱済みならそれを使用します。
task全体と確認時刻の一致が必要で、同梱分と別指定分が異なる場合は拒否します。
別指定して保存した場合、再検証にも同じ依存bundleが必要です。
準備状態がpendingなら、価格・FXの不足理由と利用制約も保持します。

現在の不足は `reasons`、旧財務準備の理由は `historical_reasons` で区別します。
限定採用があっても期間・IR等の不足は解消せず、全体は `analysis_ready=false` です。
これは内部準備CLIの接続であり、公開CLIの分析実行コマンドはまだ未実装です。
[実行準備への接続結果](docs/decision-requests/2026-09-27-financial-adoption-run-connection-outcome.md)を参照してください。


### 保存済み前期比較値の受入

7203の固定archiveについて、承認済みpolicyに列挙した2025年3月期と2026年3月期の
各6項目をオフラインで受け入れます。元の当期受入・taxonomy証明を再検証し、
比較期間の候補と当期結果の一致を検証します。他年度・他文書へ自動拡張しません。

```bash
python -m stock_research_llm_orchestrator.preparation.financial_acceptance_cli prepare-comparative \
  --source runs/financial-prepared --review runs/financial-mapping-review \
  --adoption runs/financial-accepted \
  --policy config/financial-mapping/7203-2026-comparative-approved.json \
  --output runs/financial-comparative
python -m stock_research_llm_orchestrator.preparation.financial_acceptance_cli validate-comparative \
  --source runs/financial-prepared --review runs/financial-mapping-review \
  --adoption runs/financial-accepted --input runs/financial-comparative
```

上記の入力例は、承認policyに一致する保存済みbundleを配置した場合のものです。
`values.json` はローカル専用で、CLIには採用件数だけを表示します。
`financial_disclosure_cli prepare-run` / `validate-run` の両方に
`--comparative runs/financial-comparative` を渡すと、version 2の期間別要約を生成・再検証します。
`--price-fx` も併用できます。比較入力を省略した従来のversion 1は変更していません。

6項目すべてが採用された期間だけを完全期間として数え、提出書類の期間数とは区別します。
現在は年次2/5期で、2022～2024年3月期と四半期・半期、IR・最新性の確認が不足しています。
[前期比較値の受入結果](docs/decision-requests/2026-09-27-financial-comparative-period-outcome.md)を参照してください。

### 2024年報告の当期・比較値の限定採用

保存済みS100TR7Iには、`7203-2024-draft.json`、`7203-2024-approved.json`、
`7203-2024-comparative-approved.json`（いずれも `config/financial-mapping/` 配下）を
順に使い、上記と同じmapping・当期受入・比較受入CLIで2023・2024年3月期の各6項目を採用できます。
入力には対応する2024年sourceとreviewを指定し、出力は既存bundleとは別のディレクトリに保存します。

出典は「2024年報告に掲載された値」です。`provenance.json` とmanifestに
2023年訂正内容未照合・最新性未確認の制約を保持します。
このbundleの年次2/5期は2020～2024年のローカル窓であり、既存runの2022～2026年窓とは異なります。
この受入を従来の単一報告run準備に渡すと拒否します。複数報告には下記の専用経路を使います。旧runの充足率は2/5期のままです。
[限定採用結果](docs/decision-requests/2026-09-27-retained-2024-financial-adoption-outcome.md)を参照してください。

### 複数報告の財務統合

`financial_multi_report_cli prepare` は再検証した主報告と追加報告を別bundleに統合します。
`--financial`、`--review`、`--adoption`、`--comparative`、`--price-fx`に既存2026年の入力を、
`--additional-financial`、`--additional-review`、`--additional-adoption`、`--additional-comparative`に
2024年の入力を指定し、`--output`に未使用の保存先を指定します。
再検証には同じ9入力を渡し、`validate --input <保存先>`を使います。

両報告の発行体・taskの対象範囲・評価policyを照合し、出典別task・確認時刻・制約を保持します。
価格FXは主報告との従来の時刻整合を維持します。統合時点を鮮度確認時刻にはしません。
期間・指標の重複は採用値の一致を確認し、競合時は停止します。財務数値を統合出力に転記しません。
2022～2026年窓で24項目・4/5期を確認済みですが、2022年、訂正内容照合、最新性、interim・IR等が不足し、
`analysis_ready=false`です。旧runは変更せず2/5期のまま保存しています。
[統合結果](docs/decision-requests/2026-09-27-financial-multi-report-outcome.md)を参照してください。

### 過年度年次2件の限定EDINET取得

承認済みv3は、2024-06-25と2023-06-30の一覧、および各一覧から一意に選別した
7203／E02144の年次XBRLに限定します。最大4要求、同時1・60秒間隔、retryなしです。

```bash
python -m stock_research_llm_orchestrator.preparation.edinet_evidence_cli \
  --prior-annual-campaign --allow-network --allow-credential \
  --task runs/prior-annual-task.json --config config \
  --runtime runs/dukascopy-shared-runtime --runs runs/edinet-evidence
```

campaign開始記録を共有runtime配下に永続保存します。完了・失敗・中断後とも再実行は拒否し、
別taskや別出力先でも予算をリセットしません。同じ共有runtimeを使用し、記録の削除や
別runtimeへの切替で再送しないでください。途中失敗時は保存済み原証拠のローカル検証だけを行います。
旧 `--retained-list` とは併用できません。取得は候補保存までで、過年度財務値の採用ではありません。

2024-06-25の初回一覧parse失敗については、固定原本と元のcampaign状態をhashで束縛した
v4限定続行を実装しています。所有者承認に基づく一度限りの例外で、元の送信済みslotは保持します。

```bash
python -m stock_research_llm_orchestrator.preparation.edinet_evidence_cli \
  --prior-annual-campaign --continue-prior-list runs/edinet-evidence/edinet-unaccepted-380f3b97d39e41b9803b46e3d53df0b3-list \
  --allow-network --allow-credential --task runs/edinet-prior-annual-task.json \
  --config config --runtime runs/dukascopy-shared-runtime --runs runs/edinet-evidence
```

このコマンドは初回一覧の再送を行わず、残り3要求に限定します。続行開始後は再実行できません。
新しいfinancial入力version 2はこの固定失敗一覧と元approval v3を保持し、通常の一覧再検証と区別します。
既存version 1の再検証は変更していません。実行済みかどうかはcampaign記録と受入結果を確認してください。

### 2022年比較値と年次5期間の統合

`financial_pair_acceptance_cli prepare --pair <取得pair> --policy config/financial-mapping/7203-2022-pair-approved.json --output <新規保存先>`
で、固定した訂正報告の2022年6指標を採用します。原報告・訂正の2022/2023年12組が一致し、
両方のtaxonomy検証が成功することが条件です。`validate --pair <取得pair> --input <採用bundle>`で再検証します。

複数報告CLIの`prepare`/`validate`に`--pair`と`--pair-adoption`を両方追加すると、
既存2023～2026年と合わせて2022～2026年の30項目・5/5期を統合できます。
片方のみの指定は拒否します。省略時の4/5期bundleは不変です。
訂正本文の定性的内容・最新性、四半期/半期・IR等の不足は残り、`analysis_ready=false`です。
[限定採用結果](docs/decision-requests/2026-09-27-corrected-2022-comparative-outcome.md)を参照してください。

### 必要な追加EDINET取得

ユーザーは進行に必要な取得を許可しています。対象・必要性・送信上限を実行単位で記録し、
間隔を確保し、不要な一覧・文書取得を避けます。許可済みの同じ取得判断を繰り返し求めません。
v5の`edinet_evidence_cli --amendment-pair --retained-list <固定一覧bundle>`は、
2023年原報告S100QZHYと訂正S100RAR0だけを各1回取得します。通常のtask/runtime/runs指定と
network/credential opt-inも必要です。元一覧全体のparse成功とは扱わず、raw pairとして保存します。
60秒以上の間隔、共有Coordinator、一回限りの送信slot、失敗時停止を維持します。
[取得結果](docs/decision-requests/2026-09-27-edinet-2023-pair-outcome.md)を参照してください。
