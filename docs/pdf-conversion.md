# PDF共有資料の変換と計測

DockerのPython環境には、ビルド時と起動時に本体の `pdf` extra が入ります。調査本体は同じPythonで変換モジュールを起動します。Doclingのモデルは初回に取得します。LLM補正には実行環境で認証済みのCodex CLIが必要です。

ホストで実行する場合はリポジトリルートで `UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project . --locked --extra pdf` を実行します。以下のコマンドは、その仮想環境かDocker内のPythonで実行します。

```bash
python -m stock_research_llm_orchestrator.research.pdf_conversion \
  /path/to/document.pdf --pages 1 2 --cache runs/pdf-readers
```

`--pages` を省略すると全文を対象にします。長い資料ではCLIの入力上限に達することがあります。出力先の `reader/` に補正済み本文、図表、要約と目次を保存します。同一入力・設定の完成資料は再利用します。対象ページの画像は補正担当にだけ渡します。

モデルを事前取得する場合は `docling-tools models download --output-dir runs/docling-models layout tableformer` を実行し、手動変換時に `--artifacts-path runs/docling-models` を指定できます。

## 変換の比較測定

`scripts/pdf/` のCLIは同じPDF・ページ範囲でDoclingとpypdfを比較します。manifestに記載した原本が必要です。

```bash
python scripts/pdf/benchmark.py --manifest tests/fixtures/research/docling-corpus.json \
  --output runs/docling-benchmark --engine docling --repeat 3

python scripts/pdf/benchmark.py --manifest tests/fixtures/research/docling-corpus.json \
  --output runs/pypdf-benchmark --engine pypdf --repeat 3

python scripts/pdf/package_samples.py --results runs/docling-benchmark/results.json \
  --output runs/docling-portable
```

指標の読み方は[評価報告](agent-reports/research/docling-evaluation.md)を参照してください。
