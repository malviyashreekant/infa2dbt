import json
import re
from pathlib import Path

import yaml
from typer.testing import CliRunner

from infa2dbt import validate
from infa2dbt.cli import app
from infa2dbt.config import Config
from infa2dbt.parser import parse_xml
from infa2dbt.pipeline import convert_paths


def read(out: Path, rel: str) -> str:
    return (out / rel).read_text()


def test_parser_inventory(fixtures_dir):
    (m,) = parse_xml(fixtures_dir / "m_orders.xml")
    assert m.name == "m_orders"
    assert len(m.instances) == 11
    assert {i.type for i in m.instances.values()} == {"SOURCE", "TARGET", "TRANSFORMATION"}
    assert m.parameters == {"MIN_AMT": "0"}
    assert m.transformations["RTR_SPLIT"].groups[0] == ("HIGH", "o_BAND = 'HIGH'")


def test_gates_pass_and_nothing_dropped(converted):
    out, res = converted
    assert res.findings.ok, res.findings.errors
    for plan in res.plans:
        assert {r.informatica_name for r in plan.coverage} >= set(plan.mapping.instances)


def test_every_generated_model_parses_as_oracle(converted):
    out, _ = converted
    assert validate.validate_dir(out).ok


def test_layering_and_naming(converted):
    out, _ = converted
    names = {p.stem for p in (out / "models").rglob("*.sql")}
    assert {"stg_orders", "stg_customers", "stg_sq_orders_customers", "int_fil_valid", "int_upd_high",
            "fct_orders_high", "fct_orders_low", "dim_cust_summary"} <= names
    assert read(out, "models/staging/stg_orders.sql").count("CAST(") == 5
    assert "NUMBER(12,2)" in read(out, "models/staging/stg_orders.sql")
    assert "TIMESTAMP" not in read(out, "models/staging/stg_orders.sql")  # Oracle DATE, not TIMESTAMP


def test_variable_ports_become_sequential_ctes(converted):
    sql = read(converted[0], "models/intermediate/int_fil_valid.sql")
    assert sql.index("exp_calc__v1") < sql.index("exp_calc__v2") < sql.index("exp_calc AS (")
    assert "FROM exp_calc__v1 s" in sql  # v2 reads v1, so the dependency chain is preserved


def test_unconnected_lookup_macro_and_call(converted):
    out, _ = converted
    assert "{{ lkp_lkp_region('s.CUST_ID') }}" in read(out, "models/intermediate/int_fil_valid.sql")
    macro = read(out, "macros/lkp_lkp_region.sql")
    assert "{% macro lkp_lkp_region(arg1) %}" in macro and "RN__ = 1" in macro


def test_router_update_strategy_incremental_merge(converted):
    out, _ = converted
    mart = read(out, "models/marts/fct_orders_high.sql")
    assert "materialized='incremental'" in mart and "unique_key=['ORDER_ID']" in mart
    assert "incremental_strategy" not in mart  # dbt-oracle documents unique_key only
    assert "INFA_DML_FLAG IN (0, 1)" in mart and "DELETE FROM {{ this }}" in mart
    assert (out / "models/intermediate/fct_orders_high__rejects.sql").exists()
    assert "materialized='table'" in read(out, "models/marts/fct_orders_low.sql")  # no Update Strategy
    assert "CURRENT_TIMESTAMP AS DBT_UPDATED_AT" in mart


def test_sequence_default_is_row_number_with_warning(converted):
    out, res = converted
    assert "ROW_NUMBER() OVER (ORDER BY NULL)" in read(out, "models/intermediate/int_fil_valid.sql")
    assert any("not stable across runs" in w for p in res.plans for w in p.warnings)


def test_schema_tests_include_integrity_and_business_rules(converted):
    out, _ = converted
    doc = yaml.safe_load(read(out, "models/marts/_marts__models.yml"))
    high = next(m for m in doc["models"] if m["name"] == "fct_orders_high")
    cols = {c["name"]: c.get("tests", []) for c in high["columns"]}
    assert "not_null" in cols["ORDER_ID"] and "unique" in cols["ORDER_ID"]
    assert {"accepted_values": {"values": ["Closed", "Open", "Other"]}} in cols["STATUS_DESC"]
    assert {"dbt_utils.expression_is_true": {"expression": "BAND = 'HIGH'"}} in cols["BAND"]
    assert "not_null" in cols["DBT_UPDATED_AT"]


