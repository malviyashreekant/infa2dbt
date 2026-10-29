"""Turn a Plan into dbt models (stg_, int_, fct_/dim_), coverage rows, tests and recon SQL."""
from __future__ import annotations

import re

from .config import Config
from .expr.lexer import ExprSyntaxError, parse
from .handlers import DML_FLAG, HANDLERS, NodeCtx, build_lookup_macro, sub_params
from .ir import CoverageRow, Mapping, Status
from .oracle import col, ident, is_numeric, oracle_type
from .plan import ModelSpec, Node, Plan, PlanError, Registry, build_nodes, segment, topo_order

AUDIT_COL = "DBT_UPDATED_AT"


def _cols_sql(items: list[str]) -> str:
    return ",\n    ".join(items)


def _yml_col(name: str, description: str = "", tests=None) -> dict:
    raw = name.strip('"')
    d = {"name": raw}
    if name.startswith('"'):
        d["quote"] = True
    if description:
        d["description"] = description
    if tests:
        d["tests"] = tests
    return d


# --------------------------------------------------------------------- staging
def _build_sources(plan: Plan) -> None:
    reg, cfg, mp = plan.reg, plan.cfg, plan.mapping
    for n in plan.nodes.values():
        if n.kind != "source" or n.extra.get("table") is None:
            continue
        tbl = n.extra["table"]
        base = f"stg_{ident(tbl.name)}"
        known = reg.source_defs.get(tbl.name)
        if known is not None and known == tbl and base in reg.models:
            n.model, n.layer = base, "stg"
            reg.models[base].nodes.append(f"{mp.name}:{n.name}")
            continue
        name = reg.unique(base, mp.name)
        reg.source_defs.setdefault(tbl.name, tbl)
        group, table = reg.register_source(tbl.owner, tbl.name, cfg)
        cols = [f"CAST({col(f.name)} AS {oracle_type(f.datatype, f.precision, f.scale)}) AS {col(f.name)}"
                for f in tbl.fields]
        sql = (f"{{{{ config(materialized='view') }}}}\n\n"
               f"-- Replaces Informatica source definition {tbl.name}"
               f"{' (owner ' + tbl.owner + ')' if tbl.owner else ''}\n"
               f"-- Type casting and renaming only; business filters live in the Source Qualifier model.\n"
               f"SELECT\n    {_cols_sql(cols)}\nFROM {{{{ source('{group}', '{table}') }}}}\n")
        spec = ModelSpec(name=name, layer="stg", sql=sql, nodes=[f"{mp.name}:{n.name}"],
                         description=f"Staged copy of source {tbl.name}",
                         columns=[_yml_col(col(f.name)) for f in tbl.fields])
        reg.models[name] = spec
        n.model, n.layer = name, "stg"


