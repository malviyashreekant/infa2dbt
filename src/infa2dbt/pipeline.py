"""End-to-end conversion: XML files -> dbt project + reports + gates."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .build import convert_mapping
from .config import Config
from .emit import emit_project, emit_reports
from .parser import parse_xml
from .plan import Plan, Registry
from .validate import Findings, validate_all


@dataclass
class Result:
    plans: list[Plan] = field(default_factory=list)
    findings: Findings = field(default_factory=Findings)
    review_count: int = 0
    models: int = 0


def collect_inputs(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.suffix.lower() == ".xml")
    return [path]


def convert_paths(paths: list[Path], out: Path, cfg: Config) -> Result:
    reg = Registry()
    res = Result()
    seen: set[str] = set()
    parsed = []
    for p in sorted(paths):
        for mp in parse_xml(p):
            if mp.name in seen:
                res.findings.add(f"duplicate mapping name {mp.name} (file {p})")
                continue
            seen.add(mp.name)
            parsed.append(mp)
    # converting in name order makes model naming (collision prefixes) independent of file order
    for mp in sorted(parsed, key=lambda m: (m.name, m.folder)):
        res.plans.append(convert_mapping(mp, cfg, reg))
    if not res.plans:
        res.findings.add("no MAPPING nodes found in the input")
    gates = validate_all(res.plans, reg)
    res.findings.errors += gates.errors
    emit_project(out, cfg, reg, res.plans)
    emit_reports(out, res.plans)
    res.review_count = sum(len(p.review) for p in res.plans)
    res.models = len(reg.models)
    return res
