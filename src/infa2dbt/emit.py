"""Write the dbt project and the reports. Output is deterministic: no timestamps, sorted keys."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .config import Config
from .ir import Status
from .plan import Plan, Registry

LAYER_DIR = {"stg": "staging", "int": "intermediate", "mart": "marts"}


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):  # no &id001 anchors
        return True


def _dump(obj) -> str:
    return yaml.dump(obj, Dumper=_Dumper, sort_keys=False, default_flow_style=False, width=100)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")


def emit_project(out: Path, cfg: Config, reg: Registry, plans: list[Plan]) -> None:
    params = {}
    for p in plans:
        for k, v in p.params.items():
            params[k.lower()] = v
    mats = {"staging": {"+materialized": "view"},
            "intermediate": {"+materialized": cfg.int_materialization},
            "marts": {"+materialized": "table"}}
    _write(out / "dbt_project.yml", _dump({
        "name": cfg.project_name, "version": "1.0.0", "config-version": 2, "profile": cfg.project_name,
        "model-paths": ["models"], "analysis-paths": ["analyses"], "macro-paths": ["macros"],
        "test-paths": ["tests"], "target-path": "target", "clean-targets": ["target", "dbt_packages"],
        "vars": {cfg.legacy_schema_var: "LEGACY_SCHEMA", **dict(sorted(params.items()))},
        "models": {cfg.project_name: mats},
    }))
    _write(out / "packages.yml", _dump({"packages": [{"package": "dbt-labs/dbt_utils", "version": [">=1.1.0", "<2.0.0"]}]}))
    _write(out / "profiles.yml.example", _dump({cfg.project_name: {"target": "dev", "outputs": {"dev": {
        "type": "oracle", "user": "{{ env_var('DBT_ORACLE_USER') }}", "pass": "{{ env_var('DBT_ORACLE_PASSWORD') }}",
        "protocol": "tcp", "host": "{{ env_var('DBT_ORACLE_HOST') }}", "port": 1521,
        "service": "{{ env_var('DBT_ORACLE_SERVICE') }}", "database": "{{ env_var('DBT_ORACLE_DATABASE') }}",
        "schema": "{{ env_var('DBT_ORACLE_SCHEMA') }}", "threads": 4}}}}))

    by_layer: dict[str, list] = {"stg": [], "int": [], "mart": []}
    for name in sorted(reg.models):
        m = reg.models[name]
        _write(out / "models" / LAYER_DIR[m.layer] / f"{m.name}.sql", m.sql)
        entry = {"name": m.name}
        if m.description:
            entry["description"] = m.description
        if m.tests:
            entry["tests"] = m.tests
        if m.columns:
            entry["columns"] = m.columns
        by_layer[m.layer].append(entry)
    for layer, models in by_layer.items():
        if models:
            _write(out / "models" / LAYER_DIR[layer] / f"_{LAYER_DIR[layer]}__models.yml",
                   _dump({"version": 2, "models": models}))
    if reg.sources:
        srcs = []
        for gname in sorted(reg.sources):
            g = reg.sources[gname]
            entry = {"name": gname}
            if g["schema"]:
                entry["schema"] = g["schema"]
            entry["tables"] = [{"name": t} for t in sorted(g["tables"])]
            srcs.append(entry)
        _write(out / "models" / "staging" / "_sources.yml", _dump({"version": 2, "sources": srcs}))
    for name in sorted(reg.macros):
        _write(out / "macros" / f"{name}.sql", reg.macros[name])
    for name in sorted(reg.analyses):
        _write(out / "analyses" / f"{name}.sql", reg.analyses[name])
    if reg.sequences:
        _write(out / "ddl" / "sequences.sql", "\n".join(reg.sequences[k] for k in sorted(reg.sequences)))


def coverage_markdown(plans: list[Plan]) -> str:
    lines = ["# Informatica-to-dbt coverage matrix", ""]
    for p in plans:
        lines += [f"## Mapping `{p.mapping.name}`", "",
                  "| Informatica name | Type | dbt artifact | Layer | Status | Notes |",
                  "|---|---|---|---|---|---|"]
        for r in p.coverage:
            lines.append(f"| {r.informatica_name} | {r.informatica_type} | `{r.dbt_artifact}` | {r.layer} | "
                         f"{r.status.value} | {r.notes.replace('|', '/')} |")
        counts = {s: sum(1 for r in p.coverage if r.status == s and r.informatica_type != "Router Group") for s in Status}
        lines += ["", f"Instances: {len(p.mapping.instances)} (rows incl. router groups: {len(p.coverage)}) | converted: {counts[Status.CONVERTED]} | "
                      f"needs review: {counts[Status.NEEDS_REVIEW]} | unsupported: {counts[Status.UNSUPPORTED]}", ""]
    return "\n".join(lines)


def review_markdown(plans: list[Plan]) -> str:
    lines = ["# Manual review report", "",
             "Everything below was converted with a stand-in or not at all. Each item must be resolved by a person.", ""]
    total = 0
    for p in plans:
        if not p.review:
            continue
        lines += [f"## Mapping `{p.mapping.name}`", ""]
        for i, r in enumerate(p.review, 1):
            total += 1
            lines.append(f"{i}. **{r.where}**: {r.reason}")
            if r.original:
                lines.append(f"   - original: `{r.original.strip()[:300]}`")
        lines.append("")
    if not total:
        lines.append("No items need review.")
    lines += ["", "# Warnings (behaviour that may differ from PowerCenter)", ""]
    n = 0
    for p in plans:
        for w in dict.fromkeys(p.warnings):
            n += 1
            lines.append(f"- {w}")
    if not n:
        lines.append("None.")
    return "\n".join(lines)


def conversion_log(plans: list[Plan]) -> str:
    out = []
    for p in plans:
        out.append({
            "mapping": p.mapping.name,
            "instances": sorted(p.mapping.instances),
            "models": sorted(m for m, s in p.reg.models.items() if any(x.startswith(p.mapping.name + ":") for x in s.nodes)),
            "review": [r.model_dump() for r in p.review],
            "warnings": sorted(set(p.warnings)),
            "expressions": {k: {"total": v[0], "flagged": v[1]} for k, v in sorted(p.expr_stats.items())},
        })
    return json.dumps(out, indent=2, sort_keys=True)


def emit_reports(out: Path, plans: list[Plan]) -> None:
    _write(out / "reports" / "coverage_matrix.md", coverage_markdown(plans))
    _write(out / "reports" / "manual_review.md", review_markdown(plans))
    _write(out / "reports" / "conversion_log.json", conversion_log(plans))
