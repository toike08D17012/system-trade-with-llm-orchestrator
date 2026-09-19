"""Command-line interface for the stock research application."""

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.errors import ApplicationError, ExitCode
from stock_research_llm_orchestrator.logging_setup import configure_logging


def build_parser() -> argparse.ArgumentParser:
    """Build the public P2 command tree."""
    parser = argparse.ArgumentParser(prog="stock-research")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('stock-research-llm-orchestrator')}")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="WARNING")
    commands = parser.add_subparsers(dest="command", required=True)
    config_parser = commands.add_parser("config", help="Validate versioned application configuration.")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    validate_parser = config_commands.add_parser("validate", help="Validate configuration without network access.")
    validate_parser.add_argument("--config-dir", type=Path, default=Path("./config"))
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and execute one CLI command."""
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    artifacts = validate_configuration(args.config_dir)
    display_path = str(args.config_dir)
    print(f"Validated {len(artifacts)} configuration artifacts under {display_path}.")
    return ExitCode.SUCCESS


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and normalize failures to stable, safe process results."""
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        return ExitCode.INTERRUPTED
    except ApplicationError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except Exception:
        print("Error: An unexpected internal error occurred.", file=sys.stderr)
        return ExitCode.INTERNAL_ERROR
