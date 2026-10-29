"""Snapshot tests: the generated SQL for each transformation type is compared with reviewed files.

Regenerate after an intentional change:  UPDATE_GOLDEN=1 pytest tests/test_golden.py
"""
import os
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"
CASES = {
    "stg_source_qualifier_join.sql": "models/staging/stg_sq_orders_customers.sql",
    "stg_source_definition.sql": "models/staging/stg_orders.sql",
    "int_expression_variables_lookup_sequence_filter.sql": "models/intermediate/int_fil_valid.sql",
    "int_router_group_update_strategy.sql": "models/intermediate/int_upd_high.sql",
    "int_router_group_low.sql": "models/intermediate/int_rtr_split_low.sql",
    "int_joiner_aggregator_lookup_sorter_rank.sql": "models/intermediate/int_rnk_top.sql",
    "int_deletes.sql": "models/intermediate/fct_orders_high__deletes.sql",
    "mart_incremental_merge.sql": "models/marts/fct_orders_high.sql",
    "mart_table_load.sql": "models/marts/dim_cust_summary.sql",
    "macro_unconnected_lookup.sql": "macros/lkp_lkp_region.sql",
    "marts_schema.yml": "models/marts/_marts__models.yml",
    "recon_fct_orders_high.sql": "analyses/recon_fct_orders_high.sql",
}


@pytest.mark.parametrize("golden,generated", sorted(CASES.items()))
def test_matches_golden(converted, golden, generated):
    actual = (converted[0] / generated).read_text()
    path = GOLDEN / golden
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(actual)
    assert path.exists(), f"missing golden file {path}; run with UPDATE_GOLDEN=1 and review it"
    assert actual == path.read_text()
