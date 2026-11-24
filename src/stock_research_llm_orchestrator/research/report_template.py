"""Render the versioned decision-first report without an additional prose-writing agent."""

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import cast

from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import AcquisitionAnalysisV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentConfigurationV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.configured_analysis import (
    ConfiguredAnalysisV1,
    ConfiguredMainBody,
    ConfiguredReviewBody,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.web_analysis import WebAnalysisV1
from stock_research_llm_orchestrator.research.workflow import ReportTranslation, _display


type ReportArtifact = ConfiguredAnalysisV1 | WebAnalysisV1 | AcquisitionAnalysisV1

_DECISIONS = {"candidate": "買い候補", "wait": "待機", "reject": "見送り", "insufficient_information": "判断保留"}
_CASES = {"bear": "弱気", "base": "基本", "bull": "強気"}
_OBJECTIVES = {"capital_gain": "値上がり益", "income_gain": "配当収入", None: "未設定"}
_CONFIDENCE = {"high": "高", "medium": "中", "low": "低"}
_CONCLUSION_LISTS = ("change_conditions", "missing_information", "unresolved_differences", "specialist_limitations")
_CONTEXT_LISTS = (
    "entry_reference_conditions",
    "technical_exit_reference_conditions",
    "thesis_invalidation_conditions",
    "upcoming_event_risks",
    "missing_information",
)
# Agents cite sources inline as Markdown links; only http(s) destinations without whitespace or angle brackets
# survive, with one level of balanced parentheses so URLs such as Wikipedia titles stay intact.
_INLINE_LINK = re.compile(r"\[([^\[\]\n]+)\]\((https?://(?:[^\s<>()]|\([^\s<>()]*\))+)\)")


def report_texts(artifacts: Mapping[str, ReportArtifact]) -> dict[str, str]:
    """Select every displayed narrative field without translating the worker corpus."""
    main = cast(ConfiguredMainBody, artifacts["orchestrator"].payload)
    review = cast(ConfiguredReviewBody, artifacts["primary_reviewer"].payload)
    texts: dict[str, str] = {}
    for index, conclusion in enumerate(main.conclusions):
        prefix = f"orchestrator/payload/conclusions/{index}"
        for key in ("story", "rationale", "capital_allocation_consistency"):
            texts[f"{prefix}/{key}"] = str(getattr(conclusion, key))
        for key in _CONCLUSION_LISTS:
            for item, text in enumerate(getattr(conclusion, key)):
                texts[f"{prefix}/{key}/{item}"] = text
        for item, scenario in enumerate(conclusion.scenarios):
            texts[f"{prefix}/scenarios/{item}/story"] = scenario.story
            for key in ("assumptions", "missing_information"):
                for number, text in enumerate(getattr(scenario, key)):
                    texts[f"{prefix}/scenarios/{item}/{key}/{number}"] = text
        summary = main.horizon_results[index]
        summary_prefix = f"orchestrator/payload/horizon_results/{index}"
        texts[f"{summary_prefix}/summary"] = summary.summary
        for key in ("missing_information", "counterevidence_conditions"):
            for item, text in enumerate(getattr(summary, key)):
                texts[f"{summary_prefix}/{key}/{item}"] = text
        if summary.assessment is not None:
            texts[f"{summary_prefix}/assessment/rationale"] = summary.assessment.rationale
            for item, text in enumerate(summary.assessment.condition_or_research_needs):
                texts[f"{summary_prefix}/assessment/condition_or_research_needs/{item}"] = text
    context = main.entry_exit_context
    for key in ("current_price_context", "volatility_context"):
        texts[f"orchestrator/payload/entry_exit_context/{key}"] = str(getattr(context, key))
    for key in _CONTEXT_LISTS:
        for item, text in enumerate(getattr(context, key)):
            texts[f"orchestrator/payload/entry_exit_context/{key}/{item}"] = text
    for index, difference in enumerate(main.differences):
        for key in ("track_a_position", "track_b_position", "resolution_rationale"):
            texts[f"orchestrator/payload/differences/{index}/{key}"] = str(getattr(difference, key))
        for item, text in enumerate(difference.missing_information):
            texts[f"orchestrator/payload/differences/{index}/missing_information/{item}"] = text
    for index, finding in enumerate(review.findings):
        for key in ("summary", "expected_resolution"):
            texts[f"primary_reviewer/payload/findings/{index}/{key}"] = str(getattr(finding, key))
    return texts


def _display_linked(text: str) -> str:
    """Escape narrative text while keeping inline http(s) source links clickable."""
    parts: list[str] = []
    position = 0
    for match in _INLINE_LINK.finditer(text):
        parts.append(_display(text[position : match.start()]))
        parts.append(f"[{_display(match[1])}](<{match[2]}>)")
        position = match.end()
    parts.append(_display(text[position:]))
    return "".join(parts)


def render_report(
    artifacts: Mapping[str, ReportArtifact],
    translated: ReportTranslation,
    config: AgentConfigurationV1,
    calculations: dict[str, JsonValue],
    mode: str,
    security_label: str,
    price: dict[str, JsonValue] | None = None,
    *,
    preserve_inline_links: bool = True,
) -> bytes:
    """Render recorded analysis and calculations using the approved section order."""
    originals = report_texts(artifacts)
    translations = {entry.key: entry.text for entry in translated.translations}
    if len(translations) != len(translated.translations) or set(translations) != set(originals):
        raise ValueError("template_translation_coverage_invalid")
    if any(not text.strip() for text in translations.values()) or not re.search(
        r"[\u3040-\u30ff\u3400-\u9fff]", "".join(translations.values())
    ):
        raise ValueError("template_translation_language_invalid")
    main = cast(ConfiguredMainBody, artifacts["orchestrator"].payload)
    review = cast(ConfiguredReviewBody, artifacts["primary_reviewer"].payload)

    display = _display_linked if preserve_inline_links else _display

    def text(key: str) -> str:
        return display(translations[key])

    def items(prefix: str, values: tuple[str, ...]) -> list[str]:
        return ["- " + text(f"{prefix}/{index}") for index in range(len(values))] or ["- 記載なし。"]

    def rates(horizon: str, index: int) -> list[str]:
        result = cast(list[dict[str, JsonValue]], calculations[horizon])[index]
        return [
            f"{Decimal(str(result[key])) * 100:.1f}%" if result["status"] == "conditional" else "算出不能"
            for key in ("capital_return", "income_return", "total_return")
        ]

    lines = [
        f"# {_display(security_label)} 投資分析レポート",
        "",
        "> **総合判断：中期「"
        + _DECISIONS[main.conclusions[0].decision]
        + "」、長期「"
        + _DECISIONS[main.conclusions[1].decision]
        + "」。**",
        ">",
        "> 分析状態：暫定。正式な収束・最終鮮度確認・分析完了は未達。",
        "",
        "## 1. 結論",
        "",
        "| 項目 | 中期 | 長期 |",
        "| --- | --- | --- |",
    ]
    summaries = main.horizon_results
    confidence = [
        "評価不能" if summary.assessment is None else _CONFIDENCE[summary.assessment.confidence]
        for summary in summaries
    ]
    for label, values in (
        ("分析期間", [f"{c.scenarios[0].months}か月" for c in main.conclusions]),
        ("投資判断", [_DECISIONS[c.decision] for c in main.conclusions]),
        ("確信度", confidence),
        ("基本シナリオの累計総収益率", [rates(c.horizon, 1)[2] for c in main.conclusions]),
        ("弱気シナリオの累計総収益率", [rates(c.horizon, 0)[2] for c in main.conclusions]),
        ("主目的", [_OBJECTIVES[c.primary_objective] for c in main.conclusions]),
        ("副目的", [_OBJECTIVES[c.secondary_objective] for c in main.conclusions]),
    ):
        lines.append(f"| {label} | {values[0]} | {values[1]} |")
    lines.extend(["", "## 2. 分析の基準と確認済みの事実", "", "### 2.1 対象と株価", ""])
    if price is None:
        lines.append("計算基準株価：未確認。観測株価を推測で補っていない。")
    else:
        provenance = str(price.get("provenance", ""))
        route = (
            "Web調査の報告値。原本との機械照合は未実施"
            if provenance == "agent_reported_native_web_not_machine_verified"
            else "標準経路の原本表から機械抽出した条件付き観測値。取引日網羅性・JPX一覧の鮮度は検証保留"
            if provenance == "machine_parsed_native_yfinance_observation"
            else "保存済みの価格観測値。取得・検証状態は関連成果物を参照"
        )
        lines.extend(
            [
                f"計算基準株価：{_display(str(price['price']))} JPY／株。基準日：{_display(str(price['on']))}。",
                "",
                route + "。正確な取得時刻・現在の約定可能価格を確認済みとは扱わない。",
            ]
        )
    lines.extend(
        [
            "",
            text("orchestrator/payload/entry_exit_context/current_price_context"),
            "",
            "### 2.2 会社公表の業績・配当",
            "",
            "会社公表の業績・配当は第3〜5節の記述と出典を参照。"
            "記載のない指標は未確認であり、将来の条件付き仮定を会社予想として扱わない。",
            "",
            "## 3. 投資判断の根拠",
            "",
        ]
    )
    for index, _conclusion in enumerate(main.conclusions):
        label = "中期" if index == 0 else "長期"
        prefix = f"orchestrator/payload/conclusions/{index}"
        summary_prefix = f"orchestrator/payload/horizon_results/{index}"
        lines.extend(
            [
                f"### 3.{index + 1} {label}の根拠と資金配分",
                "",
                text(f"{prefix}/rationale"),
                "",
                text(f"{prefix}/capital_allocation_consistency"),
                "",
                text(f"{summary_prefix}/summary"),
                "",
            ]
        )
        if main.horizon_results[index].assessment is not None:
            lines.extend([text(f"{summary_prefix}/assessment/rationale"), ""])
    for index, conclusion in enumerate(main.conclusions):
        prefix = f"orchestrator/payload/conclusions/{index}"
        label = "中期" if index == 0 else "長期"
        lines.extend(
            [
                f"## {index + 4}. {label}シナリオ：{conclusion.scenarios[0].months}か月",
                "",
                text(f"{prefix}/story"),
                "",
                "| シナリオ | 期末株価（円） | 累計配当（円／株） | 値上がり益率 | 配当収益率 | 合計収益率 |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for number, scenario in enumerate(conclusion.scenarios):
            terminal = scenario.terminal_price if scenario.terminal_price is not None else "未算出"
            dividend = scenario.cumulative_dividends if scenario.cumulative_dividends is not None else "未算出"
            lines.append(
                f"| {_CASES[scenario.case]} | {terminal} | {dividend} | "
                + " | ".join(rates(conclusion.horizon, number))
                + " |"
            )
        lines.extend(["", "期末EPS・評価PERの内訳は各シナリオの前提を参照。明記のない数値は未確認。", ""])
        for number, scenario in enumerate(conclusion.scenarios):
            scenario_prefix = f"{prefix}/scenarios/{number}"
            lines.extend([f"### {_CASES[scenario.case]}の前提", "", text(f"{scenario_prefix}/story"), ""])
            lines.extend(items(f"{scenario_prefix}/assumptions", scenario.assumptions))
            lines.extend(
                [
                    "",
                    "**計算上の不足情報**",
                    "",
                    *items(f"{scenario_prefix}/missing_information", scenario.missing_information),
                    "",
                ]
            )
            lines.extend(["株数単位：" + _display(scenario.per_share_basis), ""])
    lines.extend(["## 6. 主なリスクと未解明事項", ""])
    for index, conclusion in enumerate(main.conclusions):
        prefix = f"orchestrator/payload/conclusions/{index}"
        summary = main.horizon_results[index]
        summary_prefix = f"orchestrator/payload/horizon_results/{index}"
        lines.extend([f"### {'中期' if index == 0 else '長期'}", ""])
        for key, label in (
            ("missing_information", "不足情報"),
            ("specialist_limitations", "専門分析の制約"),
            ("unresolved_differences", "未解決の見解差"),
        ):
            lines.extend([f"**{label}**", "", *items(f"{prefix}/{key}", getattr(conclusion, key)), ""])
        for key, label in (("missing_information", "統合上の不足情報"), ("counterevidence_conditions", "反証条件")):
            lines.extend([f"**{label}**", "", *items(f"{summary_prefix}/{key}", getattr(summary, key)), ""])
        if summary.assessment is not None:
            lines.extend(
                [
                    "**評価に必要な確認事項**",
                    "",
                    *items(
                        f"{summary_prefix}/assessment/condition_or_research_needs",
                        summary.assessment.condition_or_research_needs,
                    ),
                    "",
                ]
            )
    for index, difference in enumerate(main.differences):
        prefix = f"orchestrator/payload/differences/{index}"
        lines.extend(
            [
                "**調査担当間の見解差**",
                "",
                "- A：" + text(f"{prefix}/track_a_position"),
                "- B：" + text(f"{prefix}/track_b_position"),
                "- 統合理由：" + text(f"{prefix}/resolution_rationale"),
                "",
                *items(f"{prefix}/missing_information", difference.missing_information),
                "",
            ]
        )
    lines.extend(["## 7. 判断を変更する条件", ""])
    for index, conclusion in enumerate(main.conclusions):
        lines.extend(
            [
                f"### {'中期' if index == 0 else '長期'}",
                "",
                *items(f"orchestrator/payload/conclusions/{index}/change_conditions", conclusion.change_conditions),
                "",
            ]
        )
    for key, label in (
        ("entry_reference_conditions", "再検討の参照条件"),
        ("technical_exit_reference_conditions", "売却の参照条件"),
        ("thesis_invalidation_conditions", "仮説の撤回条件"),
        ("upcoming_event_risks", "今後の確認契機・事象"),
        ("missing_information", "価格判断に残る不足情報"),
    ):
        lines.extend(
            [
                f"**{label}**",
                "",
                *items(f"orchestrator/payload/entry_exit_context/{key}", getattr(main.entry_exit_context, key)),
                "",
            ]
        )
    lines.extend(
        [
            text("orchestrator/payload/entry_exit_context/volatility_context"),
            "",
            "## 8. 出典と分析の検証状況",
            "",
            "### 8.1 主要出典",
            "",
        ]
    )
    source_count = 0
    for role, artifact in artifacts.items():
        research = getattr(artifact.payload, "research", None)
        if research is None:
            continue
        for source in research.sources:
            source_count += 1
            # Angle-bracket destinations keep parentheses in validated public URLs inert.
            lines.append(
                f"- [{_display(source.title)}](<{source.url}>): {_display(role)}／{_display(source.source_id)}。"
                f"確認状態：{_display(source.retrieval_status)}。"
                f"取得日時：{_display(source.retrieved_at or '未確認')}。"
            )
    if not source_count:
        lines.append("- Web出典の記録なし。[共通入力](input/common-input.json)の出典・原文を参照。")
    lines.extend(
        [
            "",
            "Web出典は担当の報告であり、原本の機械取得・検証とは区別する。",
            "",
            "### 8.2 検証・レビュー",
            "",
            "レビュー方式："
            + (
                "同一系統による開発用レビュー。"
                if config.review_policy.mode == "single_family_development"
                else "設定で宣言された異なるモデル系統によるレビュー。"
            ),
            "",
            "一次レビュー：" + ("通過（暫定）。" if review.review_passed else "未通過。結論は未採用。"),
            "",
            f"指摘は{len(review.findings)}件。未解決の指摘を以下に保持する。",
            "",
        ]
    )
    for index, finding in enumerate(review.findings):
        prefix = f"primary_reviewer/payload/findings/{index}"
        lines.extend(
            [
                f"- {_display(finding.severity)}／{_display(finding.lifecycle)}：" + text(f"{prefix}/summary"),
                "  対応：" + text(f"{prefix}/expected_resolution"),
            ]
        )
    lines.extend(
        [
            "",
            "一次レビューは統合分析と計算結果に対するもので、翻訳後の文書への追加レビューは実施していない。",
            "正式な探索収束・最終鮮度確認・分析完了は未達。最終判断と売買は人間が行う。",
            "",
            "関連成果物：[統合分析](orchestrator.json)・[レビュー](primary_reviewer.json)・[共通入力](input/common-input.json)。",
            "",
            "## 付録：計算・配当の詳細",
            "",
            "### A. 共通の計算前提",
            "",
            "- 値上がり益率：期末株価 ÷ 計算基準株価 − 1。",
            "- 配当収益率：期間内の受取対象配当総額 ÷ 計算基準株価。",
            "- 合計収益率：丸め前の値上がり益率と配当収益率の合計。",
            "- 累計・税引前・手数料控除前・配当再投資なし。年率や確率加重の期待収益ではない。",
            "",
            "### B. 配当の受給条件と内訳",
            "",
            "配当の受取日程・各回金額は第4・5節のシナリオ前提を参照。未記載の金額・日程・受給条件は未確認として扱う。",
            "株数単位・株式分割等の扱いも各シナリオの前提を参照する。買戻しを受取配当に加算しない。",
            "",
        ]
    )
    if mode == "replay":
        lines.extend(["保存応答を再生したレポートです。", ""])
    return "\n".join(lines).encode()
