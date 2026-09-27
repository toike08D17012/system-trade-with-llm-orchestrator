# 実装計画: 過年度年次2件の限定EDINET取得

## 目的と現状

[PDF表紙の確認結果](../../decision-requests/2026-09-27-annual-filing-date-discovery-outcome.md)で確定した2つの提出日から、7203／E02144の年次XBRLを取得し、原証拠・候補をローカル保存する。過年度の数値採用・taxonomy承認、四半期資料取得は含めない。

既存 `preparation/edinet_evidence.py::acquire_edinet_acceptance` は2026-06-10と年次2026年3月期を固定し、共有Coordinatorで一覧・書類の2要求を実行する。`sources/edinet/production_policy.py` はapproval/profile v2のhashを固定。`edinet_revalidation.py` の旧失敗一覧再開も当該日だけに限定されている。これら旧成果物の再検証を維持する。

## 承認対象となる送信範囲

| 順番 | 操作 | 固定対象 | 上限 |
| --- | --- | --- | --- |
| 1 | document-list、type=2 | 2024-06-25 | 1 |
| 2 | document-retrieval、type=1 | 72030／E02144、120、2023-04-01～2024-03-31 | 1 |
| 3 | document-list、type=2 | 2023-06-30 | 1 |
| 4 | document-retrieval、type=1 | 72030／E02144、120、2022-04-01～2023-03-31 | 1 |

合計最大4物理要求。書類IDは対応する一覧からのみ選択する。非取下げ、XBRLあり、legalStatus 1/2、disclosureStatus 0を確認し、候補が0件・複数件・訂正選択未解決なら停止する。今回の一覧確認だけで全訂正や最新性が確認済みとは扱わない。

同時1・要求間隔60秒、共有rate domain・credential・egress gateを維持する。retry、redirect、日付探索、別ソースfallbackは行わない。credentialは既存0600ファイルから送信直前に読み込み、表示・保存しない。rawはprivate directory、外部Agent転送なし。対象2件の取得を一度限りのcampaignとして扱う。

## 方針と変更箇所

| ファイル | 変更 |
| --- | --- |
| `config/source-approvals/edinet/v3.yaml`、`config/source-profiles/edinet/v3.yaml`（新規） | 上記限定範囲を承認後に記録。v2は不変。有効日は承認日、再確認期限は既存方針の3か月以内 |
| `sources/edinet/production_policy.py` | v2既定を維持し、明示されたv3だけを別の固定hashで検証。共通gateは維持 |
| `preparation/edinet_evidence.py` | 既存送信・保存・論理終了処理を再利用し、明示的な固定対象から日付・期間・bindingを選択。任意日付CLIは作らない |
| `preparation/edinet_evidence_cli.py` | 過年度2件campaignを選ぶ明示オプションとローカル再検証経路。旧起動方法を維持 |
| `preparation/edinet_revalidation.py` | 選別の共通部分を固定対象引数で再利用可能にする。旧再開の対象日制約は維持 |
| `preparation/edinet_campaign.py`（新規） | 一度限りの送信予算・各段階の原証拠参照・途中失敗状態を保存する責務 |
| `tests/preparation/test_edinet_evidence.py`、新規 `test_edinet_campaign.py` | 旧経路回帰、新しい固定対象、4要求上限、途中失敗・再起動・秘密非表示 |
| `README.md`、`docs/TODO.md`、新しい受入結果文書 | 実行方法・実績・未採用の境界 |

Pythonパスは `src/stock_research_llm_orchestrator/` 配下。新規campaign moduleの具体的な保存形式は、既存runtimeのphysical attempt記録・leaseを確認して最小限に決める。既存実行エンジンを丸ごと複製しない。

## 送信予算と再開

