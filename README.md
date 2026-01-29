# Relay Inventory Service

Relay Inventory consolidates multi-vendor inventory feeds into a single, normalized
output feed with deterministic merge and pricing rules.

## Repository layout

```
data/relay_inventory/         # Sample tenant config + CSV fixtures for local runs
docs/                         # Product plan and onboarding notes
scripts/local_run.py          # Local runner for CSV-based testing
src/relay_inventory/          # Core service (API, engine, adapters, persistence)
tests/relay_inventory/        # Unit + integration tests for the service
```

## Local runner

Run the pipeline against sample data in `data/relay_inventory`:

```
python scripts/local_run.py --tenant test_tenant --output-dir outputs
```

Or pass a config + vendor file mapping:

```
python scripts/local_run.py \
  --config path/to/tenant_config.yaml \
  --vendor-file vendor_1=/path/to/vendor_1.csv \
  --vendor-file vendor_2=/path/to/vendor_2.csv \
  --output-dir outputs
```

The runner writes normalized vendor CSVs and a merged inventory CSV under `outputs/`.

## API service

The FastAPI app lives in `src/relay_inventory/app/api/app.py`. Configure environment
variables like `RUNS_TABLE`, `TENANTS_TABLE`, `SQS_QUEUE_URL`, and `ARTIFACT_BUCKET`
to connect to AWS resources.

## Tests

Run the Relay Inventory test suite:

```
pytest tests/relay_inventory
```