def _build_sq(plan: Plan) -> None:
    reg, mp = plan.reg, plan.mapping
    for n in plan.nodes.values():
        if n.kind != "sq" or n.tr is None:
            continue
        tr = n.tr
        aliases: dict[str, str] = {}
        used: set[str] = set()
        for u in n.ups:
            un = plan.nodes[u]
            tbl = un.extra.get("table")
            a = re.sub(r"\W", "_", (tbl.name if tbl else u)).upper()
            if a in used:
                a = re.sub(r"\W", "_", u).upper()
            used.add(a)
            aliases[u] = a
        cols = []
        for p in tr.ports:
            if not p.is_output:
                continue
            if p.name in n.in_map:
                up, upcol = n.in_map[p.name]
                cols.append(f"{aliases[up]}.{col(upcol)} AS {col(p.name)}")
            else:
                plan.warn(n.name, f"Source Qualifier port {p.name} is not connected to a source; using NULL")
                cols.append(f"CAST(NULL AS {oracle_type(p.datatype, p.precision, p.scale)}) AS {col(p.name)}")
        a = tr.attrs
        override = (a.get("Sql Query") or "").strip()
        header = f"-- Replaces Informatica Source Qualifier {n.name}\n"
        if override:
            plan.flag(n.name, "Source Qualifier SQL override is emitted verbatim and reads physical tables directly", override)
            body = (f"-- NEEDS_REVIEW: SQL override copied verbatim; rewrite it against ref()/source()\n"
                    f"SELECT * FROM (\n{sub_params(plan, override)}\n)\n")
        else:
            frm = ",\n     ".join(f"{{{{ ref('{plan.nodes[u].model}') }}}} {aliases[u]}" for u in n.ups) or "dual"
            where = []
            udj = (a.get("User Defined Join") or "").strip()
            flt = (a.get("Source Filter") or "").strip()
            if udj:
                where.append(sub_params(plan, udj))
            elif len(n.ups) > 1:
                plan.flag(n.name, "multiple sources and no User Defined Join: PowerCenter derives the join from key "
                                  "relationships; define it explicitly")
            if flt:
                where.append(sub_params(plan, flt))
            if udj or flt:
                plan.warn(n.name, "Source Qualifier join/filter copied verbatim (SQL); check column qualifiers")
            distinct = "DISTINCT " if (a.get("Select Distinct") or "NO").upper() == "YES" else ""
            n_sorted = int(a.get("Number Of Sorted Ports") or 0) if str(a.get("Number Of Sorted Ports") or "0").isdigit() else 0
            order = ""
            if n_sorted:
                order = "\nORDER BY " + ", ".join(col(p.name) for p in tr.ports[:n_sorted])
            where_sql = ("\nWHERE " + "\n  AND ".join(f"({w})" for w in where)) if where else ""
            body = f"SELECT {distinct}{_cols_sql(cols)}\nFROM {frm}{where_sql}{order}\n"
        for k in ("Pre SQL", "Post SQL"):
            if (a.get(k) or "").strip():
                plan.flag(n.name, f"{k} on the Source Qualifier has no automatic equivalent; use a dbt hook", a[k])
        name = reg.unique(f"stg_{ident(n.name)}", mp.name)
        sql = f"{{{{ config(materialized='view') }}}}\n\n{header}{body}"
        reg.models[name] = ModelSpec(
            name=name, layer="stg", sql=sql, nodes=[f"{mp.name}:{n.name}"],
            description=f"Source Qualifier {n.name}",
            columns=[_yml_col(col(p.name)) for p in tr.ports if p.is_output])
        n.model, n.layer = name, "stg"


# ---------------------------------------------------------------- intermediate
def _build_int(plan: Plan) -> None:
    reg, mp, cfg = plan.reg, plan.mapping, plan.cfg
    for node in plan.nodes.values():
        if node.kind == "lookup" and node.extra.get("unconnected"):
            build_lookup_macro(plan, node)
    for seg in segment(plan):
        last = plan.nodes[seg[-1]]
        model = reg.unique(f"int_{ident(last.name)}", mp.name)
        seg_set = set(seg)
        imports: dict[str, str] = {}
        ctes: list[tuple[str, str]] = []
        for name in seg:
            node = plan.nodes[name]
            up_cte: dict[str, str] = {}
            for u in node.ups:
                if u in seg_set:
                    up_cte[u] = plan.nodes[u].cte
                else:
                    imp = f"src_{plan.nodes[u].cte}"
                    imports[imp] = plan.nodes[u].model
                    up_cte[u] = imp
            handler = HANDLERS.get(node.kind)
            if handler is None:
                raise PlanError(f"no handler for {node.kind}")
            node.ctes = handler(NodeCtx(plan, node, up_cte))
            node.model = model
            ctes += node.ctes
        mat = "table" if any(plan.nodes[x].extra.get("needs_table") for x in seg) else cfg.int_materialization
        head = [f"{imp} AS (\n  SELECT * FROM {{{{ ref('{m}') }}}}\n)" for imp, m in imports.items()]
        body = [f"{n} AS (\n  {sql.strip().replace(chr(10), chr(10) + '  ')}\n)" for n, sql in ctes]
        replaces = ", ".join(f"{plan.nodes[x].name} ({plan.nodes[x].type_name})" for x in seg)
        sql = (f"{{{{ config(materialized='{mat}') }}}}\n\n-- Replaces Informatica: {replaces}\n"
               f"WITH\n" + ",\n".join(head + body) + f"\n\nSELECT * FROM {last.cte}\n")
        outs = [o for o in last.outs]
        reg.models[model] = ModelSpec(
            name=model, layer="int", sql=sql, materialized=mat, nodes=[f"{mp.name}:{x}" for x in seg],
            description=f"Replaces {replaces}", columns=[_yml_col(col(o)) for o in outs])


