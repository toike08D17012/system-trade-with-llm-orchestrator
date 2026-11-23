"""Load a validated execution profile without consulting personal secrets or providers."""

from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentConfigurationV1
from stock_research_llm_orchestrator.contracts.validation.wrapper import InputFormat, validate_text


def load_agent_configuration(path: Path) -> AgentConfigurationV1:
    """Reject malformed or unknown profile fields before any provider is contacted."""
    try:
        result = validate_text(path.read_text(encoding="utf-8"), InputFormat.YAML)
        if not isinstance(result, AgentConfigurationV1):
            raise ValueError("agent_configuration_schema_invalid")
        return result
    except ValueError, TypeError:
        raise ValueError("agent_configuration_invalid") from None
