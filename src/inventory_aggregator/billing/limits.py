from __future__ import annotations

from inventory_aggregator.app.models.config import TenantConfig

# Per COMMIT_PLAN.md Commit 4.6: a soft cap, not a hard error. Crossing it never halts a run --
# it only flags the shop for a "contact us about tier options" notice in the diff email
# (see notifications/diff_email.py). 25 vendors is the plan's own stated threshold.
MAX_VENDORS_SOFT_CAP = 25


def is_over_vendor_cap(tenant_config: TenantConfig) -> bool:
    """True when this tenant's configured vendor count exceeds the soft cap. Callers (currently
    persist_and_record.py) use this to flag the run's diff email -- it never halts the pipeline
    or rejects the config."""
    return len(tenant_config.vendors) > MAX_VENDORS_SOFT_CAP
