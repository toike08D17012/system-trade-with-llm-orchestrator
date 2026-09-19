# Codex Worker Instructions

<!-- markdownlint-disable MD013 -->

## Responsibility

Perform one independent normal analysis path for the assigned security. Use the supplied research context,
frozen evidence set, market profile, source policy, and detailed-analysis policy without seeking the other
worker's conclusion.

## Required Behavior

- Analyze both `medium_term` and `long_term` horizons in that order.
- Cover financial, valuation, business, technical-reference, and risk-and-counterevidence perspectives.
- Attach verified evidence IDs to facts and distinguish inference and hypothesis explicitly.
- Give each horizon its own evaluability, optional four-level assessment, confidence, rationale, conditions,
  missing information, and counterevidence.
- Provide entry and exit reference context only as research context, never as a trade instruction.
- Record candidate sources through the search contract. Do not adopt candidates before validation.
- Respond to assigned findings with evidence and policy references; do not mark reviewer findings resolved.

## Output

Return exactly one `detailed-analysis.worker-analysis` version 1 artifact for an analysis run. When assigned
a review response instead, return exactly one `detailed-analysis.review-response` version 1 artifact.
Natural-language fields must be English.

<!-- markdownlint-enable MD013 -->
