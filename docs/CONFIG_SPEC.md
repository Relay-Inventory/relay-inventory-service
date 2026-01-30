# Relay Inventory Config Specification

This document describes the tenant configuration schema used by the API and worker.
All fields map directly to `TenantConfig` and related models in the codebase, so the
names below must be used exactly as shown. Defaults listed here are the Pydantic
defaults applied when fields are omitted. Required fields have no default.

## Top-level object: `TenantConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `1` | Schema version for validation. Only `1` is supported. |
| `tenant_id` | string | yes | — | Tenant identifier. Used in run artifact paths and API requests. |
| `timezone` | string | yes | — | IANA timezone (e.g., `America/Los_Angeles`). |
| `default_currency` | string | yes | — | ISO currency code (e.g., `USD`). |
| `vendors` | array[`VendorConfig`] | yes | — | Vendor feed definitions. |
| `pricing` | `PricingConfig` | yes | — | Pricing rules. |
| `merge` | `MergeConfig` | yes | — | Merge strategy. |
| `output` | `OutputConfig` | yes | — | Output format + columns. |
| `error_policy` | `ErrorPolicy` | no | see below | Validation/error handling thresholds. |

---

## `VendorConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `vendor_id` | string | yes | — | Vendor identifier. Used in artifact keys and vendor selection. |
| `required` | boolean | no | `true` | If `true`, missing inbound data fails the run unless `error_policy.missing_required_vendor_policy` is `warn_only`. |
| `inbound` | `InboundConfig` | yes | — | Inbound source configuration. |
| `parser` | `ParserConfig` | yes | — | Parsing and column mapping. |
| `sku_map` | `SkuMapConfig` | no | `null` | Optional SKU map input. |

### `InboundConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `type` | string | yes | — | Source type (e.g., `s3`, `local`). The worker currently reads only from `s3_prefix`. |
| `s3_prefix` | string | no | `null` | S3 prefix to list for latest inbound file. |

### `ParserConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `format` | string | yes | — | Input format (currently `csv`). |
| `delimiter` | string | no | `","` | CSV delimiter. |
| `encoding` | string | no | `"utf-8"` | Text encoding. |
| `column_map` | object | no | `{}` | Maps canonical column names → vendor column names. |

### `SkuMapConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `type` | string | yes | — | SKU map source type (e.g., `s3`, `file`). |
| `s3_key` | string | no | `null` | S3 object key for SKU map (used in worker). |
| `local_path` | string | no | `null` | Local file path (used by local runner). |

---

## `PricingConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `base_margin_pct` | decimal | yes | — | Base margin percentage (e.g., `0.20`). |
| `min_price` | decimal | yes | — | Minimum output price. |
| `shipping_handling_flat` | decimal | yes | — | Flat shipping/handling cost. |
| `map_policy` | `MapPolicyConfig` | yes | — | MAP enforcement settings. |
| `rounding` | `RoundingConfig` | yes | — | Price rounding settings. |

### `MapPolicyConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `enforce` | boolean | no | `true` | Whether to enforce MAP pricing. |
| `map_floor_behavior` | string | no | `"max(price, map_price)"` | Expression for MAP floor. |

### `RoundingConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `mode` | string | no | `"nearest"` | Rounding mode. |
| `increment` | decimal | no | `0.01` | Rounding increment. |

---

## `MergeConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `strategy` | string | yes | — | Merge strategy (e.g., `best_offer`). |
| `best_offer` | `BestOfferConfig` | no | `null` | Best-offer rules when strategy is `best_offer`. |

### `BestOfferConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `sort_by` | array[string] | no | `[]` | Sort rules (e.g., `in_stock_desc`, `lowest_landed_cost`). |
| `landed_cost` | `BestOfferLandedCost` | yes | — | Landed cost calculation. |
| `fallback_lead_time_days` | integer | no | `7` | Lead time to use when missing. |

### `BestOfferLandedCost`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `include_shipping_handling` | boolean | no | `true` | Whether to include shipping/handling in landed cost. |

---

## `OutputConfig`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `format` | string | no | `"csv"` | Output format. |
| `columns` | array[string] | yes | — | Output column order (canonical column names). |

---

## `ErrorPolicy`

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `max_invalid_rows` | integer | no | `0` | Max invalid rows before failing the run. |
| `max_invalid_row_pct` | float | no | `0.0` | Max invalid row ratio (0.0–1.0). |
| `fail_on_missing_required_columns` | boolean | no | `true` | Fail if required columns missing. |
| `missing_required_vendor_policy` | string | no | `"fail"` | Use `warn_only` to continue when required vendors are missing. |

---

## Minimal S3-backed example

```yaml
schema_version: 1
tenant_id: "acme_parts"
timezone: "America/Chicago"
default_currency: "USD"

vendors:
  - vendor_id: "vendor_1"
    inbound:
      type: "s3"
      s3_prefix: "tenants/acme_parts/inbound/vendor_1/"
    parser:
      format: "csv"
      column_map:
        sku: "SKU"
        quantity_available: "QTY"
        cost: "COST"
        map_price: "MAP"

pricing:
  base_margin_pct: 0.20
  min_price: 25.00
  shipping_handling_flat: 9.99
  map_policy:
    enforce: true
    map_floor_behavior: "max(price, map_price)"
  rounding:
    mode: "nearest"
    increment: 0.01

merge:
  strategy: "best_offer"
  best_offer:
    sort_by: ["in_stock_desc", "lowest_landed_cost"]
    landed_cost:
      include_shipping_handling: true
    fallback_lead_time_days: 7

output:
  format: "csv"
  columns: ["sku", "quantity_available", "price", "vendor_id", "updated_at"]

error_policy:
  max_invalid_rows: 0
  max_invalid_row_pct: 0.0
  fail_on_missing_required_columns: true
  missing_required_vendor_policy: "fail"
```