# ----------------------------------------------------------------------- marts
def _walk_ast(node):
    yield node
    for ch in node[1:]:
        if isinstance(ch, tuple):
            yield from _walk_ast(ch)
        elif isinstance(ch, list):
            for x in ch:
                if isinstance(x, tuple):
                    yield from _walk_ast(x)


def _outcomes(node):
    """Set of literal results of an expression, or None if any branch is not a literal."""
    k = node[0]
    if k == "str":
        return {node[1]} if node[1] != "" else set()
    if k == "num":
        return {node[1]}
    if k == "null":
        return set()
    if k == "func" and node[1] == "IIF" and len(node[2]) in (2, 3):
        parts = [_outcomes(a) for a in node[2][1:]]
        return None if any(p is None for p in parts) else set().union(*parts)
    if k == "func" and node[1] == "DECODE" and len(node[2]) >= 3:
        args = node[2][1:]
        results = args[1::2] + ([args[-1]] if len(args) % 2 == 1 else [])
        parts = [_outcomes(a) for a in results]
        return None if any(p is None for p in parts) else set().union(*parts)
    return None


def _trace(plan: Plan, name: str, column: str) -> tuple[list[tuple[str, str]], object]:
    """Follow a column upstream through pass-through ports; returns (chain, terminal port or None)."""
    chain: list[tuple[str, str]] = []
    cur, cc = name, column
    for _ in range(200):
        n = plan.nodes[cur]
        chain.append((cur, cc))
        if n.kind in ("source", "sq") or n.tr is None:
            return chain, None
        p = n.tr.port(cc)
        if n.kind == "routergroup":
            inp = (p.ref_field if p else None) or re.sub(r"\d+$", "", cc)
            chain[-1] = (cur, inp)  # predicates are written against the router's input port names
            nxt = n.in_map.get(inp)
        elif p is not None and p.expression and not p.is_variable:
            return chain, p
        else:
            nxt = n.in_map.get(cc)
        if not nxt:
            return chain, None
        cur, cc = nxt
        if plan.nodes[cur].kind == "sequence":
            return chain, None
    return chain, None


_OPS = {"=", "<>", "<", ">", "<=", ">="}


def _simple_predicates(text: str):
    """[(port, op, literal_sql)] if text is AND-only simple comparisons of a port to a literal."""
    try:
        ast = parse(text)
    except ExprSyntaxError:
        return []
    out = []

    def rec(nd):
        if nd[0] == "and":
            return rec(nd[1]) and rec(nd[2])
        if nd[0] == "bin" and nd[1] in _OPS and nd[2][0] == "id" and nd[3][0] in ("num", "str"):
            lit = nd[3][1] if nd[3][0] == "num" else "'" + nd[3][1].replace("'", "''") + "'"
            out.append((nd[2][1], nd[1], lit))
            return True
        return False
    return out if rec(ast) else []


def _derive_tests(plan: Plan, target: Node, up: str | None, tfield) -> list:
    tests = []
    if up is None or tfield.name not in target.in_map:
        return tests
    chain, term = _trace(plan, *target.in_map[tfield.name])
    if term is not None and term.expression:
        try:
            outs = _outcomes(parse(term.expression))
        except ExprSyntaxError:
            outs = None
        if outs and len(outs) <= 20:
            tests.append({"accepted_values": {"values": sorted(outs)}})
    seen = set()
    for nname, cc in chain:
        pred = plan.nodes[nname].extra.get("predicate")
        if not pred:
            continue
        for port, op, lit in _simple_predicates(pred):
            if port.lower() == cc.lower() and (nname, port, op, lit) not in seen:
                seen.add((nname, port, op, lit))
                tests.append({"dbt_utils.expression_is_true": {
                    "expression": f"{col(tfield.name)} {op} {lit}"}})
    return tests


