from __future__ import annotations

from dataclasses import dataclass

from inventory_aggregator.engine.diff import SnapshotDiff


@dataclass
class SafetyThresholds:
    max_changed_sku_pct: float = 0.5
    max_zeroed_skus: int = 50
    max_qty_drop_pct: float = 0.5


@dataclass
class SafetyDecision:
    halted: bool
    reason: str | None


def evaluate_safety(
    diff: SnapshotDiff, thresholds: SafetyThresholds, *,
    previous_total_qty: int, current_total_qty: int,
) -> SafetyDecision:
    total_skus = diff.unchanged_count + len(diff.changed) + len(diff.added_skus) + len(diff.removed_skus)
    changed_count = len(diff.changed) + len(diff.added_skus) + len(diff.removed_skus)
    if total_skus > 0:
        changed_pct = changed_count / total_skus
        if changed_pct > thresholds.max_changed_sku_pct:
            return SafetyDecision(halted=True, reason=f"{changed_pct:.0%} of SKUs changed (threshold {thresholds.max_changed_sku_pct:.0%})")

    zeroed = diff.changed[(diff.changed["current_qty"] == 0) & (diff.changed["previous_qty"] > 0)] if not diff.changed.empty else diff.changed
    zeroed_count = len(zeroed) if hasattr(zeroed, "__len__") else 0
    if zeroed_count > thresholds.max_zeroed_skus:
        return SafetyDecision(halted=True, reason=f"{zeroed_count} SKUs zeroed out (threshold {thresholds.max_zeroed_skus})")

    if previous_total_qty > 0:
        drop_pct = (previous_total_qty - current_total_qty) / previous_total_qty
        if drop_pct > thresholds.max_qty_drop_pct:
            return SafetyDecision(halted=True, reason=f"{drop_pct:.0%} drop in total available quantity (threshold {thresholds.max_qty_drop_pct:.0%})")

    return SafetyDecision(halted=False, reason=None)
