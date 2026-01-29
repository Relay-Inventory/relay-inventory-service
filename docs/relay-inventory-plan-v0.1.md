# Relay Inventory — Product & Implementation Plan (v0.1)

**Status:** Draft for rapid execution  
**Primary objective:** Ship a working MVP to real test merchants ASAP (optimize for speed + reliability, not polish).  
**Non-goal:** Building a Shopify App Store product in v0.1.

---

## 1) Goal

Relay Inventory is a **managed inventory + pricing consolidation service** for merchants who source from multiple vendors and are drowning in CSV/feeds.

**MVP Goal:**  
Turn multi-vendor raw feeds into a **single, clean, normalized output feed** (CSV/JSON) with deterministic pricing rules and traceable audit logs.

**Success metrics (MVP):**
- Time-to-onboard a merchant: **< 2 hours**
- Feed run reliability: **≥ 99% runs succeed** (or fail fast with actionable error)
- Output correctness: **Deterministic**, unit-tested core transforms
- First paid pilot: **within 45 days**
- Revenue target: **$1.5k–$2.5k/mo**, scale-ready to **$10k/mo** without rewrite

---

## 2) Vision (what this becomes, later)

Relay Inventory becomes the “boring backbone” behind specialty merchants’ operations:

1. **Ingestion Layer**: pull/vendor feeds (S3 uploads, APIs, optional legacy FTP)  
2. **Normalization Engine**: SKU mapping, standard schema, data hygiene  
3. **Pricing Engine**: cost → price rules, MAP constraints, shipping/handling logic  
4. **Merge Engine**: multi-vendor availability + lead times into a single inventory view  
5. **Delivery Layer**: CSV output, webhooks, and later direct channel pushes (Shopify/Magento/Woo)

**Later milestones (post-MVP):**
- Channel pushes (Shopify/Magento)
- MAP alerting and competitor intelligence (optional product line)
- UI dashboard (only after we have 5+ paying customers and stable workflows)

---

## 3) Motivation (why this wins)

Merchants already have the demand. Their bottleneck is ops.

Manual feed wrangling causes:
- Overselling and stockouts
- Wrong pricing and margin leakage
- Broken imports and late updates
- Hours of repetitive, low-value work weekly

Relay Inventory is valuable because it’s:
- **Reliable** (always runs)
- **Traceable** (audit trails + retained artifacts)
- **Configurable** (tenant rules without code changes)
- **Boring** (low churn; people keep what works)

---

## 4) Scale & Operating Model

### Commercial scale targets
- Short term: **3–5 merchants** at **$300–$800/mo**
- Medium term: **10–20 merchants** to reach **$5k–$10k/mo**

### Engineering scale assumptions (MVP)
- **Low data volume**: CSV files, small-to-medium catalogs, periodic updates
- **Retention**: 30 days of raw + normalized + output artifacts
- **Tenancy**: Multi-tenant by configuration + S3 prefix isolation (not full enterprise RBAC initially)

### Operations model
- “Managed service” feel: we own onboarding + troubleshooting initially
- KTLO target: keep maintenance to **< 5 hours/week** at ~$5k MRR, then hire support/contractor

---

## 5) Product Definition (MVP)

### Inputs
- Vendor feeds uploaded to S3 (preferred)  
  - Optional: Pull from vendor sources later (API/FTP)
- Tenant configuration (rules, mappings, vendor definitions)

### Outputs
- A merged inventory feed:
  - CSV (primary)
  - JSON (optional if cheap)
- Run artifacts:
  - raw ingested files
  - normalized per-vendor files
  - merged output file
  - run log + metrics
  - error report if fail

### Core behaviors
- Normalize SKUs and fields to a canonical schema
- Merge inventories across vendors per tenant-defined rules
- Apply pricing rules deterministically (including MAP constraints if available)
- Produce consistent output with stable ordering and formatting

---

## 6) Architecture (MVP)

### High-level components
1. **API Service** (FastAPI)
   - Manage tenants, configs, trigger runs, query run status, fetch artifacts
2. **Job Orchestrator**
   - SQS-backed jobs for sync runs (tenant/vendor)
3. **Worker**
   - Runs normalization + merge + pricing and writes artifacts to S3
4. **Config Store**
   - DynamoDB for tenant config + metadata (or S3 config files to start; DynamoDB recommended for scaling/updates)
5. **Artifact Store**
   - S3 with lifecycle retention policy
6. **Observability**
   - Structured logs + metrics + alarms

### Recommended AWS layout (scale-ready, still simple)
- **S3**: artifacts + inbound feeds + outputs
- **SQS**: job queue
- **ECS Fargate** (or EC2 + systemd): worker runner
- **FastAPI** on ECS/Fargate (or small EC2) behind ALB
- **DynamoDB**: tenant configs + run metadata + idempotency keys

