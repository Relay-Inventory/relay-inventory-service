"""End-to-end test chaining all five Phase 3 Lambda handlers together (plain Python calls,
no real Step Functions -- only moto-mocked S3 + DynamoDB), threading each handler's output
into the next's input exactly as the real state machine will:

    load_config -> fetch_and_hash (once per vendor) -> merge -> diff_and_safety -> persist_and_record

Reuses the three_vendor_overlap fixture (three vendors, deliberate SKU overlap on SKU-100)
that Phase 1's test_reconcile.py already proves reconcile() sums correctly across vendors.

FIXED BUG, found while first writing this test -- kept here as the historical record of why
`SnapshotDiff.is_first_run` and evaluate_safety's exemption for it exist:

diff_snapshots(previous=None, current) treats *every* row of `current` as "added". Before the
fix, evaluate_safety's max_changed_sku_pct check (default 0.5) computed changed_pct=1.0 (100%)
for ANY non-empty first run, unconditionally halting every shop's very first sync -- and since a
halted run never promotes snapshots/<shop_id>/latest.parquet, no shop could ever reach a
successful baseline at all. Confirmed by direct arithmetic against the real code before fixing:
total_skus = len(added_skus), changed_count = len(added_skus), so changed_pct was always
exactly 1.0 whenever there was at least one SKU. Fixed in engine/diff.py (added
`SnapshotDiff.is_first_run`) and engine/safety.py (evaluate_safety skips max_changed_sku_pct
when `diff.is_first_run` is True) -- see those files' own comments and
test_safety.py::test_evaluate_safety_first_run_never_halts_on_changed_sku_pct for the
unit-level regression test. This test proves the fix holds at the full-chain level too:
  1. Run 1 (genuinely the shop's first-ever run, no baseline at all): correctly reconciles the
     3-way SKU-100 overlap AND succeeds, promoting to latest.parquet despite having nothing to
     compare against.
  2. Run 2 (a shop with existing history): an ordinary small diff against a real baseline also
     succeeds and promotes -- the two scenarios use different code paths inside evaluate_safety
     (is_first_run exemption vs. an ordinary diff that simply doesn't trip any threshold) and
     both are worth covering, not just one.
"""

from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from inventory_aggregator.adapters.storage.s3 import S3Adapter
from inventory_aggregator.app.config.loader import load_tenant_config
from inventory_aggregator.engine.canonical.io import read_parquet_bytes, write_parquet_bytes
from inventory_aggregator.lambda_handlers import (
    diff_and_safety,
    fetch_and_hash,
    load_config,
    merge,
    persist_and_record,
    write_to_shopify,
)
from inventory_aggregator.lambda_handlers import write_to_shopify as write_to_shopify_module
from inventory_aggregator.persistence.single_table import SingleTable, run_sk

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "three_vendor_overlap"
BUCKET = "test-bucket"
TABLE_NAME = "shop-data"
SHOP_ID = "three_vendor_overlap"

# Hand-derived from the fixture + tenant_config.yaml, for SKU-100 (present in all 3 vendors):
#   vendor_1: qty=10, buffer_qty=2               -> adjusted qty = 8   (no rules)
#   vendor_2: qty=4,  buffer_qty=0, inclusion "quantity_available > 0" on adjusted qty (4>0 keeps)
#             -> adjusted qty = 4
#   vendor_3: qty=12, buffer_qty=5, exclusion "cost > 500" (12.75, not excluded)
#             -> adjusted qty = 7
# available_qty = sum across vendors = 8 + 4 + 7 = 19
EXPECTED_SKU_100_AVAILABLE_QTY = 19


