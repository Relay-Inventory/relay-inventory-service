# Tenant Onboarding (Upload → Trigger → Download)

This guide is written so a junior engineer can onboard a new tenant using docs
only. It covers tenant config creation, uploading vendor feeds, triggering runs,
and downloading artifacts.

## 1) Create or update the tenant config

Use the API to create a tenant (first time) or update the config (subsequent
versions). All config fields are documented in `docs/CONFIG_SPEC.md`.

**Create tenant**

```bash
curl -X POST "$RELAY_API/v1/tenants" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d @tenant_config.json
```

**Update tenant config (new version)**

```bash
curl -X PUT "$RELAY_API/v1/tenants/{tenant_id}/config" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d @tenant_config.json
```

**Example minimal config (S3 inbound)**

```json
{
  "schema_version": 1,
  "tenant_id": "acme_parts",
  "timezone": "America/Chicago",
  "default_currency": "USD",
  "vendors": [
    {
      "vendor_id": "vendor_1",
      "inbound": {
        "type": "s3",
        "s3_prefix": "tenants/acme_parts/inbound/vendor_1/"
      },
      "parser": {
        "format": "csv",
        "column_map": {
          "sku": "SKU",
          "quantity_available": "QTY",
          "cost": "COST",
          "map_price": "MAP"
        }
      }
    }
  ],
  "pricing": {
    "base_margin_pct": 0.2,
    "min_price": 25.0,
    "shipping_handling_flat": 9.99,
    "map_policy": {
      "enforce": true,
      "map_floor_behavior": "max(price, map_price)"
    },
    "rounding": {
      "mode": "nearest",
      "increment": 0.01
    }
  },
  "merge": {
    "strategy": "best_offer",
    "best_offer": {
      "sort_by": ["in_stock_desc", "lowest_landed_cost"],
      "landed_cost": {
        "include_shipping_handling": true
      },
      "fallback_lead_time_days": 7
    }
  },
  "output": {
    "format": "csv",
    "columns": ["sku", "quantity_available", "price", "vendor_id", "updated_at"]
  }
}
```

## 2) Upload vendor files to S3

For each vendor, upload the CSV to the `inbound.s3_prefix` configured above. The
worker uses the **latest object in that prefix**.

```bash
aws s3 cp vendor_1.csv \
  s3://$ARTIFACT_BUCKET/tenants/acme_parts/inbound/vendor_1/vendor_1_2024-07-01.csv
```

Repeat for each vendor prefix in the config.

## 3) Trigger a run

```bash
curl -X POST "$RELAY_API/v1/runs" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "tenant_id": "acme_parts",
    "run_type": "inventory_sync",
    "vendors": ["vendor_1"]
  }'
```

Response includes a `run_id` and `config_version`.

## 4) Check run status

```bash
curl -X GET "$RELAY_API/v1/runs/{run_id}" \
  -H "X-API-Key: $API_KEY"
```

Look at `status`, `stage`, and any `error_code` for failures.

## 5) Download outputs

Fetch presigned artifact URLs:

```bash
curl -X GET "$RELAY_API/v1/runs/{run_id}/artifacts" \
  -H "X-API-Key: $API_KEY"
```

Download `merged_inventory` from the returned map. Other useful artifacts include
`config_snapshot`, `input_manifest`, and `run_summary`.

## Tips

- Ensure the inbound prefix matches **exactly** what is in the config.
- If you update the config, trigger a new run so the latest version is used.
- For failures, consult `docs/RUNBOOK.md` for debug steps.

