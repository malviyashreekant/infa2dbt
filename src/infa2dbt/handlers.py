"""Per-transformation SQL generators.

Each handler receives a NodeCtx and returns a list of (cte_name, select_sql).
The last CTE must be named ``node.cte``. Handlers never drop a port silently:
anything they cannot translate goes through ``plan.flag``.
"""
from __future__ import annotations

import re
from typing import Callable

from .expr.lexer import ExprSyntaxError, parse
from .ir import Port, Status
from .oracle import col, ident, oracle_type
from .plan import Node, Plan

DML_FLAG = "INFA_DML_FLAG"
AGG_FUNCS = {"SUM", "AVG", "MIN", "MAX", "COUNT", "FIRST", "LAST", "MEDIAN", "STDDEV", "VARIANCE",
             "PERCENTILE", "MOVINGAVG", "MOVINGSUM", "CUME"}


# ---------------------------------------------------------------- AST helpers
def _walk(node):
    yield node
    for ch in node[1:]:
        if isinstance(ch, tuple):
            yield from _walk(ch)
        elif isinstance(ch, list):
            for x in ch:
                if isinstance(x, tuple):
                    yield from _walk(x)


def ids_in(text: str) -> list[str]:
    try:
        return [n[1] for n in _walk(parse(text)) if n[0] == "id"]
    except ExprSyntaxError:
        return []


def has_aggregate(text: str) -> bool:
    try:
        return any(n[0] == "func" and n[1] in AGG_FUNCS for n in _walk(parse(text)))
    except ExprSyntaxError:
        return False


def sub_params(plan: Plan, text: str) -> str:
    """Textual $$PARAM substitution inside verbatim SQL (as PowerCenter does)."""
    def r(m):
        name = m.group(1)
        d = plan.params.get(name)
        return "{{ var('" + name.lower() + "'" + (f", {d!r}" if d is not None else "") + ") }}"
    return re.sub(r"\$\$(\w+)", r, text)


def sql_literal(value: str, datatype: str) -> str:
    if oracle_type(datatype).startswith(("NUMBER", "BINARY")) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
        return value.strip()
    return "'" + value.replace("'", "''") + "'"


# -------------------------------------------------------------------- context
class NodeCtx:
    def __init__(self, plan: Plan, node: Node, up_cte: dict[str, str]):
        self.plan, self.node, self.up_cte = plan, node, up_cte
        self.tr = node.tr
        self.port_alias: dict[str, str] = {}
        self.scope_alias: str | None = None  # when set, ports in scope_cols are read from here
        self.scope_cols: set[str] = set()
        self.local: dict[str, str] = {}  # substituted variable ports
        self.lc = {p.name.lower(): p.name for p in (self.tr.ports if self.tr else [])}
        self.src_cte = up_cte[node.ups[0]] if node.ups else "dual"
        if not node.ups and not node.virtual:
            plan.warn(node.name, "no upstream connection; reading from DUAL")

    # raw port -> SQL for an input port
    def in_sql(self, port: str) -> str:
        if self.scope_alias and port in self.scope_cols:
            return f"{self.scope_alias}.{col(port)}"
        if port not in self.node.in_map:
            self.plan.warn(self.node.name, f"input port {port} is not connected; using NULL")
            return "NULL"
        up, upcol = self.node.in_map[port]
        u = self.plan.nodes[up]
        if u.kind == "sequence":
            if self.plan.cfg.sequence_mode == "oracle_sequence":
                self.node.extra["needs_table"] = True
            return seq_sql(self.plan, u, upcol)
        s = f"{self.port_alias.get(port, 'src')}.{col(upcol)}"
        p = self.tr.port(port) if self.tr else None
        if p and p.default and p.is_input and not re.match(r"\s*(ERROR|ABORT)\b", p.default, re.I):
            s = f"COALESCE({s}, {sql_literal(p.default, p.datatype)})"
        return s

    def ref(self, name: str) -> str:
        canon = self.lc.get(name.lower())
        if canon is None:
            self.plan.flag(self.node.name, f"expression references unknown port {name}")
            return f"NULL /* NEEDS_REVIEW: unknown port {name} */"
        if canon in self.local:
            return self.local[canon]
        p = self.tr.port(canon)
        if p.is_variable and not (self.scope_alias and canon in self.scope_cols):
            return f"NULL /* NEEDS_REVIEW: variable {canon} not yet defined */"
        return self.in_sql(canon)

    def output_expr(self, p: Port) -> str:
        if p.expression:
            return self.plan.tx(self.node.name, f"port {p.name}", p.expression, self.ref)
        if p.name in self.node.in_map or (self.scope_alias and p.name in self.scope_cols):
            return self.in_sql(p.name)
        self.plan.warn(self.node.name, f"output port {p.name} has no expression and no input; using NULL")
        return "NULL"

    def items(self, ports: list[Port]) -> str:
        return ",\n       ".join(f"{self.output_expr(p)} AS {col(p.name)}" for p in ports) or "1 AS PLACEHOLDER_"