def _ancestors(plan: Plan, name: str) -> list[Node]:
    seen, stack, out = set(), [name], []
    while stack:
        cur = stack.pop()
        for u in plan.nodes[cur].ups:
            if u not in seen:
                seen.add(u)
                out.append(plan.nodes[u])
                stack.append(u)
    return out


def _build_marts(plan: Plan) -> None:
    reg, mp, cfg = plan.reg, plan.mapping, plan.cfg
    for t in plan.nodes.values():
        if t.kind != "target" or t.extra.get("table") is None:
            continue
        tbl = t.extra["table"]
        is_dim = any(tbl.name.upper().startswith(p) for p in cfg.dim_prefixes)
        stem = ident(tbl.name)
        for p in cfg.dim_prefixes:
            if is_dim and tbl.name.upper().startswith(p):
                stem = ident(tbl.name[len(p):])
                break
        if stem.startswith(("fct_", "dim_")):
            stem = stem[4:]
        name = reg.unique(f"{'dim' if is_dim else 'fct'}_{stem}", mp.name)
        t.model, t.layer = name, "mart"
        up = t.ups[0] if t.ups else None
        if len(t.ups) > 1:
            plan.flag(t.name, f"target is fed by several pipelines ({', '.join(t.ups)}); only {up} is loaded")
        if up is None:
            plan.flag(t.name, "target has no upstream connection")

        # update strategy discovery
        us = None
        cur = up
        hops = 0
        while cur is not None and hops < 50:
            hops += 1
            n = plan.nodes[cur]
            if n.kind == "update":
                us = n
                break
            cur = n.ups[0] if len(n.ups) == 1 and n.kind != "source" else None
        direct = us is not None and us.name == up
        if us is not None and not direct:
            plan.flag(t.name, f"Update Strategy {us.name} is not directly connected to the target; DML flags are not applied")
            us = None
        flags = us.extra.get("flags", set()) if us else set()

        keys = [f for f in tbl.fields if f.key]
        key_cols = [col(f.name) for f in keys]
        mat, cfg_extra = "table", []
        if us is not None:
            if flags & {"UPDATE", "DELETE"}:
                mat = "incremental"
                if key_cols:
                    cfg_extra += [f"unique_key={[c.strip(chr(34)) for c in key_cols]!r}"]
                else:
                    plan.flag(t.name, "Update Strategy updates/deletes rows but the target has no primary key; "
                                      "define unique_key manually")
            else:
                mat = "incremental"
        elif cfg.no_update_strategy_load == "incremental_append":
            mat = "incremental"
            plan.warn(t.name, "no Update Strategy: rows are appended on every run")
        else:
            plan.warn(t.name, "no Update Strategy: assumed truncate-and-load (table materialization)")

        sel, tests_cols = [], []
        for f in tbl.fields:
            typ = oracle_type(f.datatype, f.precision, f.scale)
            if f.name in t.in_map:
                u, uc = t.in_map[f.name]
                if plan.nodes[u].kind == "sequence":
                    plan.flag(t.name, f"target column {f.name} is fed directly by a Sequence Generator; route it through an Expression")
                    sel.append(f"CAST(NULL AS {typ}) AS {col(f.name)}")
                else:
                    sel.append(f"CAST(src.{col(uc)} AS {typ}) AS {col(f.name)}")
            else:
                plan.warn(t.name, f"target column {f.name} is not connected; loaded as NULL")
                sel.append(f"CAST(NULL AS {typ}) AS {col(f.name)}")
            tests = []
            if f.key or f.not_null:
                tests.append("not_null")
            if f.key and len(keys) == 1:
                tests.append("unique")
            tests += _derive_tests(plan, t, up, f)
            tests_cols.append(_yml_col(col(f.name), tests=tests))
        sel.append(f"CURRENT_TIMESTAMP AS {AUDIT_COL}")
        tests_cols.append(_yml_col(AUDIT_COL, "Lineage: when dbt last loaded this row", ["not_null"]))
        model_tests = []
        if len(keys) > 1:
            model_tests.append({"dbt_utils.unique_combination_of_columns": {"combination_of_columns": [k.strip('"') for k in key_cols]}})
        if not keys:
            plan.flag(t.name, "target definition has no primary key: no unique test could be generated")

        post = []
        extra_models = []
        if us is not None and "DELETE" in flags and key_cols:
            dname = reg.unique(f"{name}__deletes", mp.name)
            dsel = [f"CAST(src.{col(t.in_map[k.name][1])} AS {oracle_type(k.datatype, k.precision, k.scale)}) AS {col(k.name)}"
                    for k in keys if k.name in t.in_map]
            if len(dsel) == len(keys):
                dsql = (f"{{{{ config(materialized='view') }}}}\n\n-- Keys flagged DD_DELETE by Update Strategy {us.name}\n"
                        f"SELECT {_cols_sql(dsel)}\nFROM {{{{ ref('{plan.nodes[up].model}') }}}} src\nWHERE src.{DML_FLAG} = 2\n")
                reg.models[dname] = ModelSpec(dname, "int", dsql, "view", [f"{mp.name}:{us.name}"],
                                              description=f"Rows {name} must delete")
                kc = ", ".join(key_cols)
                post.append(f"DELETE FROM {{{{ this }}}} WHERE ({kc}) IN (SELECT {kc} FROM {{{{ ref('{dname}') }}}})")
            else:
                plan.flag(t.name, "DD_DELETE keys are not all connected to the target; delete step skipped")
        if us is not None and "REJECT" in flags:
            rname = reg.unique(f"{name}__rejects", mp.name)
            rsql = (f"{{{{ config(materialized='view') }}}}\n\n-- Rows flagged DD_REJECT by Update Strategy {us.name} "
                    f"(PowerCenter writes these to the reject file)\n"
                    f"SELECT src.* FROM {{{{ ref('{plan.nodes[up].model}') }}}} src WHERE src.{DML_FLAG} = 3\n")
            reg.models[rname] = ModelSpec(rname, "int", rsql, "view", [f"{mp.name}:{us.name}"],
                                          description=f"Rejected rows for {name}")

        conf = [f"materialized='{mat}'", f"alias='{tbl.name.lower()}'"] + cfg_extra
        if post:
            conf.append("post_hook=[" + ", ".join('"' + p.replace('"', '\\"') + '"' for p in post) + "]")
        where = f"\nWHERE src.{DML_FLAG} IN (0, 1)" if us is not None else ""
        frm = f"{{{{ ref('{plan.nodes[up].model}') }}}} src" if up else "dual src"
        sql = (f"{{{{ config(\n    " + ",\n    ".join(conf) + "\n) }}\n\n"
               f"-- Replaces Informatica target {tbl.name} (instance {t.name})"
               f"{' and Update Strategy ' + us.name if us else ''}\n"
               f"SELECT\n    {_cols_sql(sel)}\nFROM {frm}{where}\n")
        reg.models[name] = ModelSpec(
            name=name, layer="mart", sql=sql, materialized=mat,
            nodes=[f"{mp.name}:{t.name}"] + ([f"{mp.name}:{us.name}"] if us else []),
            columns=tests_cols, tests=model_tests, description=f"Target {tbl.name}",
            tags=["mart"])
        plan.mart_models.append(name)
        t.extra.update(update_node=us.name if us else None, table_name=tbl.name, flags=sorted(flags))
        reg.analyses[f"recon_{name}"] = _recon_sql(plan, t, name, tbl, up)


