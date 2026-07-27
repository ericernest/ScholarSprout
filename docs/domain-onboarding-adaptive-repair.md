# Domain onboarding adaptive repair

Adaptive repair is deliberately **shadow-only** in its first version. Historical
audit records are grouped by quality issue type and repair action. A candidate
action is eligible only after the configured minimum sample count, and actions
are ranked by Wilson lower confidence bound, score improvement, token use, and
latency.

Build a reviewable candidate policy with:

```bash
python -m evaluation.domain_onboarding.adaptive_cli AUDIT_DIR \
  --output .artifacts/adaptive-repair-policy.json \
  --min-samples 20
```

Set `DOMAIN_ONBOARDING_ADAPTIVE_POLICY_FILE` to load a reviewed policy. The
pipeline records its recommendations in `repair_record.shadow_recommendations`
and metrics, but the existing deterministic repair planner remains authoritative.

Promotion to active routing requires a controlled offline/online comparison.
The current request-level success label may be shared by several actions in one
repair, so it must not be interpreted as causal per-action evidence.
