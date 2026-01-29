# Relay Inventory MVP Onboarding

## 1. Uploading vendor feeds

Upload vendor CSV files to the tenant inbound prefix:

```
tenants/{tenant_id}/inbound/{vendor_id}/{yyyy}/{mm}/{dd}/{run_id}/raw.csv
```

Example:

```
tenants/acme_parts/inbound/vendor_1/2024/05/01/9f0b/raw.csv
```

## 2. Required columns

Each vendor feed must include the columns declared in the tenant `column_map`. The canonical fields expected are:

- `sku`
- `quantity_available`
- `cost`
- `map_price`

## 3. Output artifacts

The worker writes artifacts to the following prefixes:

```
tenants/{tenant_id}/normalized/{vendor_id}/{run_id}/normalized.csv
tenants/{tenant_id}/outputs/{run_id}/merged_inventory.csv
tenants/{tenant_id}/reports/{run_id}/run_summary.json
tenants/{tenant_id}/reports/{run_id}/errors.json
```

## 4. Triggering runs

Use the API to create runs:

```
POST /v1/runs
{
  "tenant_id": "acme_parts",
  "run_type": "inventory_sync",
  "vendors": ["vendor_1", "vendor_2"]
}
```

## 5. Suggested IAM scope (minimal)

For early pilots, grant the worker role permissions scoped to the tenant prefix:

- `s3:GetObject` on `tenants/{tenant_id}/inbound/*`
- `s3:PutObject` on `tenants/{tenant_id}/normalized/*`, `tenants/{tenant_id}/outputs/*`, `tenants/{tenant_id}/reports/*`
- `dynamodb:UpdateItem` on the runs table