# --------------------------------------------------------------- reconciliation
def _recon_sql(plan: Plan, t: Node, model: str, tbl, up) -> str:
    cfg = plan.cfg
    anc = _ancestors(plan, t.name)
    srcs = sorted({n.extra["table"].name: n for n in anc if n.kind == "source" and n.extra.get("table")}.items())
    changers = sorted({f"{n.name} ({n.type_name})" for n in anc
                       if n.kind in ("filter", "routergroup", "aggregator", "joiner", "union", "rank", "normalizer",
                                     "unsupported") or (n.kind == "update" and ({"DELETE", "REJECT"} & n.extra.get("flags", set())))})
    business = [f for f in tbl.fields]
    num = [f for f in business if is_numeric(f.datatype) or f.datatype.lower() in ("integer", "small integer", "bigint", "decimal", "number", "double", "real")]
    legacy = f"{{{{ var('{cfg.legacy_schema_var}') }}}}.{tbl.name.upper()}"
    this = f"{{{{ ref('{model}') }}}}"
    colnames = ", ".join(col(f.name) for f in business)
    rows = []
    rows.append(f"SELECT 'row_count' AS metric, (SELECT COUNT(*) FROM {this}) AS dbt_value, "
                f"(SELECT COUNT(*) FROM {legacy}) AS legacy_value FROM dual")
    for f in num:
        c = col(f.name)
        rows.append(f"SELECT 'sum:{f.name}', (SELECT SUM({c}) FROM {this}), (SELECT SUM({c}) FROM {legacy}) FROM dual")
    for f in business:
        c = col(f.name)
        rows.append(f"SELECT 'non_null:{f.name}', (SELECT COUNT({c}) FROM {this}), (SELECT COUNT({c}) FROM {legacy}) FROM dual")
        rows.append(f"SELECT 'hash:{f.name}', (SELECT SUM(ORA_HASH({c})) FROM {this}), (SELECT SUM(ORA_HASH({c})) FROM {legacy}) FROM dual")
    rows.append(f"SELECT 'rows_only_in_dbt', (SELECT COUNT(*) FROM (SELECT {colnames} FROM {this} MINUS SELECT {colnames} FROM {legacy})), 0 FROM dual")
    rows.append(f"SELECT 'rows_only_in_legacy', (SELECT COUNT(*) FROM (SELECT {colnames} FROM {legacy} MINUS SELECT {colnames} FROM {this})), 0 FROM dual")
    lineage = []
    for name, n in srcs:
        owner = n.extra["table"].owner
        group, table = plan.reg.register_source(owner, name, cfg)
        lineage.append(f"SELECT 'source_rows:{name}' AS metric, COUNT(*) AS value FROM {{{{ source('{group}', '{table}') }}}}")
    lineage.append(f"SELECT 'target_rows:{tbl.name}', COUNT(*) FROM {this}")
    expectation = ("Row counts should match the source tables: only row-preserving steps are present."
                   if not changers else
                   "Row counts are expected to differ from the sources because of: " + "; ".join(changers) + ".")
    return (
        f"-- Source-to-target reconciliation for {tbl.name} (mapping {plan.mapping.name}, model {model}).\n"
        f"-- Run with: dbt compile --select {model} ; then execute target/compiled/.../analyses/recon_{model}.sql\n"
        f"--\n-- Part 1: lineage counts. {expectation}\n"
        + "\nUNION ALL\n".join(lineage) + ";\n\n"
        f"-- Part 2: parity with the table Informatica loaded (var '{cfg.legacy_schema_var}' = its schema).\n"
        f"-- Any variance <> 0 is a FAIL. Hash metrics use ORA_HASH per column (sum of row hashes).\n"
        f"SELECT metric, dbt_value, legacy_value,\n"
        f"       NVL(dbt_value, 0) - NVL(legacy_value, 0) AS variance,\n"
        f"       CASE WHEN NVL(dbt_value, 0) = NVL(legacy_value, 0) THEN 'PASS' ELSE 'FAIL' END AS status\n"
        f"FROM (\n" + "\nUNION ALL\n".join(rows) + "\n)\nORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, metric;\n"
    )


