"""Generate the sanitized PowerCenter XML fixtures used by the tests.

Run:  python tests/make_fixtures.py
The structure follows real exports (FOLDER > SOURCE/TARGET/MAPPING > TRANSFORMATION/INSTANCE/CONNECTOR).
"""
from pathlib import Path
from xml.sax.saxutils import quoteattr

OUT = Path(__file__).parent / "fixtures"


def attrs(**kw):
    return " ".join(f"{k}={quoteattr(str(v))}" for k, v in kw.items())


def sfield(name, dt="string", prec=0, scale=0, key=False, notnull=False, tag="SOURCEFIELD"):
    a = attrs(NAME=name, DATATYPE=dt, PRECISION=prec, SCALE=scale,
              KEYTYPE="PRIMARY KEY" if key else "NOT A KEY", NULLABLE="NOTNULL" if notnull else "NULL")
    return f"<{tag} {a}/>"


def source(name, owner, fields):
    return (f"<SOURCE {attrs(NAME=name, DBDNAME='ORCL', OWNERNAME=owner, DATABASETYPE='Oracle')}>\n  "
            + "\n  ".join(sfield(*f[:3], **f[3]) if len(f) == 4 else sfield(*f) for f in fields) + "\n</SOURCE>")


def target(name, fields):
    return (f"<TARGET {attrs(NAME=name, DATABASETYPE='Oracle')}>\n  "
            + "\n  ".join(sfield(*f[:3], tag="TARGETFIELD", **(f[3] if len(f) == 4 else {})) for f in fields)
            + "\n</TARGET>")


def tf(name, porttype="INPUT/OUTPUT", dt="string", prec=0, scale=0, expr=None, **kw):
    a = dict(NAME=name, DATATYPE=dt, PRECISION=prec, SCALE=scale, PORTTYPE=porttype)
    if expr is not None:
        a["EXPRESSION"] = expr
        a["EXPRESSIONTYPE"] = kw.pop("etype", "GENERAL")
    a.update(kw)
    return f"<TRANSFORMFIELD {attrs(**a)}/>"


def trans(name, typ, fields, tattrs=None, groups=None):
    body = []
    for g, t, e in (groups or []):
        body.append(f"<GROUP {attrs(NAME=g, TYPE=t, **({'EXPRESSION': e} if e is not None else {}))}/>")
    body += fields
    body += [f"<TABLEATTRIBUTE {attrs(NAME=k, VALUE=v)}/>" for k, v in (tattrs or {}).items()]
    return f"<TRANSFORMATION {attrs(NAME=name, TYPE=typ, REUSABLE='NO')}>\n  " + "\n  ".join(body) + "\n</TRANSFORMATION>"


def inst(name, typ, tname=None, ttype=None):
    a = dict(NAME=name, TYPE=typ, TRANSFORMATION_NAME=tname or name)
    if ttype:
        a["TRANSFORMATION_TYPE"] = ttype
    return f"<INSTANCE {attrs(**a)}/>"


def conn(fi, ff, ti, tf_):
    return f"<CONNECTOR {attrs(FROMFIELD=ff, FROMINSTANCE=fi, TOFIELD=tf_, TOINSTANCE=ti)}/>"


def wrap(sources, targets, mapping):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<POWERMART>\n<REPOSITORY NAME="REPO">\n<FOLDER NAME="DEMO">\n'
            + "\n".join(sources + targets) + "\n" + mapping + "\n</FOLDER>\n</REPOSITORY>\n</POWERMART>\n")