def test_reconciliation_sql(converted):
    out, _ = converted
    sql = read(out, "analyses/recon_fct_orders_high.sql")
    for needle in ("row_count", "sum:AMOUNT", "ORA_HASH", "MINUS", "var('legacy_schema')", "source_rows:ORDERS"):
        assert needle in sql
    assert "Row counts are expected to differ" in sql  # filter/router change row counts
    assert validate.check_sql("recon", sql) == []


def test_unsupported_components_are_flagged_not_dropped(converted):
    out, res = converted
    review = read(out, "reports/manual_review.md")
    assert "RNK_TOP" in review and "SP_AUDIT" in review
    matrix = read(out, "reports/coverage_matrix.md")
    assert re.search(r"\| SP_AUDIT \| Stored Procedure \| `\(none\)` \| int \| UNSUPPORTED", matrix)
    assert "int_rnk_top.rnk_top" in matrix and "NEEDS_REVIEW" in read(out, "models/intermediate/int_rnk_top.sql")


def test_output_is_deterministic(tmp_path, fixtures_dir):
    files = sorted(fixtures_dir.glob("*.xml"))
    a, b = tmp_path / "a", tmp_path / "b"
    convert_paths(files, a, Config())
    convert_paths(list(reversed(files)), b, Config())  # input order must not matter either
    ta = {p.relative_to(a): p.read_bytes() for p in a.rglob("*") if p.is_file()}
    tb = {p.relative_to(b): p.read_bytes() for p in b.rglob("*") if p.is_file()}
    assert ta.keys() == tb.keys()
    assert [k for k in ta if ta[k] != tb[k]] == []


def test_zero_drop_gate_catches_a_dropped_component(tmp_path, fixtures_dir):
    from infa2dbt.build import convert_mapping
    from infa2dbt.plan import Registry
    (mp,) = parse_xml(fixtures_dir / "m_orders.xml")
    reg = Registry()
    plan = convert_mapping(mp, Config(), reg)
    assert validate.zero_drop(plan, reg) == []
    plan.coverage = [r for r in plan.coverage if r.informatica_name != "FIL_VALID"]
    errs = validate.zero_drop(plan, reg)
    assert any("FIL_VALID" in e for e in errs)


def test_expression_gate_catches_untranslated_expression(fixtures_dir):
    from infa2dbt.build import convert_mapping
    from infa2dbt.plan import Registry
    (mp,) = parse_xml(fixtures_dir / "m_orders.xml")
    plan = convert_mapping(mp, Config(), Registry())
    assert validate.expression_gate(plan) == []
    plan.expr_stats["EXP_CALC"][0] -= 1
    assert validate.expression_gate(plan)


def test_bad_sql_is_caught():
    assert validate.check_sql("x", "SELECT FROM WHERE (") != []


def test_sequence_oracle_mode_and_param_override(tmp_path, fixtures_dir):
    out = tmp_path / "o"
    res = convert_paths([fixtures_dir / "m_orders.xml"], out, Config(sequence_mode="oracle_sequence"))
    assert res.findings.ok, res.findings.errors
    assert "CREATE SEQUENCE SEQ_KEY START WITH 1000" in (out / "ddl/sequences.sql").read_text()
    assert "materialized='table'" in (out / "models/intermediate/int_fil_valid.sql").read_text()


def test_cli_exit_codes(tmp_path, fixtures_dir):
    r = CliRunner()
    ok = r.invoke(app, ["convert", str(fixtures_dir), "--out", str(tmp_path / "o1")])
    assert ok.exit_code == 0, ok.output
    strict = r.invoke(app, ["convert", str(fixtures_dir), "--out", str(tmp_path / "o2"), "--fail-on-review"])
    assert strict.exit_code == 2
    bad = tmp_path / "bad.xml"
    bad.write_text("<POWERMART><FOLDER>")
    assert r.invoke(app, ["convert", str(bad), "--out", str(tmp_path / "o3")]).exit_code == 1
    empty = tmp_path / "empty.xml"
    empty.write_text("<POWERMART></POWERMART>")
    assert r.invoke(app, ["convert", str(empty), "--out", str(tmp_path / "o4")]).exit_code == 1
    assert r.invoke(app, ["check", str(tmp_path / "o1")]).exit_code == 0


def test_conversion_log_is_valid_json(converted):
    log = json.loads(read(converted[0], "reports/conversion_log.json"))
    assert {m["mapping"] for m in log} == {"m_orders", "m_customer_summary"}