# ------------------------------------------------------------------- coverage
def _coverage(plan: Plan) -> None:
    mp = plan.mapping
    reg = plan.reg
    groups_by_router: dict[str, list[Node]] = {}
    for n in plan.nodes.values():
        if n.kind == "routergroup":
            groups_by_router.setdefault(n.extra["router"], []).append(n)
    consumers: dict[str, list[str]] = {}
    for n in plan.nodes.values():
        for s in n.seq_ups:
            consumers.setdefault(s, []).append(n.name)

    def row(name, typ, art, layer, status, notes=""):
        plan.coverage.append(CoverageRow(informatica_name=name, informatica_type=typ, dbt_artifact=art,
                                         layer=layer, status=status, notes=notes))

    for inst in mp.instances.values():
        n = plan.nodes[inst.name]
        st = n.status
        notes = "; ".join(dict.fromkeys(n.notes))
        stats = plan.expr_stats.get(n.name)
        if stats:
            notes = f"{stats[0]} expression(s), {stats[1]} flagged" + (f"; {notes}" if notes else "")
        if inst.transformation_name != inst.name:
            notes = f"definition {inst.transformation_name}" + (f"; {notes}" if notes else "")
        if n.kind == "source":
            row(inst.name, "Source Definition", n.model or "(missing)", "stg", st if n.model else Status.UNSUPPORTED, notes)
        elif n.kind == "target":
            art = n.model or "(missing)"
            row(inst.name, "Target Definition", art, "mart", st if n.model else Status.UNSUPPORTED, notes)
        elif n.kind == "sq":
            row(inst.name, n.type_name, n.model or "(missing)", "stg", st if n.model else Status.UNSUPPORTED, notes)
        elif n.kind == "router":
            gs = groups_by_router.get(n.name, [])
            art = ", ".join(f"{g.model}.{g.cte}" for g in gs) or "(no connected groups: rows are dropped as in PowerCenter)"
            row(inst.name, n.type_name, art, "int", st, "; ".join(
                [f"{len(n.tr.groups) if n.tr else 0} group(s)"] + ([notes] if notes else [])))
        elif n.kind == "sequence":
            cons = ", ".join(consumers.get(n.name, [])) or "no consumer"
            row(inst.name, n.type_name, f"inline expression in {cons}", "int", st,
                f"mode={plan.cfg.sequence_mode}" + (f"; {notes}" if notes else ""))
        elif n.kind == "lookup" and n.extra.get("unconnected"):
            used = n.name in plan.lookups_used
            row(inst.name, "Lookup (unconnected)", f"macro {plan.lookup_macros[n.name]}", "int", st,
                ("" if used else "not called by any expression; ") + notes)
        elif n.virtual:
            plan.flag(inst.name, f"{n.type_name} has no connections, so it cannot be converted; if it is called from "
                                 f"an expression or session, replace it manually")
            row(inst.name, n.type_name, "(none)", "int", Status.UNSUPPORTED, "not connected; " + notes)
        else:
            row(inst.name, n.type_name, f"{n.model}.{n.cte}", "int", st, notes)
    for n in plan.nodes.values():
        if n.kind == "routergroup":
            row(n.name, "Router Group", f"{n.model}.{n.cte}", "int", n.status, n.extra["cond"] or "default group")
    for u in mp.unparsed_nodes:
        plan.flag(mp.name, f"mapping child node not understood by the parser: {u}")
    for c in mp.connectors:
        key = (c.from_instance, c.from_field, c.to_instance, c.to_field)
        if key not in plan.consumed:
            plan.flag(c.to_instance, f"connector {c.from_instance}.{c.from_field} -> {c.to_instance}.{c.to_field} was not applied")
    for name in plan.lookup_macros:
        pass


def convert_mapping(mp: Mapping, cfg: Config, reg: Registry) -> Plan:
    plan = build_nodes(mp, cfg, reg)
    topo_order(plan)
    _build_sources(plan)
    _build_sq(plan)
    _build_int(plan)
    _build_marts(plan)
    _coverage(plan)
    return plan