> If you want ultra-minimal first deploy: one EC2 running API + worker via supervisor is acceptable.  
> But build the code as if worker can be stateless and queued.

---

## 7) Data Model

### S3 bucket layout

```

s3://relay-inventory-prod/
tenants/
{tenant_id}/
inbound/
{vendor_id}/
{yyyy}/{mm}/{dd}/{run_id}/raw.csv
normalized/
{vendor_id}/
{run_id}/normalized.csv
outputs/
{run_id}/
merged_inventory.csv
merged_pricing.csv
reports/
{run_id}/
run_summary.json
errors.json
logs/
{run_id}/
worker.log.jsonl

```

### Retention
- S3 Lifecycle:
  - `inbound/`, `normalized/`, `outputs/`, `reports/`, `logs/` expire after **30 days**
- Optionally keep `outputs/` for 60–90 days for premium tier later

### Canonical inventory schema (CSV columns)
Minimum recommended canonical fields:
- `sku` (string, required)
- `vendor_sku` (string, optional)
- `vendor_id` (string, required)
- `quantity_available` (int, required)
- `lead_time_days` (int, optional)
- `cost` (decimal, optional)
- `map_price` (decimal, optional)
- `price` (decimal, required, derived)
- `msrp` (decimal, optional)
- `condition` (string: new/used/refurb)
- `brand` (string, optional)
- `title` (string, optional)
- `updated_at` (timestamp, required)

### Merge policy (tenant-configurable)
Rules examples:
- Prefer vendor A for availability, vendor B for cost
- If vendor A quantity is 0, fall back to vendor B
- If multiple vendors have stock, choose lowest landed cost

---

## 8) Configuration (Tenant + Vendor)

### Tenant config (YAML/JSON)
Store in DynamoDB or S3; recommend DynamoDB with versioning.

Example:

```yaml
tenant_id: "acme_parts"
timezone: "America/Los_Angeles"
default_currency: "USD"

vendors:
  - vendor_id: "vendor_1"
    inbound:
      type: "s3"
      s3_prefix: "tenants/acme_parts/inbound/vendor_1/"
    parser:
      format: "csv"
      delimiter: ","
      encoding: "utf-8"
      column_map:
        sku: "SKU"
        quantity_available: "QTY"
        cost: "COST"
        map_price: "MAP"
    sku_map:
      type: "file"
      s3_key: "tenants/acme_parts/config/sku_map_vendor_1.csv"

pricing:
  base_margin_pct: 0.20
  min_price: 25.00
  shipping_handling_flat: 9.99
  map_policy:
    enforce: true
    map_floor_behavior: "max(price, map_price)"
  rounding:
    mode: "nearest"
    increment: 0.99

merge:
  strategy: "best_offer"
  best_offer:
    sort_by:
      - "in_stock_desc"
      - "lowest_landed_cost"
    landed_cost:
      include_shipping_handling: true
    fallback_lead_time_days: 7

output:
  format: "csv"
  columns:
    - sku
    - quantity_available
    - price
    - vendor_id
    - updated_at
```

### Versioning

* Every config update increments a `config_version`
* Every run stores `{tenant_id, run_id, config_version}`

---

## 9) Public API (MVP)

### Auth

* Start simple: **API keys per tenant** (stored hashed)
* Later: Cognito / OAuth if needed

### Endpoints

* `POST /v1/tenants` (internal/admin only)
* `GET /v1/tenants/{tenant_id}`
* `PUT /v1/tenants/{tenant_id}/config`
* `POST /v1/runs` (trigger run)

  * body: `{ "tenant_id": "...", "run_type": "inventory_sync", "vendors": ["vendor_1", ...] }`
* `GET /v1/runs/{run_id}` (status)
* `GET /v1/runs/{run_id}/artifacts` (S3 presigned links)
* `GET /v1/health`

### Run statuses

* `QUEUED`
* `RUNNING`
* `SUCCEEDED`
* `FAILED` (with error report pointer)

---

## 10) Job Flow (Execution)

### 1) Trigger

* API receives run request
* Writes a run record in DynamoDB:

  * `run_id`, `tenant_id`, `config_version`, `requested_at`, `status=QUEUED`
* Enqueues job to SQS with:

  * `run_id`, `tenant_id`, `vendors`, `config_version`

### 2) Worker consumes job

* Loads tenant config by `tenant_id` + `config_version`
* Finds latest inbound files for each vendor (or uses explicit keys if provided)
* For each vendor:

  * parse CSV → canonical rows
  * apply SKU mapping
  * write normalized CSV to S3
* Merge:

  * combine vendor rows into merged availability and cost basis
