# Relay Inventory Runbook

This runbook is for quickly diagnosing and resolving failed runs in production.

## Where to look first

1. **Run status**: `GET /v1/runs/{run_id}` (check `status`, `stage`, `error_code`).
2. **Input manifest**: `<run_id>/tenants/<tenant_id>/reports/input_manifest.json`.
3. **Config snapshot**: `<run_id>/tenants/<tenant_id>/reports/config_snapshot.json`.
4. **Errors report** (if present): `<run_id>/tenants/<tenant_id>/reports/errors.json`.
5. **Run summary**: `<run_id>/tenants/<tenant_id>/reports/run_summary.json`.

> Tip: Use `/v1/runs/{run_id}/artifacts` to get presigned URLs for the reports
> above without navigating S3 manually.

---

## Operational alarms (CloudWatch)

The worker emits CloudWatch custom metrics when `CLOUDWATCH_METRICS_ENABLED=true`.
Alarms can be created via `scripts/setup_cloudwatch_alarms.py`.

### Alarm: Consecutive failures (per tenant or global)

**Trigger**: `RunFailed` metric is `1` for 3 consecutive evaluation periods
(`--consecutive-failure-threshold`, configurable).

**Response**:
1. Identify affected tenant (alarm name includes tenant, or check recent failed runs).
2. Inspect the latest run’s `error_code` and `errors.json`.
3. Fix input/config issue, then rerun.
4. If multiple tenants failing simultaneously, check shared infrastructure (S3
   bucket permissions, DynamoDB, queue connectivity).

### Alarm: Sustained SQS backlog

**Trigger**: `ApproximateNumberOfMessagesVisible` exceeds threshold for >X minutes.

**Response**:
1. Check worker capacity and deployment health (CPU/memory, task/instance count).
2. Review worker logs for `queue_receive_error` or `run_retryable_error`.
3. Scale workers or temporarily pause run creation if backlog keeps growing.
4. Validate that SQS permissions and visibility timeout are correct.

### Alarm: Worker crash loop / elevated error rate

**Trigger**: `WorkerError` count exceeds threshold within a period.

**Response**:
1. Inspect worker logs around the alarm window for exceptions.
2. Verify recent deployments or config changes (rollback if needed).
3. Confirm dependent services (S3, DynamoDB) are healthy and reachable.
4. If errors are retryable, check for downstream throttling or timeouts and
   adjust retries/backoff.

---

## Dead-letter queue (DLQ) inspection

Poison jobs (bad configs or consistently failing runs) are routed to the SQS DLQ
after `WORKER_POISON_MAX_RECEIVES` receives. Ensure the queue redrive policy
`maxReceiveCount` matches the worker threshold to keep behavior consistent.

**Inspect via AWS Console**:
1. Open the SQS DLQ.
2. Use the **Receive messages** action to peek at messages.
3. Extract the `run_id` and `tenant_id` from the JSON body.

**Inspect via CLI**:
```
aws sqs receive-message \
  --queue-url $SQS_DLQ_URL \
  --max-number-of-messages 10 \
  --attribute-names All \
  --wait-time-seconds 5
```

After capturing the `run_id`, check `/v1/runs/{run_id}` to confirm the run is
`FAILED` with `error_code=POISON_JOB`, then fix the underlying config/data issue
and create a new run.

---

## Top 5 failure modes and fixes

### 1) `missing_tenant_config`
**Symptom**: Run fails immediately in `FETCH_INPUTS`.

**Cause**: The tenant config version referenced by the run isn’t present in the
config store.

**Fix**:
- Recreate or update the tenant config via `POST /v1/tenants` or
  `PUT /v1/tenants/{tenant_id}/config`.
- Trigger a new run after confirming the config is stored.

---

### 2) `REQUIRED_VENDOR_MISSING`
**Symptom**: Run fails in `FETCH_INPUTS` with a message that required vendor inbound
is missing.

**Cause**: No objects were found under the vendor’s `inbound.s3_prefix`.

**Fix**:
- Upload the vendor file to the exact prefix configured in the tenant config.
- Confirm the prefix in `config_snapshot.json` matches the expected path.
- Re-run after the file is present.

---

### 3) `DECODE_ERROR`
**Symptom**: Run fails in `NORMALIZE` with a decode error for a vendor.

**Cause**: The inbound file encoding doesn’t match `parser.encoding` or the CSV
is malformed.

**Fix**:
- Confirm the file encoding (default `utf-8`).
- Validate the CSV with a local tool or `python scripts/local_run.py`.
- Update `parser.encoding` if needed and re-run.

---

### 4) `missing_required_columns`
**Symptom**: Run fails in `NORMALIZE` stating required columns are missing.

**Cause**: The vendor file lacks a column referenced in `parser.column_map` or
uses a different header name.

**Fix**:
- Update `parser.column_map` to point at the correct vendor header names.
- Ensure required canonical columns exist in the feed.
- Re-run after updating the config.

---

### 5) `validation_errors`
**Symptom**: Run fails in `MERGE_PRICE` with `validation errors`.

**Cause**: Too many invalid rows exceeded `error_policy.max_invalid_rows` or
`error_policy.max_invalid_row_pct`.

**Fix**:
- Inspect `reports/errors.json` to see invalid row details.
- Raise thresholds in `error_policy` (if acceptable) or fix input data.
- Re-run after changes.

---

## Other common issues

- **`no_rows_parsed`**: No rows parsed from inputs (often empty files). Ensure
  inbound files are non-empty and correctly mapped.
- **`unsupported_schema_version`**: Config `schema_version` is not `1`.
- **Warnings for missing optional vendors**: Check `reports/errors.json` for
  `OPTIONAL_VENDOR_MISSING` entries; runs still succeed.

---

## How to safely re-run

1. **Fix the root cause** (config, inbound data, or thresholds).
2. **Re-upload** inbound files if the data changed. The worker always uses the
   latest object in each vendor prefix.
3. **Trigger a new run** via `POST /v1/runs`. Every run has a new `run_id`, so
   artifacts never overwrite previous runs.
4. **Validate** by checking `reports/input_manifest.json` and
   `reports/config_snapshot.json` to confirm the intended inputs and config were
   used.
