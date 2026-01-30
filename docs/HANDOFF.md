# Relay Inventory Handoff Guide

This document is a full handoff guide for operating Relay Inventory without tribal knowledge. It covers the system architecture, workflows, operational limits, and how to respond to failures.

## 1) System overview

Relay Inventory ingests vendor CSV feeds, normalizes them into a canonical schema, merges them with pricing rules, and produces a single merged inventory feed per tenant.

**High-level flow**

```
Vendor CSVs (S3) --> API (create run) --> SQS job queue --> Worker --> Engine
         |                                                   |
         +---------------------- artifacts + reports <-------+
```

**Primary services**

- **API**: Creates runs, stores run status, and triggers worker jobs.
- **Worker**: Pulls jobs from SQS, reads vendor inputs from S3, runs the engine, and writes artifacts.
- **Engine**: Normalizes, validates, and merges inventory data according to tenant config.

## 2) Core components

### API

- **Endpoints**: `POST /v1/tenants`, `PUT /v1/tenants/{tenant_id}/config`, `POST /v1/runs`, `GET /v1/runs/{run_id}`.
- **Backpressure rule**: only one `RUNNING` run per tenant; concurrent runs return HTTP 409 (`TENANT_ALREADY_RUNNING`).

### Worker

- **Queue consumer**: SQS `relay-inventory-jobs`.
- **Concurrency**: `WORKER_MAX_CONCURRENCY` (default 1).
- **Poison jobs**: marked `FAILED` with `error_code=POISON_JOB` when receive count >= 3.
- **Metrics**: emits CloudWatch metrics for run success/failure, duration, rows processed, and heartbeat.

### Engine

- Pure data processing: no side effects besides returning normalized/merged data and errors.
- Output artifacts include normalized CSVs, merged CSV, and reports.

### DynamoDB tables

- **Runs table** (`RUNS_TABLE`): run status, stage, timestamps, error codes, and artifact keys.
- **Tenants table** (`TENANTS_TABLE`): tenant config snapshots by version.

### S3 layout

All artifacts are written under the run-scoped prefix:

```
<run_id>/tenants/<tenant_id>/
  inbound/<vendor_id>/<filename>
  normalized/<vendor_id>/normalized.csv
  outputs/merged_inventory.csv
  reports/input_manifest.json
  reports/config_snapshot.json
  reports/errors.json
  reports/run_summary.json
```

Vendor uploads are stored in the tenant inbound prefix specified in config (example):

```
tenants/<tenant_id>/inbound/<vendor_id>/
```

### SQS queues

- **Main queue**: `relay-inventory-jobs`
- **DLQ**: `relay-inventory-jobs-dlq`
- **Redrive policy**: `maxReceiveCount = 3`

## 3) Happy path (step-by-step)

1. **Vendor uploads CSVs** to the inbound prefix in S3.
   - Example: `s3://<ARTIFACT_BUCKET>/tenants/acme_parts/inbound/vendor_1/vendor_1_2024-07-01.csv`
2. **Create or update tenant config** using `POST /v1/tenants` or `PUT /v1/tenants/{tenant_id}/config`.
3. **Trigger a run** via `POST /v1/runs` with `tenant_id` and vendor list.
4. **Worker picks up job**, downloads latest vendor CSVs, writes `input_manifest.json`, and runs the engine.
5. **Outputs are written** to S3:
   - Merged output: `<run_id>/tenants/<tenant_id>/outputs/merged_inventory.csv`
   - Reports: `input_manifest.json`, `config_snapshot.json`, `run_summary.json`, `errors.json` (if errors)
6. **Merchant downloads output** by fetching presigned URLs from `/v1/runs/{run_id}/artifacts` or by direct S3 access.

## 4) Run lifecycle

**States**

```
QUEUED -> RUNNING -> SUCCEEDED
            |\
            | \-> FAILED
```

**Stages**

- `FETCH_INPUTS`: Locate and copy inbound vendor files into the run prefix.
- `NORMALIZE`: Parse vendor files into canonical rows.
- `MERGE_PRICE`: Merge normalized rows with pricing/selection logic.
- `WRITE_OUTPUTS`: Write merged output and reports.
- `COMPLETE`: Finalization.

