# 実装計画: 財務採用結果の実行準備への接続

## 目的と範囲

保存済み財務準備・mappingレビュー・限定採用を、既存の財務準備CLIから一つの実行準備結果として参照する。
採用済み6項目と未解決の証拠不足を同時に表示し、原証拠から再検証できる状態にする。
追加のネットワーク取得・credential使用・Agent転送は行わない。
実装・対象テスト・保存済み実証拠の検証・コミットまでを次工程とする。

## 確認した現状

- 公開 `cli.py` は `stock-research config validate` のみ。公開の分析実行コマンドは未実装。
- `preparation/financial_disclosure_cli.py` は `prepare` / `validate` を提供する内部CLI。
- `financial_disclosure.py::evaluate_financial` はtaskと確認時刻に結び付いた候補準備を行う。
  同じtask・同じ確認時刻のprice/FX準備を同梱できるが、採用結果は扱わない。
- `financial_acceptance.py::validate_acceptance` は原財務bundle・レビュー・採用policyから値と根拠を再生する。
- 採用bundleのmanifestは原財務manifestとレビューのhashを参照するが、元taskを複製しない。
- 旧財務manifestの `financial_mapping_unimplemented` などは履歴として残っている。
  採用bundleの `inherited_reasons` はこの履歴であり、現在の採用状態とは別である。
- 実証拠の採用結果は [限定採用結果](../../decision-requests/2026-09-27-financial-mapping-adoption-outcome.md) に記録済み。

今回の接続は内部の実行準備への接続であり、公開の分析runtimeが完成したとは表現しない。

## 実装方針

1. 新規 `preparation/financial_run.py` に、版付きの実行準備参照とsummaryのモデルを追加する。
   原財務bundle、mappingレビュー、採用bundleを明示的な入力として受け、既存validatorで再生する。
   taskと確認時刻は原財務bundleから取得し、新しいtaskや現在時刻へ暗黙に付け替えない。
2. 出力は値を含まないsummaryと依存artifactのhash参照だけにする。
   原ZIP・財務数値・旧manifestを複製/変更せず、参照元が必要であることを明記する。
   manifestに保存した任意のパスを自動で開かず、validateにも依存bundleのパスを明示的に渡す。
   inventory、hash、原証拠からの再生結果を照合する。
3. summaryにtask ID、確認時刻、銘柄、採用項目・件数・対象期間、price/FX状態、現在の不足理由、
   旧結果の理由を区別して保存する。全体は常に `pending` / `analysis_ready=false`。
   一件の限定採用がある場合、現在の状態では `financial_mapping_unimplemented` を
   `financial_mapping_partial` に置き換え、旧理由は履歴に保持する。
   年次5期・中間8期間、IR、最新性、その他開示の不足は解消扱いにしない。
   ローカル採用期間は表示するが、6項目が揃っただけで旧 `annual_periods` の充足判定を書き換えない。
4. price/FX bundleを任意入力で参照可能にする。原財務bundleに同梱される場合はそれを使用し、
   別途指定する場合もtask全体・確認時刻の一致を必須とする。
   両方ある場合は同一内容のみ許可し、競合を上書きしない。
   未指定・未同梱なら `price_fx_not_connected` を残す。
   接続されてもprice/FX自身のpending理由や不足を保持し、接続だけでreadyとは扱わない。
5. `financial_disclosure_cli.py` に `prepare-run` / `validate-run` を追加する。
   引数案は `--financial`、`--mapping-review`、`--adoption`、任意の `--price-fx` と出力/検証先。
   既存の `prepare` / `validate` の引数・出力・旧bundleの再生を変更しない。
   標準出力は値のないsummaryと集計に限定する。
6. 出力は既存private directory・原子公開を利用し、入力と出力の重なり、symlink、再公開を拒否する。
   README、TODO、結果記録を更新し、内部準備の接続と公開分析runtimeの未完成を区別する。

## 検証

- `tests/preparation/test_financial_run.py` を新設し、既存財務準備テストの合成採用fixtureを
  必要最小限共有する。fixtureの共有が必要なら専用conftestへ名前付きfixtureだけを移す。
  他テストへautouse設定を広げない。
- 6項目の採用状態と不足が併存し、値・原文がsummary/標準出力に含まれないことを確認する。
- 他task・他銘柄・異時刻の参照、改変された依存artifact、未承認policy、参照不足を拒否する。
- price/FXなし・同一task/時刻での接続・同梱分との競合を確認する。
- 一部/全項目が未採用の場合も件数・理由を正しく保持する。
- source/レビュー/採用bundleの不変、旧CLI互換、原子公開・再公開拒否、ネットワーク未使用を確認する。
- 対象pytest、変更ファイルのRuff/Mypy、コミットhookを実行する。
- 保存済み7203証拠でprepare-run/validate-runを行う。今回の実証拠はprice/FX未接続のままでもよく、
  採用6項目と残る不足を正しく報告することを完了条件とする。

## 実装前の確認

技術的なブロッカーは現時点でない。取得範囲、転送許可、採用policyの拡張は不要。
ただし `src/AGENTS.md` §6には新規実装計画後のユーザー承認待ちが明記されているため、
本計画の確認後に実装へ進む。計画作成のみで、今回コード変更・テスト・コミットは行っていない。

## Implementation Notes

2026-09-27にユーザー承認を受け、実装・保存済み実証拠のprepare-run/validate-runを完了した。
新しい参照結果だけを保存し、原証拠・採用値・旧レビューは変更していない。
fixtureを移動せず、既存財務の合成統合テストと価格FXの実bundleテストを拡張した。
採用0件の場合は現在の理由を `financial_mapping_unaccepted` とし、旧理由は履歴として分ける。
対象46テスト・Ruff・Mypyが成功。実証拠は採用6項目・price/FX未接続・全体pendingを再現した。
[接続結果](../../decision-requests/2026-09-27-financial-adoption-run-connection-outcome.md)を参照。
