"""Run the owner-approved EDINET two-request acceptance case."""

import argparse
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.edinet_evidence import (
    acquire_edinet_acceptance,
    acquire_prior_annual_campaign,
)
from stock_research_llm_orchestrator.preparation.market_revalidation import _read_file


def main(argv: list[str] | None = None) -> int:
    """Require both explicit network and credential opt-ins before admission."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--allow-credential", action="store_true")
    parser.add_argument("--amendment-pair", action="store_true")
    parser.add_argument("--retained-list", type=Path)
    parser.add_argument("--prior-annual-campaign", action="store_true")
    parser.add_argument("--continue-prior-list", type=Path)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, default=Path("/run/secrets/edinet_api_key"))
    args = parser.parse_args(argv)
    try:
        if args.amendment_pair and (args.prior_annual_campaign or args.continue_prior_list is not None):
            raise ValueError("edinet_pair_campaign_conflict")
        if args.continue_prior_list is not None and not args.prior_annual_campaign:
            raise ValueError("edinet_continuation_requires_campaign")
        if args.prior_annual_campaign and args.retained_list is not None:
            raise ValueError("edinet_campaign_retained_list_unsupported")
        acquire = acquire_prior_annual_campaign if args.prior_annual_campaign else acquire_edinet_acceptance
        extra = (
            {"continuation_list": args.continue_prior_list}
            if args.prior_annual_campaign
            else {"retained_list": args.retained_list, "amendment_pair": args.amendment_pair}
        )
        output = acquire(
            task=DetailedAnalysisTaskV1.model_validate_json(_read_file(args.task)),
            config=args.config,
            runtime=args.runtime,
            runs=args.runs,
            credential=args.credential_file,
            **extra,
            allow_network=args.allow_network,
            allow_credential=args.allow_credential,
        )
        print(f"edinet_acceptance: saved {output}; analysis_ready=false")
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"edinet_acceptance_failed: {type(error).__name__}; no retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
