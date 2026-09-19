# Agent指示原本

このディレクトリには、個別株スクリーニングの実行時に各Agentへ渡す指示の原本を配置します。将来追加する配布スクリプトが、Agentや役割に応じた実行用ディレクトリへこれらの指示を配置する想定です。

## 指示の配置方針

<!-- markdownlint-disable MD013 -->

| パス | 対象 | 内容 |
| --- | --- | --- |
| `agent-sources/` | スクリーニングを実行するAgent | 役割、入力、出力、レビュー、監査など、実行時に必要な指示の原本 |
| `AGENTS.md` | スクリーニングを含むリポジトリ全体 | 実行時の共通原則、安全境界、成果物配置、最小限のリポジトリ構成 |
| `src/AGENTS.md` | Pythonを開発するCodex | 実装、依存関係、計画、テスト、検証などの開発指示 |
| `.agents/instructions/` | リポジトリを開発するCodex | Python、Markdown、Shellの詳細な編集規約 |

<!-- markdownlint-enable MD013 -->

ルートの `AGENTS.md` には、スクリーニング実行時にも参照させる内容だけを
記載します。Python開発に固有の指示は `src/AGENTS.md` と
`.agents/instructions/python.md` に分離し、スクリーニング実行時の不要な
コンテキストを増やさない構成とします。

## 配置する内容

このディレクトリへは、次のような実行時指示を配置します。

- オーケストレーター、作業者、一次レビューワー、第三者監査の役割別指示
- 共通データ、証拠、分析、レビュー、争点、監査の入出力契約
- 調査観点、反証、データ不足、確信度の記録方法
- Agent間の独立性、権限、外部アクセス、安全境界
- 最終レポートへ引き渡す中間成果物の形式

詳細解析v1の共通指示、5役割別指示、決定的な合成順と内容hashは
[`detailed-analysis/v1/`](detailed-analysis/v1/)に配置しています。これらは指示原本と
適用記録の契約であり、Agent CLIへの物理的な配布処理はまだ実装していません。

Pythonのコーディング規約、依存関係管理、テスト、lint、型チェック、コミット前検証は配置しません。

## 開発時のAgent構成

Python実装の開発にはCodexのみを使用する想定です。Claude CodeとAntigravityには、このリポジトリ固有のPython開発指示を配布しません。

株価取得やテクニカル指標計算などのPython実装では、[Python Workspace Template](https://github.com/toike08D17012/python-workspace-template)を基に整備する開発環境とドキュメントを利用します。