- 固定campaign ID、approval hash、4つの要求slotを共有runtimeの配下で束縛する。task名や出力先を変えても予算が増えない。
- Coordinatorのlease取得後にcampaignを確認し、送信開始前にslot消費を永続化する。送信後の原本・receipt参照と対応させる。
- 並行起動を排除する。失敗・応答不明・送信開始後クラッシュのslotは再使用せず、後続の自動送信も停止する。予算を戻す操作は提供しない。
- 正常終了済み成果物はローカル再検証できる。新しいrun名で同じ要求を再送しない。失敗後のオンライン再開を自動化しないため、既存v2の失敗一覧再開を無理に汎用化しない。
- ネットワークに到達しなかったことが明白でも、本campaignでは消費済みslotを保守的に保持する。可用性より一度限りの上限を優先する。

## 保存と期間の扱い

2対象をそれぞれの提出日・対象期間に束縛した独立のfinancial入力bundleとして保存し、campaign manifestから参照する。離れた2日を連続した全日調査済み期間として表現しない。

一覧と書類の要求・応答・hash・取得時刻・approval版・論理終了を保存し、既存parserとオフラインfinancial preparationで候補を再生成する。旧資料に未対応taxonomy等があれば証拠を保持してpendingとし、scope外の値採用へ進まない。

新しいtaskと現在の確認時刻を使う。旧2026-09-26の評価時刻へ新取得を遡及させず、旧runを上書きしない。価格・FXとの接続は本取得の完了条件に含めない。

## 実装順と検証

1. 固定対象とv3 binding、campaign予算の永続化を実装する。通信前に承認・credential・パス・task・既存campaignを検査する。
2. 既存の共有送信処理を固定対象に接続し、対象別bundleとcampaign結果を保存する。
3. mock transport＋制御時計で次を検証する。
   - 成功時の正確な2日・2書類ID・合計4要求・60秒間隔・承認版・終了状態。
   - 別発行体・対象期間・未許可policy・不明な縦覧状態・0/複数候補・訂正候補で停止。
   - HTTP/parse失敗、送信開始後クラッシュ、出力失敗、2重起動、別taskで再実行しても上限が増えず再送しない。
   - credential・原文をCLI/例外へ出さない。パス重複・symlink・既存出力上書きを拒否。
   - 旧v2取得・旧retained-list再検証が従来通り動く。新bundleのオフライン再検証で原本・metadata改ざんを検出。
4. `run-pytest`等のskillに従いwrapperで対象テストを実行。共有送信・承認境界を触るため全体Pytest・Mypy、対象Ruffを既存hookで検証する。無関係な成功済み検査を重複実行しない。
5. 送信範囲の承認後に実取得する。取得結果は値を出さず文書ID・期間・件数・hash・不足理由だけ報告し、原本はGitに含めない。文書更新とコミットまで実施する。

計画作成中はテスト・通信を実行していない。

## ブロッキング事項

追加4要求は現行approval v2の範囲外であり、上記送信範囲の承認が必要。承認後は新approvalの作成、実装、検証、最大4要求の実取得、記録・コミットまで追加の形式的確認を挟まず進める。想定外の対象・上限超過・不可逆な変更が必要になった場合だけ停止して報告する。

## 互換性・ロールバック

既存v2設定・保存bundleは変更しない。新campaignを使用しなければ旧経路のまま。送信済み状態・取得記録はロールバックでも削除・リセットしない。コードの差し戻しをAPI要求予算の回復として扱わない。

## Implementation Notes

- ユーザーが限定4要求を承認。v3を追加し、既存の取得処理を固定対象に再利用した。
- 排他campaign markerは共有runtimeのlease取得より前にatomic mkdirで作成し、親ディレクトリもfsyncする。これによりlease取得前の同時起動も排除する。各slotは送信callback内で永続化する。
- 上限は同一共有runtime内で永続化される。別runtimeへの切替や管理者による記録削除を予算回復として認めない。読み取り再検証は既存financial CLIを用い、campaign自身にオンライン再開機能は設けない。
- 2件は独立のfinancial bundleとし、それぞれのsurvey_periodを提出日1日に限定する。
