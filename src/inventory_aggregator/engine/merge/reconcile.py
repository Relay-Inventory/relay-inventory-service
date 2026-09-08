from __future__ import annotations

from decimal import Decimal

import pandas as pd

from inventory_aggregator.engine.canonical.models import InventoryRecord


def reconcile(records: list[InventoryRecord], *, shipping_handling_flat: Decimal) -> pd.DataFrame:
    """Returns a DataFrame with columns: sku, available_qty, source_vendor_id, source_cost,
    vendor_count.

    available_qty = sum of quantity_available across all vendors carrying the SKU.
    source_vendor_id/source_cost = the vendor with the lowest landed cost (cost + shipping),
    among vendors with quantity_available > 0 only — a vendor with a SKU but zero stock can
    never be the routing source, even if its cost happens to be lowest.
    """
    if not records:
        return pd.DataFrame(columns=["sku", "available_qty", "source_vendor_id", "source_cost", "vendor_count"])

    df = pd.DataFrame([r.model_dump() for r in records])
    df["landed_cost"] = df["cost"].fillna(Decimal("Infinity")).astype(float) + float(shipping_handling_flat)

    available_qty = df.groupby("sku")["quantity_available"].sum().rename("available_qty")
    vendor_count = df.groupby("sku")["vendor_id"].nunique().rename("vendor_count")

    in_stock = df[df["quantity_available"] > 0]
    source_idx = in_stock.groupby("sku")["landed_cost"].idxmin()
    source = in_stock.loc[source_idx, ["sku", "vendor_id", "cost"]].set_index("sku")
    source = source.rename(columns={"vendor_id": "source_vendor_id", "cost": "source_cost"})

    result = pd.concat([available_qty, vendor_count, source], axis=1).reset_index()
    return result[["sku", "available_qty", "source_vendor_id", "source_cost", "vendor_count"]]