def _sel(items: str, frm: str, extra: str = "") -> str:
    return f"SELECT {items}\n  FROM {frm}{extra}"


# ------------------------------------------------------------------- sequence
def seq_sql(plan: Plan, seq: Node, port: str) -> str:
    a = seq.tr.attrs if seq.tr else {}
    start = int(a.get("Start Value", "1") or 1)
    inc = int(a.get("Increment By", "1") or 1)
    if a.get("Cycle", "NO").upper() == "YES":
        plan.flag(seq.name, "Sequence Generator CYCLE is not replicated")
    if port.upper() == "CURRVAL":
        plan.flag(seq.name, "CURRVAL semantics (NEXTVAL + increment) are not replicated")
    if plan.cfg.sequence_mode == "oracle_sequence":
        name = ident(seq.name).upper()
        plan.reg.sequences[name] = f"CREATE SEQUENCE {name} START WITH {start} INCREMENT BY {inc} NOCACHE;"
        plan.warn(seq.name, f"uses {name}.NEXTVAL: Oracle forbids sequences in views, materialize this chain as a table")
        seq.extra["needs_table"] = True
        return f"{name}.NEXTVAL"
    plan.warn(seq.name, "ROW_NUMBER() surrogate keys restart every run and are not stable across runs")
    return f"({start} + (ROW_NUMBER() OVER (ORDER BY NULL) - 1) * {inc})"


def stage_for_sequences(c: NodeCtx) -> list[tuple[str, str]]:
    """Window functions cannot sit inside aggregates/joins, so evaluate sequences in a first CTE."""
    n = c.node
    if not n.seq_ups:
        return []
    cols = []
    for port, (up, upcol) in n.in_map.items():
        cols.append(f"{c.in_sql(port)} AS {col(port)}")
    name = f"{n.cte}__in"
    ctes = [(name, _sel(",\n       ".join(cols), f"{c.src_cte} src"))]
    c.scope_alias, c.scope_cols = "s", set(n.in_map)
    c.src_cte = name
    return ctes