# --------------------------------------------------------------- mapping 1
def orders() -> str:
    srcs = [
        source("ORDERS", "SALES", [("ORDER_ID", "number(p,s)", 10, dict(key=True, notnull=True)), ("CUST_ID", "number(p,s)", 10),
                                   ("AMOUNT", "number(p,s)", 12, dict(scale=2)), ("STATUS", "string", 1), ("ORDER_DT", "date/time", 19)]),
        source("CUSTOMERS", "SALES", [("CUST_ID", "number(p,s)", 10, dict(key=True, notnull=True)),
                                      ("CUST_NAME", "string", 50), ("SEGMENT", "string", 10)]),
    ]
    tgts = [
        target("FCT_ORDERS_HIGH", [("ORDER_ID", "number(p,s)", 10, dict(key=True, notnull=True)), ("AMOUNT", "number(p,s)", 12, dict(scale=2)),
                                   ("BAND", "string", 4), ("STATUS_DESC", "string", 10), ("REGION", "string", 30),
                                   ("SK", "number(p,s)", 10)]),
        target("FCT_ORDERS_LOW", [("ORDER_ID", "number(p,s)", 10, dict(key=True, notnull=True)), ("AMOUNT", "number(p,s)", 12, dict(scale=2)),
                                  ("BAND", "string", 4), ("STATUS_DESC", "string", 10)]),
    ]
    sq_ports = [tf("ORDER_ID", dt="number(p,s)", prec=10), tf("CUST_ID", dt="number(p,s)", prec=10),
                tf("AMOUNT", dt="number(p,s)", prec=12, scale=2), tf("STATUS", prec=1), tf("ORDER_DT", dt="date/time", prec=19),
                tf("CUST_NAME", prec=50)]
    exp_ports = [
        tf("ORDER_ID", dt="number(p,s)", prec=10), tf("CUST_ID", "INPUT", "number(p,s)", 10),
        tf("AMOUNT", dt="number(p,s)", prec=12, scale=2), tf("STATUS", "INPUT", prec=1),
        tf("ORDER_DT", dt="date/time", prec=19), tf("CUST_NAME", prec=50), tf("SK", "INPUT", "number(p,s)", 10),
        tf("v_AMT_ROUNDED", "LOCAL VARIABLE", "number(p,s)", 12, expr="ROUND(AMOUNT, 0)"),
        tf("v_BAND", "LOCAL VARIABLE", "string", 4, expr="IIF(v_AMT_ROUNDED > 1000, 'HIGH', 'LOW')"),
        tf("o_BAND", "OUTPUT", "string", 4, expr="v_BAND"),
        tf("o_STATUS_DESC", "OUTPUT", "string", 10, expr="DECODE(STATUS, 'O', 'Open', 'C', 'Closed', 'Other')"),
        tf("o_REGION", "OUTPUT", "string", 30, expr=":LKP.LKP_REGION(CUST_ID)"),
        tf("o_ORDER_YEAR", "OUTPUT", "number(p,s)", 4, expr="GET_DATE_PART(ORDER_DT, 'YYYY')"),
        tf("o_ORDER_SK", "OUTPUT", "number(p,s)", 10, expr="SK"),
    ]
    flow = ["ORDER_ID", "AMOUNT", "o_BAND", "o_STATUS_DESC", "o_REGION", "o_ORDER_SK"]
    typ = {"ORDER_ID": ("number(p,s)", 10), "AMOUNT": ("number(p,s)", 12, 2), "o_BAND": ("string", 4),
           "o_STATUS_DESC": ("string", 10), "o_REGION": ("string", 30), "o_ORDER_SK": ("number(p,s)", 10)}
    fil_ports = [tf("ORDER_ID", dt="number(p,s)", prec=10), tf("AMOUNT", dt="number(p,s)", prec=12, scale=2),
                 tf("o_BAND", prec=4), tf("o_STATUS_DESC", prec=10), tf("o_REGION", prec=30),
                 tf("o_ORDER_SK", dt="number(p,s)", prec=10)]
    rtr_ports = [tf(n, "INPUT", *typ[n], GROUP="INPUT") for n in flow]
    rtr_ports += [tf(n + "1", "OUTPUT", *typ[n], GROUP="HIGH", REF_FIELD=n) for n in flow]
    rtr_ports += [tf(n + "2", "OUTPUT", *typ[n], GROUP="LOW") for n in flow]  # no REF_FIELD: exercises the fallback
    upd_ports = [tf(n, *("INPUT/OUTPUT", *typ[n])) for n in flow]
    t = [
        trans("SQ_ORDERS_CUSTOMERS", "Source Qualifier", sq_ports,
              {"User Defined Join": "ORDERS.CUST_ID = CUSTOMERS.CUST_ID", "Source Filter": "ORDERS.STATUS <> 'X'",
               "Sql Query": "", "Number Of Sorted Ports": "0"}),
        trans("SEQ_KEY", "Sequence", [tf("NEXTVAL", "OUTPUT", "number(p,s)", 10), tf("CURRVAL", "OUTPUT", "number(p,s)", 10)],
              {"Start Value": "1000", "Increment By": "1", "Cycle": "NO"}),
        trans("EXP_CALC", "Expression", exp_ports),
        trans("LKP_REGION", "Lookup Procedure",
              [tf("in_CUST_ID", "INPUT", "number(p,s)", 10), tf("CUST_ID", "LOOKUP/OUTPUT", "number(p,s)", 10),
               tf("REGION_NAME", "LOOKUP/OUTPUT/RETURN", "string", 30)],
              {"Lookup table name": "SALES.CUST_REGION", "Lookup condition": "CUST_ID = in_CUST_ID",
               "Lookup policy on multiple match": "Use First Value"}),
        trans("FIL_VALID", "Filter", fil_ports, {"Filter Condition": "AMOUNT > 0 AND NOT ISNULL(ORDER_ID)"}),
        trans("RTR_SPLIT", "Router", rtr_ports, None,
              [("INPUT", "INPUT", None), ("HIGH", "OUTPUT", "o_BAND = 'HIGH'"), ("LOW", "OUTPUT", "o_BAND = 'LOW'"),
               ("DEFAULT1", "OUTPUT/DEFAULT", "")]),
        trans("UPD_HIGH", "Update Strategy", upd_ports,
              {"Update Strategy Expression":
               "IIF(ISNULL(o_REGION), DD_REJECT, IIF(o_STATUS_DESC = 'Closed', DD_DELETE, DD_UPDATE))"}),
    ]
    i = [inst("ORDERS", "SOURCE", ttype="Source Definition"), inst("CUSTOMERS", "SOURCE", ttype="Source Definition"),
         inst("FCT_ORDERS_HIGH", "TARGET", ttype="Target Definition"), inst("FCT_ORDERS_LOW", "TARGET", ttype="Target Definition")]
    for name, typ_ in [("SQ_ORDERS_CUSTOMERS", "Source Qualifier"), ("SEQ_KEY", "Sequence"), ("EXP_CALC", "Expression"),
                       ("LKP_REGION", "Lookup Procedure"), ("FIL_VALID", "Filter"), ("RTR_SPLIT", "Router"),
                       ("UPD_HIGH", "Update Strategy")]:
        i.append(inst(name, "TRANSFORMATION", ttype=typ_))
    c = [conn("ORDERS", f, "SQ_ORDERS_CUSTOMERS", f) for f in ("ORDER_ID", "CUST_ID", "AMOUNT", "STATUS", "ORDER_DT")]
    c.append(conn("CUSTOMERS", "CUST_NAME", "SQ_ORDERS_CUSTOMERS", "CUST_NAME"))
    c += [conn("SQ_ORDERS_CUSTOMERS", f, "EXP_CALC", f) for f in ("ORDER_ID", "CUST_ID", "AMOUNT", "STATUS", "ORDER_DT", "CUST_NAME")]
    c.append(conn("SEQ_KEY", "NEXTVAL", "EXP_CALC", "SK"))
    c += [conn("EXP_CALC", f, "FIL_VALID", f) for f in flow]
    c += [conn("FIL_VALID", f, "RTR_SPLIT", f) for f in flow]
    c += [conn("RTR_SPLIT", f + "1", "UPD_HIGH", f) for f in flow]
    for f, tfld in zip(flow, ["ORDER_ID", "AMOUNT", "BAND", "STATUS_DESC", "REGION", "SK"]):
        c.append(conn("UPD_HIGH", f, "FCT_ORDERS_HIGH", tfld))
    for f, tfld in zip(flow[:4], ["ORDER_ID", "AMOUNT", "BAND", "STATUS_DESC"]):
        c.append(conn("RTR_SPLIT", f + "2", "FCT_ORDERS_LOW", tfld))
    m = f"<MAPPING {attrs(NAME='m_orders')}>\n" + "\n".join(t + i + c) + \
        f"\n<MAPPINGVARIABLE {attrs(NAME='$$MIN_AMT', DEFAULTVALUE='0', DATATYPE='integer')}/>\n</MAPPING>"
    return wrap(srcs, tgts, m)


