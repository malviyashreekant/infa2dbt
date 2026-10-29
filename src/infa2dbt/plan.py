"""Graph planning: turn a Mapping into nodes, then group nodes into dbt models.

Layers
  stg_   source definitions and Source Qualifiers
  int_   every other transformation (consecutive linear steps share one model as CTEs)
  fct_/dim_  target definitions
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx

from .config import Config
from .expr import Context, translate
from .ir import CoverageRow, Mapping, Port, ReviewItem, Status, Transformation
from .oracle import cte_name, ident

KIND_RULES = [
    ("source qualifier", "sq"), ("expression", "expression"), ("lookup", "lookup"),
    ("filter", "filter"), ("router", "router"), ("aggregator", "aggregator"),
    ("joiner", "joiner"), ("sorter", "sorter"), ("sequence", "sequence"),
    ("update strategy", "update"), ("union", "union"), ("rank", "rank"),
    ("normalizer", "normalizer"), ("stored procedure", "unsupported"),
    ("custom", "unsupported"), ("java", "unsupported"), ("sql", "unsupported"),
    ("transaction control", "unsupported"), ("mapplet", "unsupported"),
]
CHAIN_BREAKERS = {"joiner", "union"}  # always start a new model


class PlanError(Exception):
    pass


def kind_of(type_name: str) -> str:
    t = type_name.lower()
    for needle, kind in KIND_RULES:
        if needle in t:
            return kind
    return "unsupported"


@dataclass
class Node:
    name: str
    kind: str
    type_name: str
    tr: Optional[Transformation] = None
    in_map: dict[str, tuple[str, str]] = field(default_factory=dict)  # port -> (upstream node, upstream column)
    ups: list[str] = field(default_factory=list)  # distinct row-source upstream nodes
    seq_ups: list[str] = field(default_factory=list)  # Sequence Generator nodes feeding this node
    outs: list[str] = field(default_factory=list)  # output column names (raw port names)
    cte: str = ""
    model: str = ""
    layer: str = ""
    virtual: bool = False  # no CTE / model (sequence, unconnected lookup)
    extra: dict = field(default_factory=dict)
    ctes: list[tuple[str, str]] = field(default_factory=list)  # filled by generation
    status: Status = Status.CONVERTED
    notes: list[str] = field(default_factory=list)


@dataclass
class ModelSpec:
    name: str
    layer: str  # stg | int | mart
    sql: str = ""
    materialized: str = "view"
    nodes: list[str] = field(default_factory=list)  # Informatica instance names replaced
    columns: list[dict] = field(default_factory=list)  # for schema.yml
    tests: list[dict] = field(default_factory=list)  # model-level tests
    description: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class Registry:
    """Project-wide state shared across mappings."""
    used_models: set[str] = field(default_factory=set)
    sources: dict[str, dict[str, set[str]]] = field(default_factory=dict)  # group -> {schema, tables}
    source_defs: dict[str, object] = field(default_factory=dict)  # table name -> Table
    macros: dict[str, str] = field(default_factory=dict)
    sequences: dict[str, str] = field(default_factory=dict)
    models: dict[str, ModelSpec] = field(default_factory=dict)
    analyses: dict[str, str] = field(default_factory=dict)

    def unique(self, base: str, mapping: str) -> str:
        name = base
        if name in self.used_models:
            name = f"{base.split('_', 1)[0]}_{ident(mapping)}__{base.split('_', 1)[1]}"
        n = 2
        while name in self.used_models:
            name = f"{name}_{n}"
            n += 1
        self.used_models.add(name)
        return name

    def register_source(self, owner: str, table: str, cfg: Config) -> tuple[str, str]:
        owner = (owner or cfg.default_source_schema or "").upper()
        group = cfg.source_name if not owner else (
            cfg.source_name if owner == cfg.default_source_schema.upper() else f"{cfg.source_name}_{ident(owner)}")
        g = self.sources.setdefault(group, {"schema": owner, "tables": set()})
        g["tables"].add(table.upper())
        return group, table.upper()


@dataclass
class Plan:
    mapping: Mapping
    cfg: Config
    reg: Registry
    nodes: dict[str, Node] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    coverage: list[CoverageRow] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lookup_macros: dict[str, str] = field(default_factory=dict)
    lookups_used: set[str] = field(default_factory=set)
    expr_stats: dict[str, list[int]] = field(default_factory=dict)  # node -> [total, flagged]
    consumed: set[tuple] = field(default_factory=set)
    mart_models: list[str] = field(default_factory=list)

    # ---- bookkeeping -----------------------------------------------------
    @property
    def params(self) -> dict[str, str]:
        p = dict(self.mapping.parameters)
        p.update(self.cfg.parameters)
        return p

    def flag(self, node: str, reason: str, original: str = "") -> None:
        self.review.append(ReviewItem(where=f"{self.mapping.name}:{node}", reason=reason, original=original))
        n = self.nodes.get(node)
        if n and n.status == Status.CONVERTED:
            n.status = Status.NEEDS_REVIEW

    def warn(self, node: str, msg: str) -> None:
        self.warnings.append(f"{self.mapping.name}:{node}: {msg}")
        n = self.nodes.get(node)
        if n:
            n.notes.append(msg)

    def tx(self, node: str, label: str, text: str, ref, *, cond: bool = False) -> str:
        from .expr.translator import translate_condition
        ctx = Context(ref=ref, params=self.params, lookup_macros=self.lookup_macros,
                      overrides=self.cfg.function_overrides)
        t = (translate_condition if cond else translate)(text, ctx)
        st = self.expr_stats.setdefault(node, [0, 0])
        st[0] += 1
        for r in t.review:
            st[1] += 1
            self.flag(node, f"{label}: {r}", text)
        for w in t.warnings:
            self.warn(node, f"{label}: {w}")
        self.lookups_used |= t.lookups
        return t.sql


def build_nodes(mp: Mapping, cfg: Config, reg: Registry) -> Plan:
    plan = Plan(mapping=mp, cfg=cfg, reg=reg)
    nodes = plan.nodes

    # 1. one node per instance ---------------------------------------------
    for inst in mp.instances.values():
        if inst.type == "SOURCE":
            tbl = mp.sources.get(inst.transformation_name)
            n = Node(inst.name, "source", "Source Definition")
            n.extra["table"] = tbl
            n.outs = [f.name for f in tbl.fields] if tbl else []
            if tbl is None:
                plan.flag(inst.name, f"source definition {inst.transformation_name} not found in export")
        elif inst.type == "TARGET":
            tbl = mp.targets.get(inst.transformation_name)
            n = Node(inst.name, "target", "Target Definition")
            n.extra["table"] = tbl
            if tbl is None:
                plan.flag(inst.name, f"target definition {inst.transformation_name} not found in export")
        else:
            tr = mp.transformations.get(inst.transformation_name)
            type_name = (tr.type if tr else inst.transformation_type) or ("Mapplet" if inst.type == "MAPPLET" else "")
            n = Node(inst.name, kind_of(type_name), type_name, tr)
            if tr is None:
                n.kind = "unsupported"
                plan.flag(inst.name, f"transformation {inst.transformation_name} ({type_name}) not found in export")
            else:
                n.outs = [p.name for p in tr.ports if p.is_output and not p.is_variable]
        n.cte = cte_name(inst.name)
        nodes[inst.name] = n

    # 2. routers: one virtual node per connected group -----------------------
    router_groups: dict[str, set[str]] = {}
    for c in mp.connectors:
        src = nodes.get(c.from_instance)
        if src and src.kind == "router" and src.tr:
            p = src.tr.port(c.from_field)
            if p and p.group:
                router_groups.setdefault(src.name, set()).add(p.group)
    for rname, groups in router_groups.items():
        r = nodes[rname]
        conds = dict(r.tr.groups)
        for g in sorted(groups):
            v = Node(f"{rname}.{g}", "routergroup", "Router Group", r.tr)
            v.extra.update(router=rname, group=g, cond=conds.get(g, ""),
                           is_default=(g.upper().startswith("DEFAULT") or not conds.get(g)),
                           all_conds=[(gn, ex) for gn, ex in r.tr.groups if ex])
            v.outs = [p.name for p in r.tr.ports if p.group == g]
            v.cte = cte_name(f"{rname}_{g}")
            nodes[v.name] = v

    # 3. wire connectors into in_map ----------------------------------------
    for c in mp.connectors:
        dst = nodes.get(c.to_instance)
        src_name = c.from_instance
        src = nodes.get(src_name)
        if dst is None or src is None:
            plan.flag(c.to_instance or c.from_instance,
                      f"connector {c.from_instance}.{c.from_field} -> {c.to_instance}.{c.to_field} references a missing instance")
            continue
        if src.kind == "router" and src.tr:
            p = src.tr.port(c.from_field)
            if p and p.group:
                src_name = f"{src.name}.{p.group}"
        if dst.kind == "target" and dst.extra.get("table") is not None:
            ok = any(f.name == c.to_field for f in dst.extra["table"].fields)
        elif dst.tr is not None:
            ok = dst.tr.port(c.to_field) is not None
        else:
            ok = True
        if not ok:
            plan.flag(dst.name, f"connector targets unknown port {c.to_field}")
            continue
        dst.in_map[c.to_field] = (src_name, c.from_field)
        plan.consumed.add((c.from_instance, c.from_field, c.to_instance, c.to_field))

    # routers' virtual nodes inherit the router's inputs
    for name, v in list(nodes.items()):
        if v.kind == "routergroup":
            v.in_map = dict(nodes[v.extra["router"]].in_map)

    # 4. upstream lists ----------------------------------------------------
    for n in nodes.values():
        for _, (up, _) in n.in_map.items():
            u = nodes[up]
            if u.kind == "sequence":
                if up not in n.seq_ups:
                    n.seq_ups.append(up)
            elif up not in n.ups:
                n.ups.append(up)

    # 5. virtual nodes -------------------------------------------------------
    for n in nodes.values():
        if n.kind == "sequence":
            n.virtual = True
        if n.kind == "lookup":
            touched = bool(n.in_map) or any(
                c.from_instance == n.name for c in mp.connectors)
            if not touched:
                n.extra["unconnected"] = True
                n.virtual = True
                plan.lookup_macros[n.name] = "lkp_" + ident(n.name)
        if n.kind == "router":
            n.virtual = True  # replaced by its group nodes
        if n.kind == "unsupported" and not n.ups and not n.in_map:
            n.virtual = True
    # routers whose router node has no connected group still count as covered
    return plan


def topo_order(plan: Plan) -> None:
    g = nx.DiGraph()
    for name, n in plan.nodes.items():
        g.add_node(name)
        for u in n.ups:
            g.add_edge(u, name)
        for u in n.seq_ups:
            g.add_edge(u, name)
    try:
        plan.order = list(nx.lexicographical_topological_sort(g))
    except nx.NetworkXUnfeasible as e:
        raise PlanError(f"mapping {plan.mapping.name} contains a cycle") from e


def segment(plan: Plan) -> list[list[str]]:
    """Group int-layer nodes into chains. Returns list of node-name lists in topo order."""
    nodes = plan.nodes
    downstream: dict[str, set[str]] = {n: set() for n in nodes}
    for n in nodes.values():
        if n.virtual:
            continue
        for u in n.ups:
            downstream[u].add(n.name)

    segments: list[list[str]] = []
    seg_of: dict[str, int] = {}
    for name in plan.order:
        n = nodes[name]
        if n.virtual or n.kind in ("source", "sq", "target"):
            continue
        n.layer = "int"
        chain_to = None
        if n.kind not in CHAIN_BREAKERS and len(n.ups) == 1:
            u = nodes[n.ups[0]]
            if u.name in seg_of and len(downstream[u.name]) == 1:
                chain_to = seg_of[u.name]
        if chain_to is None:
            segments.append([name])
            seg_of[name] = len(segments) - 1
        else:
            segments[chain_to].append(name)
            seg_of[name] = chain_to
    return segments
