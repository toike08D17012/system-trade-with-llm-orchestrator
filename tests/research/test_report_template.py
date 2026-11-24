"""Cover complete disclosures, safe display, calculations and translation bindings."""

import json
from typing import cast

import pytest
from pydantic import JsonValue

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.configured_analysis import (
    ConfiguredAnalysisV1,
    ConfiguredMainBody,
)
from stock_research_llm_orchestrator.research.configured import STAGES, run_workflow
from stock_research_llm_orchestrator.research.report_template import ReportArtifact, render_report, report_texts
from stock_research_llm_orchestrator.research.workflow import ReportTranslation, _display

from .test_configured import INSTRUCTIONS, PROFILE, _invoke
from .test_workflow import _inputs


@pytest.fixture
def report_data() -> tuple[dict[str, ReportArtifact], dict[str, JsonValue]]:
    """Use validated workflow artifacts rather than synthetic artifact metadata."""
    files = run_workflow(_inputs(), load_agent_configuration(PROFILE), _invoke, INSTRUCTIONS, mode="replay")
    artifacts: dict[str, ReportArtifact] = {
        stage: ConfiguredAnalysisV1.model_validate_json(files[f"{stage}.json"]) for stage in STAGES[:-1]
    }
    return artifacts, json.loads(files["calculated-returns.json"])


def _translations(artifacts: dict[str, ReportArtifact]) -> ReportTranslation:
    return ReportTranslation.model_validate(
        {"translations": [{"key": key, "text": "日本語訳：" + value} for key, value in report_texts(artifacts).items()]}
    )


def test_template_has_decision_first_sections_and_discloses_unknowns(
    report_data: tuple[dict[str, ReportArtifact], dict[str, JsonValue]],
) -> None:
    """Unavailable structured values stay unknown and review is not formal completion."""
    artifacts, calculations = report_data
    result = render_report(
        artifacts, _translations(artifacts), load_agent_configuration(PROFILE), calculations, "replay", "1234 (XTKS)"
    ).decode()
    headings = [
        "## 1. 結論",
        "## 2. 分析の基準と確認済みの事実",
        "## 3. 投資判断の根拠",
        "## 4. 中期シナリオ",
        "## 5. 長期シナリオ",
        "## 6. 主なリスクと未解明事項",
        "## 7. 判断を変更する条件",
        "## 8. 出典と分析の検証状況",
        "## 付録：計算・配当の詳細",
    ]
    assert [result.index(heading) for heading in headings] == sorted(result.index(heading) for heading in headings)
    assert "計算基準株価：未確認" in result and "算出不能" in result
    assert "期末EPS・評価PERの内訳は各シナリオの前提を参照。明記のない数値は未確認。" in result
    assert "未記載の金額・日程・受給条件は未確認として扱う。" in result
    assert "正式な探索収束・最終鮮度確認・分析完了は未達" in result
    assert "翻訳後の文書への追加レビューは実施していない" in result
    assert "保存応答を再生したレポート" in result


def test_all_conclusion_disclosures_and_assumptions_survive_without_worker_corpus(
    report_data: tuple[dict[str, ReportArtifact], dict[str, JsonValue]],
) -> None:
    """Later list entries remain visible rather than being silently trimmed."""
    artifacts, calculations = report_data
    main = cast(ConfiguredMainBody, artifacts["orchestrator"].payload)
    first = main.conclusions[0]
    scenario = first.scenarios[0].model_copy(
        update={"assumptions": ("assumption one", "assumption two", "assumption three")}
    )
    conclusion = first.model_copy(
        update={
            "missing_information": ("first", "second", "third", "fourth"),
            "change_conditions": ("entry one", "entry two", "entry three"),
            "scenarios": (scenario, *first.scenarios[1:]),
        }
    )
    main = main.model_copy(update={"conclusions": (conclusion, main.conclusions[1])})
    artifacts["orchestrator"] = artifacts["orchestrator"].model_copy(update={"payload": main})
    selected = report_texts(artifacts)
    assert not any(key.startswith("track_") for key in selected)
    assert selected["orchestrator/payload/conclusions/0/missing_information/3"] == "fourth"
    assert selected["orchestrator/payload/conclusions/0/change_conditions/2"] == "entry three"
    assert selected["orchestrator/payload/conclusions/0/scenarios/0/assumptions/2"] == "assumption three"
    rendered = render_report(
        artifacts, _translations(artifacts), load_agent_configuration(PROFILE), calculations, "live", "1234"
    ).decode()
    for value in selected.values():
        assert _display("日本語訳：" + value) in rendered


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "blank", "english"])
def test_template_rejects_unbound_or_non_japanese_translation(
    report_data: tuple[dict[str, ReportArtifact], dict[str, JsonValue]],
    mutation: str,
) -> None:
    """Translation must cover the exact display fields once without blank values."""
    artifacts, calculations = report_data
    body = _translations(artifacts).model_dump(mode="json")
    entries = cast(list[dict[str, str]], body["translations"])
    if mutation == "missing":
        entries.pop()
    elif mutation == "duplicate":
        entries.append(entries[0])
    elif mutation == "blank":
        entries[0]["text"] = " "
    else:
        for entry in entries:
            entry["text"] = "English only."
    with pytest.raises(ValueError, match="template_translation_"):
        render_report(
            artifacts,
            ReportTranslation.model_validate(body),
            load_agent_configuration(PROFILE),
            calculations,
            "live",
            "1234",
        )