* Pricing:

  * compute landed cost
  * apply margin, floor/ceil, MAP, rounding
* Output:

  * write `merged_inventory.csv` (and `merged_pricing.csv` if separate) to S3
  * write `run_summary.json` (counts, timing, warnings)
* Update DynamoDB run status + artifact keys

### 3) Failure handling

* Fail fast on:

  * missing inbound feed
  * unparseable CSV structure
* Write `errors.json` with:

  * vendor, row number, reason, sample data
* Mark run FAILED but persist artifacts

### 4) Idempotency

* `run_id` is unique
* Worker can be retried safely:

  * outputs are written under `{run_id}` prefix

---

## 11) Re-using the Existing Codebase (Fork)

### Guiding principle

**Do not port everything. Extract only what powers “inventory + pricing consolidation.”**
Delete or ignore tracking, email, and unrelated integrations for MVP.

### Likely extraction targets

* Inventory parsing + normalization utilities
* SKU mapping logic
* Pricing utilities (margin, MAP, rounding)
* Vendor config structures / domain types (strip to essentials)

### Refactor outcome (target layout)

```
relay_inventory/
  app/
    api/                    # FastAPI routes
    auth/                   # API key middleware
    config/                 # config load + validation
    jobs/                   # SQS job schema + enqueue
    models/                 # pydantic schemas
  engine/
    canonical/              # canonical schema definitions
    parsing/                # CSV parsing, column mapping
    normalize/              # sku maps, cleaning, validation
    pricing/                # pricing engine
    merge/                  # merge strategies
  adapters/
    storage/
      s3.py                 # S3 read/write, presigned
    queue/
      sqs.py
  persistence/
    dynamo_runs.py
    dynamo_tenants.py
  tests/
  scripts/
    local_run.py            # local runner for fast iteration
```

### Explicit “stop doing” list (MVP)

* Outlook / email integration
* Tracking updates
* Fishbowl integration
* Any UI
* Shopify/Magento push unless a pilot requires it

---

## 12) Implementation Plan (Thorough, execution-first)

### Phase A — Foundations (Day 1–3)

**Deliverable:** repo builds, tests run, local runner works

1. Create new repo under Relay Inventory org
2. Add Python project scaffold

   * Use `pipenv` (consistent with your workflow)
   * Add `ruff`, `mypy` (optional), `pytest`
3. Implement local runner (`scripts/local_run.py`)

   * read tenant config from local YAML
   * read vendor CSVs from local folder
   * output canonical + merged CSVs locally
4. Define canonical schema + validation

   * strict typing for required fields
   * enforce numeric parsing rules

**Definition of done**

* `python scripts/local_run.py --tenant test_tenant` produces output CSVs
* Unit tests cover parsing + pricing edge cases

---

### Phase B — S3 I/O + Retention (Day 4–7)

**Deliverable:** S3-based ingestion + artifact writing

1. Implement S3 storage adapter

   * list latest inbound object(s) by prefix
   * download + stream parse
   * upload normalized/output artifacts
2. Define bucket layout conventions
3. Add S3 lifecycle policy (manual in console first; CDK later)

**Definition of done**

* Worker can run end-to-end using S3 inbound CSVs and produce outputs in S3

---

### Phase C — Job System (Day 8–11)

**Deliverable:** queued runs, stateless worker

1. Implement SQS queue adapter
2. Define job payload schema (pydantic)
3. Implement worker entrypoint:

   * fetch job, run engine, update run state
4. Add retry logic:

   * transient failures retry
   * parsing/config failures fail fast with report

**Definition of done**

* `POST /runs` enqueues job
* Worker picks job and produces outputs

---

### Phase D — API + Tenant Config (Day 12–16)

**Deliverable:** minimal API for runs + artifacts

1. FastAPI service
2. Tenant config storage:

   * Option 1: DynamoDB `Tenants` table with `tenant_id` PK, `config_version` SK
   * Option 2 (faster): config stored in S3 per tenant + versioning in key path
3. Runs metadata storage:

   * `Runs` table: `run_id` PK, `tenant_id`, `status`, timestamps, artifact keys
4. Presigned URL endpoint for artifacts

**Definition of done**

* Tenant config can be set/updated
* Run can be triggered and queried for status and artifacts

---

### Phase E — Hardening + Observability (Day 17–21)

**Deliverable:** production-grade failure clarity

1. Structured logging (json)
2. Run summary report:

   * row counts per vendor
   * # invalid rows dropped
   * time per stage
3. Alerts (simple):

   * CloudWatch alarm on consecutive failures
4. Input validation:

   * column presence checks
   * schema versioning

**Definition of done**

* Failures generate actionable errors and do not require reading raw logs

---

