# Relay Inventory Pilot Walkthrough

## 1. Create a tenant config

Start from the sample config:

```
cp data/relay_inventory/tenant_config.yaml tenant_config.yaml
```

Update `tenant_id`, vendor prefixes, and column maps as needed.

## 2. Upload vendor feeds

Upload vendor CSV files into the inbound prefixes:

```
tenants/{tenant_id}/inbound/vendor_1/{yyyy}/{mm}/{dd}/{run_id}/raw.csv
tenants/{tenant_id}/inbound/vendor_2/{yyyy}/{mm}/{dd}/{run_id}/raw.csv
```

## 3. Trigger a run

```
POST /v1/runs
{
  "tenant_id": "your_tenant",
  "run_type": "inventory_sync",
  "vendors": ["vendor_1", "vendor_2"]
}
```

## 4. Fetch output artifacts

Use the run artifacts endpoint to fetch presigned URLs:

```
GET /v1/runs/{run_id}/artifacts
```

Download `merged_inventory.csv` and review `run_summary.json` for row counts.
