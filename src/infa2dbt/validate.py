"""Quality gates: zero-drop, expression accounting, and static SQL checks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import sqlglot
from sqlglot.errors import ParseError

from .ir import Status
from .plan import Plan, Registry


@dataclass
class Findings:
    errors: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.errors.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


# ------------------------------------------------------------------ zero-drop
def zero_drop(plan: Plan, reg: Registry) -> list[str]:
    """Every INSTANCE and every TRANSFORMATION in the export must appear in the coverage matrix."""
    mp = plan.mapping
    errs: list[str] = []
    covered = {r.informatica_name for r in plan.coverage}
    for name in sorted(mp.instances):
        if name not in covered:
            errs.append(f"{mp.name}: instance {name} is missing from the coverage matrix")
    covered_defs = {mp.instances[n].transformation_name for n in covered if n in mp.instances}
    for tname in sorted(mp.transformations):
        if tname not in covered_defs:
            errs.append(f"{mp.name}: transformation {tname} has no instance in the coverage matrix")
    for t in sorted(mp.sources):
        if t not in covered_defs and any(i.transformation_name == t and i.type == "SOURCE" for i in mp.instances.values()):
            errs.append(f"{mp.name}: source {t} not covered")
    for r in plan.coverage:
        if "(missing)" in r.dbt_artifact:
            errs.append(f"{mp.name}: {r.informatica_name} has no dbt artifact")
            continue
        m = re.match(r"^([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)$", r.dbt_artifact.split(",")[0].strip())
        if m and r.layer in ("int", "stg", "mart") and m.group(1) in reg.models:
            if f"{m.group(2)} AS (" not in reg.models[m.group(1)].sql:
                errs.append(f"{mp.name}: CTE {m.group(2)} for {r.informatica_name} not found in {m.group(1)}")
        elif m and m.group(1) not in reg.models and r.layer != "int":
            errs.append(f"{mp.name}: model {m.group(1)} for {r.informatica_name} was not written")
    return errs


def expression_gate(plan: Plan) -> list[str]:
    """Every expression in Expression/Aggregator/Filter nodes must have been translated."""
    errs = []
    for n in plan.nodes.values():
        if n.tr is None or n.kind not in ("expression", "aggregator", "filter"):
            continue
        expected = sum(1 for p in n.tr.ports if p.expression and (n.kind != "aggregator" or p.expr_type.upper() != "GROUPBY"))
        if n.kind == "filter" and (n.tr.attrs.get("Filter Condition") or "TRUE").strip().upper() != "TRUE":
            expected += 1
        got = plan.expr_stats.get(n.name, [0, 0])[0]
        if got < expected:
            errs.append(f"{plan.mapping.name}: {n.name} has {expected} expression(s) but only {got} were translated")
    return errs


# ----------------------------------------------------------------- static SQL
class _RenderError(Exception):
    pass


def _env(macros: dict[str, str]):
    """A Jinja environment that mimics the dbt functions the generated code uses."""
    import datetime

    import jinja2

    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    g = env.globals
    g["config"] = lambda *a, **k: ""
    g["ref"] = lambda name, *a: name
    g["source"] = lambda grp, tbl: f"{grp}_{tbl}"
    g["var"] = lambda name, default=None: f"var_{name}" if default is None else str(default)  # identifier-safe stand-in
    g["this"] = "this_table"
    g["run_started_at"] = datetime.datetime(2000, 1, 1)
    for name, text in macros.items():
        mod = env.from_string(text).module
        if not hasattr(mod, name):
            raise _RenderError(f"macro file for {name} does not define it")
        g[name] = getattr(mod, name)
    return env


def render_for_parse(sql: str, macros: dict[str, str] | None = None) -> str:
    try:
        return _env(macros or {}).from_string(sql).render()
    except Exception as e:  # jinja syntax errors, undefined macros, bad macro arguments
        raise _RenderError(f"{type(e).__name__}: {e}") from e


def check_sql(name: str, sql: str, macros: dict[str, str] | None = None) -> list[str]:
    try:
        rendered = render_for_parse(sql, macros)
    except _RenderError as e:
        return [f"{name}: Jinja does not render: {str(e)[:200]}"]
    try:
        stmts = [e for e in sqlglot.parse(rendered, read="oracle") if e is not None]
    except ParseError as e:
        return [f"{name}: SQL does not parse as Oracle: {str(e).splitlines()[0][:200]}"]
    except Exception as e:  # tokenizer errors etc.
        return [f"{name}: SQL could not be tokenized: {str(e)[:200]}"]
    if not stmts:
        return [f"{name}: empty SQL"]
    return []


def check_macro(name: str, text: str, macros: dict[str, str] | None = None) -> list[str]:
    """Call the macro with dummy arguments and parse what it returns."""
    try:
        env = _env(macros or {name: text})
        fn = env.globals[name]
        n_args = fn.arguments.__len__() if hasattr(fn, "arguments") else 1
        body = str(fn(*["1"] * n_args))
    except Exception as e:
        return [f"macro {name}: Jinja does not render: {str(e)[:200]}"]
    return check_sql(f"macro {name}", f"SELECT {body.strip()} AS v FROM dual")


def model_graph_errors(reg: Registry) -> list[str]:
    errs, g = [], nx.DiGraph()
    sources = {f"{gname}.{t}" for gname, s in reg.sources.items() for t in s["tables"]}
    for name, m in reg.models.items():
        g.add_node(name)
        for ref in re.findall(r"ref\('([^']+)'\)", m.sql):
            if ref not in reg.models:
                errs.append(f"{name}: ref('{ref}') points to a model that does not exist")
            else:
                g.add_edge(ref, name)
        for grp, tbl in re.findall(r"source\('([^']+)',\s*'([^']+)'\)", m.sql):
            if f"{grp}.{tbl}" not in sources:
                errs.append(f"{name}: source('{grp}', '{tbl}') is not declared in sources.yml")
    if not nx.is_directed_acyclic_graph(g):
        errs.append("model dependency graph contains a cycle")
    return errs


def validate_all(plans: list[Plan], reg: Registry) -> Findings:
    f = Findings()
    for p in plans:
        for e in zero_drop(p, reg) + expression_gate(p):
            f.add(e)
    for name in sorted(reg.models):
        for e in check_sql(f"model {name}", reg.models[name].sql, reg.macros):
            f.add(e)
    for name in sorted(reg.macros):
        for e in check_macro(name, reg.macros[name], reg.macros):
            f.add(e)
    for name in sorted(reg.analyses):
        for e in check_sql(f"analysis {name}", reg.analyses[name], reg.macros):
            f.add(e)
    for e in model_graph_errors(reg):
        f.add(e)
    return f


def validate_dir(out: Path) -> Findings:
    """Re-check an already generated project (used by `infa2dbt check`)."""
    f = Findings()
    macros = {p.stem: p.read_text() for p in sorted((out / "macros").glob("*.sql"))}
    models = sorted((out / "models").rglob("*.sql"))
    if not models:
        f.add(f"{out}: no models found")
    for p in models:
        for e in check_sql(f"model {p.stem}", p.read_text(), macros):
            f.add(e)
    for name, text in macros.items():
        for e in check_macro(name, text, macros):
            f.add(e)
    for p in sorted((out / "analyses").glob("*.sql")):
        for e in check_sql(f"analysis {p.stem}", p.read_text(), macros):
            f.add(e)
    return f
