# Relay Inventory Infrastructure Notes

This document captures required production infrastructure settings, CloudWatch alarms, and queue policies for Relay Inventory.

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `ARTIFACT_BUCKET` | S3 bucket for run artifacts and inbound vendor uploads. | none |
| `RUNS_TABLE` | DynamoDB table for run status records. | none |
| `TENANTS_TABLE` | DynamoDB table for tenant configs. | none |
| `SQS_QUEUE_URL` | Main worker queue URL. | none |
| `CLOUDWATCH_METRICS_ENABLED` | Enable CloudWatch metric emission (`true`/`false`). | `false` |
| `CLOUDWATCH_METRICS_NAMESPACE` | Metric namespace for custom metrics. | `RelayInventory` |
| `ENVIRONMENT` | Environment dimension for metrics (ex: `prod`, `staging`). | `unknown` |
| `WORKER_MAX_CONCURRENCY` | Maximum concurrent jobs per worker process. | `1` |
| `WORKER_POISON_MAX_RECEIVES` | Receive threshold before marking a run as poison. | `3` |

## CloudWatch metrics (custom)

The worker emits custom metrics to the `RelayInventory` namespace using `PutMetricData`.

**Emitted metrics** (dimensions: `TenantId`, `Environment`):

| Metric | Unit | Emitted When | Notes |
| --- | --- | --- | --- |
| `RunSucceeded` | Count | Run finishes successfully | Value `1` on success, `0` on failure. |
| `RunFailed` | Count | Run finishes | Value `1` on failure, `0` on success. |
| `RunDurationSeconds` | Seconds | Run finishes | Total wall-clock run time. |
| `RowsProcessed` | Count | Run finishes | Total merged rows from the run summary. |
| `WorkerHeartbeat` | Count | Every worker loop | Used to detect liveness. |

The worker also emits `WorkerError` metrics (dimension: `Environment`) when queue or processing errors occur.

## CloudWatch alarm definitions

Create the following alarms in CloudWatch (no dashboard required). Adjust SNS/email targets as needed.

### Alarm 1 — Consecutive failures

- **Metric**: `RunFailed`
- **Expression**: `Sum(RunFailed) >= 3` in a **15 minute** window
- **Dimensions**: `TenantId` (optional per-tenant) + `Environment`
- **Action**: Email/SNS notification

### Alarm 2 — Queue backlog

- **Metric**: SQS `ApproximateNumberOfMessagesVisible`
- **Queue**: `relay-inventory-jobs`
- **Threshold**: `> 5` for **10 minutes**
- **Action**: Email/SNS notification

### Alarm 3 — Worker errors

- **Metric**: `RunFailed`
- **Threshold**: `>= 1` in **5 minutes**
- **Dimensions**: `Environment` (optionally per tenant)
- **Action**: Email/SNS notification

### Alarm 4 — Worker heartbeat missing

- **Metric**: `WorkerHeartbeat`
- **Condition**: Missing data (treat as breaching) for **5 minutes**
- **Action**: Email/SNS notification

## SQS queues and redrive policy

Relay Inventory uses a primary queue and a dead-letter queue (DLQ).

- **Main queue**: `relay-inventory-jobs`
- **DLQ**: `relay-inventory-jobs-dlq`
- **Redrive policy**: `maxReceiveCount = 3`

When a message hits the receive threshold, the worker marks the run as `FAILED` with `error_code=POISON_JOB` and the message is moved to the DLQ automatically by SQS.

## DLQ inspection

Use `docs/RUNBOOK.md` for step-by-step DLQ inspection and replay guidance.
