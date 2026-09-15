from __future__ import annotations

from typing import Optional

import jinja2

from inventory_aggregator.billing.limits import MAX_VENDORS_SOFT_CAP
from inventory_aggregator.persistence.single_table import FEED_STATE_PREFIX, FeedStateItem, RunItem, SingleTable

_SUBJECT_TEMPLATE = jinja2.Template(
    "Inventory Aggregator: sync {{ status_word }} for {{ run_item.shop_id }}"
)

# autoescape=True since this renders an HTML email body from data that ultimately traces back to
# merchant-controlled config/feed content (vendor_id, feed_id, safety_reason strings) -- escape
# by default rather than trusting every field is safe to interpolate raw.
_env = jinja2.Environment(autoescape=True)

_BODY_TEMPLATE = _env.from_string(
    "<html><body>"
    "<h1>Sync {{ status_word }} for {{ run_item.shop_id }}</h1>"
    "<p>Run ID: {{ run_item.run_id }}</p>"
    "<h2>What changed</h2>"
    "<ul>"
    "<li>Added SKUs: {{ diff_summary.get('added_skus', 0) }}</li>"
    "<li>Removed SKUs: {{ diff_summary.get('removed_skus', 0) }}</li>"
    "<li>Changed SKUs: {{ diff_summary.get('changed_count', 0) }}</li>"
    "</ul>"
    "<h2>Vendor feed health</h2>"
    "{% if stale_feeds %}"
    "<ul>"
    "{% for feed in stale_feeds %}"
    "<li>{{ feed.vendor_id }} / {{ feed.feed_id }}: {{ feed.last_fetch_status or 'never fetched' }}</li>"
    "{% endfor %}"
    "</ul>"
    "{% else %}"
    "<p>All vendor feeds fetched successfully this run.</p>"
    "{% endif %}"
    "<h2>Circuit breaker</h2>"
    "<p>{{ circuit_breaker_status }}</p>"
    "{% if over_vendor_cap %}"
    "<h2>Billing</h2>"
    "<p>You're over the {{ max_vendors_soft_cap }}-vendor soft cap. Contact us about tier options.</p>"
    "{% endif %}"
    "</body></html>"
)


def _stale_or_errored_feeds(table: SingleTable, shop_id: str) -> list[FeedStateItem]:
    """A feed counts as stale/errored if its most recent FEED_STATE# item's last_fetch_status
    isn't the "ok" value fetch_and_hash.py writes on a clean fetch -- including a feed that has
    never successfully fetched at all (last_fetch_status is None)."""
    feed_states = table.query(shop_id, FEED_STATE_PREFIX)
    return [fs for fs in feed_states if (fs.last_fetch_status or "").lower() != "ok"]


def render_diff_email(
    run_item: RunItem,
    diff_summary: Optional[dict],
    safety_reason: Optional[str],
    *,
    table: SingleTable,
    over_vendor_cap: bool = False,
) -> tuple[str, str]:
    """Renders the per-run merchant-facing diff email. Returns (subject, html_body).

    `table` is required to pull this shop's current FEED_STATE# items (which vendor feeds were
    stale/errored) -- not part of the three positional params named in COMMIT_PLAN.md Commit 4.5
    because it's plumbing, not content, but still needed to do the query described there
    (`SingleTable.query(shop_id, FEED_STATE_PREFIX)`). `over_vendor_cap` carries Commit 4.6's
    soft-cap flag into the render -- see billing/limits.py.
    """
    status_word = "halted" if run_item.status == "HALTED" else "succeeded"
    circuit_breaker_status = safety_reason if run_item.status == "HALTED" else "All checks passed"
    stale_feeds = _stale_or_errored_feeds(table, run_item.shop_id)

    subject = _SUBJECT_TEMPLATE.render(status_word=status_word, run_item=run_item)
    body = _BODY_TEMPLATE.render(
        status_word=status_word,
        run_item=run_item,
        diff_summary=diff_summary or {},
        circuit_breaker_status=circuit_breaker_status,
        stale_feeds=stale_feeds,
        over_vendor_cap=over_vendor_cap,
        max_vendors_soft_cap=MAX_VENDORS_SOFT_CAP,
    )
    return subject, body