**Artifacts**

- `input_manifest.json`: which vendor files were used (latest object per prefix).
- `config_snapshot.json`: full tenant config used for the run.
- `errors.json`: validation errors and missing vendor warnings.
- `run_summary.json`: row counts, timings, warnings.

## 5) Debugging guide

### Where to find critical artifacts

- `input_manifest.json`: `<run_id>/tenants/<tenant_id>/reports/input_manifest.json`
- `config_snapshot.json`: `<run_id>/tenants/<tenant_id>/reports/config_snapshot.json`
- `errors.json`: `<run_id>/tenants/<tenant_id>/reports/errors.json`
- `run_summary.json`: `<run_id>/tenants/<tenant_id>/reports/run_summary.json`

### Common failures and how to resolve

- **Missing vendor file** (`REQUIRED_VENDOR_MISSING`)
  - Check the inbound prefix in `config_snapshot.json` and confirm the file exists.
- **Encoding / parse errors** (`DECODE_ERROR`)
  - Validate CSV encoding and update `parser.encoding` if needed.
- **Schema mismatch** (`missing_required_columns`)
  - Update `parser.column_map` to match the vendor’s headers.

### How to safely rerun

1. Fix the root cause (upload corrected vendor file or update config).
2. Trigger a **new run** with `POST /v1/runs` (never reuse a run ID).
3. Verify `input_manifest.json` and `config_snapshot.json` to confirm the intended inputs were used.

## 6) Alarm response

### Alarm 1 — Consecutive failures

- **Trigger**: `RunFailed` sum >= 3 in 15 minutes.
- **Investigate**:
  1. Identify tenant(s) from the alarm dimensions.
  2. Inspect `errors.json` and `run_summary.json` for the latest failed run.
- **Resolve**: Fix the data/config issue and trigger a new run.

### Alarm 2 — Queue backlog

- **Trigger**: SQS `ApproximateNumberOfMessagesVisible` > 5 for 10 minutes.
- **Investigate**:
  1. Check worker logs for exceptions or throttling.
  2. Confirm worker concurrency and health.
- **Resolve**: Scale workers or temporarily pause new run requests.

### Alarm 3 — Worker errors

- **Trigger**: `RunFailed >= 1` in 5 minutes.
- **Investigate**: Look for recent run failures and root cause (input/config or infra).
- **Resolve**: Fix root cause and rerun.

### Alarm 4 — Worker heartbeat missing

- **Trigger**: `WorkerHeartbeat` missing for 5 minutes.
- **Investigate**:
  1. Check worker deployment health.
  2. Inspect logs for crashes.
- **Resolve**: Restart or redeploy the worker.

## 7) DLQ procedure

1. **Inspect the DLQ** (`relay-inventory-jobs-dlq`) in the AWS console.
2. **Pull a message** and extract `run_id` + `tenant_id`.
3. **Check the run status** via `GET /v1/runs/{run_id}`; it should show `error_code=POISON_JOB`.
4. **Decide**:
   - **Replay** after fixing the underlying issue (create a new run).
   - **Discard** if the data/config is invalid or no longer needed.

## 8) Onboarding a new tenant

1. Create the tenant config (`POST /v1/tenants`) using `docs/CONFIG_SPEC.md`.
2. Upload vendor CSVs to `tenants/<tenant_id>/inbound/<vendor_id>/`.
3. Trigger a run via `POST /v1/runs` and confirm the `run_id`.
4. Validate artifacts in S3 (`input_manifest.json`, `run_summary.json`).
5. Share the merged output from `outputs/merged_inventory.csv` with the merchant.

## 9) Operational limits

- **CSV size**: Keep individual vendor files under ~50 MB to avoid timeouts.
- **Max vendors per tenant**: 20 vendors per run (practical limit for memory/time).
- **Concurrency**: One active run per tenant; worker concurrency capped by `WORKER_MAX_CONCURRENCY`.
- **Retries**: Messages are moved to DLQ after 3 failed receives.
