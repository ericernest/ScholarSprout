"""Build a shadow adaptive-repair policy from persisted audit JSONL files."""

from __future__ import annotations

import argparse

from handlers.domain_onboarding.adaptive_repair import (
    AdaptiveRepairConfig,
    AdaptiveRepairPolicyBuilder,
    load_audit_records,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit_paths", nargs="+", help="Audit JSONL files or directories")
    parser.add_argument("--output", required=True, help="Candidate policy JSON path")
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument(
        "--policy-version", default="domain-repair-adaptive-v1.0.0"
    )
    args = parser.parse_args()
    records = load_audit_records(args.audit_paths)
    policy = AdaptiveRepairPolicyBuilder(
        AdaptiveRepairConfig(min_samples=args.min_samples)
    ).build(records, policy_version=args.policy_version)
    policy.save(args.output)
    print(
        f"wrote {len(policy.strategies)} shadow strategies from "
        f"{len(records)} audit records to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
