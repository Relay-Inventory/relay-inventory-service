import pytest

from relay_inventory.app.config.loader import load_tenant_config


def test_invalid_schema_version(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("schema_version: 2\ntenant_id: test\ntimezone: UTC\ndefault_currency: USD\nvendors: []\npricing: {base_margin_pct: 0, min_price: 0, shipping_handling_flat: 0, map_policy: {enforce: true}, rounding: {mode: nearest, increment: 0.01}}\nmerge: {strategy: best_offer}\noutput: {columns: [sku]}\n")
    with pytest.raises(ValueError, match="Unsupported schema_version"):
        load_tenant_config(path)