# -------------------------------------------------------------------- handlers
def h_expression(c: NodeCtx):
    n, tr = c.node, c.node.tr
    ports = tr.ports
    vars_ = [p for p in ports if p.is_variable]
    outs = [p for p in ports if p.is_output and not p.is_variable]
    ctes = stage_for_sequences(c)
    names_lc = [v.name.lower() for v in vars_]
    for i, v in enumerate(vars_):
        refs = {x.lower() for x in ids_in(v.expression or "")}
        stateful = refs & set(names_lc[i:])
        if stateful:
            c.plan.flag(n.name, f"variable port {v.name} reads {sorted(stateful)} before assignment: it depends on the "
                                f"previous row. Rewrite with LAG()/window functions", v.expression or "")
    if not vars_:
        frm = f"{c.src_cte} " + ("s" if c.scope_alias else "src")
        return ctes + [(n.cte, _sel(c.items(outs), frm))]
    if not c.scope_alias:
        in_ports = [p for p in ports if p.is_input and p.name in n.in_map]
        cols = ",\n       ".join(f"{c.in_sql(p.name)} AS {col(p.name)}" for p in in_ports) or "1 AS PLACEHOLDER_"
        stage = f"{n.cte}__in"
        ctes.append((stage, _sel(cols, f"{c.src_cte} src")))
        c.scope_alias, c.scope_cols = "s", {p.name for p in in_ports}
        prev = stage
    else:
        prev = c.src_cte
    for k, v in enumerate(vars_, 1):
        e = c.plan.tx(n.name, f"variable {v.name}", v.expression or "NULL", c.ref)
        stage = f"{n.cte}__v{k}"
        ctes.append((stage, _sel(f"s.*,\n       {e} AS {col(v.name)}", f"{prev} s")))
        c.scope_cols.add(v.name)
        prev = stage
    return ctes + [(n.cte, _sel(c.items(outs), f"{prev} s"))]


def h_filter(c: NodeCtx):
    n, tr = c.node, c.node.tr
    ctes = stage_for_sequences(c)
    cond = (tr.attrs.get("Filter Condition") or "TRUE").strip() or "TRUE"
    where = c.plan.tx(n.name, "filter condition", cond, c.ref, cond=True)
    n.extra["predicate"] = cond
    outs = [p for p in tr.ports if p.is_output and not p.is_variable]
    frm = f"{c.src_cte} " + ("s" if c.scope_alias else "src")
    return ctes + [(n.cte, _sel(c.items(outs), frm, f"\n WHERE {where}"))]


def h_routergroup(c: NodeCtx):
    n = c.node
    tr = n.tr
    x = n.extra
    if x["is_default"]:
        branches = [c.plan.tx(n.name, f"group {g}", ex, c.ref, cond=True) for g, ex in x["all_conds"]
                    if g != x["group"]]
        where = ("CASE " + " ".join(f"WHEN {b} THEN 1" for b in branches) + " ELSE 0 END = 0") if branches else "1 = 1"
    else:
        where = c.plan.tx(n.name, f"group {x['group']}", x["cond"], c.ref, cond=True)
    n.extra["predicate"] = None if x["is_default"] else x["cond"]
    items = []
    for o in n.outs:
        p = tr.port(o)
        inp = p.ref_field or re.sub(r"\d+$", "", o)
        if inp not in n.in_map:
            c.plan.flag(n.name, f"router output {o} cannot be traced to an input port")
            items.append(f"NULL AS {col(o)}")
        else:
            items.append(f"{c.in_sql(inp)} AS {col(o)}")
    return [(n.cte, _sel(",\n       ".join(items) or "1 AS PLACEHOLDER_", f"{c.src_cte} src", f"\n WHERE {where}"))]


def h_aggregator(c: NodeCtx):
    n, tr = c.node, c.node.tr
    ctes = stage_for_sequences(c)
    if tr.attrs.get("Incremental Aggregation", "NO").upper() == "YES":
        c.plan.flag(n.name, "incremental aggregation cache is not replicated")
    for v in (p for p in tr.ports if p.is_variable):
        c.local[v.name] = "(" + c.plan.tx(n.name, f"variable {v.name}", v.expression or "NULL", c.ref) + ")"
    group_ports = {p.name.lower() for p in tr.ports if p.expr_type.upper() == "GROUPBY"}
    items, group_by = [], []
    for p in (q for q in tr.ports if q.is_output and not q.is_variable):
        e = c.output_expr(p)
        if p.name.lower() in group_ports:
            items.append(f"{e} AS {col(p.name)}")
            group_by.append(e)
        elif p.expression and has_aggregate(p.expression):
            items.append(f"{e} AS {col(p.name)}")
        else:
            refs = {x.lower() for x in ids_in(p.expression)} if p.expression else {p.name.lower()}
            if refs and refs <= group_ports:
                items.append(f"{e} AS {col(p.name)}")
                group_by.append(e)
            else:
                c.plan.flag(n.name, f"port {p.name} is neither grouped nor aggregated; PowerCenter returns the last "
                                    f"row of each group, MAX() used as a stand-in", p.expression or "")
                items.append(f"MAX({e}) AS {col(p.name)}")
    frm = f"{c.src_cte} " + ("s" if c.scope_alias else "src")
    gb = ("\n GROUP BY " + ", ".join(dict.fromkeys(group_by))) if group_by else ""
    return ctes + [(n.cte, _sel(",\n       ".join(items) or "1 AS PLACEHOLDER_", frm, gb))]


