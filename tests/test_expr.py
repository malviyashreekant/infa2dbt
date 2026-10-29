import pytest

from infa2dbt.expr.translator import Context, translate, translate_condition

CTX = Context(ref=lambda n: n.upper(), params={"MAXAMT": "100", "RUN": "x"},
              lookup_macros={"lkp_cust": "lkp_lkp_cust"})


@pytest.mark.parametrize("src,expected", [
    ("IIF(ISNULL(A), 'UNK', LTRIM(RTRIM(A)))", "CASE WHEN A IS NULL THEN 'UNK' ELSE LTRIM(RTRIM(A)) END"),
    ("IIF(A > 1, 'x')", "CASE WHEN A > 1 THEN 'x' END"),
    ("DECODE(TRUE, A > 100, 'H', A > 10, 'M', 'L')", "CASE WHEN A > 100 THEN 'H' WHEN A > 10 THEN 'M' ELSE 'L' END"),
    ("DECODE(S, 'A', 'Act', 'Other')", "CASE S WHEN 'A' THEN 'Act' ELSE 'Other' END"),
    ("TO_DATE(D, 'YYYY-MM-DD HH24:MI:SS.US')", "TO_TIMESTAMP(D, 'YYYY-MM-DD HH24:MI:SS.FF6')"),
    ("TO_DATE(D, 'YYYY-MM-DD')", "TO_DATE(D, 'YYYY-MM-DD')"),
    ("A || '-' || B", "((A || '-') || B)"),
    ("A % 3", "MOD(A, 3)"),
    ("SUM(A, S = 'x')", "SUM(CASE WHEN S = 'x' THEN A END)"),
    ("ADD_TO_DATE(D, 'MM', 3)", "(ADD_MONTHS(D, 3))"),
    ("GET_DATE_PART(D, 'YYYY')", "EXTRACT(YEAR FROM D)"),
    ("A + $$MAXAMT", "(A + {{ var('maxamt', 100) }})"),
    ("SYSDATE", "SYSDATE"),
    ("DD_UPDATE", "1"),
    ("ISNULL(A)", "CASE WHEN A IS NULL THEN 1 ELSE 0 END"),
])
def test_translations(src, expected):
    t = translate(src, CTX)
    assert t.sql == expected
    assert not t.needs_review


def test_empty_string_is_null_in_oracle():
    t = translate_condition("S != ''", CTX)
    assert t.sql == "S IS NOT NULL"
    assert any("empty-string" in w for w in t.warnings)
    assert translate("''", CTX).sql == "NULL"


def test_unconnected_lookup_becomes_macro_call_with_escaped_args():
    t = translate(":LKP.lkp_cust(A, 'x''y')", CTX)
    assert t.sql.startswith("{{ lkp_lkp_cust(")
    assert "lkp_cust" in t.lookups


@pytest.mark.parametrize("src", ["FOO(1)", "IIF(A >", "ERROR('bad row')", ":SP.x(1)", "SETVARIABLE($$X, 1)", "$PMSessionName"])
def test_untranslatable_is_flagged_never_silent(src):
    t = translate(src, CTX)
    assert t.needs_review
    assert "NEEDS_REVIEW" in t.sql


def test_unknown_lookup_is_flagged():
    assert translate(":LKP.nope(A)", CTX).needs_review


def test_condition_coercion():
    assert translate_condition("AMOUNT > 0 AND NOT ISNULL(ID)", CTX).sql == "(AMOUNT > 0 AND NOT (ID IS NULL))"
    assert translate_condition("TRUE", CTX).sql == "1 = 1"
    assert translate_condition("FLAG", CTX).sql == "(FLAG) <> 0"


def test_in_case_flag():
    t = translate("IN(S, 'A', 'B', 0)", CTX)
    assert "UPPER(S) IN (UPPER('A'), UPPER('B'))" in t.sql
    assert translate("IN(S, 'A', 'B', 1)", CTX).sql.count("UPPER") == 0