### Phase F — Pilot Readiness (Day 22–30)

**Deliverable:** onboarding docs + first test merchant

1. Minimal onboarding guide:

   * how to upload feeds to S3
   * expected column maps
   * how outputs are delivered
2. Create “test tenant” with 2 vendors and real-ish sample data
3. Performance sanity:

   * run completes < few minutes for typical dataset
4. Security hygiene:

   * separate AWS IAM per environment
   * no secrets in repo
   * API keys hashed

**Definition of done**

* A merchant can upload files, trigger run, download merged feed, repeat daily

---

## 13) Testing Strategy (must-have)

### Unit tests (engine)

* Parsing:

  * delimiter/encoding edge cases
  * missing columns
  * numeric coercion
* SKU mapping:

  * missing map keys
  * collisions
* Merge:

  * tie-break behavior
  * fallback vendor behavior
* Pricing:

  * MAP enforcement
  * rounding increments
  * min price floors

### Integration tests (adapters)

* S3 read/write with localstack (optional)
* SQS end-to-end with moto (optional)
* “Golden file” tests:

  * input CSV → expected output CSV exact match

### Regression harness

* Store fixtures for 2–3 “typical vendors”
* Every change must keep outputs stable unless intentional

---

## 14) Deployment Plan (fast + reversible)

### Environments

* `dev`
* `pilot`
* `prod` (later)

### Initial deploy options

Option A (fastest):

* Single EC2 (API + worker) + SQS + S3

Option B (cleaner scale):

* ECS Fargate services: `api`, `worker`

**Recommendation:** Start with ECS Fargate if you’re comfortable; otherwise EC2 first, but keep the worker stateless.

### CI/CD

* GitHub Actions:

  * run tests
  * build Docker image
  * push to ECR
  * deploy (optional at first)

---

## 15) Security & Compliance (MVP minimal)

* API keys per tenant (rotate-able)
* No customer PII expected (mostly inventory)
* Encrypt S3 with SSE-S3 or SSE-KMS
* Least-privilege IAM roles:

  * worker: read inbound, write outputs, update runs
  * api: read configs, write runs, presign artifacts
* Per-tenant isolation via S3 prefixes and signed URLs

---

## 16) Pricing & Packaging (for early pilots)

**Do not race to the bottom.**

Suggested early structure:

* Starter: $199/mo (2 vendors, daily)
* Standard: $499/mo (5 vendors, hourly)
* Pro: $999/mo (custom logic, priority)

Setup: $500–$1500 depending on config complexity

---

## 17) Development Checklist (Codex-ready)

### Engineering tasks (ordered)

1. [ ] Canonical schema + pydantic models
2. [ ] CSV parser with column_map
3. [ ] SKU mapping module (file-backed + in-memory)
4. [ ] Pricing engine (MAP + rounding + floors)
5. [ ] Merge strategy module
6. [ ] Local runner + golden fixtures
7. [ ] S3 adapter + bucket layout
8. [ ] Run artifacts writer + run_summary/errors.json
9. [ ] SQS job schema + queue adapter
10. [ ] Worker entrypoint + retries
11. [ ] Dynamo runs table + tenants config store
12. [ ] FastAPI endpoints + API key auth
13. [ ] Presigned artifact endpoints
14. [ ] Deployment (ECS or EC2) + env configs
15. [ ] Pilot onboarding doc + example tenant config

### Definition of Done (MVP)

* A tenant can upload vendor CSVs to S3
* A run can be triggered via API
* Worker generates normalized + merged output in S3
* Status + artifacts retrievable via API
* Failures produce clear error reports
* Core logic covered with tests + golden files

---

## 18) Risks & Mitigations (be honest)

### Risk: Vendor feeds are messy and constantly change

Mitigation:

* strict parser validation + clear error reports
* per-vendor column mapping
* “feed contract” docs per vendor
* keep normalization robust and configurable

### Risk: KTLO grows with each new integration

Mitigation:

* do not add integrations early
* keep onboarding to “S3 upload + config”
* enforce a standard inbound contract whenever possible

### Risk: Scope creep (Shopify app, dashboards, etc.)

Mitigation:

* revenue-first rule: only build what a paying pilot requires

---

## 19) Immediate Next Actions (today)

1. Create `relay-inventory` repo with the target folder structure
2. Implement `scripts/local_run.py` with one fixture vendor CSV
3. Define canonical schema and write 10+ unit tests around parsing + pricing
4. Draft one tenant config YAML and validate it with pydantic
5. Start S3 adapter (read latest inbound by prefix; write outputs by run_id)

---

### Final note

Relay Inventory wins by being **boring, deterministic, and easy to onboard**.
Don’t build a platform. Build a pipeline that works every day.