def h_joiner(c: NodeCtx):
    n, tr = c.node, c.node.tr
    ports = tr.ports
    is_master = lambda p: "MASTER" in p.porttype.upper() or (p.group or "").upper() == "MASTER"  # noqa: E731
    for p in ports:
        c.port_alias[p.name] = "m" if is_master(p) else "d"
    mup = next((n.in_map[p.name][0] for p in ports if is_master(p) and p.name in n.in_map), None)
    dup = next((n.in_map[p.name][0] for p in ports if not is_master(p) and p.name in n.in_map), None)
    if n.seq_ups:
        c.plan.flag(n.name, "Sequence Generator feeding a Joiner is not supported")
    if mup is None or dup is None:
        c.plan.flag(n.name, "Joiner needs connected master and detail inputs")
        mup = mup or dup
        dup = dup or mup
        if mup is None:
            return [(n.cte, "SELECT 1 AS PLACEHOLDER_ FROM dual")]
    jt = (tr.attrs.get("Join Type") or "Normal Join").lower()
    cond_text = (tr.attrs.get("Join Condition") or "").strip()
    if cond_text:
        cond = c.plan.tx(n.name, "join condition", cond_text, c.ref, cond=True)
    else:
        c.plan.flag(n.name, "Joiner has no join condition (cartesian product)")
        cond = "1 = 1"
    m, d = c.up_cte[mup], c.up_cte[dup]
    if "full" in jt:
        frm = f"{m} m\n FULL OUTER JOIN {d} d ON {cond}"
    elif "master outer" in jt:
        frm = f"{d} d\n LEFT OUTER JOIN {m} m ON {cond}"
    elif "detail outer" in jt:
        frm = f"{m} m\n LEFT OUTER JOIN {d} d ON {cond}"
    else:
        frm = f"{d} d\n INNER JOIN {m} m ON {cond}"
    c.plan.warn(n.name, "join keys: PowerCenter may treat NULL keys as equal, SQL does not; verify NULL join keys")
    outs = [p for p in ports if p.is_output and not p.is_variable]
    return [(n.cte, _sel(c.items(outs), frm))]


def h_sorter(c: NodeCtx):
    n, tr = c.node, c.node.tr
    ctes = stage_for_sequences(c)
    low = (tr.attrs.get("Null Treated Low") or "NO").upper() == "YES"
    keys = []
    for p in tr.ports:
        if p.sort_key:
            desc = (p.sort_dir or "ASCENDING").upper().startswith("DESC")
            nulls = ("NULLS LAST" if desc else "NULLS FIRST") if low else ("NULLS FIRST" if desc else "NULLS LAST")
            keys.append(f"{col(p.name)} {'DESC' if desc else 'ASC'} {nulls}")
    distinct = (tr.attrs.get("Distinct") or "NO").upper() == "YES"
    outs = [p for p in tr.ports if p.is_output and not p.is_variable]
    if not keys:
        c.plan.warn(n.name, "Sorter has no sort keys")
    c.plan.warn(n.name, "ORDER BY is kept for traceability; it only matters if a later step depends on row order")
    items = c.items(outs)
    frm = f"{c.src_cte} " + ("s" if c.scope_alias else "src")
    sql = _sel(("DISTINCT " if distinct else "") + items, frm, ("\n ORDER BY " + ", ".join(keys)) if keys else "")
    return ctes + [(n.cte, sql)]


