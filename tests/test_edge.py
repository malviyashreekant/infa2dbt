"""Edge cases that real exports hit. Each one must either convert correctly or be flagged, never vanish."""
import make_fixtures as mf
import pytest

from infa2dbt import validate
from infa2dbt.config import Config
from infa2dbt.pipeline import convert_paths

N, S = "number(p,s)", "string"


def build(tmp_path, transformations, instances, connectors, sources=None, targets=None, extra=""):
    srcs = sources or [mf.source("SRC", "OWN", [("ID", N, 10, dict(key=True, notnull=True)), ("V", N, 10), ("GRP", S, 5)])]
    tgts = targets or [mf.target("TGT", [("ID", N, 10, dict(key=True)), ("V", N, 10), ("GRP", S, 5)])]
    base_inst = [mf.inst("SRC", "SOURCE", ttype="Source Definition"), mf.inst("TGT", "TARGET", ttype="Target Definition")]
    m = (f"<MAPPING {mf.attrs(NAME='m_edge')}>\n" + "\n".join(transformations + base_inst + instances + connectors)
         + extra + "\n</MAPPING>")
    xml = tmp_path / "edge.xml"
    xml.write_text(mf.wrap(srcs, tgts, m))
    out = tmp_path / "out"
    res = convert_paths([xml], out, Config())
    return out, res


SQ = mf.trans("SQ", "Source Qualifier", [mf.tf("ID", dt=N, prec=10), mf.tf("V", dt=N, prec=10), mf.tf("GRP", prec=5)], {"Sql Query": ""})
SQ_I = mf.inst("SQ", "TRANSFORMATION", ttype="Source Qualifier")
SRC_TO_SQ = [mf.conn("SRC", f, "SQ", f) for f in ("ID", "V", "GRP")]


def to_tgt(frm, fields=("ID", "V", "GRP")):
    return [mf.conn(frm, f, "TGT", f) for f in fields]


def review_text(res):
    return "\n".join(f"{r.where}: {r.reason}" for p in res.plans for r in p.review)


def test_stateful_variable_port_is_flagged(tmp_path):
    exp = mf.trans("EXP", "Expression", [
        mf.tf("ID", dt=N, prec=10), mf.tf("V", dt=N, prec=10), mf.tf("GRP", prec=5),
        mf.tf("v_PREV", "LOCAL VARIABLE", N, 10, expr="v_RUN"),
        mf.tf("v_RUN", "LOCAL VARIABLE", N, 10, expr="v_RUN + V"),
        mf.tf("o_RUN", "OUTPUT", N, 10, expr="v_RUN")])
    out, res = build(tmp_path, [SQ, exp], [SQ_I, mf.inst("EXP", "TRANSFORMATION", ttype="Expression")],
                     SRC_TO_SQ + [mf.conn("SQ", f, "EXP", f) for f in ("ID", "V", "GRP")] + to_tgt("EXP", ("ID", "V", "GRP")))
    assert "depends on the previous row" in review_text(res)
    assert res.findings.ok  # flagged, still a valid model


def test_ungrouped_aggregator_port_is_flagged(tmp_path):
    agg = mf.trans("AGG", "Aggregator", [
        mf.tf("GRP", "INPUT/OUTPUT", S, 5, expr="GRP", etype="GROUPBY"),
        mf.tf("V", "INPUT/OUTPUT", N, 10),  # neither grouped nor aggregated
        mf.tf("ID", "OUTPUT", N, 10, expr="COUNT(V)")])
    out, res = build(tmp_path, [SQ, agg], [SQ_I, mf.inst("AGG", "TRANSFORMATION", ttype="Aggregator")],
                     SRC_TO_SQ + [mf.conn("SQ", "GRP", "AGG", "GRP"), mf.conn("SQ", "V", "AGG", "V")] + to_tgt("AGG"))
    assert "last row" in review_text(res)
    sql = (out / "models/intermediate/int_agg.sql").read_text()
    assert "GROUP BY src.GRP" in sql and "MAX(src.V)" in sql


