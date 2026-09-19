"""Package-level smoke tests."""


def test_package_imports() -> None:
    """Import the application package without runtime side effects."""
    import stock_research_llm_orchestrator

    assert stock_research_llm_orchestrator.__name__ == "stock_research_llm_orchestrator"