def h_update(c: NodeCtx):
    n, tr = c.node, c.node.tr
    ctes = stage_for_sequences(c)
    expr = (tr.attrs.get("Update Strategy Expression") or "0").strip() or "0"
    flags = set(re.findall(r"DD_(INSERT|UPDATE|DELETE|REJECT)", expr.upper()))
    if not flags:
        if expr in ("0", "1", "2", "3"):
            flags = {["INSERT", "UPDATE", "DELETE", "REJECT"][int(expr)]}
        else:
            c.plan.flag(n.name, "update strategy flags cannot be determined statically; treated as upsert", expr)
            flags = {"INSERT", "UPDATE"}
    n.extra["flags"] = flags
    n.extra["expr"] = expr
    e = c.plan.tx(n.name, "update strategy expression", expr, c.ref)
    outs = [p for p in tr.ports if p.is_output and not p.is_variable]
    items = c.items(outs) + f",\n       {e} AS {DML_FLAG}"
    frm = f"{c.src_cte} " + ("s" if c.scope_alias else "src")
    return ctes + [(n.cte, _sel(items, frm))]


def h_union(c: NodeCtx):
    n, tr = c.node, c.node.tr
    out_ports = [p for p in tr.ports if (p.group or "").upper() == "OUTPUT"] or \
                [p for p in tr.ports if p.is_output and "INPUT" not in p.porttype.upper()]
    out_names = {p.name for p in out_ports}
    groups: dict[str, list[Port]] = {}
    for p in tr.ports:
        if p.name not in out_names and p.group:
            groups.setdefault(p.group, []).append(p)
    selects = []
    for g, gports in groups.items():
        up = next((n.in_map[p.name][0] for p in gports if p.name in n.in_map), None)
        if up is None:
            c.plan.warn(n.name, f"union group {g} has no connected input; skipped")
            continue
        cols = []
        for i, o in enumerate(out_ports):
            gp = gports[i] if i < len(gports) else None
            if gp is not None and gp.name in n.in_map:
                cols.append(f"u.{col(n.in_map[gp.name][1])} AS {col(o.name)}")
            else:
                cols.append(f"CAST(NULL AS {oracle_type(o.datatype, o.precision, o.scale)}) AS {col(o.name)}")
        selects.append(_sel(",\n       ".join(cols), f"{c.up_cte[up]} u"))
    if not selects:
        c.plan.flag(n.name, "Union has no connected input groups")
        return [(n.cte, "SELECT 1 AS PLACEHOLDER_ FROM dual")]
    return [(n.cte, "\nUNION ALL\n".join(selects))]


def _parse_lookup_condition(text: str):
    """Return [(lookup_col, op, input_port)] for 'A = B AND C >= D'."""
    try:
        ast = parse(text)
    except ExprSyntaxError:
        return None
    out = []

    def rec(nd):
        if nd[0] == "and":
            return rec(nd[1]) and rec(nd[2])
        if nd[0] == "bin" and nd[1] in ("=", "<>", "<", ">", "<=", ">=") and nd[2][0] == "id" and nd[3][0] == "id":
            out.append((nd[2][1], nd[1], nd[3][1]))
            return True
        return False
    return out if rec(ast) else None