@pytest.fixture()
def aws(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("ARTIFACT_BUCKET", BUCKET)
    monkeypatch.setenv("SHOP_DATA_TABLE", TABLE_NAME)
    with mock_aws():
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.create_bucket(Bucket=BUCKET)

        dynamo = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamo.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "shop_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "shop_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        table.meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        yield


def _run_load_through_merge(s3: S3Adapter, single_table: SingleTable) -> dict:
    """Runs load_config -> fetch_and_hash (per vendor) -> merge, and returns merge's output
    merged with the shop_id/run_id/config_version context downstream stages need -- exactly
    the shape a Step Functions Pass/ResultPath merge would produce."""
    load_result = load_config.handler({"shop_id": SHOP_ID, "table_name": TABLE_NAME})
    run_id = load_result["run_id"]

    # Upload each vendor's raw feed under the run_id load_config just minted -- fetch_and_hash
    # reads from raw/<shop_id>/<vendor_id>/<run_id>.csv.
    for vendor_config in load_result["tenant_config"]["vendors"]:
        vendor_id = vendor_config["vendor_id"]
        csv_bytes = (FIXTURE_DIR / f"{vendor_id}.csv").read_bytes()
        s3.upload_bytes(f"raw/{SHOP_ID}/{vendor_id}/{run_id}.csv", csv_bytes)

    vendor_results = []
    for vendor_config in load_result["tenant_config"]["vendors"]:
        vendor_result = fetch_and_hash.handler({
            "shop_id": SHOP_ID,
            "run_id": run_id,
            "vendor_config": vendor_config,
            "bucket": BUCKET,
            "table_name": TABLE_NAME,
        })
        vendor_results.append(vendor_result)

    merge_result = merge.handler({
        "shop_id": SHOP_ID,
        "run_id": run_id,
        "tenant_config": load_result["tenant_config"],
        "vendor_results": vendor_results,
        "bucket": BUCKET,
        "table_name": TABLE_NAME,
    })

    return {
        "shop_id": SHOP_ID,
        "run_id": run_id,
        "config_version": load_result["config_version"],
        "snapshot_key": merge_result["snapshot_key"],
    }


def test_end_to_end_first_run_reconciles_correctly_and_succeeds_with_no_baseline(aws) -> None:
    s3 = S3Adapter(BUCKET)
    single_table = SingleTable(TABLE_NAME)

    config = load_tenant_config(FIXTURE_DIR / "tenant_config.yaml")
    single_table.put_config(SHOP_ID, config.model_dump(), version=1)

    context = _run_load_through_merge(s3, single_table)

    # The candidate snapshot merge.handler wrote -- verify the 3-way SKU-100 overlap sums as
    # expected, exactly like Phase 1's test_reconcile.py proves reconcile() does for
    # overlapping SKUs.
    candidate_df = read_parquet_bytes(s3.download_bytes(context["snapshot_key"]))
    sku_100 = candidate_df[candidate_df["sku"] == "SKU-100"].iloc[0]
    assert int(sku_100["available_qty"]) == EXPECTED_SKU_100_AVAILABLE_QTY

    diff_result = diff_and_safety.handler({
        "shop_id": SHOP_ID,
        "snapshot_key": context["snapshot_key"],
        "bucket": BUCKET,
    })

    # Per the module docstring's fixed-bug note: a first run has no baseline, so every SKU is
    # "added" -- that must NOT trip max_changed_sku_pct (the is_first_run exemption).
    assert diff_result["halted"] is False
    assert diff_result["reason"] is None
    assert diff_result["diff_summary"]["added_skus"] == len(candidate_df)
    assert diff_result["diff_summary"]["removed_skus"] == 0
    assert diff_result["diff_summary"]["unchanged_count"] == 0

    persist_result = persist_and_record.handler({
        **context,
        "halted": diff_result["halted"],
        "reason": diff_result["reason"],
        "diff_summary": diff_result["diff_summary"],
        "bucket": BUCKET,
        "table_name": TABLE_NAME,
    })

    assert persist_result["status"] == "SUCCEEDED"
    promoted_df = read_parquet_bytes(s3.download_bytes(f"snapshots/{SHOP_ID}/latest.parquet"))
    promoted_sku_100 = promoted_df[promoted_df["sku"] == "SKU-100"].iloc[0]
    assert int(promoted_sku_100["available_qty"]) == EXPECTED_SKU_100_AVAILABLE_QTY

    run_item = single_table.get_item(SHOP_ID, run_sk(context["run_id"]))
    assert run_item is not None
    assert run_item.status == "SUCCEEDED"
    assert run_item.failed_stage is None
    assert run_item.error_message is None


def test_end_to_end_ordinary_run_against_existing_baseline_succeeds_and_promotes(aws) -> None:
    """Same full chain, but for a shop with existing history: latest.parquet is seeded with a
    near-identical prior snapshot (one unit off on a single SKU) before diff_and_safety runs,
    so this is an ordinary small-diff run rather than a first-ever one. Demonstrates the
    SUCCEEDED / promote-to-latest.parquet happy path the chain is designed for."""
    s3 = S3Adapter(BUCKET)
    single_table = SingleTable(TABLE_NAME)

    config = load_tenant_config(FIXTURE_DIR / "tenant_config.yaml")
    single_table.put_config(SHOP_ID, config.model_dump(), version=1)

    context = _run_load_through_merge(s3, single_table)
    candidate_df = read_parquet_bytes(s3.download_bytes(context["snapshot_key"]))

    # Seed latest.parquet with a copy of the real candidate, minus one unit off SKU-100 --
    # a tiny, realistic day-over-day diff, not a synthetic baseline invented to force a result.
    baseline_df = candidate_df.copy()
    baseline_idx = baseline_df.index[baseline_df["sku"] == "SKU-100"][0]
    baseline_df.loc[baseline_idx, "available_qty"] = EXPECTED_SKU_100_AVAILABLE_QTY - 1
    s3.upload_bytes(f"snapshots/{SHOP_ID}/latest.parquet", write_parquet_bytes(baseline_df))

    diff_result = diff_and_safety.handler({
        "shop_id": SHOP_ID,
        "snapshot_key": context["snapshot_key"],
        "bucket": BUCKET,
    })
    assert diff_result["halted"] is False
    assert diff_result["reason"] is None

    persist_result = persist_and_record.handler({
        **context,
        "halted": diff_result["halted"],
        "reason": diff_result["reason"],
        "diff_summary": diff_result["diff_summary"],
        "bucket": BUCKET,
        "table_name": TABLE_NAME,
    })

    assert persist_result["status"] == "SUCCEEDED"

    promoted_df = read_parquet_bytes(s3.download_bytes(f"snapshots/{SHOP_ID}/latest.parquet"))
    promoted_sku_100 = promoted_df[promoted_df["sku"] == "SKU-100"].iloc[0]
    assert int(promoted_sku_100["available_qty"]) == EXPECTED_SKU_100_AVAILABLE_QTY

    run_item = single_table.get_item(SHOP_ID, run_sk(context["run_id"]))
    assert run_item is not None
    assert run_item.status == "SUCCEEDED"
    assert run_item.artifacts["snapshot_key"] == context["snapshot_key"]


class _FakeAdminClient:
    """Stands in for ShopifyAdminClient for this full-chain test -- no real gql transport,
    no real network. Every quantity in the request is reported as successfully changed."""

    def __init__(self, shop_domain: str, access_token: str) -> None:
        self.shop_domain = shop_domain
        self.access_token = access_token

    def execute(self, query: str, variable_values: dict | None = None) -> dict:
        quantities = variable_values["input"]["quantities"]
        return {
            "inventorySetQuantities": {
                "inventoryAdjustmentGroup": {
                    "changes": [{"name": "available", "delta": q["quantity"]} for q in quantities]
                },
                "userErrors": [],
            }
        }


def test_end_to_end_not_halted_run_writes_to_shopify_before_persisting(
    aws, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extends the chain one step further than the other two end-to-end tests: the
    Choice(halted?) branch in the real state machine (COMMIT_PLAN.md Commit 4.3) only calls
    WriteToShopify when DiffAndSafety did not halt -- this proves that branch end to end,
    including PersistAndRecord recording the write outcome on the RUN# item afterward."""
    monkeypatch.setattr(write_to_shopify_module, "ShopifyAdminClient", _FakeAdminClient)

    secrets_client = boto3.client("secretsmanager", region_name="us-east-1")
    secrets_client.create_secret(
        Name=f"inventory-aggregator/{SHOP_ID}/shopify-access-token",
        SecretString="test-access-token",
    )

    s3 = S3Adapter(BUCKET)
    single_table = SingleTable(TABLE_NAME)

    config = load_tenant_config(FIXTURE_DIR / "tenant_config.yaml")
    config_dict = config.model_dump(mode="json")
    config_dict["location_id"] = "gid://shopify/Location/1"
    single_table.put_config(SHOP_ID, config_dict, version=1)

    context = _run_load_through_merge(s3, single_table)
    candidate_df = read_parquet_bytes(s3.download_bytes(context["snapshot_key"]))
    for sku in candidate_df["sku"]:
        single_table.put_sku_mapping(SHOP_ID, sku, f"gid://shopify/InventoryItem/{sku}")

    diff_result = diff_and_safety.handler({
        "shop_id": SHOP_ID, "snapshot_key": context["snapshot_key"], "bucket": BUCKET,
    })
    assert diff_result["halted"] is False  # first run -- the is_first_run exemption applies

    tenant_config_for_write = single_table.get_config(SHOP_ID, context["config_version"]).config
    write_result = write_to_shopify.handler({
        "shop_id": SHOP_ID,
        "snapshot_key": context["snapshot_key"],
        "tenant_config": tenant_config_for_write,
        "bucket": BUCKET,
        "table_name": TABLE_NAME,
    })
    assert write_result["write_status"] == "SUCCEEDED"
    assert write_result["written_count"] == len(candidate_df)
    assert write_result["errors"] == []

    persist_result = persist_and_record.handler({
        **context,
        "halted": diff_result["halted"],
        "reason": diff_result["reason"],
        "diff_summary": diff_result["diff_summary"],
        "write_status": write_result["write_status"],
        "written_count": write_result["written_count"],
        "write_errors": write_result["errors"],
        "bucket": BUCKET,
        "table_name": TABLE_NAME,
    })
    assert persist_result["status"] == "SUCCEEDED"

    run_item = single_table.get_item(SHOP_ID, run_sk(context["run_id"]))
    assert run_item.artifacts["write_status"] == "SUCCEEDED"
    assert run_item.artifacts["written_count"] == len(candidate_df)
    assert "write_errors" not in run_item.artifacts