def test_union_unions_all_groups(tmp_path):
    un = mf.trans("UNI", "Union Transformation", [
        mf.tf("ID", "OUTPUT", N, 10, GROUP="OUTPUT"), mf.tf("V", "OUTPUT", N, 10, GROUP="OUTPUT"),
        mf.tf("ID1", "INPUT", N, 10, GROUP="G1"), mf.tf("V1", "INPUT", N, 10, GROUP="G1"),
        mf.tf("ID2", "INPUT", N, 10, GROUP="G2"), mf.tf("V2", "INPUT", N, 10, GROUP="G2")])
    flt = mf.trans("FIL", "Filter", [mf.tf("ID", dt=N, prec=10), mf.tf("V", dt=N, prec=10)], {"Filter Condition": "V > 5"})
    conns = SRC_TO_SQ + [mf.conn("SQ", "ID", "UNI", "ID1"), mf.conn("SQ", "V", "UNI", "V1"),
                         mf.conn("SQ", "ID", "FIL", "ID"), mf.conn("SQ", "V", "FIL", "V"),
                         mf.conn("FIL", "ID", "UNI", "ID2"), mf.conn("FIL", "V", "UNI", "V2")] + to_tgt("UNI", ("ID", "V"))
    out, res = build(tmp_path, [SQ, un, flt], [SQ_I, mf.inst("UNI", "TRANSFORMATION", ttype="Union Transformation"),
                                              mf.inst("FIL", "TRANSFORMATION", ttype="Filter")], conns)
    sql = (out / "models/intermediate/int_uni.sql").read_text()
    assert "UNION ALL" in sql and res.findings.ok, res.findings.errors


def test_mapplet_instance_is_flagged_and_covered(tmp_path):
    out, res = build(tmp_path, [SQ], [SQ_I, mf.inst("MPL_X", "MAPPLET", "mplt_x")], SRC_TO_SQ + to_tgt("SQ"))
    assert any(r.informatica_name == "MPL_X" for r in res.plans[0].coverage)
    assert "MPL_X" in review_text(res)


def test_reserved_word_column_is_quoted(tmp_path):
    srcs = [mf.source("SRC", "OWN", [("ID", N, 10, dict(key=True)), ("DATE", "date/time", 19), ("GRP", S, 5)])]
    tgts = [mf.target("TGT", [("ID", N, 10, dict(key=True)), ("DATE", "date/time", 19)])]
    sq = mf.trans("SQ", "Source Qualifier", [mf.tf("ID", dt=N, prec=10), mf.tf("DATE", dt="date/time", prec=19)], {"Sql Query": ""})
    out, res = build(tmp_path, [sq], [SQ_I], [mf.conn("SRC", "ID", "SQ", "ID"), mf.conn("SRC", "DATE", "SQ", "DATE")]
                     + to_tgt("SQ", ("ID", "DATE")), sources=srcs, targets=tgts)
    assert '"DATE"' in (out / "models/staging/stg_src.sql").read_text()
    assert res.findings.ok, res.findings.errors


def test_sequence_into_aggregator_is_evaluated_in_a_first_stage(tmp_path):
    seq = mf.trans("SEQ", "Sequence", [mf.tf("NEXTVAL", "OUTPUT", N, 10)], {"Start Value": "1", "Increment By": "1"})
    agg = mf.trans("AGG", "Aggregator", [mf.tf("GRP", "INPUT/OUTPUT", S, 5, expr="GRP", etype="GROUPBY"),
                                         mf.tf("SK", "INPUT", N, 10), mf.tf("ID", "OUTPUT", N, 10, expr="MAX(SK)")])
    out, res = build(tmp_path, [SQ, seq, agg],
                     [SQ_I, mf.inst("SEQ", "TRANSFORMATION", ttype="Sequence"), mf.inst("AGG", "TRANSFORMATION", ttype="Aggregator")],
                     SRC_TO_SQ + [mf.conn("SQ", "GRP", "AGG", "GRP"), mf.conn("SEQ", "NEXTVAL", "AGG", "SK")] + to_tgt("AGG", ("ID", "GRP")))
    sql = (out / "models/intermediate/int_agg.sql").read_text()
    assert sql.index("agg__in") < sql.index("GROUP BY")  # window function sits outside the aggregate query
    assert res.findings.ok, res.findings.errors