def _lookup_source_sql(c: NodeCtx, tr, lookup_cols: list[Port]) -> tuple[str, bool]:
    plan = c.plan
    override = (tr.attrs.get("Lookup Sql Override") or "").strip()
    if override:
        plan.flag(c.node.name, "lookup SQL override emitted verbatim; verify it", override)
        return f"(\n{sub_params(plan, override)}\n)", True
    tname = (tr.attrs.get("Lookup table name") or "").strip().strip('"')
    if not tname:
        plan.flag(c.node.name, "lookup has no table name or SQL override")
        return "dual", False
    owner, _, table = tname.rpartition(".")
    group, table = plan.reg.register_source(owner, table, plan.cfg)
    cols = ", ".join(col(p.name) for p in lookup_cols) or "*"
    flt = (tr.attrs.get("Lookup source filter") or "").strip()
    where = ""
    if flt:
        plan.warn(c.node.name, "lookup source filter copied verbatim (SQL)")
        where = f"\n    WHERE {sub_params(plan, flt)}"
    return f"(\n    SELECT {cols}\n    FROM {{{{ source('{group}', '{table}') }}}}{where}\n  )", False


def _lookup_parts(c: NodeCtx):
    tr = c.node.tr
    lookup_cols = [p for p in tr.ports if "LOOKUP" in p.porttype.upper()]
    cond = _parse_lookup_condition(tr.attrs.get("Lookup condition") or tr.attrs.get("Lookup Condition") or "")
    if cond is None:
        c.plan.flag(c.node.name, "lookup condition is missing or not a simple AND of comparisons",
                    tr.attrs.get("Lookup condition", ""))
        cond = []
    policy = (tr.attrs.get("Lookup policy on multiple match") or "Use Any Value").lower()
    if (tr.attrs.get("Dynamic Lookup Cache") or "NO").upper() == "YES":
        c.plan.flag(c.node.name, "dynamic lookup cache (inserts into the cache while running) is not replicated")
    return tr, lookup_cols, cond, policy


def _dedupe_cte(c: NodeCtx, cond, policy, lookup_cols, source) -> tuple[str, bool]:
    if "all" in policy:
        return "", False
    if "error" in policy:
        c.plan.flag(c.node.name, "multiple-match policy 'Report Error' is not enforced; add a uniqueness test on the lookup keys")
        return "", False
    if not cond or any(op != "=" for _, op, _ in cond):
        c.plan.flag(c.node.name, "multiple-match policy needs equality-only lookup conditions to be replicated")
        return "", False
    keys = ", ".join(col(l) for l, _, _ in cond)
    if "first" in policy or "last" in policy:
        direction = "DESC" if "last" in policy else "ASC"
        order = ", ".join(f"{col(p.name)} {direction}" for p in lookup_cols) or "NULL"
        c.plan.warn(c.node.name, "first/last match uses PowerCenter's default ORDER BY on the lookup ports; verify")
    else:
        order = "NULL"
    return f"ROW_NUMBER() OVER (PARTITION BY {keys} ORDER BY {order})", True


def h_lookup(c: NodeCtx):
    n = c.node
    tr, lookup_cols, cond, policy = _lookup_parts(c)
    ctes = stage_for_sequences(c)
    source, _ = _lookup_source_sql(c, tr, lookup_cols)
    rn, dedupe = _dedupe_cte(c, cond, policy, lookup_cols, source)
    lk = f"{n.cte}__lkp"
    cols = "l.*" + (f", {rn} AS RN__" if dedupe else "")
    ctes.append((lk, f"SELECT {cols}\n  FROM {source} l"))
    on = [f"lkp.{col(l)} {op} {c.in_sql(c.lc.get(i.lower(), i))}" for l, op, i in cond]
    if dedupe:
        on.append("lkp.RN__ = 1")
    on_sql = " AND ".join(on) or "1 = 1"
    items = []
    for p in tr.ports:
        if not p.is_output or p.is_variable:
            continue
        if "LOOKUP" in p.porttype.upper():
            e = f"lkp.{col(p.name)}"
            if p.default and not re.match(r"\s*(ERROR|ABORT)\b", p.default, re.I):
                e = f"COALESCE({e}, {sql_literal(p.default, p.datatype)})"
            items.append(f"{e} AS {col(p.name)}")
        else:
            items.append(f"{c.in_sql(p.name)} AS {col(p.name)}")
    frm = f"{c.src_cte} " + ("s" if c.scope_alias else "src")
    ctes.append((n.cte, _sel(",\n       ".join(items) or "1 AS PLACEHOLDER_", frm,
                             f"\n LEFT OUTER JOIN {lk} lkp ON {on_sql}")))
    return ctes


