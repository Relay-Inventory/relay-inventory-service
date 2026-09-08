from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class InventoryRecord(BaseModel):
    sku: str
    vendor_sku: Optional[str] = None
    vendor_id: str
    quantity_available: int
    lead_time_days: Optional[int] = None
    cost: Optional[Decimal] = None
    map_price: Optional[Decimal] = None
    price: Decimal
    msrp: Optional[Decimal] = None
    condition: Optional[str] = None
    brand: Optional[str] = None
    title: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("sku", "vendor_id")
    @classmethod
    def required_stripped(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value is required")
        return value.strip()

    @field_validator("quantity_available")
    @classmethod
    def non_negative_quantity(cls, value: int) -> int:
        if value < 0:
            raise ValueError("quantity_available must be >= 0")
        return value

    @field_validator("condition")
    @classmethod
    def normalize_condition(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"new", "used", "refurb"}:
            raise ValueError("condition must be new, used, or refurb")
        return normalized


CANONICAL_COLUMNS = [
    "sku",
    "vendor_sku",
    "vendor_id",
    "quantity_available",
    "lead_time_days",
    "cost",
    "map_price",
    "price",
    "msrp",
    "condition",
    "brand",
    "title",
    "updated_at",
]