def test_numeric_zero_safe_markdown_and_web_price_provenance(
    report_data: tuple[dict[str, ReportArtifact], dict[str, JsonValue]],
) -> None:
    """Zero distributions and prices remain numeric; text cannot inject tables or HTML."""
    artifacts, calculations = report_data
    main = cast(ConfiguredMainBody, artifacts["orchestrator"].payload)
    first = main.conclusions[0]
    scenario = first.scenarios[0].model_copy(update={"terminal_price": "0", "cumulative_dividends": "0"})
    conclusion = first.model_copy(
        update={
            "scenarios": (scenario, *first.scenarios[1:]),
            "rationale": "<script>alert(1)</script> | [click](javascript:bad)\n# injected",
        }
    )
    main = main.model_copy(update={"conclusions": (conclusion, main.conclusions[1])})
    artifacts["orchestrator"] = artifacts["orchestrator"].model_copy(update={"payload": main})
    calculations["medium_term"] = [
        {"status": "conditional", "capital_return": "-1", "income_return": "0", "total_return": "-1"} for _ in range(3)
    ]
    result = render_report(
        artifacts,
        _translations(artifacts),
        load_agent_configuration(PROFILE),
        calculations,
        "live",
        "1234 | <img>",
        {"price": "100", "on": "2026-11-17", "provenance": "agent_reported_native_web_not_machine_verified"},
    ).decode()
    assert "| 弱気 | 0 | 0 | -100.0% | 0.0% | -100.0% |" in result
    assert "<script>" not in result and "<img>" not in result
    assert "\\|" in result and "\\[click\\]" in result
    assert "\n# injected" not in result
    assert "原本との機械照合は未実施" in result and "100 JPY／株" in result


def test_inline_source_links_in_narrative_stay_clickable(
    report_data: tuple[dict[str, ReportArtifact], dict[str, JsonValue]],
) -> None:
    """Agent-written http(s) links render as links while surrounding text stays escaped."""
    artifacts, calculations = report_data
    translated = _translations(artifacts).model_dump(mode="json")
    entries = cast(list[dict[str, str]], translated["translations"])
    entries[0]["text"] = (
        "株価(終値) [JPXの休場日](https://www.jpx.co.jp/calendar/) "
        "[Wiki](https://en.wikipedia.org/wiki/Advantest_(company)) [bad](javascript:x)"
    )
    result = render_report(
        artifacts,
        ReportTranslation.model_validate(translated),
        load_agent_configuration(PROFILE),
        calculations,
        "live",
        "1234",
    ).decode()
    assert "株価\\(終値\\) [JPXの休場日](<https://www.jpx.co.jp/calendar/>)" in result
    assert "[Wiki](<https://en.wikipedia.org/wiki/Advantest_(company)>)" in result
    assert "\\[bad\\]\\(javascript:x\\)" in result
    assert "\\]\\(http" not in result


def test_report_sources_are_linked_with_original_retrieval_status() -> None:
    """Reported sources are shown without upgrading search results to page verification."""
    from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.web_analysis import WebAnalysisV1
    from stock_research_llm_orchestrator.research.web import run_workflow as web_workflow

    from .test_web import INSTRUCTIONS as WEB_INSTRUCTIONS
    from .test_web import _invoke as web_invoke

    config = load_agent_configuration(PROFILE)
    files = web_workflow(_inputs(), config, web_invoke, WEB_INSTRUCTIONS, mode="replay")
    artifacts: dict[str, ReportArtifact] = {
        stage: WebAnalysisV1.model_validate_json(files[f"{stage}.json"]) for stage in STAGES[:-1]
    }
    rendered = render_report(
        artifacts, _translations(artifacts), config, json.loads(files["calculated-returns.json"]), "live", "1234"
    ).decode()
    assert "[Synthetic original source](<https://example.com/orchestrator>)" in rendered
    assert "確認状態：page\\_read" in rendered
    assert "Web出典は担当の報告であり、原本の機械取得・検証とは区別する" in rendered
