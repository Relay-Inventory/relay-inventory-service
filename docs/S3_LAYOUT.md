# S3 Layout & Artifact Keys

This service uses a single artifact bucket (configured via `ARTIFACT_BUCKET`) and
writes run artifacts under a run-scoped prefix. Vendor uploads can live anywhere
in the bucket so long as the tenant config points at the correct `s3_prefix`.

## Bucket overview

```
s3://<ARTIFACT_BUCKET>/
  tenants/<tenant_id>/inbound/<vendor_id>/...   # vendor uploads (config-driven)
  <run_id>/tenants/<tenant_id>/...              # run artifacts (worker output)
```

### Vendor uploads (inbound)

The worker reads from **the latest object** under each vendor's `inbound.s3_prefix`.
Example prefix from config:

```
tenants/acme_parts/inbound/vendor_1/
```

Upload raw vendor CSVs here. The worker will copy the latest object into the
run-specific inbound folder for traceability.

## Run artifact prefix

The worker writes all artifacts under:

```
<run_id>/tenants/<tenant_id>/
```

> Example: `7c3d.../tenants/acme_parts/`

### Artifact types and keys

| Artifact | Key pattern | Notes |
| --- | --- | --- |
| Input manifest | `<run_id>/tenants/<tenant_id>/reports/input_manifest.json` | Includes resolved inbound objects and metadata. |
| Config snapshot | `<run_id>/tenants/<tenant_id>/reports/config_snapshot.json` | Captures the exact config used in the run. |
| Inbound copy | `<run_id>/tenants/<tenant_id>/inbound/<vendor_id>/<filename>` | Copy of the source vendor file used. |
| Normalized output | `<run_id>/tenants/<tenant_id>/normalized/<vendor_id>/normalized.csv` | Canonicalized vendor rows. |
| Merged output | `<run_id>/tenants/<tenant_id>/outputs/merged_inventory.csv` | Final merged inventory output. |
| Run summary | `<run_id>/tenants/<tenant_id>/reports/run_summary.json` | Row counts, warnings, timing. |
| Errors report | `<run_id>/tenants/<tenant_id>/reports/errors.json` | Present when validation or vendor issues exist. |

### Artifact names in API responses

The `/v1/runs/{run_id}/artifacts` endpoint returns a map of logical names to
presigned URLs. Common keys include:

- `config_snapshot`
- `input_manifest`
- `inbound_<vendor_id>`
- `normalized_<vendor_id>`
- `merged_inventory`
- `run_summary`
- `errors` (when applicable)