def build_lookup_macro(plan: Plan, node: Node) -> None:
    """Unconnected lookup -> reusable dbt macro returning a scalar subquery."""
    c = NodeCtx(plan, node, {})
    tr, lookup_cols, cond, policy = _lookup_parts(c)
    inputs = [p for p in tr.ports if "INPUT" in p.porttype.upper() and "LOOKUP" not in p.porttype.upper()]
    ret = next((p for p in tr.ports if "RETURN" in p.porttype.upper()), None)
    if ret is None:
        outs = [p for p in tr.ports if "LOOKUP" in p.porttype.upper() and p.is_output]
        ret = outs[0] if len(outs) == 1 else None
    macro = plan.lookup_macros[node.name]
    if ret is None:
        plan.flag(node.name, "unconnected lookup has no identifiable return port")
        plan.reg.macros[macro] = (f"{{% macro {macro}() %}}NULL /* NEEDS_REVIEW: no return port on "
                                  f"{node.name} */{{% endmacro %}}\n")
        return
    arg_of = {p.name.lower(): f"arg{i + 1}" for i, p in enumerate(inputs)}
    source, _ = _lookup_source_sql(c, tr, lookup_cols)
    rn, dedupe = _dedupe_cte(c, cond, policy, lookup_cols, source)
    where = []
    for l, op, i in cond:
        a = arg_of.get(i.lower())
        if a is None:
            plan.flag(node.name, f"lookup condition uses {i}, which is not an input port")
            continue
        where.append(f"lkp.{col(l)} {op} {{{{ {a} }}}}")
    if dedupe:
        where.append("lkp.RN__ = 1")
    inner = f"SELECT l.*{', ' + rn + ' AS RN__' if dedupe else ''} FROM {source} l"
    params = ", ".join(arg_of.values())
    body = (f"{{% macro {macro}({params}) %}}\n(SELECT lkp.{col(ret.name)}\n   FROM ({inner}) lkp\n"
            f"  WHERE {' AND '.join(where) or '1 = 1'})\n{{% endmacro %}}\n")
    plan.reg.macros[macro] = body
    node.extra["macro_args"] = [p.name for p in inputs]


STUB_HINTS = {
    "rank": "Rank is not auto-converted: use RANK()/ROW_NUMBER() OVER (PARTITION BY group ports ORDER BY rank port) "
            "and filter on the Top/Bottom N attribute",
    "normalizer": "Normalizer is not auto-converted: unpivot with UNPIVOT or a CROSS JOIN over the occurrence columns",
}


def h_stub(c: NodeCtx):
    n = c.node
    reason = STUB_HINTS.get(n.kind, f"{n.type_name} transformation cannot be converted automatically")
    c.plan.flag(n.name, reason)
    n.status = Status.UNSUPPORTED
    items = []
    for p in (q for q in n.tr.ports if q.is_output and not q.is_variable) if n.tr else []:
        if p.name in n.in_map:
            items.append(f"{c.in_sql(p.name)} AS {col(p.name)}")
        else:
            items.append(f"CAST(NULL AS {oracle_type(p.datatype, p.precision, p.scale)}) AS {col(p.name)}")
    return [(n.cte, _sel(",\n       ".join(items) or "1 AS PLACEHOLDER_", f"{c.src_cte} src") +
             f"\n/* NEEDS_REVIEW: {reason} */")]


HANDLERS: dict[str, Callable[[NodeCtx], list[tuple[str, str]]]] = {
    "expression": h_expression, "filter": h_filter, "routergroup": h_routergroup,
    "aggregator": h_aggregator, "joiner": h_joiner, "sorter": h_sorter, "update": h_update,
    "union": h_union, "lookup": h_lookup, "rank": h_stub, "normalizer": h_stub, "unsupported": h_stub,
}