def test_reusable_transformation_with_different_instance_name(tmp_path):
    re_t = mf.trans("re_filter", "Filter", [mf.tf("ID", dt=N, prec=10), mf.tf("V", dt=N, prec=10), mf.tf("GRP", prec=5)],
                    {"Filter Condition": "V > 0"}).replace('REUSABLE="NO"', 'REUSABLE="YES"')
    unrelated = mf.trans("re_unused", "Filter", [mf.tf("ID", dt=N, prec=10)], {"Filter Condition": "ID > 0"}).replace('REUSABLE="NO"', 'REUSABLE="YES"')
    # reusable definitions live at folder level, outside the MAPPING node
    srcs = [mf.source("SRC", "OWN", [("ID", N, 10, dict(key=True)), ("V", N, 10), ("GRP", S, 5)]), re_t, unrelated]
    out, res = build(tmp_path, [SQ], [SQ_I, mf.inst("FIL_A", "TRANSFORMATION", "re_filter", "Filter")],
                     SRC_TO_SQ + [mf.conn("SQ", f, "FIL_A", f) for f in ("ID", "V", "GRP")] + to_tgt("FIL_A"), sources=srcs)
    cov = {r.informatica_name for r in res.plans[0].coverage}
    assert "FIL_A" in cov
    assert "re_unused" not in res.plans[0].mapping.transformations  # not instantiated here, so not part of this mapping
    assert res.findings.ok, res.findings.errors
    assert "V > 0" in (out / "models/intermediate/int_fil_a.sql").read_text()


def test_external_entities_are_not_resolved(tmp_path):
    xml = tmp_path / "xxe.xml"
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP_SECRET")
    xml.write_text(f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file://{secret}">]>'
                   f'<POWERMART><FOLDER NAME="&x;"><MAPPING NAME="m"></MAPPING></FOLDER></POWERMART>')
    from infa2dbt.parser import parse_xml
    from lxml import etree
    try:
        (m,) = parse_xml(xml)
        assert "TOP_SECRET" not in m.folder
    except etree.XMLSyntaxError:
        pass  # refusing the document is also safe: the file was never read


def test_connector_to_unknown_port_is_flagged(tmp_path):
    out, res = build(tmp_path, [SQ], [SQ_I], SRC_TO_SQ + [mf.conn("SQ", "ID", "SQ", "NOPE")] + to_tgt("SQ"))
    assert "unknown port NOPE" in review_text(res)


def test_missing_source_definition_is_flagged(tmp_path):
    out, res = build(tmp_path, [SQ], [SQ_I, mf.inst("GHOST", "SOURCE", "GHOST", "Source Definition")], SRC_TO_SQ + to_tgt("SQ"))
    assert "GHOST" in review_text(res)
    assert not res.findings.ok  # a coverage row without an artifact must fail the zero-drop gate


def test_cycle_is_reported(tmp_path):
    f1 = mf.trans("F1", "Filter", [mf.tf("ID", dt=N, prec=10)], {"Filter Condition": "TRUE"})
    f2 = mf.trans("F2", "Filter", [mf.tf("ID", dt=N, prec=10)], {"Filter Condition": "TRUE"})
    from infa2dbt.plan import PlanError
    with pytest.raises(PlanError):
        build(tmp_path, [SQ, f1, f2], [SQ_I, mf.inst("F1", "TRANSFORMATION", ttype="Filter"), mf.inst("F2", "TRANSFORMATION", ttype="Filter")],
              SRC_TO_SQ + [mf.conn("F1", "ID", "F2", "ID"), mf.conn("F2", "ID", "F1", "ID")] + to_tgt("F1", ("ID",)))


def test_source_qualifier_sql_override_is_flagged(tmp_path):
    sq = mf.trans("SQ", "Source Qualifier", [mf.tf("ID", dt=N, prec=10), mf.tf("V", dt=N, prec=10), mf.tf("GRP", prec=5)],
                  {"Sql Query": "SELECT ID, V, GRP FROM OWN.SRC WHERE V > $$MIN"})
    out, res = build(tmp_path, [sq], [SQ_I], SRC_TO_SQ + to_tgt("SQ"))
    assert "override" in review_text(res)
    assert "{{ var('min') }}" in (out / "models/staging/stg_sq.sql").read_text()