# --------------------------------------------------------------- mapping 2
def customer_summary() -> str:
    srcs = [
        source("CUSTOMERS", "SALES", [("CUST_ID", "number(p,s)", 10, dict(key=True, notnull=True)),
                                      ("CUST_NAME", "string", 50), ("SEGMENT", "string", 10)]),
        source("ORDERS", "SALES", [("ORDER_ID", "number(p,s)", 10, dict(key=True, notnull=True)), ("CUST_ID", "number(p,s)", 10),
                                   ("AMOUNT", "number(p,s)", 12, dict(scale=2)), ("STATUS", "string", 1), ("ORDER_DT", "date/time", 19)]),
    ]
    tgts = [target("DIM_CUST_SUMMARY", [("CUST_ID", "number(p,s)", 10, dict(key=True, notnull=True)), ("CUST_NAME", "string", 50),
                                        ("SEGMENT", "string", 10), ("TOTAL_AMT", "number(p,s)", 14),
                                        ("ORDER_CNT", "number(p,s)", 10)])]
    N, S = "number(p,s)", "string"
    t = [
        trans("SQ_CUSTOMERS", "Source Qualifier", [tf("CUST_ID", dt=N, prec=10), tf("CUST_NAME", prec=50)],
              {"Sql Query": ""}),
        trans("SQ_ORDERS", "Source Qualifier", [tf("CUST_ID", dt=N, prec=10), tf("AMOUNT", dt=N, prec=12)], {"Sql Query": ""}),
        trans("JNR_CUST_ORDERS", "Joiner",
              [tf("CUST_ID", "INPUT/OUTPUT/MASTER", N, 10), tf("CUST_NAME", "INPUT/OUTPUT/MASTER", S, 50),
               tf("CUST_ID1", "INPUT", N, 10), tf("AMOUNT", "INPUT/OUTPUT", N, 12)],
              {"Join Type": "Detail Outer Join", "Join Condition": "CUST_ID = CUST_ID1"}),
        trans("AGG_CUST", "Aggregator",
              [tf("CUST_ID", "INPUT/OUTPUT", N, 10, expr="CUST_ID", etype="GROUPBY"),
               tf("CUST_NAME", "INPUT/OUTPUT", S, 50, expr="CUST_NAME", etype="GROUPBY"),
               tf("AMOUNT", "INPUT", N, 12),
               tf("o_TOTAL", "OUTPUT", N, 14, expr="SUM(AMOUNT)"), tf("o_CNT", "OUTPUT", N, 10, expr="COUNT(AMOUNT)")]),
        trans("LKP_SEG", "Lookup Procedure",
              [tf("in_CUST_ID", "INPUT/OUTPUT", N, 10), tf("in_CUST_NAME", "INPUT/OUTPUT", S, 50),
               tf("in_TOTAL", "INPUT/OUTPUT", N, 14), tf("in_CNT", "INPUT/OUTPUT", N, 10),
               tf("CUST_ID", "LOOKUP", N, 10), tf("SEGMENT", "LOOKUP/OUTPUT", S, 10)],
              {"Lookup table name": "SALES.CUSTOMERS", "Lookup condition": "CUST_ID = in_CUST_ID",
               "Lookup policy on multiple match": "Use First Value"}),
        trans("SRT_CUST", "Sorter",
              [tf("in_CUST_ID", "INPUT/OUTPUT", N, 10, ISSORTKEY="YES", SORTDIRECTION="ASCENDING"),
               tf("in_CUST_NAME", "INPUT/OUTPUT", S, 50), tf("in_TOTAL", "INPUT/OUTPUT", N, 14),
               tf("in_CNT", "INPUT/OUTPUT", N, 10), tf("SEGMENT", "INPUT/OUTPUT", S, 10)], {"Distinct": "NO"}),
        trans("RNK_TOP", "Rank",
              [tf("in_CUST_ID", "INPUT/OUTPUT", N, 10), tf("in_CUST_NAME", "INPUT/OUTPUT", S, 50),
               tf("in_TOTAL", "INPUT/OUTPUT", N, 14), tf("in_CNT", "INPUT/OUTPUT", N, 10),
               tf("SEGMENT", "INPUT/OUTPUT", S, 10), tf("RANKINDEX", "OUTPUT", N, 10)],
              {"Top/Bottom": "Top", "Number Of Ranks": "100"}),
        trans("SP_AUDIT", "Stored Procedure", [tf("RETURN_VALUE", "OUTPUT", N, 10)], {"Stored Procedure Name": "AUDIT_LOG"}),
    ]
    i = [inst("CUSTOMERS", "SOURCE", ttype="Source Definition"), inst("ORDERS", "SOURCE", ttype="Source Definition"),
         inst("DIM_CUST_SUMMARY", "TARGET", ttype="Target Definition")]
    for name, typ_ in [("SQ_CUSTOMERS", "Source Qualifier"), ("SQ_ORDERS", "Source Qualifier"), ("JNR_CUST_ORDERS", "Joiner"),
                       ("AGG_CUST", "Aggregator"), ("LKP_SEG", "Lookup Procedure"), ("SRT_CUST", "Sorter"),
                       ("RNK_TOP", "Rank"), ("SP_AUDIT", "Stored Procedure")]:
        i.append(inst(name, "TRANSFORMATION", ttype=typ_))
    c = [conn("CUSTOMERS", "CUST_ID", "SQ_CUSTOMERS", "CUST_ID"), conn("CUSTOMERS", "CUST_NAME", "SQ_CUSTOMERS", "CUST_NAME"),
         conn("ORDERS", "CUST_ID", "SQ_ORDERS", "CUST_ID"), conn("ORDERS", "AMOUNT", "SQ_ORDERS", "AMOUNT"),
         conn("SQ_CUSTOMERS", "CUST_ID", "JNR_CUST_ORDERS", "CUST_ID"), conn("SQ_CUSTOMERS", "CUST_NAME", "JNR_CUST_ORDERS", "CUST_NAME"),
         conn("SQ_ORDERS", "CUST_ID", "JNR_CUST_ORDERS", "CUST_ID1"), conn("SQ_ORDERS", "AMOUNT", "JNR_CUST_ORDERS", "AMOUNT"),
         conn("JNR_CUST_ORDERS", "CUST_ID", "AGG_CUST", "CUST_ID"), conn("JNR_CUST_ORDERS", "CUST_NAME", "AGG_CUST", "CUST_NAME"),
         conn("JNR_CUST_ORDERS", "AMOUNT", "AGG_CUST", "AMOUNT"),
         conn("AGG_CUST", "CUST_ID", "LKP_SEG", "in_CUST_ID"), conn("AGG_CUST", "CUST_NAME", "LKP_SEG", "in_CUST_NAME"),
         conn("AGG_CUST", "o_TOTAL", "LKP_SEG", "in_TOTAL"), conn("AGG_CUST", "o_CNT", "LKP_SEG", "in_CNT")]
    for f in ("in_CUST_ID", "in_CUST_NAME", "in_TOTAL", "in_CNT", "SEGMENT"):
        c.append(conn("LKP_SEG", f, "SRT_CUST", f))
        c.append(conn("SRT_CUST", f, "RNK_TOP", f))
    for f, tfld in [("in_CUST_ID", "CUST_ID"), ("in_CUST_NAME", "CUST_NAME"), ("SEGMENT", "SEGMENT"),
                    ("in_TOTAL", "TOTAL_AMT"), ("in_CNT", "ORDER_CNT")]:
        c.append(conn("RNK_TOP", f, "DIM_CUST_SUMMARY", tfld))
    m = f"<MAPPING {attrs(NAME='m_customer_summary')}>\n" + "\n".join(t + i + c) + "\n</MAPPING>"
    return wrap(srcs, tgts, m)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    (OUT / "m_orders.xml").write_text(orders(), encoding="utf-8")
    (OUT / "m_customer_summary.xml").write_text(customer_summary(), encoding="utf-8")
    print("fixtures written to", OUT)
